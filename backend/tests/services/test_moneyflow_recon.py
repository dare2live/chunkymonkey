"""Moneyflow layers: named, not conserved, not identity."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import duck_mem
from services.data_sources.moneyflow_recon import (
    FACT_DC,
    FACT_TS,
    compare_eod_vendors,
    compare_tick_vs_eod,
    compact_yyyymmdd,
    fetch_history_ticks,
    load_eod_dc,
    load_eod_tushare,
    minute_vendor_status,
    moneyflow_publication_status,
    reject_cross_source_sum,
    reject_qfq_input,
    tick_active_delta,
)


def test_three_layers_are_named_and_minute_is_unpublished():
    status = moneyflow_publication_status()
    assert status["eod_dc"]["table"] == FACT_DC
    assert status["eod_tushare"]["table"] == FACT_TS
    assert status["eod_dc"]["accepted"] is False
    assert status["minute"]["status"] == "blocked_no_publication"
    assert minute_vendor_status()["accepted"] is False
    assert status["tick"]["method"] == "tdx_tick_active_delta_v1"
    assert status["formula_winner_rate"] is False
    assert status["primary_cut"] is False


def test_cross_source_sum_is_forbidden():
    with pytest.raises(ValueError, match="forbidden to sum"):
        reject_cross_source_sum(
            [("eod_vendor_imbalance", 1.0), ("tick_active_imbalance", 2.0)]
        )
    reject_cross_source_sum(
        [("eod_vendor_imbalance", 1.0), ("eod_vendor_imbalance", 2.0)]
    )


def test_qfq_is_rejected_as_flow_input():
    with pytest.raises(ValueError, match="banned qfq"):
        reject_qfq_input("v_price_kline_qfq")
    assert reject_qfq_input(FACT_DC) == FACT_DC


def test_empty_tick_or_eod_is_not_a_match():
    report = compare_tick_vs_eod({"status": "empty_recon"}, 10.0)
    assert report["status"] == "empty_recon"
    assert report["identity"] is False
    report = compare_eod_vendors(None, None)
    assert report["status"] == "empty_recon"
    assert report["identity"] is False


def test_equal_numbers_are_still_not_identity():
    ticks = tick_active_delta(
        [
            {"price": 10.0, "vol": 1.0, "buyorsell": 0},
            {"price": 10.0, "vol": 1.0, "buyorsell": 1},
        ]
    )
    assert ticks["delta"] == 0.0
    body = compare_tick_vs_eod(ticks, 0.0)
    assert body["identity"] is False
    assert body["relation"] == "tick_active_delta_is_not_eod_vendor_imbalance"
    eod = compare_eod_vendors(100.0, 100.0)
    assert eod["abs_diff"] == 0
    assert eod["identity"] is False


def test_tick_delta_uses_labeled_buyorsell_convention():
    body = tick_active_delta(
        [
            {"price": 10.0, "vol": 2.0, "buyorsell": 0},
            {"price": 10.0, "vol": 1.0, "buyorsell": 1},
            {"price": 10.0, "vol": 1.0, "buyorsell": 2},
        ]
    )
    assert body["buy"] == 20.0
    assert body["sell"] == 10.0
    assert body["unknown"] == 10.0
    assert body["delta"] == 10.0
    assert body["buyorsell_convention"] == "0_buy_1_sell"
    assert body["unit"] == "price_times_lot"


def test_load_eod_facts_compact_dates_and_reject_wrong_table():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE fact_stock_moneyflow_dc_daily (
            trade_date VARCHAR, ts_code VARCHAR, net_amount DOUBLE
        )
        """
    )
    con.execute(
        """
        CREATE TABLE fact_stock_moneyflow_daily (
            trade_date DATE, ts_code VARCHAR, net_mf_amount DOUBLE
        )
        """
    )
    con.execute(
        "INSERT INTO fact_stock_moneyflow_dc_daily VALUES ('20260825', '000001.SZ', 12.5)"
    )
    con.execute(
        "INSERT INTO fact_stock_moneyflow_daily VALUES (DATE '2026-08-25', '000001.SZ', 8.0)"
    )
    dc = load_eod_dc(con, "000001.SZ", "20260825")
    ts = load_eod_tushare(con, "000001.SZ", "20260825")
    assert dc["net_amount"] == 12.5
    assert ts["net_mf_amount"] == 8.0
    assert compact_yyyymmdd(date(2026, 8, 25)) == "20260825"
    with pytest.raises(ValueError, match="eastmoney EOD"):
        load_eod_dc(con, "000001.SZ", "20260825", table=FACT_TS)


def test_fetch_history_ticks_is_bounded_and_rejects_bj():
    class _Client:
        def __init__(self):
            self.calls = []

        def transactions(self, symbol, start, offset, date):
            self.calls.append((symbol, start, offset, date))
            if start >= 1600:
                return []
            return [
                {"time": "09:30", "price": 10.0, "vol": 1, "buyorsell": 0}
                for _ in range(offset)
            ]

    client = _Client()
    payload = fetch_history_ticks(client, "000001.SZ", "20260825", max_ticks=10, page=8)
    assert payload["truncated"] is True
    assert payload["n"] == 10
    assert payload["coverage"] == "truncated_sample"
    assert client.calls[0][0] == "000001"
    with pytest.raises(ValueError, match="BJ"):
        fetch_history_ticks(client, "920008.BJ", "20260825")
