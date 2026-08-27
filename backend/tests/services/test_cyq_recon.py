"""Nominal-K chip overlay vs cyq_perf archive: not identity, not a formula input."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from conftest import duck_mem
from services.data_sources.cyq_recon import (
    BANNED_K_INPUT,
    CANONICAL_K,
    CYQ_LANDING,
    compare_chip_day,
    compare_chip_sample,
    compact_yyyymmdd,
    cyq_publication_status,
    is_known_empty_day,
    join_bars_and_basic,
    load_cyq_perf,
    load_daily_basic,
    load_nominal_bars,
    overlay_chips,
    reject_cyq_as_accepted,
    reject_qfq_bars,
)


def test_cyq_perf_is_not_accepted_publication():
    status = cyq_publication_status()
    assert status["accepted"] is False
    assert status["baseline"] is None
    assert status["landing"] == CYQ_LANDING
    assert status["formula_winner_rate"] is False
    assert status["primary_cut"] is False
    with pytest.raises(ValueError, match="not accepted"):
        reject_cyq_as_accepted("raw_tushare_cyq_perf")


def test_qfq_and_raw_daily_are_rejected_as_chip_input():
    with pytest.raises(ValueError, match="banned qfq"):
        reject_qfq_bars("price_kline_qfq_tushare")
    with pytest.raises(ValueError, match="banned qfq"):
        reject_qfq_bars("v_price_kline_qfq")
    with pytest.raises(ValueError, match="banned qfq"):
        reject_qfq_bars("raw_tushare_daily")
    assert reject_qfq_bars(CANONICAL_K) == CANONICAL_K
    for name in BANNED_K_INPUT:
        with pytest.raises(ValueError):
            reject_qfq_bars(name)


def test_empty_chip_recon_is_not_a_match():
    report = compare_chip_sample([], [])
    assert report["status"] == "empty_recon"
    assert report["identity"] is False
    assert report["formula_winner_rate"] is False


def test_equal_cost_50_is_still_not_identity():
    model = {
        "trade_date": "20260820",
        "cost_50pct": 10.0,
        "winner_rate": 40.0,
        "status": "ok",
    }
    vendor = {
        "trade_date": "20260820",
        "cost_50pct": 10.0,
        "winner_rate": 40.0,
    }
    body = compare_chip_day(model, vendor)
    assert body["cost_50pct"]["numeric_near"] is True
    assert body["identity"] is False
    assert body["formula_winner_rate"] is False
    assert body["coordinate_left"] == "nominal_unadjusted"


def test_known_empty_day_is_not_zero_equals_zero():
    assert is_known_empty_day("20260615") is True
    report = compare_chip_sample(
        [{"trade_date": "20260615", "cost_50pct": 1.0, "status": "ok"}],
        [],
    )
    assert report["identity"] is False
    assert report["per_day"][0]["status"] == "known_empty_day"


def test_overlay_seeds_then_decays():
    rows = overlay_chips(
        [
            {"trade_date": "20260818", "ts_code": "000001.SZ", "close": 10.0, "turnover_rate_f": 1.0},
            {"trade_date": "20260819", "ts_code": "000001.SZ", "close": 12.0, "turnover_rate_f": 50.0},
            {"trade_date": "20260820", "ts_code": "000001.SZ", "close": 12.0, "turnover_rate_f": 100.0},
        ]
    )
    assert rows[0]["winner_rate"] == 0.0
    assert rows[0]["cost_50pct"] == 10.0
    assert rows[1]["cost_50pct"] == 10.0
    assert abs(rows[1]["winner_rate"] - 50.0) < 1e-9
    assert rows[2]["cost_50pct"] == 12.0
    assert rows[2]["winner_rate"] == 0.0
    assert all(r["method"] == "turnover_overlay_v1" for r in rows)
    assert all(r["coordinate"] == "nominal_unadjusted" for r in rows)


def test_load_nominal_rejects_qfq_table():
    con = duck_mem()
    con.execute("CREATE TABLE v_price_kline_qfq (trade_date DATE, ts_code VARCHAR, close DOUBLE, vol DOUBLE)")
    with pytest.raises(ValueError, match="banned qfq"):
        load_nominal_bars(con, "000001.SZ", start="20260801", end="20260820", table="v_price_kline_qfq")


def test_load_joins_compact_dates_and_skips_known_empty_vendor():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE canonical_nominal_ohlcv_daily (
            trade_date DATE, ts_code VARCHAR, close DOUBLE, vol DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO canonical_nominal_ohlcv_daily VALUES
        (DATE '2026-08-18', '000001.SZ', 10.0, 100),
        (DATE '2026-08-19', '000001.SZ', 12.0, 100)
        """
    )
    con.execute(
        """
        CREATE TABLE raw_tushare_daily_basic (
            trade_date VARCHAR, ts_code VARCHAR, float_share DOUBLE,
            turnover_rate DOUBLE, turnover_rate_f DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO raw_tushare_daily_basic VALUES
        ('20260818', '000001.SZ', 1000, 1.0, 1.0),
        ('20260819', '000001.SZ', 1000, 2.0, 50.0)
        """
    )
    con.execute(
        """
        CREATE TABLE raw_tushare_cyq_perf (
            trade_date VARCHAR, ts_code VARCHAR, winner_rate DOUBLE,
            cost_5pct DOUBLE, cost_50pct DOUBLE, cost_95pct DOUBLE, weight_avg DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO raw_tushare_cyq_perf VALUES
        ('20260615', '000001.SZ', 0, 0, 0, 0, 0),
        ('20260819', '000001.SZ', 40, 9, 10, 12, 10.5)
        """
    )
    bars = load_nominal_bars(con, "000001.SZ", start="20260818", end="20260819")
    basic = load_daily_basic(con, "000001.SZ", start="20260818", end="20260819")
    vendor = load_cyq_perf(con, "000001.SZ", start="20260615", end="20260819")
    assert [r["trade_date"] for r in bars] == ["20260818", "20260819"]
    assert compact_yyyymmdd(date(2026, 8, 19)) == "20260819"
    joined = join_bars_and_basic(bars, basic)
    model = overlay_chips(joined)
    assert vendor == [
        {
            "trade_date": "20260819",
            "ts_code": "000001.SZ",
            "winner_rate": 40.0,
            "cost_5pct": 9.0,
            "cost_50pct": 10.0,
            "cost_95pct": 12.0,
            "weight_avg": 10.5,
        }
    ]
    report = compare_chip_sample(model, vendor)
    assert report["identity"] is False
    assert report["compared"] == 1
    assert report["formula_winner_rate"] is False


def test_formulas_do_not_consume_cyq_winner_rate():
    cfg = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "strategy_packages"
        / "formulas.yaml"
    )
    text = cfg.read_text(encoding="utf-8")
    assert "winner_rate" not in text
    assert "cyq_perf" not in text
    assert "cyq_recon" not in text
