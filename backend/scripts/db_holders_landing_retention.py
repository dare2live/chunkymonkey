#!/usr/bin/env python3
"""Landing retention — archive ACCEPTED batches that are not the published pointer.

Policy (git log --grep db_bloat_deep_dive §B / FOUNDATION F3):
  - KEEP landing for accepted_partition.batch_id
  - KEEP latest ACCEPTED per partition only when no pointer exists
  - KEEP landing for non-ACCEPTED batches (LANDED/REJECTED — in-flight audit)
  - ARCHIVE other ACCEPTED landing rows to parquet (cold fuse), then DELETE
  - NEVER bare DROP the landing table
  - Skip-land (row-hash identity) prevents same-payload storms

Dry-run default. ``--execute`` archives + deletes + writes mart_data_deletion_record.
Follow with ``db_compact.py --db smartmoney|org_holding --execute`` to reclaim file blocks.

Usage:
  PYTHONPATH=backend python backend/scripts/db_holders_landing_retention.py
  PYTHONPATH=backend python backend/scripts/db_holders_landing_retention.py --dataset org_holding --execute --max-archive-batches 2000
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.data_sources.holders_top10_schema import (  # noqa: E402
    DATASET_ID as HOLDERS_DATASET_ID,
    LANDING_TABLE as HOLDERS_LANDING,
)
from services.data_sources.org_holding_schema import (  # noqa: E402
    DATASET_ID as ORG_DATASET_ID,
    LANDING_TABLE as ORG_LANDING,
)
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.holders_landing_retention import (  # noqa: E402
    RetentionPlan,
    apply_retention,
    build_retention_plan,
    ensure_deletion_record_table,
    slice_archive_plan,
)

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"
DATASETS = {
    "holders_top10": {
        "dataset_id": HOLDERS_DATASET_ID,
        "landing_table": HOLDERS_LANDING,
        "archive_dir": REPO / "data" / "archive" / "holders_landing_retention",
    },
    "org_holding": {
        "dataset_id": ORG_DATASET_ID,
        "landing_table": ORG_LANDING,
        "archive_dir": REPO / "data" / "archive" / "org_holding_landing_retention",
    },
}


def _db_path(dataset: str) -> Path:
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    alias = "org_holding" if dataset == "org_holding" else "smartmoney"
    return REPO / m["databases"][alias]["path"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="archive+DELETE (default dry-run)")
    ap.add_argument(
        "--dataset",
        choices=sorted(DATASETS),
        default="holders_top10",
        help="disclosure landing table to retain",
    )
    ap.add_argument(
        "--max-archive-batches",
        type=int,
        default=0,
        help="archive at most N superseded batches this run (0 = all; use to bound COPY spill)",
    )
    ap.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="parquet archive directory (default per --dataset)",
    )
    args = ap.parse_args(argv)
    spec = DATASETS[args.dataset]
    archive_dir = args.archive_dir or spec["archive_dir"]

    db = _db_path(args.dataset)
    if not db.exists():
        print(f"FAIL: db missing: {db}", file=sys.stderr)
        return 2

    conn = duck_connect(str(db), read_only=not args.execute)
    try:
        plan: RetentionPlan = build_retention_plan(
            conn,
            dataset_id=spec["dataset_id"],
            landing_table=spec["landing_table"],
        )
        if args.max_archive_batches:
            plan = slice_archive_plan(conn, plan, args.max_archive_batches)
        print(f"=== {args.dataset} landing retention ===")
        print(f"  dataset_id={spec['dataset_id']}")
        print(f"  landing={spec['landing_table']}")
        print(
            f"  partitions={plan.partition_count} "
            f"keep_batches={plan.keep_batch_count} "
            f"archive_batches={plan.archive_batch_count}"
        )
        print(
            f"  landing_rows total={plan.total_landing_rows} "
            f"keep={plan.keep_landing_rows} "
            f"archive={plan.archive_landing_rows}"
        )
        if plan.archive_landing_rows <= 0:
            print("  nothing to archive; done.")
            return 0
        if not args.execute:
            print("  DRY-RUN: pass --execute to archive parquet + DELETE archive rows.")
            return 0

        ensure_deletion_record_table(conn)
        run_id = f"{args.dataset}_landing_retention_{uuid.uuid4().hex[:12]}"
        result = apply_retention(
            conn,
            plan=plan,
            archive_dir=archive_dir,
            run_id=run_id,
        )
        print(f"  archived_parquet={result.archive_path}")
        print(f"  archived_rows={result.archived_rows} deleted_rows={result.deleted_rows}")
        print(f"  deletion_run_id={run_id}")
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
