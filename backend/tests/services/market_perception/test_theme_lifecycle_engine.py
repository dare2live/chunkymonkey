from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.market_perception.theme_lifecycle_engine import (
    compute_theme_lifecycle_for_date,
    compute_theme_lifecycle_for_range,
)


def _make_conn(snapshot: date, *, observed: bool = True):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    conn.execute(
        """
        CREATE TABLE fact_sector_momentum_daily (
            sector_name TEXT,
            date TEXT,
            ret_20d DOUBLE,
            ret_60d DOUBLE,
            excess_20d DOUBLE,
            excess_60d DOUBLE,
            price_vs_ma20 DOUBLE,
            price_vs_ma60 DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_stock_industry_pit (
            stock_code TEXT,
            effective_from TEXT,
            effective_to TEXT,
            tdx_l1_name TEXT,
            confidence_level TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_stock_kline_daily (
            trade_date TEXT,
            stock_code TEXT,
            pct_change DOUBLE,
            amount DOUBLE
        )
        """
    )
    days = [snapshot - timedelta(days=1), snapshot]
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)", [(d.isoformat(),) for d in days])
    sectors = [
        ("AI", 0.25, 0.35, 0.20, 0.30, 0.12, 0.18),
        ("消费", 0.05, 0.10, 0.04, 0.08, 0.03, 0.06),
        ("金融", -0.08, -0.04, -0.10, -0.06, -0.04, -0.03),
        ("周期", 0.01, -0.02, 0.00, -0.01, 0.01, -0.01),
    ]
    for d in days:
        conn.executemany(
            "INSERT INTO fact_sector_momentum_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(name, d.isoformat(), *vals) for name, *vals in sectors],
        )
    confidence = "observed_snapshot" if observed else "current_label_fallback"
    pit_rows = []
    kline_rows = []
    for sector_idx, sector in enumerate(["AI", "消费", "金融", "周期"]):
        for i in range(25):
            code = f"{sector_idx}{i:05d}"
            pit_rows.append((code, days[0].isoformat(), days[-1].isoformat(), sector, confidence))
            pct = 2.0 if sector == "AI" and i < 20 else -1.0 if sector == "金融" else 0.5
            if sector == "AI" and i < 3:
                pct = 9.8
            for d in days:
                kline_rows.append((d.isoformat(), code, pct, 1000.0 + i))
    conn.executemany("INSERT INTO mart_stock_industry_pit VALUES (?, ?, ?, ?, ?)", pit_rows)
    conn.executemany("INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?, ?)", kline_rows)
    return conn


def test_theme_lifecycle_mainline_and_diffusion():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    result = compute_theme_lifecycle_for_date(conn, snapshot)
    rows = result["themes"]
    ai = next(row for row in rows if row["theme_name"] == "AI")

    assert result["rows"] == 4
    assert ai["mainline_rank"] == 1
    assert ai["is_mainline"] is True
    assert ai["theme_score"] > 0.5
    assert ai["diffusion_state"] == "板块扩散"
    assert ai["lifecycle_stage"] in {"主升", "高潮"}


def test_theme_lifecycle_excludes_current_label_fallback():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot, observed=False)

    with pytest.raises(ValueError, match="observed PIT industry coverage incomplete"):
        compute_theme_lifecycle_for_range(conn, snapshot, snapshot)


def test_theme_lifecycle_today_excluded():
    today = date.today()
    conn = _make_conn(today)

    with pytest.raises(ValueError, match="today/future"):
        compute_theme_lifecycle_for_range(conn, today, today)
