"""Ops repair for provider-truncated org periods (sharded re-fetch).

NOT daily_update. One period per loop, oldest truncated first, ≤40/session.
Uses ``allow_existing_refresh=True`` only (explicit ops; mass history banned).

Usage:
    python backend/scripts/org_holding_period_repair_truncated.py --dry-run
    python backend/scripts/org_holding_period_repair_truncated.py --max-periods 3
    python backend/scripts/org_holding_period_repair_truncated.py --period 2025-12-31
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.org_holding_aif10 import (  # noqa: E402
    DEFAULT_START_PERIOD,
    sync_period,
)
from services.org_holding_population import (  # noqa: E402
    count_raw_org_rows,
    count_raw_org_stocks,
)
from services.org_holding_truncation_audit import (  # noqa: E402
    list_truncated_org_periods,
)

SESSION_MAX = 40
REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "data" / "reports" / "org_holding_truncation_repair_latest.json"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def repair_truncated_periods(
    conn,
    *,
    max_periods: int,
    start_period: str = DEFAULT_START_PERIOD,
    only_period: str | None = None,
    dry_run: bool = False,
) -> dict:
    cap = min(max(1, int(max_periods)), SESSION_MAX)
    truncated = list_truncated_org_periods(conn, start_period=start_period)
    if only_period:
        truncated = [t for t in truncated if t["report_date"] == only_period[:10]]
    truncated.sort(key=lambda x: x["report_date"])
    if dry_run:
        return {
            "status": "dry_run",
            "truncated_count": len(truncated),
            "would_repair": [t["report_date"] for t in truncated[:cap]],
        }
    repairs: list[dict] = []
    for i, item in enumerate(truncated[:cap]):
        period = item["report_date"]
        before_rows = int(item["raw_rows"])
        before_stocks = int(item["raw_stocks"])
        print(
            f"[org-trunc-repair] {i + 1}/{cap} period={period} "
            f"before rows={before_rows} stocks={before_stocks}",
            flush=True,
        )
        result = sync_period(conn, period, allow_existing_refresh=True)
        after_rows = count_raw_org_rows(conn, period)
        after_stocks = count_raw_org_stocks(conn, period)
        repairs.append(
            {
                "report_date": period,
                "before_rows": before_rows,
                "before_stocks": before_stocks,
                "after_rows": after_rows,
                "after_stocks": after_stocks,
                "sync_status": result.get("status"),
                "provider_count": result.get("provider_count"),
                "fetched_rows": result.get("fetched_rows"),
                "truncated": result.get("truncated"),
                "shard_count": result.get("shard_count"),
            }
        )
        if result.get("status") == "provider_truncated":
            return {
                "status": "failed",
                "repaired_count": len([r for r in repairs if r["sync_status"] == "ok"]),
                "remaining_truncated": len(truncated) - i - 1,
                "repairs": repairs,
                "error": "provider_truncated_after_sharded_fetch",
            }
        if result.get("status") not in {"ok", "empty"}:
            return {
                "status": "partial",
                "repaired_count": len([r for r in repairs if r["sync_status"] == "ok"]),
                "repairs": repairs,
                "error": result.get("error") or result.get("status"),
            }
    remaining = list_truncated_org_periods(conn, start_period=start_period)
    return {
        "status": "completed" if not remaining else "partial",
        "repaired_count": len(repairs),
        "remaining_truncated": len(remaining),
        "repairs": repairs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair provider-truncated org periods")
    ap.add_argument("--max-periods", type=int, default=int(os.environ.get("ORG_TRUNC_REPAIR_MAX", 1)))
    ap.add_argument("--start-period", default=DEFAULT_START_PERIOD)
    ap.add_argument("--period", help="Repair single YYYY-MM-DD only")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    try:
        before = list_truncated_org_periods(conn, start_period=args.start_period)
        print(
            "truncated_before: "
            + json.dumps(
                {"count": len(before), "periods": [b["report_date"] for b in before]},
                ensure_ascii=False,
            ),
            flush=True,
        )
        out = repair_truncated_periods(
            conn,
            max_periods=args.max_periods,
            start_period=args.start_period,
            only_period=args.period,
            dry_run=args.dry_run,
        )
        out["truncated_before_count"] = len(before)
        if not args.dry_run:
            after = list_truncated_org_periods(conn, start_period=args.start_period)
            out["truncated_after_count"] = len(after)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps({"run_date": _stamp(), "result": out}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        conn.close()
    return 0 if out.get("status") in {"completed", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
