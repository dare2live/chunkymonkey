"""Org holding bounded older-quarter fill — plan + execute (N=1/run)."""
from __future__ import annotations

import asyncio

import duckdb
import pytest

from services import org_holding_aif10 as m
from services.org_holding_period_catchup import (
    ORG_PERIOD_CATCHUP_MAX,
    fill_oldest_missing_org_period,
    plan_older_org_period_fill,
)


@pytest.fixture(autouse=True)
def _no_live_org_count_probe(monkeypatch):
    monkeypatch.setattr(m, "_probe_period_count", lambda *_a, **_k: 0)


def _seed_plannable_complete(con, *, plannable: str = "2026-03-31") -> None:
    m.ensure_tables(con)
    con.execute(
        """
        INSERT INTO raw_org_holding_aif10
        (report_date, available_date, stock_code, holder_code, fund_derivecode)
        VALUES (?, '2026-04-30', '600000', 'H1', '')
        """,
        [plannable],
    )
    con.commit()


def test_plan_older_fill_picks_oldest_missing(monkeypatch):
    from datetime import date

    con = duckdb.connect(":memory:")
    _seed_plannable_complete(con)
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    plan = plan_older_org_period_fill(
        con,
        plannable="2026-03-31",
        start_period="2019-03-31",
        today=date(2026, 5, 15),
    )
    assert plan["fill_target_period"] == "2019-03-31"
    assert plan["missing_older_count"] > 1
    assert plan["older_remaining"] == plan["missing_older_count"] - 1
    assert len(plan["due_partitions"]) == ORG_PERIOD_CATCHUP_MAX
    con.close()


def test_plan_older_fill_idle_when_calendar_complete(monkeypatch):
    from datetime import date

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    for q in m.enumerate_quarter_ends("2025-12-31", "2026-03-31"):
        con.execute(
            """
            INSERT INTO raw_org_holding_aif10
            (report_date, stock_code, holder_code, fund_derivecode)
            VALUES (?, '600000', 'H1', '')
            """,
            [q],
        )
    con.commit()
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    plan = plan_older_org_period_fill(
        con,
        plannable="2026-03-31",
        start_period="2025-12-31",
        today=date(2026, 5, 15),
    )
    assert plan["fill_target_period"] is None
    assert plan["older_remaining"] == 0
    con.close()


def test_fill_oldest_calls_sync_period_once(monkeypatch):
    con = duckdb.connect(":memory:")
    _seed_plannable_complete(con)
    calls: list[tuple] = []

    def _fake_sync(_conn, period, *, allow_existing_refresh=False):
        calls.append((period, allow_existing_refresh))
        return {
            "report_date": period,
            "status": "ok",
            "written_rows": 100,
            "accepted_partitions": ["20260430"],
        }

    monkeypatch.setattr(m, "sync_period", _fake_sync)
    out = fill_oldest_missing_org_period(
        con, plannable="2026-03-31", start_period="2019-03-31"
    )
    assert out["action"] == "fill_older_period"
    assert out["status"] == "completed"
    assert len(calls) == 1
    assert calls[0][1] is False
    assert calls[0][0] == out["report_date"]
    con.close()


def test_sync_incremental_fills_oldest_when_plannable_complete(monkeypatch):
    con = duckdb.connect(":memory:")
    _seed_plannable_complete(con)
    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)
    filled: list[str] = []

    def _fake_sync(_conn, period, *, allow_existing_refresh=False):
        filled.append(period)
        return {
            "report_date": period,
            "status": "ok",
            "written_rows": 50,
            "accepted_partitions": ["20260430"],
        }

    monkeypatch.setattr(m, "sync_period", _fake_sync)
    result = asyncio.run(m.sync_org_holding_incremental(con))
    assert result["action"] == "fill_older_period"
    assert result["status"] == "completed"
    assert len(filled) == 1
    assert filled[0] == "2018-12-31"
    con.close()


def test_sync_incremental_skips_fill_when_no_missing(monkeypatch):
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    for q in m.enumerate_quarter_ends(m.DEFAULT_START_PERIOD, "2026-03-31"):
        con.execute(
            """
            INSERT INTO raw_org_holding_aif10
            (report_date, stock_code, holder_code, fund_derivecode)
            VALUES (?, '600000', 'H1', '')
            """,
            [q],
        )
    con.commit()
    monkeypatch.setattr(m, "latest_plannable_report_date", lambda today=None: "2026-03-31")
    monkeypatch.setattr(m, "accepted_has_org_holding_partition", lambda *_a, **_k: True)

    def _boom(*_a, **_k):
        raise AssertionError("must not fetch when calendar complete")

    monkeypatch.setattr(m, "sync_period", _boom)
    result = asyncio.run(m.sync_org_holding_incremental(con))
    assert result["action"] == "skip_current"
    assert "bounded fill idle" in result["message"]
    con.close()


def test_fill_older_period_source_unavailable_raises(monkeypatch):
    con = duckdb.connect(":memory:")
    _seed_plannable_complete(con)

    def _fail(_conn, period, *, allow_existing_refresh=False):
        return {
            "report_date": period,
            "status": "source_unavailable",
            "error": "timeout",
            "written_rows": 0,
        }

    monkeypatch.setattr(m, "sync_period", _fail)
    with pytest.raises(RuntimeError, match="org_holding_older_fill_failed"):
        fill_oldest_missing_org_period(
            con, plannable="2026-03-31", start_period="2019-03-31"
        )
    con.close()


def test_gap_report_completeness_miss_after_deadline_still_fetches():
    from datetime import date

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    gap = m.org_holding_period_gap_report(
        con, today=date(2026, 8, 31), start_period="2026-06-30"
    )
    assert gap["plannable"] == "2026-06-30"
    assert gap["completeness_due"] is True
    assert gap["status"] == "completeness_miss"
    assert gap["action"] == "fetch_then_accept"
    assert "2026-06-30" in gap["completeness_miss_periods"]
    con.close()


def test_gap_report_h1_before_deadline_is_in_season_not_miss():
    from datetime import date

    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    gap = m.org_holding_period_gap_report(
        con, today=date(2026, 8, 30), start_period="2026-06-30"
    )
    assert gap["completeness_due"] is False
    assert gap["status"] == "plannable_missing"
    assert gap["action"] == "fetch_then_accept"
    con.close()


def test_count_accepted_org_stocks_follows_report_date_not_deadline():
    from services.org_holding_population import count_accepted_org_stocks

    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_org_holding_detail_period (
            report_date VARCHAR,
            available_date VARCHAR,
            stock_code VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO canonical_org_holding_detail_period VALUES "
        "('2026-06-30', '2026-07-20', '600519')"
    )
    assert count_accepted_org_stocks(con, "2026-06-30") == 1
    con.close()
