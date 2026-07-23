"""Shared frontier decision primitive — local axis max vs target probe/calendar.

Owner model (analysis/data_frontier_detection_system_20260723.md):
  local max(axis) → calendar/disclosure rule → should-have set → fetch gap

This is one typed compare + day-window policy helper — not a DetectionService,
plugin bus, or DAG. Generalized from holders equal-day sparse fix (e040f4889).

Axis values are domain frontiers (trade_date / notice_date / ann_date /
report_period), never wall-clock 「对昨天」.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

FrontierAxis = Literal["trade_date", "notice_date", "ann_date", "report_period"]
FrontierOutcome = Literal[
    "skip_behind",
    "equal_day_population_gap",
    "advance_window",
    "pending_clock",
    "hard_fail",
]
WatermarkDayPolicy = Literal["atomic_skip", "ann_reprobe"]


@dataclass(frozen=True)
class FrontierDecision:
    outcome: FrontierOutcome
    axis: FrontierAxis
    local_max: Optional[str]
    target_max: Optional[str]
    reason: str


def normalize_frontier_value(value: Optional[str], *, axis: FrontierAxis) -> Optional[str]:
    """Normalize axis values for compare. Dates → YYYYMMDD; periods stay ISO/compact digits."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if axis == "report_period":
        # Keep YYYY-MM-DD when present; else compact digits (YYYYMMDD / YYYYMM).
        if "-" in text:
            return text[:10]
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits or None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def decide_frontier(
    *,
    axis: FrontierAxis,
    local_max: Optional[str],
    target_max: Optional[str],
    clock_pending: bool = False,
    probe_failed: bool = False,
) -> FrontierDecision:
    """Compare local stock frontier to provider probe or eligible calendar end.

    Outcomes:
      - hard_fail: probe/local read failed closed
      - pending_clock: publication clock not open (caller-supplied)
      - skip_behind: target strictly behind local (nothing new)
      - equal_day_population_gap: same frontier day — date equality ≠ population complete
      - advance_window: target ahead of local, or target unknown (fail-open to window)
    """
    local_n = normalize_frontier_value(local_max, axis=axis)
    target_n = normalize_frontier_value(target_max, axis=axis)
    if probe_failed:
        return FrontierDecision(
            outcome="hard_fail",
            axis=axis,
            local_max=local_n,
            target_max=target_n,
            reason="probe_failed",
        )
    if clock_pending:
        return FrontierDecision(
            outcome="pending_clock",
            axis=axis,
            local_max=local_n,
            target_max=target_n,
            reason="publication_clock_pending",
        )
    if target_n is None:
        # Holders path: provider probe None → safety-window advance (not silent skip).
        return FrontierDecision(
            outcome="advance_window",
            axis=axis,
            local_max=local_n,
            target_max=None,
            reason="target_unknown_advance",
        )
    if local_n is None:
        return FrontierDecision(
            outcome="advance_window",
            axis=axis,
            local_max=None,
            target_max=target_n,
            reason="local_empty_bootstrap",
        )
    if target_n < local_n:
        return FrontierDecision(
            outcome="skip_behind",
            axis=axis,
            local_max=local_n,
            target_max=target_n,
            reason="target_behind_local",
        )
    if target_n == local_n:
        return FrontierDecision(
            outcome="equal_day_population_gap",
            axis=axis,
            local_max=local_n,
            target_max=target_n,
            reason="equal_frontier_population_unproven",
        )
    return FrontierDecision(
        outcome="advance_window",
        axis=axis,
        local_max=local_n,
        target_max=target_n,
        reason="target_ahead_of_local",
    )


def plan_incremental_days(
    days: Sequence[str],
    *,
    watermark: Optional[str],
    decision: FrontierDecision,
    policy: WatermarkDayPolicy,
    explicit_start: bool,
    backfill: bool,
    pending_replay_day: Optional[str] = None,
) -> list[str]:
    """Apply watermark-day inclusion to a planned day list.

    Policies:
      - atomic_skip (dense by_trade_date): drop wm day when window continues past it
        (assumes full-day batch landed atomically). Equal single-day windows stay.
      - ann_reprobe (by_ann_date sparse disclosure): never drop wm day on equal/advance —
        cheap full-day re-pull closes same-day late-filer gaps (holders bug class).
    """
    out = [str(d).replace("-", "")[:8] for d in days if d]
    if backfill or explicit_start or not out:
        return out
    wm = normalize_frontier_value(watermark, axis="ann_date")
    if not wm or out[0] != wm:
        return out
    pending = normalize_frontier_value(pending_replay_day, axis="ann_date")
    if pending and out[0] == pending:
        return out
    if policy == "ann_reprobe":
        if decision.outcome in (
            "equal_day_population_gap",
            "advance_window",
        ):
            return out
        # skip_behind / pending_clock / hard_fail: drop wm day if window continues
        if len(out) > 1:
            return out[1:]
        return [] if decision.outcome == "skip_behind" else out
    # atomic_skip — legacy dense trade-date semantics
    if len(out) > 1:
        return out[1:]
    return out


def org_holding_period_frontier_hook(
    *,
    local_max_period: Optional[str],
    plannable_period: Optional[str],
    clock_pending: bool = False,
) -> FrontierDecision:
    """Optional report_period compare for future repair tooling only.

    org_holding stays period-gap (existence of latest plannable). Equal period must
    NOT be read as equal_day_population_gap → by-date invent. Callers that need
    population repair use an explicit knife; this hook only exposes typed compare.
    """
    decision = decide_frontier(
        axis="report_period",
        local_max=local_max_period,
        target_max=plannable_period,
        clock_pending=clock_pending,
    )
    if decision.outcome == "equal_day_population_gap":
        # Remap label honesty: period equal = existence check complete for planner,
        # not sparse same-day population probe (no NOTICE_DATE faucet).
        return FrontierDecision(
            outcome="skip_behind",
            axis="report_period",
            local_max=decision.local_max,
            target_max=decision.target_max,
            reason="period_present_no_by_date_population_probe",
        )
    return decision


__all__ = [
    "FrontierAxis",
    "FrontierDecision",
    "FrontierOutcome",
    "WatermarkDayPolicy",
    "decide_frontier",
    "normalize_frontier_value",
    "org_holding_period_frontier_hook",
    "plan_incremental_days",
]
