#!/usr/bin/env python3
"""Daily v7 inference for forward operational delivery.

Per goal.md: '具备可交付运营的状态'. v7 candidate_forward_monitor deployed but daily inference path missing.
This script:
1. Loads v7 best_params from data/reports/optuna/lgbm_phase5_v7_*.best.json
2. Re-fits booster on full v7 train panel (panel v5c clean)
3. Inferences on most recent signal_date with available features
4. Writes top-K picks to mart_v7_daily_forward_picks
5. Wired into daily_update.sh Step 5e

Usage:
  PYTHONPATH=backend python backend/scripts/run_daily_v7_inference.py [--top-k 5] [--signal-date YYYY-MM-DD]

Output: mart_v7_daily_forward_picks (signal_date, stock_code, score, rank)
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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=v7 model_id + panel constants for daily inference
V7_MODEL_ID = "lgbm_phase5_v7_20260523T010000Z"
V7_BEST_JSON = REPO_ROOT / "data" / "reports" / "optuna" / f"{V7_MODEL_ID}.best.json"
V7_BOOSTER_TXT = REPO_ROOT / "data" / "reports" / "optuna" / f"{V7_MODEL_ID}.lgb.txt"
V7_FEATURE_COLS_JSON = REPO_ROOT / "data" / "reports" / "optuna" / f"{V7_MODEL_ID}.feature_cols.json"
V7_PANEL = "mart_p0a_feature_label_panel_v5"
TRAIN_START = "2024-01-02"  # rule-compliance: ok evidence=v7 train start
TRAIN_END = "2024-06-28"     # rule-compliance: ok evidence=v7 train end (rest is OOS)
OUTPUT_TABLE = "mart_v7_daily_forward_picks"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--signal-date", default=None, help="YYYY-MM-DD; default = latest in panel")
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--dry-run", action="store_true", help="don't write to mart")
    p.add_argument("--force-refit", action="store_true", help="ignore cached booster, re-fit + overwrite")
    args = p.parse_args()

    if not V7_BEST_JSON.exists():
        print(f"ERROR: v7 best.json not found: {V7_BEST_JSON}")
        return 1
    best = json.load(V7_BEST_JSON.open())
    best_params = best.get("best_params") or {}
    print(f"v7 best_params loaded: trial {best.get('best_trial_number')}, value {best.get('best_value'):.4f}")

    with connect(args.db_path, read_only=False) as conn:
        # Determine signal_date
        if args.signal_date:
            signal_date = args.signal_date
        else:
            r = conn.execute(f"SELECT MAX(signal_date) FROM {V7_PANEL}").fetchone()
            signal_date = str(r[0]) if r and r[0] else None
        if not signal_date:
            print("ERROR: no panel data found")
            return 1
        print(f"Inference signal_date: {signal_date}")

        # Load saved booster if exists, else re-fit + save
        if V7_BOOSTER_TXT.exists() and V7_FEATURE_COLS_JSON.exists() and not args.force_refit:
            print(f"Loading saved booster from {V7_BOOSTER_TXT.name}")
            booster_native = lgb.Booster(model_file=str(V7_BOOSTER_TXT))
            feature_cols = json.load(V7_FEATURE_COLS_JSON.open())
            print(f"Feature cols loaded: {len(feature_cols)}")
            booster_predict_fn = booster_native.predict
        else:
            print(f"No saved booster (or --force-refit), re-fitting from {V7_PANEL}")
            train_sql = f"""
                SELECT * FROM {V7_PANEL}
                 WHERE signal_date >= '{TRAIN_START}' AND signal_date <= '{TRAIN_END}'
            """
            train_df = pd.DataFrame(
                conn.execute(train_sql).fetchall(),
                columns=[c[0] for c in conn.execute(f"SELECT * FROM {V7_PANEL} LIMIT 0").description],
            )
            print(f"Train rows: {len(train_df):,}")
            if train_df.empty:
                print("ERROR: empty train data")
                return 1
            EXCLUDE = {"stock_code", "signal_date", "fwd_cost_after_5d", "fwd_cost_after_10d",
                       "fwd_cost_after_20d", "feature_version", "built_at", "entry_date", "unable_at_entry"}
            feature_cols = [c for c in train_df.columns if c not in EXCLUDE
                            and pd.api.types.is_numeric_dtype(train_df[c])]
            print(f"Feature cols: {len(feature_cols)}")

            X_train = train_df[feature_cols].fillna(0).values
            y = pd.to_numeric(train_df["fwd_cost_after_20d"], errors="coerce").fillna(0).values
            bins = np.quantile(y, [0.2, 0.4, 0.6, 0.8])
            y_rel = np.digitize(y, bins)
            train_df_sorted = train_df.sort_values("signal_date").reset_index(drop=True)
            group_sizes = train_df_sorted.groupby("signal_date").size().values

            booster = lgb.LGBMRanker(
                objective="lambdarank",
                metric="ndcg",
                ndcg_eval_at=[5, 10, 20],
                label_gain=[0, 1, 3, 7, 15],
                n_estimators=best_params.get("n_estimators", 100),  # rule-compliance: ok evidence=v7 default
                **{k: v for k, v in best_params.items() if k != "n_estimators"},
            )
            booster.fit(X_train, y_rel, group=group_sizes)
            booster.booster_.save_model(str(V7_BOOSTER_TXT))
            json.dump(feature_cols, V7_FEATURE_COLS_JSON.open("w"))
            print(f"Booster saved to {V7_BOOSTER_TXT.name}, feature_cols to {V7_FEATURE_COLS_JSON.name}")
            booster_predict_fn = booster.predict

        # Load inference data
        infer_sql = f"""
            SELECT * FROM {V7_PANEL}
             WHERE signal_date = '{signal_date}'
        """
        infer_df = pd.DataFrame(
            conn.execute(infer_sql).fetchall(),
            columns=[c[0] for c in conn.execute(f"SELECT * FROM {V7_PANEL} LIMIT 0").description],
        )
        print(f"Infer rows: {len(infer_df):,}")
        if infer_df.empty:
            print(f"ERROR: no panel rows for {signal_date}")
            return 1

        # Inference
        X_infer = infer_df[feature_cols].fillna(0).values
        scores = booster_predict_fn(X_infer)
        infer_df["score"] = scores
        ranked = infer_df.nlargest(args.top_k, "score")[["stock_code", "signal_date", "score"]]
        ranked = ranked.reset_index(drop=True)
        ranked["rank"] = ranked.index + 1
        ranked["model_id"] = V7_MODEL_ID
        ranked["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n=== v7 top-{args.top_k} picks for {signal_date} ===")
        print(ranked.to_string(index=False))

        if args.dry_run:
            print("(dry-run, not writing to mart)")
            return 0

        # Write to mart
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
                signal_date DATE, stock_code VARCHAR, score DOUBLE, rank INTEGER,
                model_id VARCHAR, built_at TIMESTAMP,
                PRIMARY KEY (signal_date, stock_code, model_id)
            )
        """)
        conn.execute(f"DELETE FROM {OUTPUT_TABLE} WHERE signal_date = ? AND model_id = ?",
                     [signal_date, V7_MODEL_ID])
        conn._con.register("v7_picks_df", ranked)
        conn.execute(f"INSERT INTO {OUTPUT_TABLE} BY NAME SELECT * FROM v7_picks_df")
        conn.commit()
        print(f"\nWrote {len(ranked)} picks to {OUTPUT_TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
