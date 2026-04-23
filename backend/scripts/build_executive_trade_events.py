#!/usr/bin/env python3
"""M5 step 2：ak.stock_ggcg_em → raw_executive_trade → fact_executive_trade_event

数据源：东方财富-高管持股（实际包含所有股东增减持，非仅限高管）
主键：(notice_date, stock_code, direction)  —— 同日同股多股东合并

设计：
  raw_executive_trade   每条原始记录（股东级）
  fact_executive_trade_event  按 (notice_date, stock_code, direction) 聚合 + forward return
    增持 = buy, 减持 = sell
    是否个人：股东名称不含公司关键字

Label：gain_20d/60d/max_drawdown_20d/60d（从 price_kline qfq 重算，与 fact_lhb_event 口径一致）

幂等：每次 DROP 重建 raw + fact。不增量。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import get_conn
from services.market_db import get_market_conn

logger = logging.getLogger("build_exec_trade")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


RAW_DDL = """
DROP TABLE IF EXISTS raw_executive_trade;
CREATE TABLE raw_executive_trade (
    notice_date         TEXT NOT NULL,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    shareholder_name    TEXT,
    direction           TEXT,
    change_qty_wan      REAL,
    change_pct_total    REAL,
    change_pct_float    REAL,
    after_qty_wan       REAL,
    after_pct_total     REAL,
    start_date          TEXT,
    end_date            TEXT,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rxt_stock ON raw_executive_trade(stock_code);
CREATE INDEX IF NOT EXISTS idx_rxt_date ON raw_executive_trade(notice_date);
"""


FACT_DDL = """
DROP TABLE IF EXISTS fact_executive_trade_event;
CREATE TABLE fact_executive_trade_event (
    notice_date             TEXT NOT NULL,
    stock_code              TEXT NOT NULL,
    direction               TEXT NOT NULL,
    n_shareholders          INTEGER,
    total_change_qty_wan    REAL,
    total_change_pct_total  REAL,
    max_change_pct_total    REAL,
    any_individual          INTEGER,
    any_corporate           INTEGER,
    gain_20d                REAL,
    gain_60d                REAL,
    max_drawdown_20d        REAL,
    max_drawdown_60d        REAL,
    built_at                TEXT,
    PRIMARY KEY (notice_date, stock_code, direction)
);
CREATE INDEX IF NOT EXISTS idx_fxt_stock ON fact_executive_trade_event(stock_code);
CREATE INDEX IF NOT EXISTS idx_fxt_date ON fact_executive_trade_event(notice_date);
CREATE INDEX IF NOT EXISTS idx_fxt_dir ON fact_executive_trade_event(direction);
"""


CORPORATE_HINTS = ("公司", "集团", "基金", "管理", "合伙", "企业", "投资", "科技",
                   "有限", "股份", "控股", "实业", "产业", "资产")


def is_corporate(name: str) -> bool:
    if not name:
        return False
    return any(k in name for k in CORPORATE_HINTS)


def fetch_raw() -> pd.DataFrame:
    import akshare as ak
    logger.info("调用 ak.stock_ggcg_em(symbol='全部')")
    df = ak.stock_ggcg_em(symbol="全部")
    logger.info("返回 %d 行", len(df))
    return df


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.rename(columns={
        "代码": "stock_code",
        "名称": "stock_name",
        "股东名称": "shareholder_name",
        "持股变动信息-增减": "direction",
        "持股变动信息-变动数量": "change_qty_wan",
        "持股变动信息-占总股本比例": "change_pct_total",
        "持股变动信息-占流通股比例": "change_pct_float",
        "变动后持股情况-持股总数": "after_qty_wan",
        "变动后持股情况-占总股本比例": "after_pct_total",
        "变动开始日": "start_date",
        "变动截止日": "end_date",
        "公告日": "notice_date",
    })
    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    # 过滤 direction
    df = df[df["direction"].isin(["增持", "减持"])]
    # 过滤异常日期
    df = df[df["notice_date"].notna()]
    # 保留需要的列
    keep = [
        "notice_date", "stock_code", "stock_name", "shareholder_name",
        "direction", "change_qty_wan", "change_pct_total", "change_pct_float",
        "after_qty_wan", "after_pct_total", "start_date", "end_date",
    ]
    return df[keep].reset_index(drop=True)


def aggregate_events(raw: pd.DataFrame) -> pd.DataFrame:
    logger.info("按 (notice_date, stock_code, direction) 聚合")
    raw = raw.copy()
    raw["is_ind"] = (~raw["shareholder_name"].fillna("").map(is_corporate)).astype(int)
    raw["is_corp"] = (raw["shareholder_name"].fillna("").map(is_corporate)).astype(int)
    agg = (
        raw.groupby(["notice_date", "stock_code", "direction"], sort=False)
        .agg(
            n_shareholders=("shareholder_name", "count"),
            total_change_qty_wan=("change_qty_wan", "sum"),
            total_change_pct_total=("change_pct_total", "sum"),
            max_change_pct_total=("change_pct_total", "max"),
            any_individual=("is_ind", "max"),
            any_corporate=("is_corp", "max"),
        )
        .reset_index()
    )
    agg["direction"] = agg["direction"].map({"增持": "buy", "减持": "sell"})
    logger.info("聚合后 %d 事件（buy=%d, sell=%d）",
                len(agg),
                int((agg["direction"] == "buy").sum()),
                int((agg["direction"] == "sell").sum()))
    return agg


def compute_forward_returns(events: pd.DataFrame) -> pd.DataFrame:
    logger.info("加载 price_kline")
    codes = sorted(events["stock_code"].astype(str).unique())
    mkt = get_market_conn()
    # 分批查询避免 IN 列表过大
    chunk = 500
    px_parts = []
    for i in range(0, len(codes), chunk):
        sub = codes[i:i + chunk]
        part = pd.read_sql_query(
            f"""SELECT code, date, close
                FROM price_kline
                WHERE freq='daily' AND adjust='qfq'
                  AND code IN ({','.join(['?']*len(sub))})""",
            mkt, params=sub,
        )
        px_parts.append(part)
    mkt.close()
    px = pd.concat(px_parts, ignore_index=True) if px_parts else pd.DataFrame()
    logger.info("price_kline 行 %d（覆盖 %d 股票）", len(px), px["code"].nunique() if not px.empty else 0)

    px = px.sort_values(["code", "date"])
    grouped = {c: g.reset_index(drop=True) for c, g in px.groupby("code", sort=False)}

    out = events.copy()
    out["gain_20d"] = np.nan
    out["gain_60d"] = np.nan
    out["max_drawdown_20d"] = np.nan
    out["max_drawdown_60d"] = np.nan

    for idx, row in out.iterrows():
        code = str(row["stock_code"])
        trade_date = str(row["notice_date"])
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
            window = after.iloc[1 : 1 + n]
            if window.empty:
                continue
            exit_price = float(window.iloc[-1]["close"])
            out.at[idx, col_gain] = exit_price / entry_price - 1
            path_ret = window["close"].astype(float) / entry_price - 1
            out.at[idx, col_mdd] = float(path_ret.min())
    logger.info("forward return 覆盖率 20d=%.1f%%  60d=%.1f%%",
                100 * out["gain_20d"].notna().mean(),
                100 * out["gain_60d"].notna().mean())
    return out


def write_raw(conn, raw: pd.DataFrame) -> None:
    conn.executescript(RAW_DDL)
    raw.to_sql("raw_executive_trade", conn, if_exists="append", index=False)
    conn.commit()
    logger.info("写入 raw_executive_trade %d 行", len(raw))


def write_fact(conn, events: pd.DataFrame) -> None:
    conn.executescript(FACT_DDL)
    events = events.copy()
    events["built_at"] = datetime.utcnow().isoformat()
    cols = [
        "notice_date", "stock_code", "direction", "n_shareholders",
        "total_change_qty_wan", "total_change_pct_total", "max_change_pct_total",
        "any_individual", "any_corporate",
        "gain_20d", "gain_60d", "max_drawdown_20d", "max_drawdown_60d", "built_at",
    ]
    events[cols].to_sql("fact_executive_trade_event", conn, if_exists="append", index=False)
    conn.commit()
    logger.info("写入 fact_executive_trade_event %d 行", len(events))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = fetch_raw()
    norm = normalize(raw)
    agg = aggregate_events(norm)
    enriched = compute_forward_returns(agg)

    if args.dry_run:
        logger.info("DRY RUN: 不落库; raw=%d events=%d", len(norm), len(enriched))
        return

    conn = get_conn()
    try:
        write_raw(conn, norm)
        write_fact(conn, enriched)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
