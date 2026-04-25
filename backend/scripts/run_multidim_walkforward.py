#!/usr/bin/env python3
"""Fixed-parameter walk-forward validation for the multidim model."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import pandas as pd

from services.db import get_conn
from services.model_feature_schema import DEFAULT_LABEL_NAME, REGIME_FEATURE_COLS
from scripts.train_multidim_model import (
    FEATURE_COLS,
    compute_ic,
    decile_metrics,
    ensure_model_schema,
    load_panel,
    train_lgb,
)


logger = logging.getLogger("multidim_walkforward")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_model_validation_fold (
    run_id TEXT NOT NULL,
    fold_id INTEGER NOT NULL,
    model_id TEXT,
    feature_schema_version TEXT,
    label_name TEXT,
    train_start TEXT, train_end TEXT,
    valid_start TEXT, valid_end TEXT,
    test_start TEXT, test_end TEXT,
    n_train INTEGER,
    n_valid INTEGER,
    n_test INTEGER,
    n_features INTEGER,
    params_json TEXT,
    test_ic REAL,
    test_rank_ic REAL,
    test_top_decile_avg REAL,
    test_bottom_decile_avg REAL,
    test_long_short_spread REAL,
    test_winrate_top REAL,
    best_iteration INTEGER,
    built_at TEXT,
    PRIMARY KEY (run_id, fold_id)
);

CREATE TABLE IF NOT EXISTS mart_model_walkforward_prediction (
    run_id TEXT NOT NULL,
    fold_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
    pred_score REAL,
    rank_in_date INTEGER,
    percentile REAL,
    PRIMARY KEY (run_id, fold_id, stock_code, date)
);
"""


DEFAULT_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "learning_rate": 0.04,
    "num_leaves": 31,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l1": 0.01,
    "lambda_l2": 0.1,
    "max_depth": 6,
    "verbose": -1,
    "seed": 42,
    "feature_fraction_seed": 42,
    "bagging_seed": 42,
    "data_random_seed": 42,
}


