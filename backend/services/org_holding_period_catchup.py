"""Org holding bounded older-quarter fill — one oldest missing period per run.

When latest plannable is complete (skip_current), fill the oldest calendar gap
via ``sync_period(..., allow_existing_refresh=False)``. NOT ``backfill()`` / NOT
mass refresh.

Law: ``plan_partition_catchup`` (calendar \\ local_raw, P≤plannable,
oldest_first, N=1). Evidence: ``analysis/org_period_bounded_fill_20260724.md``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from services.data_sources.frontier_decision import plan_partition_catchup

ORG_PERIOD_CATCHUP_MAX = 1  # N=1 per run (owner bounded fill)


def _normalize_period(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "-" in text:
        return text[:10]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def _distinct_local_periods(conn: Any) -> list[str]:
    from services.org_holding_population import list_local_org_report_periods

    return list_local_org_report_periods(conn)


def plan_older_org_period_fill(
    conn: Any,
    *,
    plannable: str,
    start_period: str,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Due-set for one oldest missing quarter at or below plannable."""
    from services.org_holding_aif10 import DEFAULT_START_PERIOD, enumerate_quarter_ends

    start = _normalize_period(start_period) or DEFAULT_START_PERIOD
    target = _normalize_period(plannable)
    if not target:
        return {
            "fill_target_period": None,
            "older_remaining": 0,
            "missing_older_count": 0,
            "catchup_law": "plan_partition_catchup",
            "catchup_reason": "no_plannable",
        }

    calendar = enumerate_quarter_ends(start, target)
    local = _distinct_local_periods(conn)
    plan_one = plan_partition_catchup(
        axis="report_period",
        source_partitions=(),
        accepted_partitions=local,
        watermark=target,
        calendar_partitions=calendar,
        max_partitions=ORG_PERIOD_CATCHUP_MAX,
        order="oldest_first",
    )
    plan_all = plan_partition_catchup(
        axis="report_period",
        source_partitions=(),
        accepted_partitions=local,
        watermark=target,
        calendar_partitions=calendar,
        max_partitions=max(len(calendar), 1),
        order="oldest_first",
    )
    due_all = list(plan_all.due_partitions)
    fill_target = plan_one.due_partitions[0] if plan_one.due_partitions else None
    missing_older = len(due_all)
    older_remaining = max(missing_older - (1 if fill_target else 0), 0)
    return {
        "fill_target_period": fill_target,
        "older_remaining": older_remaining,
        "missing_older_count": missing_older,
        "due_partitions": list(plan_one.due_partitions),
        "catchup_law": "plan_partition_catchup",
        "catchup_reason": plan_one.reason,
    }


