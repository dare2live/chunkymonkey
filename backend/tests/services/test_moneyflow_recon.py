"""Moneyflow layers: named, not conserved, not identity."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

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
    probe_eastmoney_datacenter_flow,
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
    assert status["tdx_mac"]["layer"] == "tdx_mac_capital_flow"
    assert status["tdx_mac"]["accepted"] is False
    assert status["eastmoney_datacenter"]["status"] == "unprobed"
    assert status["eastmoney_datacenter"]["table"] is None
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
    with pytest.raises(ValueError, match="forbidden to sum"):
        reject_cross_source_sum(
            [("tdx_mac_capital_flow", 1.0), ("eastmoney_datacenter_flow", 2.0)]
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


def test_eastmoney_probe_classifies_zero_rows_timeout_and_mismatch():
    class _Client:
        def get_v1(self, report_name, **_kw):
            if report_name == "RPT_MUTUAL_STOCK_HOLDRANKN_NEW":
                return {
                    "data": [{"SECUCODE": "600519.SH", "HOLD_SHARES": 1}],
                    "count": 1,
                    "pages": 1,
                }
            if report_name == "RPT_DMSK_FUND_FLOW":
                return {"data": [], "count": 0, "pages": 0}
            if report_name == "RPT_F10_FUNDFLOW":
                raise TimeoutError("timed out")
            if report_name == "RPT_STOCK_FUNDFLOW":
                raise RuntimeError("HTTP 500 datacenter")
            if report_name == "RPT_F10_MAIN_FUNDFLOW":
                return {
                    "data": [{"SECUCODE": "600519.SH", "SECURITY_NAME": "Kweichow"}],
                    "count": 1,
                }
            return {"data": [], "count": 0}

    probe = probe_eastmoney_datacenter_flow(
        _Client(),
        report_names=(
            "RPT_DMSK_FUND_FLOW",
            "RPT_F10_FUNDFLOW",
            "RPT_STOCK_FUNDFLOW",
            "RPT_F10_MAIN_FUNDFLOW",
        ),
    )
    by_name = {row["report_name"]: row["status"] for row in probe["candidates"]}
    assert by_name["RPT_DMSK_FUND_FLOW"] == "zero_rows"
    assert by_name["RPT_F10_FUNDFLOW"] == "timeout"
    assert by_name["RPT_STOCK_FUNDFLOW"] == "http_error"
    assert by_name["RPT_F10_MAIN_FUNDFLOW"] == "missing_fields"
    assert probe["control"]["status"] == "ok_control"
    assert probe["status"] == "probe_failed"
    assert probe["accepted"] is False
    assert probe["ok_report_names"] == []


def test_eastmoney_zero_rows_with_control_is_product_mismatch():
    class _Client:
        def get_v1(self, report_name, **_kw):
            if report_name == "RPT_MUTUAL_STOCK_HOLDRANKN_NEW":
                return {"data": [{"SECUCODE": "600519.SH"}], "count": 1}
            return {"data": [], "count": 0, "pages": 0}

    probe = probe_eastmoney_datacenter_flow(
        _Client(), report_names=("RPT_DMSK_FUND_FLOW", "RPT_F10_FUNDFLOW")
    )
    assert probe["status"] == "product_mismatch"
    assert probe["control"]["status"] == "ok_control"
    status = moneyflow_publication_status(eastmoney_probe=probe)
    assert status["eastmoney_datacenter"]["status"] == "product_mismatch"
    assert status["eastmoney_datacenter"]["table"] is None
    assert status["primary_cut"] is False


def test_datacenter_envelope_9501_is_product_mismatch_not_zero_rows():
    from services.data_sources.moneyflow_recon import classify_datacenter_envelope

    got = classify_datacenter_envelope(
        "RPT_DMSK_FUND_FLOW",
        {
            "success": False,
            "code": 9501,
            "message": "报表配置不存在,RPT_DMSK_FUND_FLOW",
            "result": None,
        },
    )
    assert got["status"] == "product_mismatch"
    assert got["rows"] == 0


def test_named_layer_inventory_lists_tdx_mac_not_a_tushare_domain():
    import yaml

    path = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "factor_family_inventory.yaml"
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    family = data["families"]["vendor_flow_proxy"]
    assert "tdx_mac_capital_flow" in family["named_layers"]
    assert "eastmoney_datacenter_flow" not in family["named_layers"]
    assert family["sync_domains"] == ["moneyflow", "moneyflow_dc"]
    assert family["stack_eligibility"] == "defer"
