#!/usr/bin/env python3
"""Holders landing retention — archive non-latest ACCEPTED batches, reclaim disk.

Policy (analysis/db_bloat_deep_dive_20260723.md §B / FOUNDATION F3):
  - KEEP landing for latest ACCEPTED batch per partition_value
  - KEEP landing for non-ACCEPTED batches (LANDED/REJECTED — in-flight audit)
  - ARCHIVE other ACCEPTED landing rows to parquet (cold fuse), then DELETE
  - NEVER bare DROP landing_miaoxiang_holders_top10
  - Skip-land already prevents new same-payload storms; this cleans the pile

Dry-run default. ``--execute`` archives + deletes + writes mart_data_deletion_record.
Follow with ``db_compact.py --db smartmoney --execute`` to reclaim file blocks.

Usage:
  PYTHONPATH=backend python backend/scripts/db_holders_landing_retention.py
  PYTHONPATH=backend python backend/scripts/db_holders_landing_retention.py --execute
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
    DATASET_ID,
    LANDING_TABLE,
)
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.holders_landing_retention import (  # noqa: E402
    RetentionPlan,
    apply_retention,
    build_retention_plan,
    ensure_deletion_record_table,
)

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"
DEFAULT_ARCHIVE_DIR = REPO / "data" / "archive" / "holders_landing_retention"


def _db_path() -> Path:
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return REPO / m["databases"]["smartmoney"]["path"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="archive+DELETE (default dry-run)")
    ap.add_argument(
        "--archive-dir",
        type=Path,
        default=DEFAULT_ARCHIVE_DIR,
        help="parquet archive directory",
    )
    args = ap.parse_args(argv)

    db = _db_path()
    if not db.exists():
        print(f"FAIL: smartmoney db missing: {db}", file=sys.stderr)
        return 2

    conn = duck_connect(str(db), read_only=not args.execute)
    try:
        plan: RetentionPlan = build_retention_plan(conn)
        print("=== holders landing retention ===")
        print(f"  dataset_id={DATASET_ID}")
        print(f"  landing={LANDING_TABLE}")
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
        run_id = (
            "holders_landing_retention_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        result = apply_retention(
            conn,
            plan=plan,
            archive_dir=args.archive_dir,
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
