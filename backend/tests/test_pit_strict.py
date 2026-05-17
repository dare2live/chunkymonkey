"""PIT-strict regression tests for Phase 1.3."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import duck_mem
from scripts.preflight_panel_build import (
    PreflightConfig,
    compare_panel_row_counts,
    run_preflight_or_exit,
)
from services.labels.universe import pit_active_ever


def _smart_conn_with_listing_status(stock_count: int, *, watermark: str = "2026-05-15"):
    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE dim_listing_status (
            stock_code TEXT PRIMARY KEY,
            listed_date DATE,
            delisted_date DATE
        )
        """
    )
    conn.executemany(
        "INSERT INTO dim_listing_status VALUES (?, DATE '2020-01-01', NULL)",
        [(f"600{i:03d}",) for i in range(stock_count)],
    )
    conn.execute(
        """
        CREATE TABLE mart_data_source_watermark (
            data_domain TEXT,
            source_name TEXT,
            source_tier SMALLINT,
            last_data_date TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO mart_data_source_watermark VALUES ('kline_daily', 'tdxhub_quote', 1, ?)",
        [watermark],
    )
    return conn


def _market_conn_with_coverage(covered_codes: int, *, trade_date: str = "2026-05-15"):
    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE v_price_kline_qfq (
            code TEXT,
            date TEXT,
            freq TEXT,
            adjust TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO v_price_kline_qfq VALUES (?, ?, 'daily', 'qfq')",
        [(f"600{i:03d}", trade_date) for i in range(covered_codes)],
    )
    return conn


def test_future_listing_not_leaked():
    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE dim_listing_status (
            stock_code TEXT PRIMARY KEY,
            listed_date DATE,
            delisted_date DATE
        )
        """
    )
    conn.execute("INSERT INTO dim_listing_status VALUES ('600000', DATE '2024-01-01', NULL)")
    conn.execute("INSERT INTO dim_listing_status VALUES ('600001', DATE '2024-01-11', NULL)")

    stocks = pit_active_ever(conn, "2024-01-10")

    assert "600000" in stocks
    assert "600001" not in stocks


def test_preflight_blocks_kline_gap():
    smart = _smart_conn_with_listing_status(10, watermark="2026-05-15")
    market = _market_conn_with_coverage(8)
    config = PreflightConfig(current_date=date(2026, 5, 17), min_coverage_pct=0.95)

    with pytest.raises(SystemExit) as exc:
        run_preflight_or_exit(smart, market, config)

    assert exc.value.code == 1


def test_panel_v6_row_count_not_less_than_v4_prints_diff(capsys):
    conn = duck_mem()
    conn.execute("CREATE TABLE mart_p0a_feature_label_panel_v4(stock_code TEXT)")
    conn.execute("CREATE TABLE mart_p0a_feature_label_panel_v6(stock_code TEXT)")
    conn.executemany(
        "INSERT INTO mart_p0a_feature_label_panel_v4 VALUES (?)",
        [("600000",), ("600001",)],
    )
    conn.executemany(
        "INSERT INTO mart_p0a_feature_label_panel_v6 VALUES (?)",
        [("600000",), ("600001",), ("000001",)],
    )

    result = compare_panel_row_counts(conn)

    captured = capsys.readouterr()
    assert "panel row count diff:" in captured.out
    assert result["diff"] == 1
