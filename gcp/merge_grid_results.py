#!/usr/bin/env python3
"""Merge per-job Optuna trial results from VM back to local mart_p1_optuna_trials.

After Wave 1 / Wave 2 grid jobs complete on GCP VM, this script:
1. Reads per-job log files (trials are also persisted via run_p0b_lightgbm_optuna_v4 callback to mart_p1_optuna_trials on VM)
2. Either:
   (a) Run on VM: trials already in /home/.../chunkymonkey/data/smartmoney.duckdb
   (b) After download to local: just merge by run_id

Usage:
    # On VM: gsutil cp data/smartmoney.duckdb gs://...
    # On local: gsutil cp gs://... data/smartmoney_vm.duckdb && merge_grid_results.py
    python gcp/merge_grid_results.py --vm-duckdb data/smartmoney_vm.duckdb
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb

from backend.services.db import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("merge_grid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vm-duckdb", required=True,
                        help="downloaded smartmoney.duckdb from VM containing grid trials")
    parser.add_argument("--run-id-prefix", default="v",
                        help="filter run_ids to merge (default 'v' covers v3_/v4_)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not Path(args.vm_duckdb).exists():
        log.error(f"VM duckdb not found: {args.vm_duckdb}")
        return 1

    # Attach VM duckdb to local
    log.info(f"=== Merge grid trials from {args.vm_duckdb} → {DB_PATH} ===")
    local_con = duckdb.connect(str(DB_PATH))
    try:
        local_con.execute(f"ATTACH '{args.vm_duckdb}' AS vm (READ_ONLY)")

        # List run_ids in VM
        vm_runs = local_con.execute("""
            SELECT run_id, COUNT(*) AS n_trials,
                   MIN(value) AS worst, MAX(value) AS best,
                   MAX(rank_ic_mean) AS best_ic
              FROM vm.mart_p1_optuna_trials
             WHERE run_id LIKE ?
             GROUP BY run_id ORDER BY best DESC
        """, [f"{args.run_id_prefix}%"]).fetchall()

        log.info(f"VM has {len(vm_runs)} run_ids matching '{args.run_id_prefix}*':")
        for r in vm_runs:
            log.info(f"  {r[0]}: {r[1]} trials, best_value={r[3]:.4f}, best_ic={r[4]:.4f}")

        if args.dry_run:
            log.info("--dry-run: not writing")
            return 0

        # Merge trials (INSERT OR REPLACE to handle re-runs)
        inserted = 0
        for run_id_row in vm_runs:
            run_id = run_id_row[0]
            trials = local_con.execute("""
                SELECT * FROM vm.mart_p1_optuna_trials WHERE run_id = ?
            """, [run_id]).fetchall()
            for t in trials:
                try:
                    cols = [c[0] for c in local_con.execute(
                        "DESCRIBE mart_p1_optuna_trials").fetchall()]
                    placeholders = ",".join(["?"] * len(t))
                    col_list = ",".join(cols)
                    local_con.execute(
                        f"INSERT OR REPLACE INTO mart_p1_optuna_trials ({col_list}) "
                        f"VALUES ({placeholders})",
                        list(t)
                    )
                    inserted += 1
                except Exception as e:
                    log.warning(f"insert err for {run_id}: {e}")
        log.info(f"Merged {inserted} trial rows into local mart_p1_optuna_trials")

        # Cross-config comparison
        log.info("\n=== Cross-config gate comparison ===")
        cmp = local_con.execute("""
            SELECT
              CASE
                WHEN run_id LIKE 'v3_all_%' THEN 'v3_all'
                WHEN run_id LIKE 'v4_all_%' THEN 'v4_all'
                WHEN run_id LIKE 'v4_drop_dead_%' THEN 'v4_drop_dead'
                WHEN run_id LIKE 'v4_a158_lhb_mc_%' THEN 'v4_a158_lhb_mc'
                WHEN run_id LIKE 'wave2_%' THEN 'wave2'
                ELSE 'other'
              END AS config,
              COUNT(DISTINCT run_id) AS n_runs,
              COUNT(*) AS n_trials,
              MAX(rank_ic_mean) AS best_ic,
              AVG(rank_ic_mean) AS avg_ic
            FROM mart_p1_optuna_trials
            WHERE rank_ic_mean IS NOT NULL
            GROUP BY config ORDER BY best_ic DESC
        """).fetchall()
        log.info(f"  {'config':25s} | n_runs | n_trials | best_ic | avg_ic | gate")
        for c in cmp:
            gate = "GREEN" if c[3] >= 0.030 else ("YELLOW" if c[3] >= 0.0275 else "RED")
            log.info(f"  {c[0]:25s} | {c[1]:>6} | {c[2]:>8} | {c[3]:.4f} | {c[4]:.4f} | {gate}")

    finally:
        local_con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
