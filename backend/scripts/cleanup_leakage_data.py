#!/usr/bin/env python3
"""Cleanup script for leakage data residue (Codex adc5b44520 PIT 专项 review 后).

清掉 DB 里之前 chain v4/v5 含 leakage 跑出来的中间数据 + 物理 ALTER 已知 leakage cols.

Process gap fix (2026-05-15): 之前 kill 进程 + 修代码后没 verify DB residue.
现加 explicit cleanup step.

用法:
    # dry-run (默认, 只 list 不删)
    PYTHONPATH=backend python backend/scripts/cleanup_leakage_data.py

    # 实操删除
    PYTHONPATH=backend python backend/scripts/cleanup_leakage_data.py --execute
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cleanup_leakage")

SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"

# Codex adc5b44520 leakage cols (训练时 _META_FIELDS exclude, 物理也 drop)
LEAKAGE_COLS_V3_PANEL = [
    "inst_quality_wavg", "inst_quality_max", "inst_total_holding_ratio",
    "inst_holder_cnt", "top_inst_holding_ratio",
    "sector_ret_5d", "sector_ret_20d", "sector_ret_60d",
    "sector_excess_20d", "sector_excess_60d",
]

# Chain v4/v5 含 leakage 跑出的 run_id/model_id (Optuna + train + ablation)
LEAKAGE_RUN_IDS = [
    "p0b_optuna_v3_smoke_20d",
    "p0b_optuna_v3_full_20d",
    "p1_ablation_v1",  # chain v4 P1 ablation (跑了 baseline + drop_alpha158 + drop_risk + drop_financial + drop_events 15/22)
]
LEAKAGE_MODEL_IDS = [
    "lgbm_v3_20d",          # chain v4 用 102 features 含 leakage
    "lambdamart_v3_20d",
]


def _count_by_key(conn, table: str, key_column: str, values: list[str]) -> dict[str, int]:
    if not values:
        return {}
    placeholders = ", ".join("?" for _ in values)
    try:
        rows = conn.execute(
            f"""
            SELECT {key_column} AS key_value, COUNT(*) AS n
              FROM {table}
             WHERE {key_column} IN ({placeholders})
             GROUP BY {key_column}
            """,
            values,
        ).fetchall()
    except Exception as exc:
        log.debug(f"  {table}.{key_column} missing (expected for fresh DB): {exc}")
        return {}
    return {str(row[0]): int(row[1] or 0) for row in rows}


def _delete_by_key(conn, table: str, key_column: str, values: list[str]) -> None:
    if not values:
        return
    placeholders = ", ".join("?" for _ in values)
    conn.execute(f"DELETE FROM {table} WHERE {key_column} IN ({placeholders})", values)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup leakage data residue")
    parser.add_argument("--execute", action="store_true", help="实际执行 (默认 dry-run)")
    parser.add_argument("--keep-panel-cols", action="store_true",
                        help="只清 trial/predictions, 不 ALTER panel drop cols (现状保留)")
    args = parser.parse_args()

    log.info(f"=== Leakage data cleanup ({'EXECUTE' if args.execute else 'DRY-RUN'}) ===")
    log.info(f"  keep_panel_cols: {args.keep_panel_cols}")

    conn = duckdb.connect(str(SMART_DB))
    try:
        # 1. mart_p1_optuna_trials 残留
        optuna_counts = _count_by_key(conn, "mart_p1_optuna_trials", "run_id", LEAKAGE_RUN_IDS)
        for rid in LEAKAGE_RUN_IDS:
            n = optuna_counts.get(rid, 0)
            log.info(f"  mart_p1_optuna_trials run_id='{rid}': {n} rows")
        if args.execute and any(optuna_counts.values()):
            _delete_by_key(conn, "mart_p1_optuna_trials", "run_id", LEAKAGE_RUN_IDS)
            log.info("    DELETED mart_p1_optuna_trials leakage rows")

        # 2. mart_p0b_oos_predictions 残留
        oos_counts = _count_by_key(conn, "mart_p0b_oos_predictions", "model_id", LEAKAGE_MODEL_IDS)
        for mid in LEAKAGE_MODEL_IDS:
            n = oos_counts.get(mid, 0)
            log.info(f"  mart_p0b_oos_predictions model_id='{mid}': {n} rows")
        if args.execute and any(oos_counts.values()):
            _delete_by_key(conn, "mart_p0b_oos_predictions", "model_id", LEAKAGE_MODEL_IDS)
            log.info("    DELETED mart_p0b_oos_predictions leakage rows")

        # 3. mart_p0b_walkforward_eval 残留
        walkforward_run_ids = LEAKAGE_RUN_IDS + ["p0b_lgbm_v3_20d", "p0b_lambdamart_v3_20d"]
        walkforward_counts = _count_by_key(conn, "mart_p0b_walkforward_eval", "run_id", walkforward_run_ids)
        for rid in walkforward_run_ids:
            n = walkforward_counts.get(rid, 0)
            log.info(f"  mart_p0b_walkforward_eval run_id='{rid}': {n} rows")
        if args.execute and any(walkforward_counts.values()):
            _delete_by_key(conn, "mart_p0b_walkforward_eval", "run_id", walkforward_run_ids)
            log.info("    DELETED mart_p0b_walkforward_eval leakage rows")

        # 4. mart_p1_ablation_result
        ablation_counts = _count_by_key(conn, "mart_p1_ablation_result", "run_id", LEAKAGE_RUN_IDS)
        for rid in LEAKAGE_RUN_IDS:
            n = ablation_counts.get(rid, 0)
            log.info(f"  mart_p1_ablation_result run_id='{rid}': {n} rows")
        if args.execute and any(ablation_counts.values()):
            _delete_by_key(conn, "mart_p1_ablation_result", "run_id", LEAKAGE_RUN_IDS)
            log.info("    DELETED mart_p1_ablation_result leakage rows")

        # 5. mart_p0a_feature_label_panel_v3 物理 leakage cols
        if not args.keep_panel_cols:
            log.info("")
            log.info("=== panel ALTER TABLE DROP COLUMN (leakage cols) ===")
            try:
                cols_exist = {r[0] for r in conn.execute("DESCRIBE mart_p0a_feature_label_panel_v3").fetchall()}
                for col in LEAKAGE_COLS_V3_PANEL:
                    if col in cols_exist:
                        log.info(f"  ALTER TABLE mart_p0a_feature_label_panel_v3 DROP COLUMN {col}")
                    else:
                        log.info(f"  {col}: already absent")
                if args.execute:
                    drop_sql = "\n".join(
                        f"ALTER TABLE mart_p0a_feature_label_panel_v3 DROP COLUMN IF EXISTS {col};"
                        for col in LEAKAGE_COLS_V3_PANEL
                    )
                    conn.execute(drop_sql)
                    log.info("    DROPPED leakage panel columns")
            except Exception as e:
                log.error(f"ALTER failed: {e}")
                log.error("  Fallback: 重 build panel via build_p0a_feature_panel_v3.py (改 SQL 删 CTE)")

        log.info("")
        log.info("=== Cleanup summary ===")
        if not args.execute:
            log.info("DRY-RUN — 加 --execute 实际执行")
        else:
            log.info("DONE")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
