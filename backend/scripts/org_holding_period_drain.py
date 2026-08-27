"""Org holding ops hole drain — explicit oldest-first loop (NOT daily_update auto).

daily_update / pipeline acquire stays N=1/run (anti-mass guard for automatic
runs). This script loops ``fill_oldest_missing_org_period`` until
``missing_older_count==0`` or ``--max-partitions`` (hard cap ≤40/session).

Each iteration = one ``sync_period(..., allow_existing_refresh=False)`` only.
Never ``backfill()`` / never refresh populated periods.

Env:
    ORG_PERIOD_DRAIN_MAX — default max partitions when --max-partitions omitted
                           (falls back to 40; daily auto ORG_PERIOD_CATCHUP_MAX=1 unchanged)

Usage:
    python backend/scripts/org_holding_period_drain.py --dry-run
    python backend/scripts/org_holding_period_drain.py --max-partitions 27
    ORG_PERIOD_DRAIN_MAX=27 python backend/scripts/org_holding_period_drain.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.org_holding_aif10 import (  # noqa: E402
    DEFAULT_START_PERIOD,
    org_holding_period_gap_report,
)
from services.org_holding_db import connect_org_holding  # noqa: E402
from services.org_holding_period_catchup import (  # noqa: E402
    fill_oldest_missing_org_period,
    plan_older_org_period_fill,
)

SESSION_MAX_PARTITIONS = 40
REPO = Path(__file__).resolve().parents[2]
GAP_REPORT = REPO / "data" / "reports" / "org_holding_period_gap_latest.json"


def _run_date() -> str:
    """Ops artifact stamp (not a trade-date cutoff / end_date)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_gap_report(*, gap: dict, result: dict) -> None:
    GAP_REPORT.parent.mkdir(parents=True, exist_ok=True)
    GAP_REPORT.write_text(
        json.dumps(
            {
                "run_date": _run_date(),
                "gap": gap,
                "result": result,
                "source": "org_holding_period_drain.py",
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def drain_older_org_periods(
    conn,
    *,
    max_partitions: int,
    start_period: str = DEFAULT_START_PERIOD,
    dry_run: bool = False,
) -> dict:
    """Loop oldest-first fill; stop on zero remaining, cap, or hard failure."""
    cap = min(max(1, int(max_partitions)), SESSION_MAX_PARTITIONS)
    iterations: list[dict] = []
    filled = 0
    last_error: str | None = None

    for i in range(cap):
        gap = org_holding_period_gap_report(conn, start_period=start_period)
        plan = plan_older_org_period_fill(
            conn,
            plannable=str(gap.get("plannable") or ""),
            start_period=start_period,
        )
        missing = int(plan.get("missing_older_count") or 0)
        target = plan.get("fill_target_period")
        if not target or missing == 0:
            _write_gap_report(
                gap=gap,
                result={
                    "action": "drain_complete",
                    "status": "completed",
                    "filled_periods": filled,
                    "message": "no older missing quarters",
                },
            )
            return {
                "status": "completed",
                "filled_periods": filled,
                "missing_older_before": missing,
                "missing_older_after": 0,
                "iterations": iterations,
            }
        if dry_run:
            return {
                "status": "dry_run",
                "would_fill": target,
                "missing_older_count": missing,
                "max_partitions": cap,
            }
        print(
            f"[org-drain] {i + 1}/{cap} fill oldest={target} "
            f"missing_older={missing}",
            flush=True,
        )
        try:
            fill_out = fill_oldest_missing_org_period(
                conn,
                plannable=str(gap.get("plannable") or ""),
                start_period=start_period,
            )
        except RuntimeError as exc:
            last_error = str(exc)
            iterations.append(
                {
                    "iteration": i + 1,
                    "target": target,
                    "status": "failed",
                    "error": last_error,
                }
            )
            gap_after = org_holding_period_gap_report(conn, start_period=start_period)
            _write_gap_report(
                gap=gap_after,
                result={
                    "action": "fill_older_period",
                    "status": "failed",
                    "error": last_error,
                    "filled_periods": filled,
                },
            )
            return {
                "status": "failed",
                "filled_periods": filled,
                "missing_older_after": int(
                    gap_after.get("missing_older_count")
                    or gap_after.get("missing_count")
                    or 0
                ),
                "last_error": last_error,
                "iterations": iterations,
            }
        iterations.append(
            {
                "iteration": i + 1,
                "target": fill_out.get("report_date"),
                "status": fill_out.get("status"),
                "written": fill_out.get("written"),
                "fetch_status": fill_out.get("fetch_status"),
            }
        )
        if fill_out.get("status") not in {"completed", "partial"}:
            last_error = fill_out.get("message") or "fill_not_completed"
            gap_after = org_holding_period_gap_report(conn, start_period=start_period)
            _write_gap_report(
                gap=gap_after,
                result={
                    "action": "fill_older_period",
                    "status": fill_out.get("status"),
                    "error": last_error,
                    "filled_periods": filled,
                },
            )
            return {
                "status": "partial",
                "filled_periods": filled,
                "missing_older_after": int(
                    gap_after.get("missing_older_count")
                    or gap_after.get("missing_count")
                    or 0
                ),
                "last_error": last_error,
                "iterations": iterations,
            }
        filled += 1

    gap_after = org_holding_period_gap_report(conn, start_period=start_period)
    remaining = int(
        gap_after.get("missing_older_count")
        or gap_after.get("missing_count")
        or 0
    )
    _write_gap_report(
        gap=gap_after,
        result={
            "action": "drain_capped",
            "status": "partial" if remaining else "completed",
            "filled_periods": filled,
            "remaining": remaining,
            "message": f"stopped at session cap {cap}",
        },
    )
    return {
        "status": "partial" if remaining else "completed",
        "filled_periods": filled,
        "missing_older_after": remaining,
        "session_cap": cap,
        "iterations": iterations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Org holding ops oldest-first hole drain")
    ap.add_argument(
        "--max-partitions",
        type=int,
        default=int(os.environ.get("ORG_PERIOD_DRAIN_MAX", SESSION_MAX_PARTITIONS)),
        help=f"Max periods this session (≤{SESSION_MAX_PARTITIONS})",
    )
    ap.add_argument(
        "--start-period",
        default=DEFAULT_START_PERIOD,
        help="Calendar scan start (default org DEFAULT_START_PERIOD)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Plan only; no provider I/O")
    args = ap.parse_args()

    conn = connect_org_holding()
    try:
        gap_before = org_holding_period_gap_report(conn, start_period=args.start_period)
        before = int(
            gap_before.get("missing_older_count")
            or gap_before.get("missing_count")
            or 0
        )
        print(
            "org_drain_before: "
            + json.dumps(
                {
                    "missing_older_count": before,
                    "fill_target_period": gap_before.get("fill_target_period"),
                    "plannable": gap_before.get("plannable"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        out = drain_older_org_periods(
            conn,
            max_partitions=args.max_partitions,
            start_period=args.start_period,
            dry_run=args.dry_run,
        )
        out["missing_older_before"] = before
        if not args.dry_run:
            gap_after = org_holding_period_gap_report(conn, start_period=args.start_period)
            out["missing_older_after"] = int(
                gap_after.get("missing_older_count")
                or gap_after.get("missing_count")
                or 0
            )
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        conn.close()

    if out.get("status") in {"failed", "error"}:
        return 1
    if out.get("status") == "partial" and out.get("missing_older_after", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
