"""Tests for panel-build preflight gate."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import duck_mem
from scripts.preflight_panel_build import PreflightConfig, run_preflight_or_exit


def _smart_conn(*, stock_count: int = 10, watermark: str = "2026-05-15"):
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


def _market_conn(*, covered_codes: int = 10, trade_date: str = "2026-05-15"):
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


def test_preflight_stale_watermark_exits_1():
    smart = _smart_conn(watermark="2026-05-09")
    market = _market_conn(covered_codes=10)
    config = PreflightConfig(current_date=date(2026, 5, 17), watermark_sla_days=7)

    with pytest.raises(SystemExit) as exc:
        run_preflight_or_exit(smart, market, config)

    assert exc.value.code == 1