def fill_oldest_missing_org_period(
    conn: Any,
    *,
    plannable: str,
    start_period: str,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Fetch+accept one oldest missing quarter (bounded, no mass refresh)."""
    from services.org_holding_aif10 import sync_period

    plan = plan_older_org_period_fill(
        conn,
        plannable=plannable,
        start_period=start_period,
        today=today,
    )
    target = plan.get("fill_target_period")
    if not target:
        return {
            "action": "skip_current",
            "status": "skipped",
            "fill_plan": plan,
            "message": "no older missing quarters to fill",
        }
    result = sync_period(conn, target, allow_existing_refresh=False)
    if result.get("status") == "source_unavailable":
        raise RuntimeError(f"org_holding_older_fill_failed:{result.get('error')}")
    written = int(result.get("written_rows") or 0)
    accept_ok = bool(result.get("accepted_partitions")) or written > 0
    return {
        "action": "fill_older_period",
        "status": "completed" if accept_ok else "partial",
        "report_date": target,
        "written": written,
        "fetch_status": result.get("status"),
        "accept": {
            "status": "accepted" if accept_ok else "accept_failed",
            "partitions": result.get("accepted_partitions") or [],
            "legacy_rows_written": result.get("legacy_rows_written"),
        },
        "fill_plan": plan,
        "message": (
            f"bounded fill: oldest missing={target} wrote={written} "
            f"older_remaining={plan.get('older_remaining')} "
            f"(N=1/run; not mass refresh / not backfill())"
        ),
    }


async def sync_older_org_period_if_due(conn: Any, gap: dict) -> dict | None:
    """When plannable complete, fill oldest missing quarter (N=1). Returns None if idle."""
    target = gap.get("plannable")
    if str(gap.get("action") or "") != "skip_current" or not gap.get("fill_target_period"):
        return None
    import asyncio

    from services.org_holding_aif10 import (
        DEFAULT_START_PERIOD,
        _plannable_available_yyyymmdd,
    )

    loop = asyncio.get_running_loop()
    fill_out = await loop.run_in_executor(
        None,
        lambda: fill_oldest_missing_org_period(
            conn,
            plannable=str(target),
            start_period=DEFAULT_START_PERIOD,
        ),
    )
    written = int(fill_out.get("written") or 0)
    return {
        "domain": "org_holding",
        "count": written,
        "status": fill_out.get("status"),
        "action": fill_out.get("action"),
        "report_date": fill_out.get("report_date"),
        "available_date": _plannable_available_yyyymmdd(
            str(fill_out.get("report_date") or "")
        ),
        "written": written,
        "fetch_status": fill_out.get("fetch_status"),
        "accept": fill_out.get("accept"),
        "next_period": gap.get("next_period"),
        "next_period_unlock": gap.get("next_period_unlock"),
        "gap": gap,
        "fill_plan": fill_out.get("fill_plan"),
        "message": fill_out.get("message"),
    }


def org_due_row_from_gap(gap: dict[str, Any], *, source: str) -> dict[str, Any]:
    plannable = gap.get("plannable")
    fill_target = gap.get("fill_target_period")
    action = str(gap.get("action") or "skip_current")
    if gap.get("bounded_fill_action") == "fill_older_period" and fill_target:
        action = "fill_older_period"
    details = {
        "fetch_then_accept": f"plannable={plannable} raw=missing → fetch+accept one period",
        "accept_from_local_raw": f"plannable={plannable} raw=present accepted=missing → accept",
        "fill_older_period": (
            f"plannable={plannable} complete; fill oldest missing={fill_target} "
            f"(N=1/run; older_remaining={gap.get('older_remaining')})"
        ),
        "skip_current": (
            f"plannable={plannable} current; next {gap.get('next_period')} "
            f"unlocks {gap.get('next_period_unlock')} (not forever blocked)"
        ),
    }
    return {
        "domain": "org_holding",
        "watermark": plannable,
        "days_ago": 0 if action == "skip_current" else 1,
        "status": gap.get("status"),
        "will_fetch": action
        in {"fetch_then_accept", "accept_from_local_raw", "fill_older_period"},
        "kind": "period_incremental",
        "action": action,
        "detail": details.get(action, f"action={action} plannable={plannable}"),
        "source": source,
        "next_period": gap.get("next_period"),
        "next_period_unlock": gap.get("next_period_unlock"),
        "fill_target_period": fill_target,
        "older_remaining": gap.get("older_remaining"),
    }


def org_holding_due_item(*, repo: Path) -> dict[str, Any] | None:
    """Period-domain due row for ops manual-run preview."""
    import json

    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect as duck_connect
    from services.org_holding_aif10 import org_holding_period_gap_report

    try:
        path = get_database_manifest().path_for("smartmoney")
        if path.is_file():
            conn = duck_connect(str(path), read_only=True)
            try:
                gap = org_holding_period_gap_report(conn)
            finally:
                conn.close()
            return org_due_row_from_gap(gap, source="live_ro")
    except Exception:  # noqa: BLE001
        pass

    latest = repo / "data" / "reports" / "org_holding_period_gap_latest.json"
    if not latest.is_file():
        return {
            "domain": "org_holding",
            "watermark": None,
            "days_ago": 0,
            "status": "unchecked",
            "will_fetch": True,
            "kind": "period_incremental",
            "action": "check_required",
            "detail": "no live DB / no gap artifact — next daily_update must check",
        }
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    gap = payload.get("gap") if isinstance(payload, dict) else None
    if not isinstance(gap, dict):
        return None
    return org_due_row_from_gap(gap, source="gap_latest")


__all__ = [
    "ORG_PERIOD_CATCHUP_MAX",
    "fill_oldest_missing_org_period",
    "org_due_row_from_gap",
    "org_holding_due_item",
    "plan_older_org_period_fill",
    "sync_older_org_period_if_due",
]
