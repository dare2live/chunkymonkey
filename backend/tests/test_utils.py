import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add backend directory to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.utils import (
    clamp,
    clamp_score,
    latest_completed_trade_date,
    normalize_ymd,
    parse_any_date,
    percentile_ranks,
    safe_float,
)

def test_safe_float():
    assert safe_float(1.5) == 1.5
    assert safe_float("2.3") == 2.3
    assert safe_float(None) is None
    assert safe_float("abc") is None
    assert safe_float(float('nan')) is None

def test_percentile_ranks():
    assert percentile_ranks([]) == []
    assert percentile_ranks([5.0]) == [50.0]
    assert percentile_ranks([None, None]) == [None, None]
    assert percentile_ranks([10.0, 30.0, 20.0, None]) == [0.0, 100.0, 50.0, None]
    assert percentile_ranks([10.0, 10.0, 20.0]) == [25.0, 25.0, 100.0]

def test_normalize_ymd():
    assert normalize_ymd("2026-04-12") == "2026-04-12"
    assert normalize_ymd("20260412") == "2026-04-12"
    assert normalize_ymd("2026/04/12") == "2026-04-12"
    assert normalize_ymd(None) is None
    assert normalize_ymd("abc") is None
    assert normalize_ymd("2026-04") is None

def test_clamp():
    assert clamp(5.0, 0.0, 10.0) == 5.0
    assert clamp(-5.0, 0.0, 10.0) == 0.0
    assert clamp(15.0, 0.0, 10.0) == 10.0

def test_clamp_score():
    assert clamp_score(50.123, 0.0, 100.0) == 50.12
    assert clamp_score(None, 20.0, 100.0) == 20.0
    assert clamp_score(150.0, 0.0, 100.0) == 100.0

def test_parse_any_date():
    assert parse_any_date("2025-01-15") == datetime(2025, 1, 15)
    assert parse_any_date("20250115") == datetime(2025, 1, 15)
    assert parse_any_date(None) is None
    assert parse_any_date("") is None
    assert parse_any_date("abc") is None
    assert parse_any_date("  2025-01-15  ") == datetime(2025, 1, 15)


def test_latest_completed_trade_date_before_close_uses_previous_trade_day():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER NOT NULL)")
    conn.executemany(
        "INSERT INTO dim_trading_calendar(trade_date, is_trading) VALUES (?, ?)",
        [("2026-04-13", 1), ("2026-04-14", 1), ("2026-04-15", 1)],
    )
    assert latest_completed_trade_date(conn, datetime(2026, 4, 14, 0, 30)) == "2026-04-13"
    conn.close()


def test_latest_completed_trade_date_after_close_can_use_same_day():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER NOT NULL)")
    conn.executemany(
        "INSERT INTO dim_trading_calendar(trade_date, is_trading) VALUES (?, ?)",
        [("2026-04-13", 1), ("2026-04-14", 1), ("2026-04-15", 1)],
    )
    assert latest_completed_trade_date(conn, datetime(2026, 4, 14, 16, 5)) == "2026-04-14"
    conn.close()

