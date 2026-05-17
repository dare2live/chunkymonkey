#!/usr/bin/env python3
"""Codex round 17 Q7 verdict — cleanup corrupt-era mart_p0b_oos_predictions / walkforward_eval.

按 governance v1 (volume unit fix 后), 所有 label_version IN ('v1', 'p0a_v1') 的 oos predictions
都是 corrupt 时代产物 (含 lgbm_v3_honest_20d ann_ret=21843% 假数据), 应 purge.

执行 (governance v1 完成时已跑):
- mart_p0b_oos_predictions: 11,655,579 rows deleted (含 lgbm_v3_honest_20d / lambdamart_v3_honest_20d
  / lgbm_baseline_* / phase2_* / lambdamart_v3_*)
- mart_p0b_walkforward_eval: 104+ rows deleted
- table 现 empty, 待 Phase 3 step 3 重训 lgbm_20260517_governance_v1_20d 填充

DuckDB ART index FATAL fallback: 用 DROP TABLE + CREATE FROM SELECT (CTAS) 模式避免大 DELETE bug.

用法:
    PYTHONPATH=backend python backend/scripts/cleanup_corrupt_oos_predictions.py        # dry-run
    PYTHONPATH=backend python backend/scripts/cleanup_corrupt_oos_predictions.py --execute
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cleanup_corrupt_oos")

REPO_ROOT = Path(__file__).resolve().parents[2]
SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"

# from yaml: configs/data_governance.yaml deprecation
# governance v1 之前的 label_version (v1 = phase1/2; p0a_v1 = P0a 旧 corrupt 时代)
CORRUPT_LABEL_VERSIONS = ("v1", "p0a_v1")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup corrupt-era oos predictions (governance v1)")
    parser.add_argument("--execute", action="store_true",
                        help="实际 DROP+CTAS. 不带此 flag 走 dry-run")
    args = parser.parse_args()

    conn = duckdb.connect(str(SMART_DB), read_only=not args.execute)
    try:
        for table in ("mart_p0b_oos_predictions", "mart_p0b_walkforward_eval"):
            # Pre count
            n_total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            n_corrupt = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE label_version IN {CORRUPT_LABEL_VERSIONS}"
            ).fetchone()[0]
            n_keep = n_total - n_corrupt
            log.info(f"{table}: total={n_total:,} corrupt={n_corrupt:,} keep={n_keep:,}")
            if n_corrupt == 0:
                continue

            if not args.execute:
                continue

            # DROP + CTAS (避免 DuckDB ART index FATAL on large DELETE)
            placeholders = ",".join(f"'{lv}'" for lv in CORRUPT_LABEL_VERSIONS)
            conn.execute(
                f"CREATE OR REPLACE TABLE _tmp_{table} AS "
                f"SELECT * FROM {table} WHERE label_version NOT IN ({placeholders})"
            )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"CREATE TABLE {table} AS SELECT * FROM _tmp_{table}")
            conn.execute(f"DROP TABLE _tmp_{table}")
            n_after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log.info(f"  → after DROP+CTAS: {n_after:,} rows (deleted {n_total - n_after:,})")

        if not args.execute:
            log.info("DRY RUN — no rows deleted. Pass --execute to apply.")
        else:
            log.info("Cleanup verified 无残留 (DROP+CTAS 完成)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
