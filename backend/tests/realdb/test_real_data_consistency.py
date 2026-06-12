"""Opt-in read-only validation against local production DuckDB files."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.duck_adapter import connect
from services.utils import latest_completed_trade_date


pytestmark = [
    pytest.mark.realdb,
    pytest.mark.skipif(os.environ.get("CM_REALDB") != "1", reason="set CM_REALDB=1 to run real DB checks"),
]

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SMART_DB = DATA_DIR / "smartmoney.duckdb"
MARKET_DB = DATA_DIR / "market.duckdb"


def _connect_existing(path: Path):
    if not path.exists():
        pytest.fail(f"required DuckDB file missing: {path}")
    return connect(str(path), read_only=True, timeout=5)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def test_real_trading_calendar_is_available_and_has_completed_trade_date():
    conn = _connect_existing(SMART_DB)
    try:
        assert _table_exists(conn, "dim_trading_calendar")
        count, min_date, max_date = conn.execute(
            """
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date)
              FROM dim_trading_calendar
             WHERE is_trading = 1
            """
        ).fetchone()
        latest = latest_completed_trade_date(conn)

        assert count >= 5000  # 2026-06-12 日历扩展后 5343 行 (2005-01-04 起)
        assert min_date <= "2005-01-04"  # 防回退: 起点回缩 = 静默 clamp 复发 (CLAUDE.md §4.5)
        assert max_date >= latest
        assert latest is not None
    finally:
        conn.close()


def test_real_tdxhub_kline_reaches_latest_completed_trade_date():
    smart = _connect_existing(SMART_DB)
    market = _connect_existing(MARKET_DB)
    try:
        latest_trade = latest_completed_trade_date(smart)
        row = market.execute(
            """
            SELECT MAX(date), COUNT(*), COUNT(DISTINCT code)
              FROM price_kline_tdxhub
             WHERE freq = 'daily' AND adjust = 'qfq'
            """
        ).fetchone()

        assert latest_trade is not None
        assert row[0] == latest_trade
        assert row[1] > 1_000_000
        assert row[2] >= 5_000
    finally:
        smart.close()
        market.close()


def test_real_canonical_kline_has_no_duplicate_keys():
    market = _connect_existing(MARKET_DB)
    try:
        market.execute("SELECT 1 FROM v_price_kline_qfq LIMIT 1").fetchone()
        duplicate_count = market.execute(
            """
            SELECT COUNT(*)
              FROM (
                SELECT code, date, freq, adjust, COUNT(*) AS n
                  FROM v_price_kline_qfq
                 GROUP BY code, date, freq, adjust
                HAVING COUNT(*) > 1
              )
            """
        ).fetchone()[0]

        assert duplicate_count == 0
    finally:
        market.close()


def test_real_fallback_rows_do_not_overlap_existing_primary_keys():
    market = _connect_existing(MARKET_DB)
    try:
        market.execute("SELECT 1 FROM v_price_kline_qfq LIMIT 1").fetchone()
        canonical_overlap = market.execute(
            """
            SELECT COUNT(*)
              FROM v_price_kline_qfq c
             WHERE c.is_fallback
               AND EXISTS (
                    SELECT 1
                      FROM price_kline_tdxhub p
                     WHERE p.code = c.code
                       AND p.date = c.date
                       AND p.freq = c.freq
                       AND p.adjust = c.adjust
                       AND p.open IS NOT NULL AND p.open > 0
                       AND p.close IS NOT NULL AND p.close > 0
               )
            """
        ).fetchone()[0]

        assert canonical_overlap == 0
    finally:
        market.close()


def test_real_xdxr_adjustment_events_are_unique():
    market = _connect_existing(MARKET_DB)
    try:
        assert _table_exists(market, "price_kline_tdxhub_adjustment_event")
        duplicate_events = market.execute(
            """
            SELECT COUNT(*)
              FROM (
                SELECT code, event_date, event_hash, COUNT(*) AS n
                  FROM price_kline_tdxhub_adjustment_event
                 GROUP BY code, event_date, event_hash
                HAVING COUNT(*) > 1
              )
            """
        ).fetchone()[0]

        assert duplicate_events == 0
    finally:
        market.close()
