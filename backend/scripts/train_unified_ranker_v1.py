#!/usr/bin/env python3
"""Phase 4.2 MVP: train unified LightGBM ranker on panel_unified_v1.

Codex review agent a8d412b0 verdict applied:
- Train window shifted to 2024-11-01 onwards (perception coverage starts then; pre-2024-11 perception all NULL = no training signal)
- Use v7 best_params as smoke baseline (Phase 4.2b: real Optuna re-search ~50 trials, deferred to GCP)
- Keep all rows including NULL-heavy (LightGBM learns default direction natively)
- Output OOS RankIC + NDCG + spreads for honest comparison vs v7

Inputs:
- mart_p0a_feature_label_panel_unified_v1 (166 cols, 2.7M rows)
- data/reports/optuna/lgbm_phase5_v7_20260523T010000Z.best.json (smoke baseline params)

Outputs:
- data/reports/optuna/unified_ranker_v1_<timestamp>.lgb.txt (booster artifact)
- data/reports/optuna/unified_ranker_v1_<timestamp>.feature_cols.json
- data/reports/optuna/unified_ranker_v1_<timestamp>.oos_metrics.json
- mart_unified_v1_oos_predictions (OOS scoring for Phase 4 gate)

Usage:
  PYTHONPATH=backend python backend/scripts/train_unified_ranker_v1.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

PANEL = "mart_p0a_feature_label_panel_unified_v1"
V7_BEST_JSON = REPO_ROOT / "data" / "reports" / "optuna" / "lgbm_phase5_v7_20260523T010000Z.best.json"
# rule-compliance: ok evidence=Codex a8d412b0 verdict shifted train to 2024-11+ (perception coverage starts then)
TRAIN_START = "2024-11-01"  # rule-compliance: ok evidence=perception mart coverage starts 2024-11-01
TRAIN_END = "2025-06-30"    # rule-compliance: ok evidence=train/oos split 8 months train + 10 months oos
OOS_START = "2025-07-01"    # rule-compliance: ok evidence=train_end + 1 day
OOS_END = "2026-04-30"      # rule-compliance: ok evidence=panel v5 max signal_date 2026-04-30
LABEL = "fwd_cost_after_20d"
EXCLUDE = {"stock_code", "signal_date", "entry_date", "unable_at_entry",
           "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
           "feature_version", "built_at"}


def _rank_ic(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute Spearman rank correlation."""
    valid = ~np.isnan(y_true) & ~np.isnan(y_pred)
    if valid.sum() < 10:
        return float("nan")
    rho, _ = spearmanr(y_true[valid], y_pred[valid])
    return float(rho)


