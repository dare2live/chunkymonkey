#!/usr/bin/env python3
"""M5 step 1：raw_lhb_daily → fact_lhb_event ETL（§2 新数据源接入候选分析）。

目标：把已落库的龙虎榜日度明细事件化，与 fact_institution_event 形成平行结构，
使得 run_portfolio_mvp.py 可以把 LHB 作为独立事件源进入 simulator 回测。

Schema 设计：
  PK (trade_date, stock_code) —— 同一股票同日多 rank_reason 去重（取 max net_buy）
  inst_buy_seats  从 interpretation 解析 "N家机构买入"（N 为整数），无匹配则 0
  is_inst_net_buy is_inst_net_buy = (net_buy > 0 AND inst_buy_seats >= 1)
  gain_20d/60d    从 market.duckdb price_kline qfq 重算（不信任 raw.post_* 字段）
  max_drawdown_*  同 fact_institution_event 口径

PIT：本脚本每次重建全量 fact_lhb_event；forward return 只在价量已覆盖的窗口有值。
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import get_conn
from services.market_db import get_market_conn

logger = logging.getLogger("build_lhb_events")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


FACT_LHB_EVENT_DDL = """
DROP TABLE IF EXISTS fact_lhb_event;
CREATE TABLE fact_lhb_event (
    trade_date        TEXT NOT NULL,
    stock_code        TEXT NOT NULL,
    n_rank_reasons    INTEGER,
    rank_reasons      TEXT,
    close_price       REAL,
    change_pct        REAL,
    net_buy           REAL,
    buy_amount        REAL,
    sell_amount       REAL,
    turnover          REAL,
    turnover_rate     REAL,
    float_cap         REAL,
    net_buy_pct       REAL,
    interpretation    TEXT,
    inst_buy_seats    INTEGER,
    is_inst_net_buy   INTEGER,
    gain_20d          REAL,
    gain_60d          REAL,
    max_drawdown_20d  REAL,
    max_drawdown_60d  REAL,
    built_at          TEXT,
    PRIMARY KEY (trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_flhb_stock ON fact_lhb_event(stock_code);
CREATE INDEX IF NOT EXISTS idx_flhb_date ON fact_lhb_event(trade_date);
CREATE INDEX IF NOT EXISTS idx_flhb_inst ON fact_lhb_event(is_inst_net_buy);
"""


INST_BUY_RE = re.compile(r"(\d+)\s*家\s*机构\s*(?:买入|净买入)")


def _parse_inst_seats(interp: Optional[str]) -> int:
    if not interp:
        return 0
    m = INST_BUY_RE.search(interp)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def load_raw_lhb(conn) -> pd.DataFrame:
    logger.info("加载 raw_lhb_daily")
    df = pd.read_sql_query(
        """
        SELECT trade_date, stock_code, rank_reason, interpretation,
               close_price, change_pct, net_buy, buy_amount, sell_amount,
               turnover, turnover_rate, float_cap, net_buy_pct
        FROM raw_lhb_daily
        """,
        conn,
    )
    logger.info("raw 行数 %d", len(df))
    return df


def dedup_and_parse(df: pd.DataFrame) -> pd.DataFrame:
    """按 (trade_date, stock_code) 去重：
    - net_buy/buy_amount/sell_amount 取 max（不同 rank_reason 理论上值一致，max 保险）
    - rank_reasons 拼接，interpretation 取 net_buy 最大那行的
    - inst_buy_seats 取所有 rank_reason 中的最大值
    """
    df = df.copy()
    df["inst_buy_seats_per_row"] = df["interpretation"].apply(_parse_inst_seats)

    # 按每组 max net_buy 排序，再 groupby
    df["_net_buy_fillna"] = df["net_buy"].fillna(-1)
    df = df.sort_values(
        ["trade_date", "stock_code", "_net_buy_fillna"],
        ascending=[True, True, False],
    )

    agg = (
        df.groupby(["trade_date", "stock_code"], sort=False)
        .agg(
            n_rank_reasons=("rank_reason", "count"),
            rank_reasons=("rank_reason", lambda s: "|".join(sorted(set(s)))),
            close_price=("close_price", "first"),
            change_pct=("change_pct", "first"),
            net_buy=("net_buy", "max"),
            buy_amount=("buy_amount", "max"),
            sell_amount=("sell_amount", "max"),
            turnover=("turnover", "max"),
            turnover_rate=("turnover_rate", "max"),
            float_cap=("float_cap", "first"),
            net_buy_pct=("net_buy_pct", "max"),
            interpretation=("interpretation", "first"),
            inst_buy_seats=("inst_buy_seats_per_row", "max"),
        )
        .reset_index()
    )
    agg["is_inst_net_buy"] = (
        (agg["net_buy"].fillna(0) > 0) & (agg["inst_buy_seats"] >= 1)
    ).astype(int)
    logger.info(
        "去重后 %d 事件; is_inst_net_buy=1: %d",
        len(agg),
        int(agg["is_inst_net_buy"].sum()),
    )
    return agg


def compute_forward_returns(events: pd.DataFrame) -> pd.DataFrame:
    """对每个事件计算 gain_20d / gain_60d / max_drawdown_20d/60d。

    entry_price = trade_date 后第 1 个交易日收盘（T+1 open 近似为 close）
    gain_Nd = close_{T+N交易日} / entry_price - 1
    max_drawdown_Nd = min(close_t / entry_price - 1) over t in [T+1, T+N]
    """
    logger.info("加载 price_kline（用于 forward return）")
    codes = sorted(events["stock_code"].astype(str).unique())
    mkt = get_market_conn()
    px = pd.read_sql_query(
        f"""
        SELECT code, date, close
        FROM price_kline
        WHERE freq='daily' AND adjust='qfq'
          AND code IN ({','.join(['?']*len(codes))})
        """,
        mkt,
        params=codes,
    )
    mkt.close()
    logger.info("price_kline 行 %d", len(px))

    px = px.sort_values(["code", "date"])
    # 每 code -> date -> close 的 dict 便于按索引定位
    grouped = {c: g.reset_index(drop=True) for c, g in px.groupby("code", sort=False)}

    out = events.copy()
    out["gain_20d"] = np.nan
    out["gain_60d"] = np.nan
    out["max_drawdown_20d"] = np.nan
    out["max_drawdown_60d"] = np.nan

    for idx, row in out.iterrows():
        code = str(row["stock_code"])
        trade_date = row["trade_date"]
        g = grouped.get(code)
        if g is None or g.empty:
            continue
        after = g[g["date"] > trade_date]
        if after.empty:
            continue
        entry_price = float(after.iloc[0]["close"])
        if entry_price <= 0 or pd.isna(entry_price):
            continue
        for n, col_gain, col_mdd in [(20, "gain_20d", "max_drawdown_20d"),
                                      (60, "gain_60d", "max_drawdown_60d")]:
            window = after.iloc[1 : 1 + n]  # 从 T+2 交易日开始到 T+N+1
            if window.empty:
                continue
            exit_price = float(window.iloc[-1]["close"])
            out.at[idx, col_gain] = exit_price / entry_price - 1
            path_ret = window["close"].astype(float) / entry_price - 1
            out.at[idx, col_mdd] = float(path_ret.min())
    logger.info(
        "forward return 覆盖率 20d=%.1f%%, 60d=%.1f%%",
        100 * out["gain_20d"].notna().mean(),
        100 * out["gain_60d"].notna().mean(),
    )
    return out


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _insert_frame(conn, table_name: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    duck = conn.raw if hasattr(conn, "raw") else conn
    temp_name = f"_{table_name}_insert"
    duck.register(temp_name, df)
    try:
        columns = ", ".join(_quote_ident(col) for col in df.columns)
        duck.execute(
            f"INSERT INTO {_quote_ident(table_name)} ({columns}) "
            f"SELECT {columns} FROM {_quote_ident(temp_name)}"
        )
    finally:
        duck.unregister(temp_name)


def write_fact(conn, events: pd.DataFrame) -> None:
    conn.executescript(FACT_LHB_EVENT_DDL)
    events = events.copy()
    events["built_at"] = datetime.utcnow().isoformat()
    cols = [
        "trade_date", "stock_code", "n_rank_reasons", "rank_reasons",
        "close_price", "change_pct", "net_buy", "buy_amount", "sell_amount",
        "turnover", "turnover_rate", "float_cap", "net_buy_pct",
        "interpretation", "inst_buy_seats", "is_inst_net_buy",
        "gain_20d", "gain_60d", "max_drawdown_20d", "max_drawdown_60d",
        "built_at",
    ]
    _insert_frame(conn, "fact_lhb_event", events[cols])
    conn.commit()
    logger.info("写入 fact_lhb_event %d 行", len(events))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        raw = load_raw_lhb(conn)
        dedup = dedup_and_parse(raw)
        enriched = compute_forward_returns(dedup)
        if not args.dry_run:
            write_fact(conn, enriched)
        else:
            logger.info("DRY RUN: 不落库; enriched rows=%d", len(enriched))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
