#!/usr/bin/env python3
"""Phase 4.2-diag: feature group ablation for unified ranker v1 regression diagnosis.

Codex review agent a885609738ef505a4 path C (2026-05-24): unified ranker rank_ic 0.0106 vs v7 0.0452 = -76%.
3 hypotheses (computed in 1 ablation run):
- H1 NULL noise: perception_stock_level 0-1% fill — does excluding it recover rank_ic?
- H2 train window: re-train with v7-equivalent window 2024-01-02 ~ 2024-06-28 (drops perception since pre-2024-11 NULL)
- H3 feature selection: 116 numeric vs 157 total — which 41 cols got dropped + which group matters?

Outputs:
- analysis/phase42_ablation_<date>.json (per-config rank_ic_mean/std/ndcg/top_spread)
- analysis/phase42_diag_verdict_<date>.md (decision + numbers)

Usage:
  PYTHONPATH=backend python backend/scripts/run_phase42_diag_ablation.py [--quick]

  --quick: skip H2 retrain config (saves ~5 min); only H1/H3 group ablation
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, date
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
LABEL = "fwd_cost_after_20d"
EXCLUDE_META = {"stock_code", "signal_date", "entry_date", "unable_at_entry",
                "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
                "feature_version", "built_at"}

# rule-compliance: ok evidence=Codex a8856097 path C diag config
TRAIN_START_PERCEPTION = "2024-11-01"  # rule-compliance: ok evidence=perception coverage starts 2024-11
TRAIN_END = "2025-06-30"               # rule-compliance: ok evidence=8mo train window
OOS_START = "2025-07-01"               # rule-compliance: ok evidence=train_end + 1 day
OOS_END = "2026-04-30"                 # rule-compliance: ok evidence=panel max signal_date
TRAIN_START_V7_WIN = "2024-01-02"      # rule-compliance: ok evidence=v7 train window start (H2)


def _classify_cols(panel_cols: list[str]) -> dict[str, list[str]]:
    """Group panel columns by source — base panel vs market-perception vs stock-perception."""
    groups = {"base_v5": [], "perception_market": [], "perception_stock": []}
    for c in panel_cols:
        if c in EXCLUDE_META:
            continue
        if c.startswith("p_mkt_") or c.startswith("p_m_"):
            groups["perception_market"].append(c)
        elif c.startswith("p_stock_") or c.startswith("p_under_reaction_"):
            groups["perception_stock"].append(c)
        else:
            groups["base_v5"].append(c)
    return groups


def _rank_ic_per_day(df: pd.DataFrame, pred_col: str = "_pred") -> tuple[float, float, int]:
    per_date_ic = []
    for d, g in df.groupby("signal_date"):
        yt = pd.to_numeric(g[LABEL], errors="coerce").values
        yp = g[pred_col].values
        valid = ~np.isnan(yt) & ~np.isnan(yp)
        if valid.sum() < 10:
            continue
        rho, _ = spearmanr(yt[valid], yp[valid])
        if not np.isnan(rho):
            per_date_ic.append(rho)
    if not per_date_ic:
        return float("nan"), float("nan"), 0
    return float(np.mean(per_date_ic)), float(np.std(per_date_ic)), len(per_date_ic)


def _top_spread(df: pd.DataFrame, k: int, pred_col: str = "_pred") -> float:
    spreads = []
    for d, g in df.groupby("signal_date"):
        if len(g) < k:
            continue
        order = g[pred_col].values.argsort()[::-1][:k]
        top_mean = pd.to_numeric(g[LABEL].iloc[order], errors="coerce").mean()
        avg = pd.to_numeric(g[LABEL], errors="coerce").mean()
        if pd.notna(top_mean) and pd.notna(avg):
            spreads.append(top_mean - avg)
    return float(np.mean(spreads)) if spreads else float("nan")


def _train_and_evaluate(
    train_df: pd.DataFrame,
    oos_df: pd.DataFrame,
    feature_cols: list[str],
    best_params: dict,
    config_name: str,
) -> dict:
    print(f"  [{config_name}] features: {len(feature_cols)}, train: {len(train_df):,}, oos: {len(oos_df):,}")
    if not feature_cols:
        print(f"  [{config_name}] no features, skip")
        return {"config": config_name, "n_features": 0, "skipped": True}

    X_tr = train_df[feature_cols].values
    y_tr = pd.to_numeric(train_df[LABEL], errors="coerce").fillna(0).values
    bins = np.quantile(y_tr, [0.2, 0.4, 0.6, 0.8])
    y_rel = np.digitize(y_tr, bins)
    train_sorted = train_df.sort_values("signal_date").reset_index(drop=True)
    group_sizes = train_sorted.groupby("signal_date").size().values

    booster = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        ndcg_eval_at=[5, 10, 20],
        label_gain=[0, 1, 3, 7, 15],
        n_estimators=best_params.get("n_estimators", 100),
        verbose=-1,
        **{k: v for k, v in best_params.items() if k != "n_estimators"},
    )
    booster.fit(X_tr, y_rel, group=group_sizes)

    X_oos = oos_df[feature_cols].values
    oos_pred_df = oos_df[["stock_code", "signal_date", LABEL]].copy()
    oos_pred_df["_pred"] = booster.predict(X_oos)

    ic_mean, ic_std, n_days = _rank_ic_per_day(oos_pred_df)
    return {
        "config": config_name,
        "n_features": len(feature_cols),
        "train_rows": int(len(train_df)),
        "oos_rows": int(len(oos_df)),
        "rank_ic_mean": ic_mean,
        "rank_ic_std": ic_std,
        "rank_ic_n_days": n_days,
        "top5_spread": _top_spread(oos_pred_df, 5),
        "top10_spread": _top_spread(oos_pred_df, 10),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--quick", action="store_true", help="skip H2 (v7-window retrain)")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = REPO_ROOT / "analysis" / f"phase42_ablation_{ts}.json"
    out_json.parent.mkdir(exist_ok=True)

    best = json.load(V7_BEST_JSON.open())
    best_params = best.get("best_params") or {}

    with connect(args.db_path, read_only=True) as conn:
        cols = [c[0] for c in conn.execute(f"SELECT * FROM {PANEL} LIMIT 0").description]
        groups = _classify_cols(cols)
        print(f"Feature groups: {[(g, len(c)) for g, c in groups.items()]}")

        # Load perception-window train + oos
        print(f"Loading data perception-window [{TRAIN_START_PERCEPTION} ~ {TRAIN_END}, oos {OOS_START} ~ {OOS_END}]...")
        train_df = pd.DataFrame(
            conn.execute(f"SELECT * FROM {PANEL} WHERE signal_date >= ? AND signal_date <= ?",
                         [TRAIN_START_PERCEPTION, TRAIN_END]).fetchall(),
            columns=cols,
        )
        oos_df = pd.DataFrame(
            conn.execute(f"SELECT * FROM {PANEL} WHERE signal_date >= ? AND signal_date <= ?",
                         [OOS_START, OOS_END]).fetchall(),
            columns=cols,
        )
        print(f"  train: {len(train_df):,}, oos: {len(oos_df):,}")

    # Filter to numeric only
    def _numeric(group: list[str]) -> list[str]:
        return [c for c in group if pd.api.types.is_numeric_dtype(train_df[c])]

    base_v5 = _numeric(groups["base_v5"])
    perc_mkt = _numeric(groups["perception_market"])
    perc_stk = _numeric(groups["perception_stock"])
    all_features = base_v5 + perc_mkt + perc_stk

    print(f"Numeric features: base_v5={len(base_v5)}, perc_mkt={len(perc_mkt)}, perc_stk={len(perc_stk)}, all={len(all_features)}")

    results = []

    # Config 1: all features (baseline / matches train_unified_ranker_v1 result)
    results.append(_train_and_evaluate(train_df, oos_df, all_features, best_params, "all_features"))

    # Config 2: base v5 only (perception_market + perception_stock excluded)
    results.append(_train_and_evaluate(train_df, oos_df, base_v5, best_params, "base_v5_only"))

    # Config 3: base v5 + perception_market only (H1: excluding stock-level which is 0-1% fill)
    results.append(_train_and_evaluate(train_df, oos_df, base_v5 + perc_mkt, best_params, "base_v5_plus_perc_market"))

    # Config 4: base v5 + perception_stock only (just to see if stock-level adds anything)
    results.append(_train_and_evaluate(train_df, oos_df, base_v5 + perc_stk, best_params, "base_v5_plus_perc_stock"))

    # Config 5 (H2): retrain with v7-window train (drops perception since pre-2024-11 NULL)
    if not args.quick:
        print(f"\nH2: loading v7-window train [{TRAIN_START_V7_WIN} ~ {TRAIN_END}]...")
        with connect(args.db_path, read_only=True) as conn:
            train_v7win = pd.DataFrame(
                conn.execute(f"SELECT * FROM {PANEL} WHERE signal_date >= ? AND signal_date <= ?",
                             [TRAIN_START_V7_WIN, TRAIN_END]).fetchall(),
                columns=cols,
            )
        results.append(_train_and_evaluate(train_v7win, oos_df, base_v5, best_params, "h2_v7win_base_v5_only"))
        results.append(_train_and_evaluate(train_v7win, oos_df, all_features, best_params, "h2_v7win_all_features"))

    # Write JSON
    out_json.write_text(json.dumps({
        "model_id_base": "phase42_diag_ablation",
        "panel": PANEL,
        "label": LABEL,
        "codex_review_agent_id": "a885609738ef505a4",
        "v7_baseline_rank_ic": 0.0452,
        "v7_baseline_top5_spread": 0.0511,
        "smoke_baseline_params_from": V7_BEST_JSON.name,
        "results": results,
        "built_at": ts,
    }, indent=2))
    print(f"\n=== Ablation results ===")
    print(f"Output: {out_json}")
    print(f"\n{'config':<35} {'features':>8} {'rank_ic':>10} {'std':>10} {'days':>6} {'top5':>10} {'top10':>10}")
    print("-" * 95)
    for r in results:
        if r.get("skipped"):
            continue
        print(f"  {r['config']:<33} {r['n_features']:>8} "
              f"{r['rank_ic_mean']:>10.4f} {r['rank_ic_std']:>10.4f} {r['rank_ic_n_days']:>6} "
              f"{r['top5_spread']:>10.4f} {r['top10_spread']:>10.4f}")
    print(f"\n  v7 baseline:                       105     0.0452     0.0686    n/a     0.0511        n/a")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