def latest_params(conn, model_id: str | None) -> tuple[str | None, dict]:
    if model_id:
        row = conn.execute(
            "SELECT model_id, best_params_json FROM mart_multidim_model WHERE model_id = ?",
            (model_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT model_id, best_params_json FROM mart_multidim_model ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None, dict(DEFAULT_PARAMS)
    params = json.loads(row["best_params_json"] or "{}")
    out = dict(DEFAULT_PARAMS)
    out.update(params)
    out.update({"objective": "regression", "metric": "rmse", "verbose": -1})
    return row["model_id"], out


def build_folds(dates: list, train_days: int, valid_days: int, test_days: int, step_days: int) -> list[dict]:
    folds = []
    start = 0
    fold_id = 1
    while True:
        train_end = start + train_days
        valid_end = train_end + valid_days
        test_end = valid_end + test_days
        if test_end > len(dates):
            break
        folds.append({
            "fold_id": fold_id,
            "train": (dates[start], dates[train_end - 1]),
            "valid": (dates[train_end], dates[valid_end - 1]),
            "test": (dates[valid_end], dates[test_end - 1]),
        })
        fold_id += 1
        start += step_days
    return folds


def load_label_dates(conn, start: str, end: str, label_name: str) -> list[str]:
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='fact_feature_panel'"
    ).fetchall()}
    if label_name not in cols:
        raise RuntimeError(f"fact_feature_panel 缺少 label 列: {label_name}")
    rows = conn.execute(
        f"""
        SELECT DISTINCT date
        FROM fact_feature_panel
        WHERE date >= ? AND date <= ? AND {label_name} IS NOT NULL
        ORDER BY date
        """,
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def _slice(df: pd.DataFrame, bounds: tuple) -> pd.DataFrame:
    return df[(df["date"] >= bounds[0]) & (df["date"] <= bounds[1])].copy()


def write_fold(conn, row: dict, pred_df: pd.DataFrame | None) -> None:
    conn.executescript(DDL)
    cols = [
        "run_id", "fold_id", "model_id", "feature_schema_version", "label_name",
        "train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end",
        "n_train", "n_valid", "n_test", "n_features", "params_json",
        "test_ic", "test_rank_ic", "test_top_decile_avg", "test_bottom_decile_avg",
        "test_long_short_spread", "test_winrate_top", "best_iteration", "built_at",
    ]
    conn.execute(
        f"INSERT OR REPLACE INTO mart_model_validation_fold ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        tuple(row.get(c) for c in cols),
    )
    if pred_df is not None and not pred_df.empty:
        duck = conn.raw if hasattr(conn, "raw") else conn
        duck.register("_wf_pred", pred_df)
        duck.execute("INSERT OR REPLACE INTO mart_model_walkforward_prediction SELECT * FROM _wf_pred")
        duck.unregister("_wf_pred")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None, help="复用哪个模型的 best_params; 默认最新")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--label-name", default=DEFAULT_LABEL_NAME)
    parser.add_argument("--train-days", type=int, default=378)
    parser.add_argument("--valid-days", type=int, default=63)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument("--step-days", type=int, default=63)
    parser.add_argument("--max-folds", type=int, default=0)
    parser.add_argument("--regime-aware", action="store_true")
    parser.add_argument("--save-predictions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        ensure_model_schema(conn)
        source_model_id, params = latest_params(conn, args.model_id)
        if args.dry_run:
            dates = load_label_dates(conn, args.start, args.end, args.label_name)
        else:
            df = load_panel(conn, args.start, args.end, label_name=args.label_name)
            dates = sorted(df["date"].unique())
        folds = build_folds(dates, args.train_days, args.valid_days, args.test_days, args.step_days)
        if args.max_folds > 0:
            folds = folds[:args.max_folds]
        logger.info("walk-forward folds=%d source_model=%s", len(folds), source_model_id)
        for fold in folds:
            logger.info("fold %d train=%s valid=%s test=%s", fold["fold_id"], fold["train"], fold["valid"], fold["test"])
        if args.dry_run:
            return

        # df is loaded above in non-dry-run branch.
        feature_cols = [c for c in FEATURE_COLS if c in df.columns]
        if _ADDED := [c for c in df.columns if c.startswith("a158_")]:
            feature_cols += _ADDED
        if args.regime_aware:
            feature_cols += [c for c in REGIME_FEATURE_COLS if c in df.columns]

        run_id = f"walkforward_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat()
        for fold in folds:
            train = _slice(df, fold["train"])
            valid = _slice(df, fold["valid"])
            test = _slice(df, fold["test"])
            model = train_lgb(
                train[feature_cols].values,
                train["label_value"].values,
                valid[feature_cols].values,
                valid["label_value"].values,
                params,
                num_round=400,
                feature_name=feature_cols,
            )
            pred = model.predict(test[feature_cols].values, num_iteration=model.best_iteration)
            ic, rank_ic = compute_ic(test["label_value"].values, pred, test["date"].values)
            dec = decile_metrics(test["label_value"].values, pred, test["date"].values)
            row = {
                "run_id": run_id,
                "fold_id": fold["fold_id"],
                "model_id": source_model_id,
                "feature_schema_version": "walkforward_fixed_params",
                "label_name": args.label_name,
                "train_start": fold["train"][0], "train_end": fold["train"][1],
                "valid_start": fold["valid"][0], "valid_end": fold["valid"][1],
                "test_start": fold["test"][0], "test_end": fold["test"][1],
                "n_train": len(train), "n_valid": len(valid), "n_test": len(test),
                "n_features": len(feature_cols),
                "params_json": json.dumps(params, ensure_ascii=False),
                "test_ic": ic, "test_rank_ic": rank_ic,
                "test_top_decile_avg": dec["top_avg"],
                "test_bottom_decile_avg": dec["bot_avg"],
                "test_long_short_spread": dec["spread"],
                "test_winrate_top": dec["winrate_top"],
                "best_iteration": int(model.best_iteration or 0),
                "built_at": built_at,
            }
            pred_out = None
            if args.save_predictions:
                pred_out = test[["stock_code", "date"]].copy()
                pred_out.insert(0, "fold_id", fold["fold_id"])
                pred_out.insert(0, "run_id", run_id)
                pred_out["pred_score"] = pred
                pred_out["rank_in_date"] = pred_out.groupby("date")["pred_score"].rank(ascending=False, method="min").astype(int)
                pred_out["percentile"] = pred_out.groupby("date")["pred_score"].rank(pct=True)
                pred_out = pred_out[["run_id", "fold_id", "stock_code", "date", "pred_score", "rank_in_date", "percentile"]]
            write_fold(conn, row, pred_out)
            logger.info(
                "fold %d IC=%.4f RankIC=%.4f spread=%.4f WR=%.3f",
                fold["fold_id"], ic, rank_ic, dec["spread"], dec["winrate_top"],
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
