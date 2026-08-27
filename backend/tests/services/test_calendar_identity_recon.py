"""Calendar / ST identity recon: set-diff vs accepted, banned stopped tables."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import duck_mem
from services.data_sources.calendar_identity_recon import (
    ACCEPTED_CAL_TABLE,
    ACCEPTED_ST_TABLE,
    compare_open_days,
    compare_st_names,
    fuyao_calendar_days,
    fuyao_ticker_rows,
    load_accepted_open_days,
    load_accepted_st_codes,
    name_flags_st,
    reject_banned_baseline,
    suspend_recon_status,
    tdx_stock_rows,
    BANNED_CAL_BASELINE,
    BANNED_ST_BASELINE,
    BANNED_SUSPEND_BASELINE,
)


def test_banned_baselines_are_rejected():
    with pytest.raises(ValueError, match="raw_tushare_trade_cal"):
        reject_banned_baseline(
            "raw_tushare_trade_cal",
            banned=BANNED_CAL_BASELINE,
            accepted=ACCEPTED_CAL_TABLE,
        )
    with pytest.raises(ValueError, match="raw_tushare_stock_st"):
        reject_banned_baseline(
            "raw_tushare_stock_st",
            banned=BANNED_ST_BASELINE,
            accepted=ACCEPTED_ST_TABLE,
        )
    with pytest.raises(ValueError, match="raw_tushare_suspend_d"):
        suspend_recon_status(baseline="raw_tushare_suspend_d")


def test_suspend_is_blocked_without_pretending_raw_is_truth():
    body = suspend_recon_status()
    assert body["status"] == "blocked_no_publication"
    assert body["baseline"] is None
    assert "intraday" in body["reason"]


def test_name_flags_st_prefix_only():
    assert name_flags_st("*ST康美") is True
    assert name_flags_st("ST海德") is True
    assert name_flags_st("S*ST佳通") is True
    assert name_flags_st("贵州茅台") is False
    assert name_flags_st("G特") is False


def test_open_day_set_diff_uses_overlap_window():
    source = [date(2025, 8, 20), date(2025, 8, 21), date(2025, 8, 22)]
    accepted = [date(2025, 8, 21), date(2025, 8, 22), date(2025, 8, 25)]
    report = compare_open_days(source, accepted)
    assert report["window"] == {"start": "2025-08-21", "end": "2025-08-22"}
    assert report["intersection"] == 2
    assert report["only_source"] == 0
    assert report["only_accepted"] == 0


def test_open_day_only_source_inside_window():
    report = compare_open_days(
        [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)],
        [date(2026, 8, 20), date(2026, 8, 22)],
    )
    assert report["window"] == {"start": "2026-08-20", "end": "2026-08-22"}
    assert report["only_source"] == 1
    assert report["only_source_sample"] == ["2026-08-21"]


def test_load_accepted_calendar_rejects_raw_table(monkeypatch):
    con = duck_mem()
    con.execute(
        f"CREATE TABLE {ACCEPTED_CAL_TABLE} (cal_date DATE, is_open TINYINT)"
    )
    con.execute(
        f"INSERT INTO {ACCEPTED_CAL_TABLE} VALUES (DATE '2026-08-20', 1), (DATE '2026-08-21', 0)"
    )
    days = load_accepted_open_days(con)
    assert days == [date(2026, 8, 20)]
    with pytest.raises(ValueError, match="banned baseline"):
        load_accepted_open_days(con, table="raw_tushare_trade_cal")


def test_st_name_set_diff_against_accepted_codes():
    con = duck_mem()
    con.execute(
        f"CREATE TABLE {ACCEPTED_ST_TABLE} (trade_date DATE, ts_code VARCHAR)"
    )
    con.execute(
        f"INSERT INTO {ACCEPTED_ST_TABLE} VALUES "
        "(DATE '2026-08-26', '000982.SZ'), (DATE '2026-08-26', '600234.SH')"
    )
    accepted = load_accepted_st_codes(con, "20260826")
    source = fuyao_ticker_rows(
        [
            {"thscode": "000982.SZ", "name": "ST中核"},
            {"thscode": "600519.SH", "name": "贵州茅台"},
            {"thscode": "000001.SZ", "name": "*ST平安"},
        ]
    )
    report = compare_st_names(source, accepted)
    assert report["intersection"] == 1
    assert report["only_source"] == 1
    assert report["only_accepted"] == 1
    assert report["only_source_sample"] == ["000001.SZ"]
    with pytest.raises(ValueError, match="banned baseline"):
        load_accepted_st_codes(con, "20260826", table="raw_tushare_stock_st")


def test_rest_json_requires_code_zero(monkeypatch):
    from services.data_sources.sources import fuyao as fy

    class _Resp:
        status = 200

        def read(self):
            return b'{"code": 1003, "message": "bad"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fy.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(fy.FuyaoRestError, match="code=1003"):
        fy.rest_json("/api/meta/tickers/list", api_key="x")


def test_fuyao_calendar_items_and_tdx_rows():
    days = fuyao_calendar_days([{"date": "20260820"}])
    assert days == [date(2026, 8, 20)]
    rows = tdx_stock_rows(
        [
            {"code": "000001", "name": "平安银行"},
            {"code": "200016", "name": "深康佳B"},
            {"code": "880516", "name": "ST板块"},
        ],
        market=0,
    )
    assert rows == [{"ts_code": "000001.SZ", "name": "平安银行"}]
    with pytest.raises(ValueError, match="market 0/1"):
        tdx_stock_rows([], market=2)
