"""Freeze disclosure DatasetSnapshot from live accepted partitions.

Usage:
  PYTHONPATH=backend python backend/scripts/freeze_disclosure_dataset_snapshot.py
  PYTHONPATH=backend python backend/scripts/freeze_disclosure_dataset_snapshot.py --bounded
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_access.resolver import connect_ro  # noqa: E402
from services.data_sources.disclosure_dataset_snapshot import (  # noqa: E402
    default_snapshot_path,
    freeze_disclosure_dataset_snapshot,
)
from services.data_sources.disclosure_shadow_compare import (  # noqa: E402
    compare_disclosure_research_shadow,
)
from services.db import get_conn  # noqa: E402
from services.org_holding_db import connect_org_holding  # noqa: E402

# Shadow gate for this development freeze: one MATCH partition per domain,
# all strictly before holdout_start=20250601. Serving-window canaries
# (holders 20260717 / org 20190430 / stk 20260706) are not the freeze set;
# org 20190430 currently MISMATCHES and must not block a pre-holdout freeze.
_FREEZE_SHADOW = {
    "holders_top10": "20250531",
    "org_holding": "20250430",
    "stk_holdertrade": "20250530",
}

# Development freeze: accepted partitions strictly before holdout_start=20250601.
_BOUNDED_SETS = {
    "holders_top10": [
        "20250331",
        "20250430",
        "20250508",
        "20250512",
        "20250521",
        "20250528",
        "20250530",
        "20250531",
    ],
    "org_holding": ["20240831", "20250430"],
    "stk_holdertrade": [
        "20250418",
        "20250506",
        "20250508",
        "20250513",
        "20250523",
        "20250528",
        "20250530",
    ],
}

_BOUNDED_NOTES = (
    "development_before_holdout_20250601",
    "org_shadow_match_20240831_20250430",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--bounded",
        action="store_true",
        help="Freeze bounded_accepted_partitions with explicit date sets",
    )
    ap.add_argument(
        "--path",
        default="",
        help="Output path (default data/lineage/disclosure_dataset_snapshot.json)",
    )
    args = ap.parse_args()

    sm = get_conn()
    raw = connect_ro("tushare_raw")
    org = connect_org_holding(read_only=True)
    try:
        # Shadow gate uses pre-holdout MATCH canaries, not the 2026 serving window.
        shadow = compare_disclosure_research_shadow(
            sm,
            partitions=_FREEZE_SHADOW,
            domain_conns={"stk_holdertrade": raw, "org_holding": org},
        )
        if not shadow.cutover_allowed:
            print(
                f"[freeze] cutover blocked overall={shadow.overall_status}",
                file=sys.stderr,
            )
            return 2

        path = Path(args.path) if args.path else default_snapshot_path()
        snap = freeze_disclosure_dataset_snapshot(
            {
                "holders_top10": sm,
                "org_holding": org,
                "stk_holdertrade": raw,
            },
            shadow=shadow,
            path=path,
            partition_sets=_BOUNDED_SETS if args.bounded else None,
            extra_notes=_BOUNDED_NOTES if args.bounded else (),
            # B0 consumers must not expand to live full accepted calendars.
            nominal_conn=raw,
        )
        print(json.dumps(snap.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        print(f"[freeze] wrote {path} scope={snap.scope}", file=sys.stderr)
    finally:
        sm.close()
        raw.close()
        org.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
