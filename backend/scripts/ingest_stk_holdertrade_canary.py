"""E0 canary CLI: accept one stk_holdertrade ann_date from legacy tushare_raw.

Usage:
    PYTHONPATH=backend python backend/scripts/ingest_stk_holdertrade_canary.py \\
        --accept-legacy-partition 20260706
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_sources.disclosure_dual_write import (  # noqa: E402
    accept_stk_holdertrade_partition_from_legacy,
)
from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--accept-legacy-partition",
        required=True,
        help="E0 canary: formal land→accept one ann_date from legacy (YYYYMMDD)",
    )
    ap.add_argument(
        "--rewrite-legacy",
        action="store_true",
        help="Also rewrite legacy partition (DELETE+INSERT); default no-op mirror",
    )
    ap.add_argument(
        "--target-db",
        default="tushare_raw",
        help="database_manifest alias (default tushare_raw)",
    )
    args = ap.parse_args()

    path = str(get_database_manifest().path_for(args.target_db))
    conn = duck_connect(path, read_only=False)
    try:
        outcome = accept_stk_holdertrade_partition_from_legacy(
            conn,
            args.accept_legacy_partition,
            rewrite_legacy=bool(args.rewrite_legacy),
        )
        print(
            "[stk-holdertrade] ACCEPT_LEGACY "
            f"status={outcome.status} partitions={outcome.partitions} "
            f"canonical_rows={outcome.canonical_rows} "
            f"batch_ids={outcome.batch_ids} db={args.target_db}"
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
