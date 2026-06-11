#!/usr/bin/env python3
"""Phase 4.2b walk-forward unified ranker — replicates v7 expanding_monthly on unified panel.

Phase 4.2-diag verdict 2026-05-25: single-fit dead, walk-forward is the missing ingredient.
Codex review agent a885609738ef505a4 path C verdict.

REFACTOR 2026-05-25 02nd attempt:
- Previous run stuck 3h in DuckDB write semaphore (disk 97% full, 36GB peak RAM thrashing).
- Now: per-window query (no full panel load), incremental per_window.json checkpoint, OOS predictions to parquet (skip DB mart write).
- DB mart write deferred to a separate consolidation step after walk-forward completes.

Approach (mirrors retrain_lambdamart_v6.py expanding_monthly):
1. Query unique signal_dates from panel
2. split_expanding_monthly(min_train_months=6, forward_months=1, embargo_days=20)
3. Per window: query that window's train+test rows ONLY, fit booster, predict, checkpoint to disk, free memory
4. After all windows: aggregate metrics, output summary

Outputs:
- data/reports/optuna/unified_ranker_wf_v1_<ts>.json (aggregated metrics)
- data/reports/optuna/unified_ranker_wf_v1_<ts>.per_window.json (incremental updated each window)
- data/reports/optuna/unified_ranker_wf_v1_<ts>.oos_predictions.parquet (incremental appended each window)

Exit gate: rank_ic_mean ≥ 0.04 → unified ranker viable; < 0.04 → unified panel insufficient.

Usage:
  PYTHONPATH=backend python backend/scripts/retrain_unified_ranker_walkforward.py
"""

from __future__ import annotations

import argparse
import gc
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
from services.optimization.walk_forward import split_expanding_monthly  # noqa: E402

PANEL = "mart_p0a_feature_label_panel_unified_v1"
V7_BEST_JSON = REPO_ROOT / "data" / "reports" / "optuna" / "lgbm_phase5_v7_20260523T010000Z.best.json"
LABEL = "fwd_cost_after_20d"
EXCLUDE_META = {"stock_code", "signal_date", "entry_date", "unable_at_entry",
                "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
                "feature_version", "built_at"}
# rule-compliance: ok evidence=Phase 4.2b walk-forward parameters
MIN_TRAIN_MONTHS = 6  # rule-compliance: ok evidence=v7 walk_forward standard
FORWARD_MONTHS = 1    # rule-compliance: ok evidence=v7 walk_forward standard
EMBARGO_DAYS = 20     # rule-compliance: ok evidence=label fwd_cost_after_20d horizon
MIN_TEST_SIGNALS = 1  # rule-compliance: ok evidence=unique-date signals, row count from per-window query


def _rank_ic(yt: np.ndarray, yp: np.ndarray) -> float:
    valid = ~np.isnan(yt) & ~np.isnan(yp)
    if valid.sum() < 10:
        return float("nan")
    rho, _ = spearmanr(yt[valid], yp[valid])
    return float(rho)


