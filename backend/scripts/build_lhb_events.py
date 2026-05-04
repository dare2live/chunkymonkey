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
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return value != value
    except Exception:
        return False


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_number(values: list[Any]) -> float | None:
    numbers = [_to_float(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return max(numbers) if numbers else None


def _first_present(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if not _is_missing(value):
            return value
    return None


def _coverage(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get(key) is not None) / len(rows)


def load_raw_lhb(conn) -> list[dict[str, Any]]:
    logger.info("加载 raw_lhb_daily")
    rows = _records_from_cursor(conn.execute(
        """
        SELECT trade_date, stock_code, rank_reason, interpretation,
               close_price, change_pct, net_buy, buy_amount, sell_amount,
               turnover, turnover_rate, float_cap, net_buy_pct
        FROM raw_lhb_daily
        """
    ))
    logger.info("raw 行数 %d", len(rows))
    return rows


def dedup_and_parse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (trade_date, stock_code) 去重：
    - net_buy/buy_amount/sell_amount 取 max（不同 rank_reason 理论上值一致，max 保险）
    - rank_reasons 拼接，interpretation 取 net_buy 最大那行的
    - inst_buy_seats 取所有 rank_reason 中的最大值
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        trade_date = str(row.get("trade_date") or "")
        stock_code = str(row.get("stock_code") or "").zfill(6)
        groups.setdefault((trade_date, stock_code), []).append({**row, "stock_code": stock_code})

    agg = []
    for (trade_date, stock_code), group_rows in sorted(groups.items()):
        ordered = sorted(
            group_rows,
            key=lambda row: _to_float(row.get("net_buy")) if _to_float(row.get("net_buy")) is not None else -1.0,
            reverse=True,
        )
        rank_reasons = sorted({
            str(row.get("rank_reason")).strip()
            for row in group_rows
            if not _is_missing(row.get("rank_reason")) and str(row.get("rank_reason")).strip()
        })
        inst_buy_seats = max((_parse_inst_seats(row.get("interpretation")) for row in group_rows), default=0)
        net_buy = _max_number([row.get("net_buy") for row in group_rows])
        agg.append({
            "trade_date": trade_date,
            "stock_code": stock_code,
            "n_rank_reasons": len(rank_reasons),
            "rank_reasons": "|".join(rank_reasons),
            "close_price": _first_present(ordered, "close_price"),
            "change_pct": _first_present(ordered, "change_pct"),
            "net_buy": net_buy,
            "buy_amount": _max_number([row.get("buy_amount") for row in group_rows]),
            "sell_amount": _max_number([row.get("sell_amount") for row in group_rows]),
            "turnover": _max_number([row.get("turnover") for row in group_rows]),
            "turnover_rate": _max_number([row.get("turnover_rate") for row in group_rows]),
            "float_cap": _first_present(ordered, "float_cap"),
            "net_buy_pct": _max_number([row.get("net_buy_pct") for row in group_rows]),
            "interpretation": _first_present(ordered, "interpretation"),
            "inst_buy_seats": inst_buy_seats,
            "is_inst_net_buy": 1 if (net_buy or 0) > 0 and inst_buy_seats >= 1 else 0,
        })
    logger.info(
        "去重后 %d 事件; is_inst_net_buy=1: %d",
        len(agg),
        sum(int(row.get("is_inst_net_buy") or 0) for row in agg),
    )
    return agg


def _apply_forward_returns(
    events: list[dict[str, Any]],
    prices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in prices:
        code = str(row.get("code") or "").zfill(6)
        if not code:
            continue
        grouped.setdefault(code, []).append(row)
    for code in grouped:
        grouped[code].sort(key=lambda row: str(row.get("date") or ""))

    out = []
    for event in events:
        row = dict(event)
        row["gain_20d"] = None
        row["gain_60d"] = None
        row["max_drawdown_20d"] = None
        row["max_drawdown_60d"] = None

        code = str(row.get("stock_code") or "").zfill(6)
        trade_date = str(row.get("trade_date") or "")
        price_rows = grouped.get(code) or []
        after = [price for price in price_rows if str(price.get("date") or "") > trade_date]
        if not after:
            out.append(row)
            continue
        entry_price = _to_float(after[0].get("close"))
        if entry_price is None or entry_price <= 0:
            out.append(row)
            continue
        for n, col_gain, col_mdd in [
            (20, "gain_20d", "max_drawdown_20d"),
            (60, "gain_60d", "max_drawdown_60d"),
        ]:
            window = after[1 : 1 + n]
            closes = [_to_float(price.get("close")) for price in window]
            closes = [close for close in closes if close is not None]
            if not closes:
                continue
            row[col_gain] = closes[-1] / entry_price - 1
            row[col_mdd] = min(close / entry_price - 1 for close in closes)
        out.append(row)
    return out


def compute_forward_returns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对每个事件计算 gain_20d / gain_60d / max_drawdown_20d/60d。

    entry_price = trade_date 后第 1 个交易日收盘（T+1 open 近似为 close）
    gain_Nd = close_{T+N交易日} / entry_price - 1
    max_drawdown_Nd = min(close_t / entry_price - 1) over t in [T+1, T+N]
    """
    logger.info("加载 price_kline（用于 forward return）")
    codes = sorted({str(row.get("stock_code") or "").zfill(6) for row in events if row.get("stock_code")})
    if not codes:
        return _apply_forward_returns(events, [])
    placeholders = ",".join(["?"] * len(codes))
    mkt = get_market_conn()
    try:
        prices = _records_from_cursor(mkt.execute(
            f"""
            SELECT code, date, close
            FROM price_kline
            WHERE freq='daily' AND adjust='qfq'
              AND code IN ({placeholders})
            """,
            codes,
        ))
    finally:
        mkt.close()
    logger.info("price_kline 行 %d", len(prices))

    out = _apply_forward_returns(events, prices)
    logger.info(
        "forward return 覆盖率 20d=%.1f%%, 60d=%.1f%%",
        100 * _coverage(out, "gain_20d"),
        100 * _coverage(out, "gain_60d"),
    )
    return out


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _insert_rows(conn, table_name: str, rows: list[dict[str, Any]], cols: list[str]) -> None:
    if not rows:
        return
    columns = ", ".join(_quote_ident(col) for col in cols)
    placeholders = ", ".join(["?"] * len(cols))
    conn.executemany(
        f"INSERT INTO {_quote_ident(table_name)} ({columns}) VALUES ({placeholders})",
        [tuple(row.get(col) for col in cols) for row in rows],
    )


def write_fact(conn, events: list[dict[str, Any]]) -> None:
    conn.executescript(FACT_LHB_EVENT_DDL)
    built_at = datetime.utcnow().isoformat()
    cols = [
        "trade_date", "stock_code", "n_rank_reasons", "rank_reasons",
        "close_price", "change_pct", "net_buy", "buy_amount", "sell_amount",
        "turnover", "turnover_rate", "float_cap", "net_buy_pct",
        "interpretation", "inst_buy_seats", "is_inst_net_buy",
        "gain_20d", "gain_60d", "max_drawdown_20d", "max_drawdown_60d",
        "built_at",
    ]
    out = [{**row, "built_at": built_at} for row in events]
    _insert_rows(conn, "fact_lhb_event", out, cols)
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