def _ndcg_at_k(y_true: np.ndarray, y_pred: np.ndarray, k: int) -> float:
    """Simple top-K NDCG approximation: gain at top-K predicted."""
    if len(y_true) < k:
        return float("nan")
    order = np.argsort(-y_pred)[:k]
    gains = y_true[order]
    ideal = np.sort(y_true)[::-1][:k]
    dcg = np.sum(gains / np.log2(np.arange(2, k + 2)))
    idcg = np.sum(ideal / np.log2(np.arange(2, k + 2)))
    return float(dcg / idcg) if idcg > 0 else float("nan")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--top-k", type=int, default=10)
    args = p.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # Phase ψ.5 allowlist: 产物文件名时间戳非 trade_date
    model_id = f"unified_ranker_v1_{ts}"
    booster_path = REPO_ROOT / "data" / "reports" / "optuna" / f"{model_id}.lgb.txt"
    feat_path = REPO_ROOT / "data" / "reports" / "optuna" / f"{model_id}.feature_cols.json"
    metrics_path = REPO_ROOT / "data" / "reports" / "optuna" / f"{model_id}.oos_metrics.json"

    best = json.load(V7_BEST_JSON.open())
    best_params = best.get("best_params") or {}
    print(f"Smoke baseline params from {V7_BEST_JSON.name}: trial {best.get('best_trial_number')}")

    with connect(args.db_path, read_only=False) as conn:
        cols = [c[0] for c in conn.execute(f"SELECT * FROM {PANEL} LIMIT 0").description]
        feature_cols = [c for c in cols if c not in EXCLUDE]
        print(f"Total cols: {len(cols)}, feature_cols: {len(feature_cols)}")

        print(f"Loading train [{TRAIN_START} ~ {TRAIN_END}]...")
        train_df = pd.DataFrame(
            conn.execute(f"SELECT * FROM {PANEL} WHERE signal_date >= ? AND signal_date <= ?",
                         [TRAIN_START, TRAIN_END]).fetchall(),
            columns=cols,
        )
        print(f"Train rows: {len(train_df):,}")

        print(f"Loading OOS [{OOS_START} ~ {OOS_END}]...")
        oos_df = pd.DataFrame(
            conn.execute(f"SELECT * FROM {PANEL} WHERE signal_date >= ? AND signal_date <= ?",
                         [OOS_START, OOS_END]).fetchall(),
            columns=cols,
        )
        print(f"OOS rows: {len(oos_df):,}")

        if train_df.empty or oos_df.empty:
            print("ERROR: empty train or OOS")
            return 1

        # Numeric features only (LightGBM doesn't take strings)
        feature_cols = [c for c in feature_cols if pd.api.types.is_numeric_dtype(train_df[c])]
        print(f"Numeric features (final): {len(feature_cols)}")

        X_train = train_df[feature_cols].values
        y_train = pd.to_numeric(train_df[LABEL], errors="coerce").fillna(0).values
        bins = np.quantile(y_train, [0.2, 0.4, 0.6, 0.8])
        y_rel = np.digitize(y_train, bins)
        train_sorted = train_df.sort_values("signal_date").reset_index(drop=True)
        group_sizes = train_sorted.groupby("signal_date").size().values

        print("Fitting unified booster...")
        booster = lgb.LGBMRanker(
            objective="lambdarank",
            metric="ndcg",
            ndcg_eval_at=[5, 10, 20],
            label_gain=[0, 1, 3, 7, 15],
            n_estimators=best_params.get("n_estimators", 100),
            **{k: v for k, v in best_params.items() if k != "n_estimators"},
        )
        booster.fit(X_train, y_rel, group=group_sizes)
        booster.booster_.save_model(str(booster_path))
        json.dump(feature_cols, feat_path.open("w"))
        print(f"Booster saved: {booster_path.name}")

        # OOS inference + metrics
        print("Computing OOS metrics...")
        X_oos = oos_df[feature_cols].values
        y_oos_true = pd.to_numeric(oos_df[LABEL], errors="coerce").values
        y_oos_pred = booster.predict(X_oos)

        # Per-date RankIC
        per_date_ic = []
        for d, g in oos_df.assign(_pred=y_oos_pred).groupby("signal_date"):
            yt = pd.to_numeric(g[LABEL], errors="coerce").values
            yp = g["_pred"].values
            ic = _rank_ic(yt, yp)
            if not np.isnan(ic):
                per_date_ic.append(ic)
        rank_ic_mean = float(np.mean(per_date_ic)) if per_date_ic else float("nan")
        rank_ic_std = float(np.std(per_date_ic)) if per_date_ic else float("nan")

        ndcg5 = _ndcg_at_k(y_oos_true, y_oos_pred, 5)
        ndcg10 = _ndcg_at_k(y_oos_true, y_oos_pred, 10)
        ndcg20 = _ndcg_at_k(y_oos_true, y_oos_pred, 20)

        # Top-K spread
        order = np.argsort(-y_oos_pred)
        top5_mean = float(np.nanmean(y_oos_true[order[:5]])) if len(order) >= 5 else float("nan")
        top10_mean = float(np.nanmean(y_oos_true[order[:10]])) if len(order) >= 10 else float("nan")
        avg_label = float(np.nanmean(y_oos_true))
        top5_spread = top5_mean - avg_label
        top10_spread = top10_mean - avg_label

        metrics = {
            "model_id": model_id,
            "panel": PANEL,
            "train_start": TRAIN_START,
            "train_end": TRAIN_END,
            "oos_start": OOS_START,
            "oos_end": OOS_END,
            "n_train_rows": int(len(train_df)),
            "n_oos_rows": int(len(oos_df)),
            "n_features": int(len(feature_cols)),
            "rank_ic_mean": rank_ic_mean,
            "rank_ic_std": rank_ic_std,
            "rank_ic_n_days": len(per_date_ic),
            "ndcg5": ndcg5,
            "ndcg10": ndcg10,
            "ndcg20": ndcg20,
            "top5_spread": top5_spread,
            "top10_spread": top10_spread,
            "smoke_baseline_params_from": V7_BEST_JSON.name,
            "built_at": ts,
        }
        json.dump(metrics, metrics_path.open("w"), indent=2)
        print(f"\n=== Unified Ranker v1 OOS metrics ===")
        for k, v in metrics.items():
            print(f"  {k}: {v}")

        # Write OOS predictions to mart (for Phase 4 gate)
        oos_df_out = oos_df[["stock_code", "signal_date"]].copy()
        oos_df_out["score"] = y_oos_pred
        oos_df_out["model_id"] = model_id
        oos_df_out["built_at"] = ts
        conn.execute("DROP TABLE IF EXISTS mart_unified_v1_oos_predictions")
        conn._con.register("unified_oos_df", oos_df_out)
        conn.execute("CREATE TABLE mart_unified_v1_oos_predictions AS SELECT * FROM unified_oos_df")
        conn.commit()
        print(f"\nWrote {len(oos_df_out):,} OOS predictions to mart_unified_v1_oos_predictions")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
