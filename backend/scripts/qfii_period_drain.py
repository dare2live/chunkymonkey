"""QFII ops hole drain — oldest-first missing report periods (NOT daily auto).

daily_update ``sync_qfii_incremental`` only fills the latest plannable quarter.
This script fills calendar holes oldest-first with ``sync_qfii_quarter`` only
for periods that have zero local rows (no mass refresh of populated quarters).

    python backend/scripts/qfii_period_drain.py --dry-run
    python backend/scripts/qfii_period_drain.py --max-partitions 22
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.qfii_client import (  # noqa: E402
    DEFAULT_START_PERIOD,
    list_missing_qfii_report_dates,
    latest_plannable_report_date,
    sync_qfii_quarter,
)

SESSION_MAX = 40


async def _drain(conn, *, missing: list[str], max_partitions: int) -> dict:
    filled: list[dict] = []
    for i, q in enumerate(missing[:max_partitions], start=1):
        print(
            f"[qfii-drain] {i}/{min(len(missing), max_partitions)} fill {q} "
            f"remaining={len(missing) - i + 1}",
            flush=True,
        )
        result = await sync_qfii_quarter(conn, q)
        filled.append(result)
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        if result.get("status") == "source_unavailable":
            return {
                "status": "failed",
                "filled_periods": len(
                    [x for x in filled if int(x.get("written_rows") or 0) > 0]
                ),
                "last_error": result.get("error"),
                "iterations": filled,
                "missing_after": list_missing_qfii_report_dates(
                    conn, start_date=DEFAULT_START_PERIOD
                ),
            }
    after = list_missing_qfii_report_dates(conn, start_date=DEFAULT_START_PERIOD)
    return {
        "status": "completed" if not after else "partial",
        "filled_periods": len(
            [x for x in filled if int(x.get("written_rows") or 0) > 0]
        ),
        "iterations": filled,
        "missing_before": missing,
        "missing_after": after,
        "missing_after_count": len(after),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-period", default=DEFAULT_START_PERIOD)
    ap.add_argument("--max-partitions", type=int, default=SESSION_MAX)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    max_n = max(1, min(int(args.max_partitions), SESSION_MAX))

    conn = get_conn()
    try:
        missing = list_missing_qfii_report_dates(conn, start_date=args.start_period)
        print(
            "qfii_drain_before:",
            json.dumps(
                {
                    "missing_count": len(missing),
                    "fill_target": missing[0] if missing else None,
                    "plannable": latest_plannable_report_date(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if args.dry_run:
            print(
                json.dumps(
                    {"missing": missing[:max_n], "missing_total": len(missing)},
                    ensure_ascii=False,
                )
            )
            return 0
        if not missing:
            print(json.dumps({"status": "idle", "missing_after_count": 0}, ensure_ascii=False))
            return 0
        out = asyncio.run(_drain(conn, missing=missing, max_partitions=max_n))
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0 if out.get("status") in {"completed", "partial", "idle"} else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
