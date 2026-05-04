#!/usr/bin/env python3
"""Train and register the TDX keep challenger as lifecycle=challenger."""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import optuna
import pandas as pd

from services.db import get_conn
from services.ml_lifecycle.registry import propose_challenger
from services.model_feature_schema import (
    DEFAULT_LABEL_NAME,
    TDX_KEEP_CHALLENGER_SCHEMA_VERSION,
    feature_cols_to_json,
)
from scripts.train_multidim_model import (
    compute_ic,
    decile_metrics,
    ensure_model_schema,
    load_panel,
    make_objective,
    resolve_feature_group,
    split_time_series,
)


logger = logging.getLogger("tdx_keep_train")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
optuna.logging.set_verbosity(optuna.logging.WARNING)


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


def _sample(df: pd.DataFrame, max_rows: int) -> pd.DataFrame:
    if max_rows <= 0 or len(df) <= max_rows:
        return df
    return df.sample(n=max_rows, random_state=42).sort_values(["date", "stock_code"])


def train_tdx_keep_challenger(
    *,
    feature_table: str,
    feature_set_id: str,
    schema_version: str,
    model_prefix: str,
    trials: int,
    start: str,
    end: str,
    optuna_max_rows: int,
    num_round: int,
) -> dict:
    t0 = time.time()
    conn = get_conn()
    ensure_model_schema(conn)
    df = load_panel(
        conn,
        start,
        end,
        label_name=DEFAULT_LABEL_NAME,
        with_alpha158=False,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
    )
    conn.close()
    if df.empty:
        raise RuntimeError(f"{feature_table} 无可训练数据")

    feature_cols, resolved_schema = resolve_feature_group("tdx_keep_v1", df, regime_aware=False)
    if schema_version != resolved_schema:
        logger.warning("requested schema=%s resolved=%s; using requested in registry", schema_version, resolved_schema)

    train, valid, holdout = split_time_series(df)
    train_opt = _sample(train, optuna_max_rows)
    valid_opt = _sample(valid, max(1, optuna_max_rows // 4))
    if trials > 0:
        logger.info(
            "Optuna start trials=%d train_sample=%d valid_sample=%d full_train=%d full_valid=%d",
            trials, len(train_opt), len(valid_opt), len(train), len(valid),
        )
        study = optuna.create_study(direction="maximize")
        study.optimize(make_objective(train_opt, valid_opt, feature_cols), n_trials=trials)
        best = dict(study.best_params)
        objective_score = float(study.best_value)
    else:
        best = {}
        objective_score = None
    params = dict(DEFAULT_PARAMS)
    params.update(best)
    params.update({"objective": "regression", "metric": "rmse", "verbose": -1})
    best = params

    train_valid = pd.concat([train, valid], ignore_index=True)
    model = lgb.train(
        best,
        lgb.Dataset(train_valid[feature_cols].values, label=train_valid["label_value"].values, feature_name=feature_cols),
        num_boost_round=num_round,
    )
    pred = model.predict(holdout[feature_cols].values)
    ic, rank_ic = compute_ic(holdout["label_value"].values, pred, holdout["date"].values)
    dec = decile_metrics(holdout["label_value"].values, pred, holdout["date"].values)
    fi = dict(zip(feature_cols, model.feature_importance(importance_type="gain").tolist()))
    model_id = f"{model_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    created_at = datetime.utcnow().isoformat()

    conn = get_conn()
    ensure_model_schema(conn)
    conn.execute(
        """
        INSERT INTO mart_multidim_model (
            model_id, created_at,
            train_start, train_end, valid_start, valid_end, holdout_start, holdout_end,
            n_features, best_params_json,
            holdout_ic, holdout_rank_ic,
            holdout_top_decile_avg, holdout_bottom_decile_avg,
            holdout_long_short_spread, holdout_winrate_top,
            feature_importance_json, feature_cols_json, label_name, feature_schema_version,
            notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            model_id,
            created_at,
            str(train["date"].min()),
            str(train["date"].max()),
            str(valid["date"].min()),
            str(valid["date"].max()),
            str(holdout["date"].min()),
            str(holdout["date"].max()),
            len(feature_cols),
            json.dumps(best, ensure_ascii=False),
            ic,
            rank_ic,
            dec["top_avg"],
            dec["bot_avg"],
            dec["spread"],
            dec["winrate_top"],
            json.dumps(fi, ensure_ascii=False),
            feature_cols_to_json(feature_cols),
            DEFAULT_LABEL_NAME,
            schema_version,
            json.dumps(
                {
                    "feature_group": "tdx_keep_v1",
                    "feature_table": feature_table,
                    "feature_set_id": feature_set_id,
                    "trials": trials,
                    "optuna_objective_score": objective_score,
                    "promote_to_champion": False,
                },
                ensure_ascii=False,
            ),
        ),
    )
    pred_df = holdout[["stock_code", "date"]].copy()
    pred_df["model_id"] = model_id
    pred_df["pred_score"] = pred
    pred_df["rank_in_date"] = pred_df.groupby("date")["pred_score"].rank(ascending=False, method="min").astype(int)
    pred_df["percentile"] = pred_df.groupby("date")["pred_score"].rank(pct=True)
    pred_df = pred_df[["model_id", "stock_code", "date", "pred_score", "rank_in_date", "percentile"]]
    duck = conn.raw if hasattr(conn, "raw") else conn
    duck.register("_tdx_keep_pred", pred_df)
    duck.execute("INSERT INTO mart_multidim_prediction SELECT * FROM _tdx_keep_pred")
    duck.unregister("_tdx_keep_pred")
    conn.commit()
    conn.close()

    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "multidim_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{model_id}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    propose_challenger(
        model_id,
        training_config={
            "feature_table": feature_table,
            "feature_set_id": feature_set_id,
            "schema_version": schema_version,
            "feature_group": "tdx_keep_v1",
            "n_features": len(feature_cols),
            "trials": trials,
            "num_round": num_round,
            "model_path": str(model_path),
            "promote_to_champion": False,
        },
        ic_holdout=rank_ic,
    )
    result = {
        "model_id": model_id,
        "feature_schema_version": schema_version,
        "n_features": len(feature_cols),
        "keep_features_in_model": [c for c in feature_cols if c in {
            "forecast_profit_yoy_mid",
            "avg_float_shares_change_pct_tdx",
            "ocf_to_profit_tdx",
            "fund_shares_qoq",
            "forecast_range_width",
        }],
        "holdout_ic": ic,
        "holdout_rank_ic": rank_ic,
        "holdout_long_short_spread": dec["spread"],
        "holdout_winrate_top": dec["winrate_top"],
        "promote_to_champion": False,
        "model_path": str(model_path),
        "elapsed_minutes": (time.time() - t0) / 60.0,
    }
    logger.info("TDX keep challenger trained: %s", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-table", default="fact_feature_panel_tdx_keep_challenger")
    parser.add_argument("--feature-set-id", default="tdx_keep_challenger_v1")
    parser.add_argument("--schema-version", default=TDX_KEEP_CHALLENGER_SCHEMA_VERSION)
    parser.add_argument("--model-prefix", default="tdx_keep_challenger")
    parser.add_argument("--trials", type=int, default=80)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--optuna-max-rows", type=int, default=600_000)
    parser.add_argument("--num-round", type=int, default=260)
    args = parser.parse_args()
    train_tdx_keep_challenger(
        feature_table=args.feature_table,
        feature_set_id=args.feature_set_id,
        schema_version=args.schema_version,
        model_prefix=args.model_prefix,
        trials=args.trials,
        start=args.start,
        end=args.end,
        optuna_max_rows=args.optuna_max_rows,
        num_round=args.num_round,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
