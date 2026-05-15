#!/usr/bin/env python3
"""P0b LightGBM Optuna walk-forward (Codex 7-day plan Day 4) — v3 panel.

读 mart_p0a_feature_label_panel_v3 → Optuna 搜 Codex Q4 search space
→ best params 重训 → 入 mart_p0b_oos_predictions + walkforward_eval + mart_p1_optuna_trials

Codex Q4 search space:
- max_depth 3-8
- num_leaves: 15-min(127, 2^max_depth-1) log
- learning_rate: 0.01-0.08 log
- n_estimators: 默认 300 (smoke) / 2000 + early_stop=100 (正式 --full)
- min_child_samples: 20-300 log
- feature_fraction: 0.55-0.95
- bagging_fraction: 0.60-1.00
- bagging_freq: 1-5
- reg_alpha (lambda_l1): 1e-8 - 10.0 log
- reg_lambda (lambda_l2): 1e-8 - 50.0 log
- min_split_gain (min_gain_to_split): 0.0 - 0.2

Objective (Codex 推荐): mean(daily_rank_ic per window) - 0.5 * std(per window)
- 惩罚窗口间不稳定 (高 IC 但波动大也不好)

用法:
    # smoke (默认 50 trials, n_est=300, min_train_months=12)
    PYTHONPATH=backend python backend/scripts/run_p0b_lightgbm_optuna_v3.py \
        --label fwd_cost_after_20d --run-id p0b_optuna_smoke_20d

    # 正式 (200 trials, n_est=2000 + early_stop=100)
    PYTHONPATH=backend python backend/scripts/run_p0b_lightgbm_optuna_v3.py \
        --label fwd_cost_after_20d --run-id p0b_optuna_200_20d \
        --n-trials 200 --full
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p0b_optuna")


OPTUNA_TRIALS_DDL = """
CREATE TABLE IF NOT EXISTS mart_p1_optuna_trials (
    run_id TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    state TEXT,
    value DOUBLE,
    rank_ic_mean DOUBLE,
    rank_ic_std DOUBLE,
    n_windows INTEGER,
    params_json TEXT,
    duration_s DOUBLE,
    built_at TEXT,
    PRIMARY KEY (run_id, trial_number)
);
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="P0b LightGBM Optuna walk-forward v3")
    parser.add_argument("--label", default="fwd_cost_after_20d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--n-trials", type=int, default=50, help="Optuna trial 数 (50 smoke, 200 正式)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-30")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--min-train-months", type=int, default=12,
                        help="smoke 12 跳前面少样本窗 (默认 6) — full 也建议 12")
    parser.add_argument("--full", action="store_true",
                        help="正式模式: n_estimators=2000 + early_stop=100 (vs smoke n_est=300 no early_stop)")
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v3",
                        help="读哪张 panel (v1 / v2 / v3 默认 v3)")
    args = parser.parse_args()

    try:
        import optuna  # lazy
    except ImportError:
        log.error("optuna not installed: pip install optuna")
        return 1

    from services.ml_ranking.lightgbm_walkforward import (
        LightGBMWalkForwardConfig,
        train_lightgbm_walkforward,
    )

    run_id = args.run_id or f"p0b_optuna_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, label={args.label}, n_trials={args.n_trials}, full={args.full}")

    # Load panel
    conn = duck_connect(str(DB_PATH))
    try:
        conn.execute(OPTUNA_TRIALS_DDL)
        log.info(f"Loading DataFrame from {args.feature_panel} ...")
        import pandas as pd
        df = conn._con.execute(
            f"SELECT * FROM {args.feature_panel} ORDER BY signal_date, stock_code"
        ).fetchdf()
        log.info(f"Loaded {len(df):,} rows × {len(df.columns)} cols")

        df = df[df["signal_date"] >= pd.to_datetime(args.start_date)]
        df = df[df["signal_date"] <= pd.to_datetime(args.end_date)]
        df = df[df[args.label].notna()].copy()
        log.info(f"After date+label filter: {len(df):,} rows")

        # numeric coercion + Codex adc5b44520 leakage cols 排除
        non_feature = {
            "stock_code", "signal_date", "entry_date", "unable_at_entry",
            "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
            "feature_version", "built_at", "industry_pit_confidence",
            # Codex adc5b44520 CRITICAL: latest-snapshot institution_profile leakage
            "inst_quality_wavg", "inst_quality_max", "inst_total_holding_ratio",
            "inst_holder_cnt", "top_inst_holding_ratio",
            # Codex adc5b44520 MAJOR: 99.978% current_label_fallback contamination
            "sector_ret_5d", "sector_ret_20d", "sector_ret_60d",
            "sector_excess_20d", "sector_excess_60d",
        }
        feature_columns = [c for c in df.columns if c not in non_feature
                           and pd.api.types.is_numeric_dtype(df[c])]
        log.info(f"feature_columns ({len(feature_columns)}): {feature_columns[:10]}...")

        rows = df.to_dict("records")
        log.info(f"Converted to {len(rows):,} dicts (will retain in-memory during Optuna)")

    finally:
        conn.close()

    # Optuna search
    n_est_default = 2000 if args.full else 300
    early_stop = 100 if args.full else None

    def objective(trial: "optuna.Trial") -> float:
        max_depth = trial.suggest_int("max_depth", 3, 8)
        # Codex M2 (a163ca58): max(15, 2^max_depth-1) bug — max_depth=3 时 2^3-1=7,
        # max(15,7)=15 让 num_leaves search up to 15 >> 树 max 7. 改 min:
        num_leaves_low = min(15, 2 ** max_depth - 1)
        num_leaves_high = min(127, 2 ** max_depth - 1)
        if num_leaves_high <= num_leaves_low:
            num_leaves_high = num_leaves_low + 1
        cfg = LightGBMWalkForwardConfig(
            label_field=args.label,
            min_train_months=args.min_train_months,
            forward_months=1,
            n_estimators=n_est_default,
            early_stopping_rounds=early_stop,
            max_depth=max_depth,
            num_leaves=trial.suggest_int("num_leaves", num_leaves_low, num_leaves_high, log=True),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            min_child_samples=trial.suggest_int("min_child_samples", 20, 300, log=True),
            feature_fraction=trial.suggest_float("feature_fraction", 0.55, 0.95),
            bagging_fraction=trial.suggest_float("bagging_fraction", 0.60, 1.00),
            bagging_freq=trial.suggest_int("bagging_freq", 1, 5),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            reg_lambda=trial.suggest_float("reg_lambda", 1e-8, 50.0, log=True),
            min_split_gain=trial.suggest_float("min_split_gain", 0.0, 0.2),
            feature_columns=feature_columns,
            random_state=args.seed,
        )
        result = train_lightgbm_walkforward(rows, cfg)
        if not result.windows:
            return -10.0
        ics = [w.rank_ic for w in result.windows if not math.isnan(w.rank_ic)]
        if not ics:
            return -10.0
        mean_ic = float(np.mean(ics))
        # Codex M3 (a163ca58): sample std (ddof=1) not population std (ddof=0)
        std_ic = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
        score = mean_ic - 0.5 * std_ic
        trial.set_user_attr("rank_ic_mean", mean_ic)
        trial.set_user_attr("rank_ic_std", std_ic)
        trial.set_user_attr("n_windows", len(ics))
        log.info(f"trial {trial.number}: mean_ic={mean_ic:.4f} std_ic={std_ic:.4f} → score={score:.4f}")
        return score

    sampler = optuna.samplers.TPESampler(seed=args.seed)
    study = optuna.create_study(direction="maximize", sampler=sampler,
                                study_name=run_id)
    study.optimize(objective, n_trials=args.n_trials, gc_after_trial=True)

    # Persist trials
    conn = duck_connect(str(DB_PATH))
    try:
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        for t in study.trials:
            conn.execute(
                """INSERT OR REPLACE INTO mart_p1_optuna_trials
                   (run_id, trial_number, state, value, rank_ic_mean, rank_ic_std,
                    n_windows, params_json, duration_s, built_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    run_id, t.number, t.state.name,
                    t.value if t.value is not None else None,
                    t.user_attrs.get("rank_ic_mean"),
                    t.user_attrs.get("rank_ic_std"),
                    t.user_attrs.get("n_windows"),
                    json.dumps(t.params, ensure_ascii=False),
                    t.duration.total_seconds() if t.duration else None,
                    built_at,
                ],
            )
    finally:
        conn.close()

    log.info("")
    log.info("=== Optuna Best Trial ===")
    log.info(f"  trial #{study.best_trial.number}")
    log.info(f"  score: {study.best_value:.4f}")
    log.info(f"  rank_ic_mean: {study.best_trial.user_attrs.get('rank_ic_mean'):.4f}")
    log.info(f"  rank_ic_std: {study.best_trial.user_attrs.get('rank_ic_std'):.4f}")
    log.info(f"  params: {study.best_trial.params}")
    log.info(f"All {args.n_trials} trials saved to mart_p1_optuna_trials (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
