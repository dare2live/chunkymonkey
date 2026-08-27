"""Org-holding backtest PIT is announcement date, not statutory deadline."""
from __future__ import annotations

from services.data_sources.org_holding_announcement import (
    is_decision_visible,
    merge_announcement_maps,
    pit_visible_rows,
    resolve_available_iso,
    stamp_available_dates,
)


def test_load_period_announcement_map_from_fixture_conns():
    import duckdb

    from services.data_sources.org_holding_announcement import load_period_announcement_map

    income = duckdb.connect(":memory:")
    income.execute(
        "CREATE TABLE raw_tushare_income "
        "(ts_code VARCHAR, end_date VARCHAR, f_ann_date VARCHAR)"
    )
    income.execute(
        "INSERT INTO raw_tushare_income VALUES ('600519.SH','20250630','20250720')"
    )
    holders = duckdb.connect(":memory:")
    holders.execute(
        "CREATE TABLE canonical_top10_float_holders_period "
        "(stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR)"
    )
    holders.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "VALUES ('000001','2025-06-30','2025-08-10')"
    )
    merged = load_period_announcement_map(
        "2025-06-30", income_conn=income, holders_conn=holders
    )
    assert merged["600519"] == "20250720"
    assert merged["000001"] == "20250810"
    income.close()
    holders.close()


def test_income_announcement_wins_over_holders_notice():
    merged = merge_announcement_maps(
        income_first={"600519.SH": "2025-07-20"},
        holders_first={"600519": "2025-07-22"},
    )
    assert merged["600519"] == "20250720"


def test_holders_fills_when_income_missing():
    merged = merge_announcement_maps(
        income_first={},
        holders_first={"000001": "2026-08-10"},
    )
    assert merged["000001"] == "20260810"


def test_early_filer_visible_before_statutory_deadline():
    """H1 2025: 茅台 7/20 已公告; 法定截止 8/31 不是已知日."""
    assert is_decision_visible(asof="20250721", announcement="20250720") is True
    assert is_decision_visible(asof="20250719", announcement="20250720") is False
    assert is_decision_visible(asof="20250721", announcement="20250831") is False


def test_late_filer_after_deadline_is_not_visible_on_deadline():
    """Stamping deadline as known-at would leak this row on 4/30."""
    assert is_decision_visible(asof="20260430", announcement="20260506") is False
    assert is_decision_visible(asof="20260506", announcement="20260506") is True


def test_null_announcement_excluded_from_backtest():
    assert is_decision_visible(asof="20250831", announcement=None) is False
    assert (
        is_decision_visible(
            asof="20250831",
            announcement=None,
            first_seen="20250827",
            allow_first_seen=False,
        )
        is False
    )
    assert (
        is_decision_visible(
            asof="20250827",
            announcement=None,
            first_seen="20250827",
            allow_first_seen=True,
        )
        is True
    )


def test_pit_truncation_future_announcements_do_not_change_cutoff_snapshot():
    early = {
        "stock_code": "600519",
        "report_date": "20250630",
        "announcement": "20250720",
    }
    late = {
        "stock_code": "000001",
        "report_date": "20250630",
        "announcement": "20250815",
    }
    asof = "20250721"
    before = pit_visible_rows([early], asof)
    after = pit_visible_rows([early, late], asof)
    assert before == after
    assert [r["stock_code"] for r in after] == ["600519"]


def test_deadline_as_available_date_is_the_wrong_backtest_filter():
    """Old gate: hide everyone until 8/31, then show everyone — both wrong."""
    rows = [
        {"stock_code": "600519", "announcement": "20250720"},
        {"stock_code": "000001", "announcement": "20250815"},
    ]
    by_announcement = {
        r["stock_code"] for r in pit_visible_rows(rows, "20250721")
    }
    by_deadline = {
        r["stock_code"]
        for r in rows
        if is_decision_visible(asof="20250721", announcement="20250831")
    }
    assert by_announcement == {"600519"}
    assert by_deadline == set()


def test_stamp_uses_announcement_not_disclosure_deadline():
    stamped = stamp_available_dates(
        [{"stock_code": "600519", "report_date": "2026-06-30"}],
        announcement_by_stock={"600519": "2026-08-10"},
        land_date="2026-08-27",
        today="2026-08-27",
    )
    assert stamped[0]["available_date"] == "2026-08-10"
    assert stamped[0]["available_date"] != "2026-08-31"


def test_stamp_first_seen_when_announcement_unknown():
    stamped = stamp_available_dates(
        [{"stock_code": "600000", "report_date": "2026-06-30"}],
        announcement_by_stock={},
        land_date="2026-08-27",
        today="2026-08-27",
    )
    assert stamped[0]["available_date"] == "2026-08-27"


def test_stamp_rejects_future_announcement_in_favor_of_land_date():
    stamped = stamp_available_dates(
        [{"stock_code": "600519", "report_date": "2026-06-30"}],
        announcement_by_stock={"600519": "2026-08-31"},
        land_date="2026-08-27",
        today="2026-08-27",
    )
    assert stamped[0]["available_date"] == "2026-08-27"


def test_resolve_never_returns_date_before_report_period():
    iso = resolve_available_iso(
        stock_code="600519",
        report_date="2026-06-30",
        announcement_by_stock={"600519": "2026-04-30"},
        land_date="2026-08-27",
        today="2026-08-27",
    )
    assert iso == "2026-08-27"
