"""Shared frontier decision primitive — typed outcomes + day-window policies."""
from __future__ import annotations

from services.data_sources.frontier_decision import (
    decide_frontier,
    org_holding_period_frontier_hook,
    plan_incremental_days,
)


def test_decide_frontier_three_compare_branches():
    behind = decide_frontier(
        axis="notice_date", local_max="20260723", target_max="20260722"
    )
    assert behind.outcome == "skip_behind"

    equal = decide_frontier(
        axis="notice_date", local_max="20260723", target_max="20260723"
    )
    assert equal.outcome == "equal_day_population_gap"

    ahead = decide_frontier(
        axis="notice_date", local_max="20260722", target_max="20260723"
    )
    assert ahead.outcome == "advance_window"


def test_decide_frontier_pending_and_hard_fail():
    pending = decide_frontier(
        axis="trade_date",
        local_max="20260722",
        target_max="20260723",
        clock_pending=True,
    )
    assert pending.outcome == "pending_clock"

    failed = decide_frontier(
        axis="ann_date",
        local_max="20260722",
        target_max=None,
        probe_failed=True,
    )
    assert failed.outcome == "hard_fail"


def test_decide_frontier_target_none_advances():
    out = decide_frontier(axis="notice_date", local_max="20260722", target_max=None)
    assert out.outcome == "advance_window"
    assert out.reason == "target_unknown_advance"


def test_ann_reprobe_keeps_watermark_day_on_equal_and_advance():
    days = ["20260722", "20260723"]
    equal = decide_frontier(
        axis="ann_date", local_max="20260722", target_max="20260722"
    )
    assert plan_incremental_days(
        days,
        watermark="20260722",
        decision=equal,
        policy="ann_reprobe",
        explicit_start=False,
        backfill=False,
    ) == ["20260722", "20260723"]

    advance = decide_frontier(
        axis="ann_date", local_max="20260722", target_max="20260723"
    )
    assert plan_incremental_days(
        days,
        watermark="20260722",
        decision=advance,
        policy="ann_reprobe",
        explicit_start=False,
        backfill=False,
    ) == ["20260722", "20260723"]


def test_atomic_skip_still_drops_watermark_day_when_window_continues():
    days = ["20260722", "20260723"]
    advance = decide_frontier(
        axis="trade_date", local_max="20260722", target_max="20260723"
    )
    assert plan_incremental_days(
        days,
        watermark="20260722",
        decision=advance,
        policy="atomic_skip",
        explicit_start=False,
        backfill=False,
    ) == ["20260723"]


def test_explicit_start_and_backfill_never_strip_days():
    days = ["20260722", "20260723"]
    advance = decide_frontier(
        axis="ann_date", local_max="20260722", target_max="20260723"
    )
    assert plan_incremental_days(
        days,
        watermark="20260722",
        decision=advance,
        policy="ann_reprobe",
        explicit_start=True,
        backfill=False,
    ) == days
    assert plan_incremental_days(
        days,
        watermark="20260722",
        decision=advance,
        policy="atomic_skip",
        explicit_start=False,
        backfill=True,
    ) == days


def test_org_holding_hook_remaps_equal_to_skip_not_population_gap():
    out = org_holding_period_frontier_hook(
        local_max_period="2026-03-31",
        plannable_period="2026-03-31",
    )
    assert out.outcome == "skip_behind"
    assert out.reason == "period_present_no_by_date_population_probe"
    assert out.axis == "report_period"
