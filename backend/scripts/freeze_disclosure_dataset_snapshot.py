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

# Serving canary partitions that already MATCH (cutover gate).
_SERVING = {
    "holders_top10": "20260717",
    "org_holding": "20190430",
    "stk_holdertrade": "20260706",
}

# Bounded broaden — recent/small accepts (+ canaries). Org 20260430 is a
# documented stock subset (600519,000001), not full-universe.
# 2026-07-21 chunk: +holders 20260711/20260715; +stk 20260714/20260715
# via land-then-accept --from-local-raw (DUPLICATE_GRAIN dates fail-closed).
_BOUNDED_SETS = {
    "holders_top10": [
        "20260508",
        "20260616",
        "20260618",
        "20260619",
        "20260623",
        "20260703",
        "20260709",
        "20260710",
        "20260711",
        "20260713",
        "20260714",
        "20260715",
        "20260717",
    ],
    "org_holding": ["20190430", "20260430"],
    "stk_holdertrade": [
        "20260518",
        "20260608",
        "20260706",
        "20260713",
        "20260714",
        "20260715",
    ],
}

_BOUNDED_NOTES = (
    "org_20260430_stock_subset_600519_000001",
    "holders_stk_full_legacy_accept_small_recent",
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
    try:
        # Shadow cutover gate stays on serving MATCH canaries.
        shadow = compare_disclosure_research_shadow(
            sm,
            partitions=_SERVING,
            domain_conns={"stk_holdertrade": raw},
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
                "org_holding": sm,
                "stk_holdertrade": raw,
            },
            shadow=shadow,
            path=path,
            partition_sets=_BOUNDED_SETS if args.bounded else None,
            extra_notes=_BOUNDED_NOTES if args.bounded else (),
        )
        print(json.dumps(snap.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        print(f"[freeze] wrote {path} scope={snap.scope}", file=sys.stderr)
    finally:
        sm.close()
        raw.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