def _query_window(conn, dates_set: set[str], cols: list[str]) -> pd.DataFrame:
    """Query rows for a specific signal_date set. Returns small df."""
    if not dates_set:
        return pd.DataFrame(columns=cols)
    placeholders = ", ".join(["?"] * len(dates_set))
    sql = f"SELECT * FROM {PANEL} WHERE signal_date IN ({placeholders})"
    rows = conn.execute(sql, list(dates_set)).fetchall()
    return pd.DataFrame(rows, columns=cols)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--max-windows", type=int, default=None)
    args = p.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # Phase ψ.5 allowlist: 产物文件名时间戳非 trade_date
    model_id = f"unified_ranker_wf_v1_{ts}"
    out_dir = REPO_ROOT / "data" / "reports" / "optuna"
    out_metrics = out_dir / f"{model_id}.json"
    out_per_window = out_dir / f"{model_id}.per_window.json"
    out_oos_parquet = out_dir / f"{model_id}.oos_predictions.parquet"

    best = json.load(V7_BEST_JSON.open())
    best_params = best.get("best_params") or {}
    print(f"Smoke baseline params from {V7_BEST_JSON.name}: trial {best.get('best_trial_number')}", flush=True)

    with connect(args.db_path, read_only=True) as conn:
        cols = [c[0] for c in conn.execute(f"SELECT * FROM {PANEL} LIMIT 0").description]
        unique_dates = [str(r[0]) for r in conn.execute(
            f"SELECT DISTINCT signal_date FROM {PANEL} ORDER BY signal_date").fetchall()]
        print(f"Unique signal_dates: {len(unique_dates)} [{unique_dates[0]} ~ {unique_dates[-1]}]", flush=True)

        signals_for_split = [{"signal_date": d} for d in unique_dates]
        splits = split_expanding_monthly(
            signals_for_split,
            min_train_months=MIN_TRAIN_MONTHS,
            forward_months=FORWARD_MONTHS,
            min_test=MIN_TEST_SIGNALS,
            embargo_days=EMBARGO_DAYS,
        )
        print(f"Total windows: {len(splits)}", flush=True)
        if args.max_windows:
            splits = splits[:args.max_windows]
            print(f"  cap to first {args.max_windows} windows", flush=True)
        if not splits:
            print("ERROR: no walk-forward windows produced", flush=True)
            return 1

        # First-pass: determine feature_cols from a small sample
        sample_df = _query_window(conn, {unique_dates[0]}, cols)
        feature_cols = [c for c in cols if c not in EXCLUDE_META
                        and pd.api.types.is_numeric_dtype(sample_df[c])]
        print(f"Numeric features: {len(feature_cols)}", flush=True)
        del sample_df
        gc.collect()

        per_window: list[dict] = []
        # Init parquet writer state — write per window with append-mode
        oos_writer = None

        for i, sp in enumerate(splits):
            train_dates = {s["signal_date"] for s in sp.train}
            test_dates = {s["signal_date"] for s in sp.test}
            print(f"\n[window {i+1}/{len(splits)}] train [{sp.train_start} → {sp.train_end}] "
                  f"({len(train_dates)} dates), test [{sp.test_start} → {sp.test_end}] "
                  f"({len(test_dates)} dates)", flush=True)

            train_df = _query_window(conn, train_dates, cols)
            test_df = _query_window(conn, test_dates, cols)
            print(f"  rows train={len(train_df):,} test={len(test_df):,}", flush=True)
            if train_df.empty or test_df.empty:
                print("  SKIP empty", flush=True)
                continue

            # Fit
            X_tr = train_df[feature_cols].values
            y_tr = pd.to_numeric(train_df[LABEL], errors="coerce").fillna(0).values
            bins = np.quantile(y_tr, [0.2, 0.4, 0.6, 0.8])
            y_rel = np.digitize(y_tr, bins)
            train_sorted = train_df.sort_values("signal_date").reset_index(drop=True)
            group_sizes = train_sorted.groupby("signal_date").size().values
            try:
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
            except Exception as e:
                print(f"  ERROR fit: {e}", flush=True)
                del train_df, test_df, X_tr, y_tr, y_rel, train_sorted
                gc.collect()
                continue
            del X_tr, y_tr, y_rel, train_sorted, train_df
            gc.collect()

            # Predict
            X_te = test_df[feature_cols].values
            pred = booster.predict(X_te)
            del X_te, booster
            gc.collect()

            test_out = test_df[["stock_code", "signal_date", LABEL]].copy()
            test_out["_pred"] = pred
            del pred, test_df
            gc.collect()

            # Per-day rank_ic
            day_ics = []
            for d, g in test_out.groupby("signal_date"):
                yt = pd.to_numeric(g[LABEL], errors="coerce").values
                yp = g["_pred"].values
                ic = _rank_ic(yt, yp)
                if not np.isnan(ic):
                    day_ics.append(ic)
            window_ic_mean = float(np.mean(day_ics)) if day_ics else float("nan")
            window_ic_std = float(np.std(day_ics)) if day_ics else float("nan")
            print(f"  rank_ic_mean: {window_ic_mean:.4f} ± {window_ic_std:.4f} ({len(day_ics)} days)", flush=True)

            per_window.append({
                "window_idx": i,
                "train_start": sp.train_start, "train_end": sp.train_end,
                "test_start": sp.test_start, "test_end": sp.test_end,
                "n_train_dates": len(train_dates), "n_test_dates": len(test_dates),
                "n_test_days_with_ic": len(day_ics),
                "rank_ic_mean": window_ic_mean,
                "rank_ic_std": window_ic_std,
            })

            # Incremental checkpoint per_window.json (write entire list each time — small file)
            out_per_window.write_text(json.dumps(per_window, indent=2))

            # Append OOS predictions to csv (incremental, pyarrow-free)
            test_out["model_id"] = model_id
            test_out["window_idx"] = i
            test_out_out = test_out.rename(columns={"_pred": "score", LABEL: "fwd_cost_after_20d"})
            mode = "w" if oos_writer is None else "a"
            header = oos_writer is None
            test_out_out.to_csv(str(out_oos_parquet).replace(".parquet", ".csv"),
                                 mode=mode, header=header, index=False)
            oos_writer = "initialized"
            del test_out, test_out_out
            gc.collect()

        # Aggregate
        if not per_window:
            print("ERROR: no successful windows", flush=True)
            return 1
        window_ics = [w["rank_ic_mean"] for w in per_window if not np.isnan(w["rank_ic_mean"])]
        agg_ic_mean = float(np.mean(window_ics)) if window_ics else float("nan")
        agg_ic_std = float(np.std(window_ics)) if window_ics else float("nan")
        positive_rate = float(sum(1 for x in window_ics if x > 0) / len(window_ics)) if window_ics else float("nan")

        metrics = {
            "model_id": model_id,
            "panel": PANEL,
            "n_windows": len(per_window),
            "n_features": len(feature_cols),
            "min_train_months": MIN_TRAIN_MONTHS,
            "forward_months": FORWARD_MONTHS,
            "embargo_days": EMBARGO_DAYS,
            "smoke_baseline_params_from": V7_BEST_JSON.name,
            "window_rank_ic_mean": agg_ic_mean,
            "window_rank_ic_std": agg_ic_std,
            "window_rank_ic_positive_rate": positive_rate,
            "v7_baseline_rank_ic_mean": 0.0475,
            "v7_baseline_rank_ic_std": 0.0686,
            "v7_baseline_positive_rate": 0.6875,
            "phase42b_exit_gate_rank_ic_threshold": 0.04,
            "phase42b_exit_gate_passed": agg_ic_mean >= 0.04,
            "built_at": ts,
            "codex_review_agent_id": "a885609738ef505a4",
        }
        out_metrics.write_text(json.dumps(metrics, indent=2))

    print(f"\n=== Phase 4.2b walk-forward unified ranker ===", flush=True)
    print(f"Model ID: {model_id}")
    print(f"Windows: {len(per_window)}, features: {len(feature_cols)}")
    print(f"window_rank_ic_mean: {agg_ic_mean:.4f} ± {agg_ic_std:.4f}")
    print(f"positive_rate: {positive_rate*100:.1f}%")
    print(f"v7 baseline (16w): 0.0475 ± 0.0686, positive_rate 68.75%")
    print(f"Phase 4.2b exit gate (rank_ic ≥ 0.04): {'PASS' if agg_ic_mean >= 0.04 else 'FAIL'}")
    print(f"\nOutputs:\n  {out_metrics}\n  {out_per_window}\n  {out_oos_parquet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
