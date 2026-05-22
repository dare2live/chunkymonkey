#!/usr/bin/env python3
"""v3 Phase A — Feature ablation to find OOS RankIC collapse root cause.

Background: stability model true train-log Phase4 verdict BLOCK (IS=0.114, OOS=0.0086,
relative_drop=92.43%). Stability penalty did not fix OOS RankIC collapse.

Hypothesis H1: feature panel over-specified, some feature group adds noise causing overfit.

Method: drop one feature group at a time, train LightGBM on train period, eval OOS RankIC
on holdout period. Compare to baseline (all 122 features).

Output: per-ablation OOS RankIC, identify groups whose removal IMPROVES OOS.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=v3 plan §Phase A OOS split, test period = last ~5 months matches stability model final walk-forward test windows
TRAIN_END = "2025-11-30"  # rule-compliance: ok evidence=walk-forward boundary
TEST_START = "2025-12-01"  # rule-compliance: ok evidence=walk-forward boundary
TEST_END = "2026-04-14"  # rule-compliance: ok evidence=stability model test_end
LABEL_COL = "fwd_cost_after_20d"

META_COLS = {"stock_code", "signal_date", "built_at", "trade_date_dt", "entry_date"}
LABEL_COLS = {"fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"}


def _group_of(col: str) -> str:
    if col in META_COLS or col in LABEL_COLS:
        return "META"
    if col.startswith("a158_"):
        return "alpha158"
    if col.startswith("inst_") or "institution" in col.lower():
        return "institution"
    if "sniper" in col.lower():
        return "sniper"
    if "lhb" in col.lower():
        return "lhb"
    if "capital" in col.lower() or "fund" in col.lower():
        return "capital_flow"
    if col.startswith("sm_"):
        return "smart_money"
    if col.startswith("sector_"):
        return "sector"
    if col.startswith("tom_"):
        return "calendar"
    if col.startswith("formula_"):
        return "formula"
    if col.startswith("exec_"):
        return "executive"
    if col.startswith("survey_"):
        return "survey"
    if col.startswith("event_"):
        return "event_window"
    if col.startswith("holder_"):
        return "holder"
    if col.startswith("vol_") or col.startswith("mom_") or col.startswith("beta_") or col == "sharpe_60d":
        return "vol_mom"
    if col.startswith("pe_") or col.startswith("pb") or col.startswith("ps_") or col.startswith("roe_"):
        return "fundamental"
    if col in {"mcap_decile", "top_inst_holding_ratio", "industry_pit_confidence", "unable_at_entry", "feature_version"}:
        return "context"
    return "uncategorized"


def main() -> int:
    db_path = str(REPO_ROOT / "data" / "smartmoney.duckdb")
    with connect(db_path, read_only=True) as conn:
        cols = [c[0] for c in conn.execute("SELECT * FROM mart_p0a_feature_label_panel_v4 LIMIT 0").description]
        feature_cols = [c for c in cols if c not in META_COLS and c not in LABEL_COLS and c != "unable_at_entry"]
        print(f"[ablation] panel cols: {len(cols)}, feature_cols: {len(feature_cols)}")

        groups = {}
        for c in feature_cols:
            g = _group_of(c)
            groups.setdefault(g, []).append(c)
        print(f"[ablation] feature groups:")
        for g in sorted(groups, key=lambda x: -len(groups[x])):
            print(f"  {g}: {len(groups[g])} cols")
        print()

        # Load training data: train_start ~ TRAIN_END
        print(f"[ablation] loading train+test panel data ...")
        sel_cols = ["signal_date", "stock_code"] + feature_cols + [LABEL_COL]
        select_sql = ", ".join(sel_cols)
        df = pd.DataFrame(
            conn.execute(
                f"""
                SELECT {select_sql}
                  FROM mart_p0a_feature_label_panel_v4
                 WHERE signal_date >= '2023-01-03'
                   AND signal_date <= '{TEST_END}'
                   AND {LABEL_COL} IS NOT NULL
                """
            ).fetchall(),
            columns=sel_cols,
        )
        df["signal_date"] = pd.to_datetime(df["signal_date"])
        for c in feature_cols + [LABEL_COL]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=[LABEL_COL])

        train_mask = df["signal_date"] <= pd.to_datetime(TRAIN_END)
        test_mask = df["signal_date"] >= pd.to_datetime(TEST_START)
        print(f"[ablation] train rows: {train_mask.sum():,} ({df.loc[train_mask, 'signal_date'].min().date()} → {df.loc[train_mask, 'signal_date'].max().date()})")
        print(f"[ablation] test rows:  {test_mask.sum():,} ({df.loc[test_mask, 'signal_date'].min().date()} → {df.loc[test_mask, 'signal_date'].max().date()})")
        print()

    import lightgbm as lgb

    def _train_and_eval(feature_subset: list[str], label: str) -> dict:
        X_train = df.loc[train_mask, feature_subset].fillna(0.0).values.astype(np.float32)
        y_train = df.loc[train_mask, LABEL_COL].values
        X_test = df.loc[test_mask, feature_subset].fillna(0.0).values.astype(np.float32)
        y_test = df.loc[test_mask, LABEL_COL].values
        test_dates = df.loc[test_mask, "signal_date"].values

        model = lgb.LGBMRegressor(
            n_estimators=100,
            max_depth=7,
            num_leaves=80,
            learning_rate=0.041,
            n_jobs=-1,
            random_state=42,
            verbose=-1,
        )
        model.fit(X_train, y_train, callbacks=[lgb.log_evaluation(period=0)])

        # OOS RankIC: per-date Spearman corr(pred, label), then mean
        preds = model.predict(X_test)
        oos_df = pd.DataFrame({"date": test_dates, "pred": preds, "label": y_test})
        # Spearman per date
        rank_ics = []
        for d, g in oos_df.groupby("date"):
            if len(g) < 5:
                continue
            ic = g["pred"].rank().corr(g["label"].rank())
            if np.isfinite(ic):
                rank_ics.append(ic)
        oos_mean = np.mean(rank_ics) if rank_ics else 0
        oos_std = np.std(rank_ics) if rank_ics else 0
        oos_ir = oos_mean / oos_std * np.sqrt(len(rank_ics)) if oos_std > 0 else 0

        # IS RankIC (single global) — for IS-OOS gap
        is_preds = model.predict(X_train)
        is_df = pd.DataFrame({"date": df.loc[train_mask, "signal_date"].values, "pred": is_preds, "label": y_train})
        is_ics = []
        for d, g in is_df.groupby("date"):
            if len(g) < 5:
                continue
            ic = g["pred"].rank().corr(g["label"].rank())
            if np.isfinite(ic):
                is_ics.append(ic)
        is_mean = np.mean(is_ics) if is_ics else 0

        return {
            "n_features": len(feature_subset),
            "is_rank_ic": is_mean,
            "oos_rank_ic_mean": oos_mean,
            "oos_rank_ic_std": oos_std,
            "oos_rank_ic_ir": oos_ir,
            "n_test_dates": len(rank_ics),
            "relative_drop": 1 - (oos_mean / is_mean) if is_mean else None,
        }

    print(f"[ablation] === BASELINE (all {len(feature_cols)} features) ===")
    baseline = _train_and_eval(feature_cols, LABEL_COL)
    print(f"  IS RankIC: {baseline['is_rank_ic']:.5f}")
    print(f"  OOS RankIC: {baseline['oos_rank_ic_mean']:.5f} (std {baseline['oos_rank_ic_std']:.5f}, IR {baseline['oos_rank_ic_ir']:.3f})")
    print(f"  relative_drop: {baseline['relative_drop']*100:.2f}%")
    print(f"  n_test_dates: {baseline['n_test_dates']}")
    print()

    # Ablation: drop one group at a time
    results = {"baseline": baseline}
    ablate_groups = ["alpha158", "institution", "lhb", "executive", "survey", "event_window", "holder",
                     "smart_money", "sector", "calendar", "formula", "vol_mom", "fundamental", "context"]

    for g in ablate_groups:
        if g not in groups:
            continue
        drop_cols = groups[g]
        keep_cols = [c for c in feature_cols if c not in drop_cols]
        if len(keep_cols) < 10:
            continue
        print(f"[ablation] === drop {g} ({len(drop_cols)} cols, {len(keep_cols)} remain) ===")
        res = _train_and_eval(keep_cols, LABEL_COL)
        results[f"drop_{g}"] = res
        delta_oos = res["oos_rank_ic_mean"] - baseline["oos_rank_ic_mean"]
        delta_drop = (res["relative_drop"] - baseline["relative_drop"]) * 100 if res["relative_drop"] and baseline["relative_drop"] else None
        sign = "+" if delta_oos > 0 else ""
        print(f"  IS: {res['is_rank_ic']:.5f}  OOS: {res['oos_rank_ic_mean']:.5f} (delta {sign}{delta_oos:+.5f})")
        print(f"  rel_drop: {res['relative_drop']*100:.2f}% (vs baseline {baseline['relative_drop']*100:.2f}%, delta {delta_drop:+.2f}pp)")
        print()

    # Summary table sorted by OOS RankIC improvement
    print("=" * 70)
    print("SUMMARY — sorted by OOS RankIC delta vs baseline")
    print("=" * 70)
    print(f"{'config':30s} {'OOS RankIC':>12s} {'delta':>10s} {'rel_drop':>10s} {'n_feat':>8s}")
    print("-" * 70)
    sorted_results = sorted(results.items(), key=lambda x: -x[1]["oos_rank_ic_mean"])
    for name, r in sorted_results:
        delta = r["oos_rank_ic_mean"] - baseline["oos_rank_ic_mean"]
        print(f"{name:30s} {r['oos_rank_ic_mean']:>12.5f} {delta:>+10.5f} {r['relative_drop']*100:>9.2f}% {r['n_features']:>8d}")
    print()
    print("Interpretation:")
    print("  drop_X > baseline = X adds noise/overfit (drop X helps)")
    print("  drop_X < baseline = X carries useful signal (keep X)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
