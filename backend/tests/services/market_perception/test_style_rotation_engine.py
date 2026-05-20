from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.market_perception.style_rotation_engine import compute_style_rotation_for_range


def _make_conn(snapshot: date):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    days = [snapshot - timedelta(days=i) for i in range(35)]
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)", [(d.isoformat(),) for d in days])
    conn.execute(
        """
        CREATE TABLE fact_stock_kline_daily (
            stock_code TEXT,
            trade_date TEXT,
            close DOUBLE,
            amount DOUBLE
        )
        """
    )
    codes = [f"00000{i}" for i in range(1, 11)]
    for idx, code in enumerate(codes, start=1):
        rows = []
        ordered_days = sorted(days)
        for offset, day in enumerate(ordered_days):
            close = 10.0 + idx
            if idx <= 3 and day == snapshot:
                close *= 1.05
            if idx >= 8 and day == snapshot:
                close *= 0.98
            if idx >= 8 and day >= snapshot - timedelta(days=20):
                close += 0.03 * offset
            amount = 1000.0 * idx
            rows.append((code, day.isoformat(), close, amount))
        conn.executemany("INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?, ?)", rows)
    conn.execute(
        """
        CREATE TABLE fact_market_cap_decile_daily (
            stock_code TEXT,
            trade_date DATE,
            market_cap_proxy DOUBLE,
            mcap_decile INTEGER,
            source_max_trade_date DATE,
            built_at TIMESTAMP
        )
        """
    )
    conn.executemany(
        "INSERT INTO fact_market_cap_decile_daily VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        [(code, snapshot.isoformat(), float(i), i, snapshot.isoformat()) for i, code in enumerate(codes, start=1)],
    )
    return conn


def test_style_rotation_detects_small_cap_preference():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    frame = compute_style_rotation_for_range(conn, snapshot, snapshot)
    row = frame.iloc[0]

    assert float(row["size_preference_score"]) > 0.0
    assert str(row["style_bias"]).startswith("小盘")
    assert row["style_source"] == "market_cap_decile"


def test_style_rotation_falls_back_to_liquidity_proxy_when_mcap_missing():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)
    conn.execute("DELETE FROM fact_market_cap_decile_daily")

    frame = compute_style_rotation_for_range(conn, snapshot, snapshot)

    assert frame.iloc[0]["style_source"] == "amount_liquidity_proxy"


def test_style_rotation_uses_trading_calendar_gate():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)
    non_trading = snapshot - timedelta(days=1)
    conn.execute("UPDATE dim_trading_calendar SET is_trading = 0 WHERE trade_date = ?", [non_trading.isoformat()])

    frame = compute_style_rotation_for_range(conn, non_trading, snapshot)

    assert non_trading.isoformat() not in set(frame["snapshot_date"])


def test_style_rotation_today_excluded():
    today = date.today()
    conn = _make_conn(today)

    with pytest.raises(ValueError, match="today/future"):
        compute_style_rotation_for_range(conn, today, today)
