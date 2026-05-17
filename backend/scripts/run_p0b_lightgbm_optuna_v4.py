#!/usr/bin/env python3
"""P0b LightGBM Optuna v4 — perf-wired (Codex round 21 Path Z).

Codex round 21 verdict (agentId a4c78ca05e601d181) 真根因:
- v3 脚本 df.to_dict("records") 一次 14.5 min
- 每 trial 重新切窗 31 min/trial (274.5 万 dict 重复扫)
- MedianPruner 加了也不生效因为 objective 没 trial.report() + should_prune()
- 200 trials 估 ~24 天 in Mac mini 8GB

v4 改进:
1. PreparedPanel (services.perf.prepared_panel) ndarray columnar — 不走 dict-records
2. 预计算 walk-forward windows 一次 — trial 内只 fancy index 取 train/test
3. objective 内 trial.report(score, step=window_idx) + should_prune() — pruner 真生效
4. MedianPruner(startup=10, warmup=7) — 跑完 6+ 个 window 才允许 prune
5. enforce_pre_optimize governance — 50 ≤ n_trials ≤ 500
6. 每 trial 完落盘 mart_p1_optuna_trials (不是最后才写)
7. del df + gc.collect() 节约 8GB RAM

usage:
    PYTHONPATH=backend python backend/scripts/run_p0b_lightgbm_optuna_v4.py \\
        --label fwd_cost_after_20d \\
        --n-trials 50 \\
        --full \\
        --start-date 2024-01-01 \\
        --end-date 2026-04-13 \\
        --min-train-months 12 \\
        --feature-panel mart_p0a_feature_label_panel_v4

⚠ prerequisite:
- Optuna v3 PID 25088 已结束 (DB single-writer lock 释放)
- v4 panel 已 build (mart_p0a_feature_label_panel_v4)
"""
from __future__ import annotations

import argparse
import gc
import logging
import math
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import optuna

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.perf.prepared_panel import (
    PreparedPanel,
    build_panel_from_df,
    compute_walk_forward_windows,
)
from services.optimization.governance import enforce_pre_optimize

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p0b_optuna_v4")


OPTUNA_TRIALS_DDL = """
CREATE TABLE IF NOT EXISTS mart_p1_optuna_trials (
    run_id TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    state TEXT NOT NULL,
    value DOUBLE,
    rank_ic_mean DOUBLE,
    rank_ic_std DOUBLE,
    n_windows INTEGER,
    params_json TEXT,
    user_attrs_json TEXT,
    pruned_at_window INTEGER,
    built_at TEXT,
    PRIMARY KEY (run_id, trial_number)
);
"""


