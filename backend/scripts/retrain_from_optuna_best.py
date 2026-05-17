#!/usr/bin/env python3
"""Retrain LGBM with best Optuna trial params.

Reads mart_p0a_optuna_trials, picks highest rank_ic_mean, builds CLI command
for train_p0b_lightgbm.py.

Usage:
    PYTHONPATH=backend python backend/scripts/retrain_from_optuna_best.py
    PYTHONPATH=backend python backend/scripts/retrain_from_optuna_best.py --run-id-prefix p0b_optuna_v4_
    PYTHONPATH=backend python backend/scripts/retrain_from_optuna_best.py --execute

If --execute, runs the retrain command directly. Otherwise just prints it.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from services.db import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("retrain_optuna_best")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id-prefix", default="p0b_optuna_v4_")
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v4")
    parser.add_argument("--label", default="fwd_cost_after_20d")
    parser.add_argument("--model-id", default=None,
                        help="default 'lgbm_v4_<best_trial_run_id_suffix>'")
    parser.add_argument("--min-train-months", type=int, default=12)  # rule-compliance: ok evidence=Optuna-v4-baseline
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-13")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--execute", action="store_true",
                        help="run command directly; default just print")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        r = con.execute(
            "SELECT run_id, trial_number, value, rank_ic_mean, rank_ic_std, params_json "
            "FROM mart_p1_optuna_trials "
            "WHERE run_id LIKE ? AND state='COMPLETE' "
            "ORDER BY value DESC LIMIT 1",
            [f"{args.run_id_prefix}%"]
        ).fetchone()
    finally:
        con.close()

    if not r:
        log.error(f"No COMPLETE trials found for prefix {args.run_id_prefix}")
        return 1
    run_id, trial_n, value, ic_mean, ic_std, params_json = r
    log.info(f"Best trial: {run_id} trial={trial_n}")
    log.info(f"  value={value:.4f}, rank_ic={ic_mean:.4f} ± {ic_std:.4f}")
    params = json.loads(params_json) if params_json else {}
    log.info(f"  params: {params}")

    # Construct retrain command
    model_id = args.model_id or f"lgbm_v4_optbest_{run_id.split('_')[-1]}"
    cmd = [
        "python", "backend/scripts/train_p0b_lightgbm.py",
        "--feature-panel", args.feature_panel,
        "--label", args.label,
        "--model-id", model_id,
        "--min-train-months", str(args.min_train_months),
        "--start-date", args.start_date,
        "--end-date", args.end_date,
        "--feature-version", "p0a_v4",
    ]
    # Inject hyperparams from best trial
    if "learning_rate" in params:
        cmd += ["--learning-rate", str(params["learning_rate"])]
    if "num_leaves" in params:
        cmd += ["--num-leaves", str(params["num_leaves"])]
    n_est = 2000 if "max_depth" in params else 200  # full mode if max_depth tuned
    cmd += ["--n-estimators", str(n_est)]

    log.info("Retrain command:")
    log.info(f"  {' '.join(cmd)}")

    if args.execute:
        log.info("Executing ...")
        result = subprocess.run(cmd, env={"PYTHONPATH": "backend", **__import__("os").environ},
                                cwd=Path(__file__).resolve().parents[2])
        return result.returncode
    else:
        log.info("--execute not specified, dry-run only. Add --execute to run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
