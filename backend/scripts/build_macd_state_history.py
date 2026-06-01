#!/usr/bin/env python3
"""Phase η+++++ — MACD state history mart backfill.

This script materializes MACD active state rows into ``mart_macd_state_history``
without touching the tradeable trigger table.

The table is diagnostic evidence for upstream candidate supply:
  - holding: MACD已进入金叉窗口后仍在 DEA 上方
  - imminent: MACD gap 很小, 接近交叉

It is intentionally separate from ``fact_technical_trigger`` so buy/recommendation
consumers keep their current trigger semantics.

Usage:
  PYTHONPATH=backend python backend/scripts/build_macd_state_history.py \
      [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date as _date, timedelta
from pathlib import Path

import duckdb
import numpy as np

from services.db import get_conn
from services.formula_engine.ddl import ensure_formula_tables
from services.formula_engine.macd_golden_cross import MacdGoldenCross
from services.utils import latest_completed_trade_date


MARKET_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
LOOKBACK_DAYS = 180


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_macd_state_history")


def _iso_date(value: str, *, name: str) -> str:
    try:
        return _date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO date YYYY-MM-DD, got {value!r}") from exc


def _resolve_end_date(mkt_conn, requested_end: str | None) -> str:
    row = mkt_conn.execute(
        "SELECT MAX(date) FROM v_price_kline_qfq WHERE adjust='qfq'"
    ).fetchone()
    kline_max = row[0] if row else None

    smart_conn = get_conn()
    try:
        cal_max = latest_completed_trade_date(smart_conn)
    finally:
        smart_conn.close()
    if not cal_max:
        raise RuntimeError("latest_completed_trade_date returned None — dim_trading_calendar not seeded")

    if requested_end is None:
        return min(kline_max, cal_max) if kline_max else cal_max
    requested_end = _iso_date(requested_end, name="end")
    return min(requested_end, cal_max) if kline_max else requested_end


def _resolve_history_start(mkt_conn, requested_start: str) -> str:
    """给 MACD 预热一个足够长的历史窗口,但不早于市场最早 K 线。"""
    row = mkt_conn.execute(
        "SELECT MIN(date) FROM v_price_kline_qfq WHERE adjust='qfq'"
    ).fetchone()
    kline_min = row[0] if row else None
    start_dt = _date.fromisoformat(requested_start)
    lookback_dt = (start_dt - timedelta(days=LOOKBACK_DAYS)).isoformat()
    if not kline_min:
        return lookback_dt
    return max(kline_min, lookback_dt)


def _resolve_default_start(mkt_conn, end: str) -> str:
    """默认写入窗口：从 end 往前回溯 LOOKBACK_DAYS 天，再裁到最早 K 线。"""
    row = mkt_conn.execute(
        "SELECT MIN(date) FROM v_price_kline_qfq WHERE adjust='qfq'"
    ).fetchone()
    kline_min = row[0] if row else None
    default_start = (_date.fromisoformat(end) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    if not kline_min:
        return default_start
    return max(kline_min, default_start)


def _load_all_kline_grouped(mkt_conn, start: str, end: str) -> dict[str, dict]:
    t0 = time.time()
    log.info("一次性拉全市场 K 线...")
    arr = mkt_conn.execute(
        """
        SELECT code, date, open, high, low, close, volume, amount
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND date >= ? AND date <= ?
         ORDER BY code, date
        """,
        [start, end],
    ).fetchnumpy()
    log.info(f"  K 线 {len(arr['code']):,} 行, SQL 耗时 {time.time()-t0:.1f}s")

    codes_array = arr["code"]
    unique_codes, first_idx = np.unique(codes_array, return_index=True)
    sort_perm = np.argsort(first_idx)
    unique_codes = unique_codes[sort_perm]
    first_idx = first_idx[sort_perm]
    last_idx = np.concatenate([first_idx[1:], [len(codes_array)]])

    grouped: dict[str, dict] = {}
    for code, s, e in zip(unique_codes, first_idx, last_idx):
        sl = slice(int(s), int(e))
        grouped[code] = {
            "dates": arr["date"][sl],
            "opens": arr["open"][sl].astype(float),
            "highs": arr["high"][sl].astype(float),
            "lows": arr["low"][sl].astype(float),
            "closes": arr["close"][sl].astype(float),
            "volumes": arr["volume"][sl].astype(float),
            "amounts": arr["amount"][sl].astype(float),
        }
    log.info(f"  groupby 后 {len(grouped):,} 只股票, 总耗时 {time.time()-t0:.1f}s")
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="默认从 end 往前回溯 LOOKBACK_DAYS 天")
    parser.add_argument("--end", default=None, help="默认 K 线最新已完成交易日")
    args = parser.parse_args()

    mkt_conn = duckdb.connect(str(MARKET_DB_PATH), read_only=True)
    try:
        end = _resolve_end_date(mkt_conn, args.end)
        if args.start is None:
            start = _resolve_default_start(mkt_conn, end)
        else:
            start = _iso_date(args.start, name="start")
        if start > end:
            raise ValueError(f"start {start} must be <= end {end}")

        history_start = _resolve_history_start(mkt_conn, start)
        log.info(f"MACD state history 区间: {start} - {end}; warmup={history_start} - {start}")
        grouped = _load_all_kline_grouped(mkt_conn, history_start, end)
        formula = MacdGoldenCross()

        rows: list[tuple] = []
        for idx, code in enumerate(grouped.keys(), start=1):
            kl = grouped[code]
            if len(kl["dates"]) < 30:
                continue
            state_rows = formula.compute_state_history(
                code=code,
                dates=kl["dates"],
                opens=kl["opens"],
                highs=kl["highs"],
                lows=kl["lows"],
                closes=kl["closes"],
                volumes=kl["volumes"],
                amounts=kl["amounts"],
            )
            state_rows = [row for row in state_rows if start <= row.date <= end]
            rows.extend(
                (
                    r.stock_code,
                    r.date,
                    r.formula_id,
                    r.formula_variant,
                    r.state,
                    r.strength,
                    json.dumps(list(r.reason_codes), ensure_ascii=False),
                )
                for r in state_rows
            )
            if idx % 1000 == 0:
                log.info("  处理 %s 只股票, state rows=%s", f"{idx:,}", f"{len(rows):,}")

        conn = get_conn()
        try:
            ensure_formula_tables(conn)
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "DELETE FROM mart_macd_state_history WHERE formula_id = ? AND date >= ? AND date <= ?",
                    [formula.metadata.formula_id, start, end],
                )
                if rows:
                    conn.executemany(
                        """
                        INSERT INTO mart_macd_state_history
                          (stock_code, date, formula_id, formula_variant, state, strength, reason_codes_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    log.exception("ROLLBACK failed after MACD state history write error")
                raise
        finally:
            conn.close()

        log.info("写入 mart_macd_state_history: %s rows", f"{len(rows):,}")
        print(f"MACD state history rebuilt: {len(rows):,} rows ({start} ~ {end})")
        return 0
    finally:
        mkt_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