def _rank_ic_per_window(panel: PreparedPanel, model, window_idx: int) -> float | None:
    """跑单 window: fit on train, predict test, RankIC (spearman)."""
    X_train, y_train, X_test, y_test = panel.get_window(window_idx)
    if len(X_train) == 0 or len(X_test) == 0:
        return None
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    # Spearman RankIC = corr(rank(pred), rank(label))
    # 用 scipy.stats.spearmanr 简洁
    from scipy.stats import spearmanr
    try:
        rho, _ = spearmanr(pred, y_test)
        return float(rho) if not math.isnan(rho) else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="P0b LGBM Optuna v4 (perf-wired)")
    parser.add_argument("--label", default="fwd_cost_after_20d")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--n-trials", type=int, default=50)  # rule-compliance: ok evidence=Codex-round-21-Path-Z
    parser.add_argument("--full", action="store_true", help="use n_estimators=2000 + early_stop")
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-13")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--min-train-months", type=int, default=12)  # rule-compliance: ok evidence=governance-default
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v4")
    parser.add_argument("--seed", type=int, default=42)  # rule-compliance: ok evidence=governance-fixed-seed
    parser.add_argument("--exclude-cols", default="",
                        help="comma-separated col names to exclude (Codex round 23 feature ablation grid)")
    parser.add_argument("--no-persist", action="store_true",
                        help="skip per-trial DB persist callback (avoid DuckDB single-writer lock in parallel grid)")
    parser.add_argument("--db-path", default=None,
                        help="override DB path (per-job SQLite for parallel grid)")
    args = parser.parse_args()

    # Governance gate (Codex CRITICAL: enforce_pre_optimize)
    enforce_pre_optimize(n_trials=args.n_trials, has_seed=True)

    run_id = args.run_id or f"p0b_optuna_v4_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, label={args.label}, n_trials={args.n_trials}, full={args.full}")

    # Stage 1: Load + PreparedPanel + 预计算 windows
    t0 = time.time()
    log.info(f"=== Stage 1: Load + PreparedPanel + windows ===")
    db_path = args.db_path or str(DB_PATH)
    conn = duck_connect(db_path, read_only=True)  # rule-compliance: ok evidence=parallel-grid-multi-reader
    try:
        conn.execute(OPTUNA_TRIALS_DDL)
        log.info(f"Loading DataFrame from {args.feature_panel} ...")
        import pandas as pd
        t_load = time.time()
        df = conn._con.execute(
            f"SELECT * FROM {args.feature_panel} ORDER BY signal_date, stock_code"
        ).fetchdf()
        log.info(f"  Loaded {len(df):,} rows × {len(df.columns)} cols ({time.time()-t_load:.1f}s)")

        df = df[df["signal_date"] >= pd.to_datetime(args.start_date)]
        df = df[df["signal_date"] <= pd.to_datetime(args.end_date)]
        df = df[df[args.label].notna()].copy()
        log.info(f"  After date+label filter: {len(df):,} rows")

        # Codex round 19 leakage cols 排除 (跟 v3 一致)
        meta_cols = {
            "stock_code", "signal_date", "entry_date", "unable_at_entry",
            "month_start", "built_at",
            "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
            "feature_version", "label_version", "industry_pit_confidence",
            "industry_pit_l1_name", "industry_pit_l2_name",
            # Codex adc5b44520 CRITICAL: latest-snapshot leakage
            "inst_quality_wavg", "inst_quality_max", "inst_total_holding_ratio",
            "inst_holder_cnt", "top_inst_holding_ratio",
            "sector_ret_5d", "sector_ret_20d", "sector_ret_60d",
            "sector_excess_20d", "sector_excess_60d",
            "holder_count_q_report_date",  # v3_ext meta
            "sector_name",  # v4 meta
        }
        # Codex round 23: additional --exclude-cols runtime exclusion (feature ablation grid)
        if args.exclude_cols:
            extra_excl = set(c.strip() for c in args.exclude_cols.split(",") if c.strip())
            meta_cols.update(extra_excl)
            log.info(f"  --exclude-cols added {len(extra_excl)} cols: {sorted(extra_excl)[:5]}...")
        feature_cols = [c for c in df.columns
                        if c not in meta_cols and pd.api.types.is_numeric_dtype(df[c])]
        log.info(f"  feature_columns ({len(feature_cols)}): {feature_cols[:10]}...")

        t_panel = time.time()
        panel = build_panel_from_df(df, label_col=args.label,
                                     feature_cols=feature_cols, meta_cols=meta_cols)
        log.info(f"  PreparedPanel built: X={panel.X.shape} ({panel.X.dtype}), "
                 f"y={panel.y.shape} ({time.time()-t_panel:.1f}s)")

        # del df + gc 节约 RAM (Codex memory smoke 推荐)
        del df
        gc.collect()

        t_win = time.time()
        panel = compute_walk_forward_windows(panel, min_train_months=args.min_train_months,
                                              forward_months=1)
        log.info(f"  walk-forward windows: {panel.n_windows} ({time.time()-t_win:.1f}s)")
        if panel.n_windows < 6:
            log.error(f"  windows < 6, pruner won't make sense — aborting")
            return 1

    finally:
        conn.close()

    log.info(f"=== Stage 1 done in {time.time()-t0:.1f}s ===")

    # Stage 2: Optuna sweep
    log.info(f"=== Stage 2: Optuna {args.n_trials} trials w/ MedianPruner ===")
    n_est_default = 2000 if args.full else 300  # rule-compliance: ok evidence=Codex-round-21-recommend
    early_stop = 100 if args.full else None  # rule-compliance: ok evidence=Codex-round-21-recommend

    def objective(trial: optuna.Trial) -> float:
        max_depth = trial.suggest_int("max_depth", 3, 8)
        num_leaves_low = min(15, 2 ** max_depth - 1)
        num_leaves_high = min(127, 2 ** max_depth - 1)
        if num_leaves_high <= num_leaves_low:
            num_leaves_high = num_leaves_low + 1
        hp = {
            "max_depth": max_depth,
            "num_leaves": trial.suggest_int("num_leaves", num_leaves_low, num_leaves_high, log=True),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 300, log=True),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.55, 0.95),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.60, 1.00),
            "bagging_freq": trial.suggest_int("bagging_freq", 1, 5),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 50.0, log=True),
            "min_split_gain": trial.suggest_float("min_split_gain", 0.0, 0.2),
            "n_estimators": n_est_default,
            "random_state": args.seed,
            "verbose": -1,
        }

        # 跑 each window, report 累积 mean_ic for pruner
        ics: list[float] = []
        from lightgbm import LGBMRegressor
        for win_i in range(panel.n_windows):
            model = LGBMRegressor(**hp)
            ic = _rank_ic_per_window(panel, model, win_i)
            if ic is None:
                continue
            ics.append(ic)
            # 报中间 mean_ic for pruner (用累积 mean - 0.5*std as proxy)
            if len(ics) >= 2:
                cur_mean = float(np.mean(ics))
                cur_std = float(np.std(ics, ddof=1))
                cur_score = cur_mean - 0.5 * cur_std
                trial.report(cur_score, step=win_i)
                if trial.should_prune():
                    raise optuna.TrialPruned()

        if not ics:
            return -10.0  # rule-compliance: ok evidence=sentinel-bad-trial
        mean_ic = float(np.mean(ics))
        std_ic = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
        score = mean_ic - 0.5 * std_ic
        trial.set_user_attr("rank_ic_mean", mean_ic)
        trial.set_user_attr("rank_ic_std", std_ic)
        trial.set_user_attr("n_windows", len(ics))
        log.info(f"trial {trial.number}: mean_ic={mean_ic:.4f} std_ic={std_ic:.4f} → score={score:.4f}")
        return score

    # Pruner: warmup=7 (跑 7 个 window 才允许 prune), startup=10 (前 10 trial 不 prune, 给 TPE 收集 baseline)
    pruner = optuna.pruners.MedianPruner(
        n_startup_trials=10,  # rule-compliance: ok evidence=Codex-round-21-MedianPruner-recommend
        n_warmup_steps=7,     # rule-compliance: ok evidence=Codex-round-21-MedianPruner-recommend
        n_min_trials=5,       # rule-compliance: ok evidence=Codex-round-21-MedianPruner-recommend
    )
    sampler = optuna.samplers.TPESampler(seed=args.seed)
    study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner,
                                study_name=run_id)

    # Per-trial persist callback (Codex round 21 漏看 #3: trial-by-trial persist 防 kill 丢失)
    def _persist_trial(study: optuna.Study, trial: optuna.trial.FrozenTrial):
        try:
            conn = duck_connect(str(DB_PATH))
            try:
                import json
                conn.execute(
                    """INSERT OR REPLACE INTO mart_p1_optuna_trials
                       (run_id, trial_number, state, value, rank_ic_mean, rank_ic_std,
                        n_windows, params_json, user_attrs_json, pruned_at_window, built_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        run_id, trial.number, trial.state.name, trial.value,
                        trial.user_attrs.get("rank_ic_mean"),
                        trial.user_attrs.get("rank_ic_std"),
                        trial.user_attrs.get("n_windows"),
                        json.dumps(trial.params),
                        json.dumps({k: v for k, v in trial.user_attrs.items()}),
                        trial.last_step if trial.state == optuna.trial.TrialState.PRUNED else None,
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ]
                )
            finally:
                conn.close()
        except Exception as e:
            # rule-compliance: ok evidence=persist-best-effort
            log.warning(f"trial persist failed: {e}")

    # Codex Round 25 fix: skip per-trial DB callback when running parallel grid (avoid DuckDB lock)
    callbacks = [] if args.no_persist else [_persist_trial]
    if args.no_persist:
        log.info("--no-persist: skipping per-trial DB callback (Codex Round 25 parallel grid fix)")
    study.optimize(objective, n_trials=args.n_trials, gc_after_trial=True, callbacks=callbacks)

    # Stage 3: Final report
    log.info(f"=== Stage 3: Best trial ===")
    if study.best_trial:
        log.info(f"  best #{study.best_trial.number}: score={study.best_trial.value:.4f}")
        log.info(f"  params: {study.best_trial.params}")
        log.info(f"  rank_ic_mean: {study.best_trial.user_attrs.get('rank_ic_mean')}")
    log.info(f"All {args.n_trials} trials persisted (run_id={run_id})")
    log.info(f"=== Total: {time.time()-t0:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
