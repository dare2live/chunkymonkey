#!/usr/bin/env python3
"""Train and register the TDX keep challenger as lifecycle=challenger."""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import optuna

from services.db import get_conn
from services.ml_lifecycle.registry import propose_challenger
from services.model_feature_schema import (
    BASE_FEATURE_COLS,
    DEFAULT_LABEL_NAME,
    DENSE_V2_FEATURE_COLS,
    TDX_KEEP_CHALLENGER_SCHEMA_VERSION,
    TDX_KEEP_FEATURE_COLS,
    feature_cols_to_json,
)
from scripts.run_feature_ablation import (
    compute_ic,
    decile_metrics,
    _dates,
    _matrix,
    _quote_ident,
    _rank_percentiles,
    _records_from_cursor,
    _values,
    split_time_series_records,
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


MODEL_DDL = """
CREATE TABLE IF NOT EXISTS mart_multidim_model (
    model_id TEXT PRIMARY KEY,
    created_at TEXT,
    train_start TEXT, train_end TEXT,
    valid_start TEXT, valid_end TEXT,
    holdout_start TEXT, holdout_end TEXT,
    n_features INTEGER,
    best_params_json TEXT,
    holdout_ic REAL, holdout_rank_ic REAL,
    holdout_top_decile_avg REAL, holdout_bottom_decile_avg REAL,
    holdout_long_short_spread REAL,
    holdout_winrate_top REAL,
    feature_importance_json TEXT,
    feature_cols_json TEXT,
    label_name TEXT,
    feature_schema_version TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS mart_multidim_prediction (
    model_id TEXT,
    stock_code TEXT,
    date TEXT,
    pred_score REAL,
    rank_in_date INTEGER,
    percentile REAL,
    PRIMARY KEY (model_id, stock_code, date)
);
"""


def ensure_model_schema(conn) -> None:
    conn.executescript(MODEL_DDL)
    cols = {row[0] for row in conn.execute("DESCRIBE mart_multidim_model").fetchall()}
    if "feature_cols_json" not in cols:
        conn.execute("ALTER TABLE mart_multidim_model ADD COLUMN feature_cols_json TEXT")
    if "label_name" not in cols:
        conn.execute("ALTER TABLE mart_multidim_model ADD COLUMN label_name TEXT")
    if "feature_schema_version" not in cols:
        conn.execute("ALTER TABLE mart_multidim_model ADD COLUMN feature_schema_version TEXT")
    conn.commit()


def _sample(rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    if max_rows <= 0 or len(rows) <= max_rows:
        return rows
    picked = random.Random(42).sample(rows, max_rows)
    return sorted(picked, key=lambda row: (str(row.get("date")), str(row.get("stock_code"))))


def _table_columns(conn, table: str) -> set[str]:
    duck = conn.raw if hasattr(conn, "raw") else conn
    return {
        row[0]
        for row in duck.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def load_panel_records(
    conn,
    start_date: str,
    end_date: str,
    *,
    feature_table: str,
    feature_set_id: str | None,
) -> list[dict[str, Any]]:
    duck = conn.raw if hasattr(conn, "raw") else conn
    panel_cols = _table_columns(conn, feature_table)
    if DEFAULT_LABEL_NAME not in panel_cols:
        raise RuntimeError(f"{feature_table} 缺少 label 列: {DEFAULT_LABEL_NAME}")
    feature_cols = []
    seen = set()
    for col in [*BASE_FEATURE_COLS, *DENSE_V2_FEATURE_COLS, *TDX_KEEP_FEATURE_COLS]:
        if col in panel_cols and col not in seen:
            feature_cols.append(col)
            seen.add(col)
    regime_expr = "p.regime_flag" if "regime_flag" in panel_cols else "NULL AS regime_flag"
    select_cols = [
        "p.stock_code",
        "p.date",
        regime_expr,
        f"p.{_quote_ident(DEFAULT_LABEL_NAME)} AS label_value",
        *[f"CAST(p.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}" for col in feature_cols],
    ]
    where = [
        f"p.date >= ? AND p.date <= ? AND p.{_quote_ident(DEFAULT_LABEL_NAME)} IS NOT NULL"
    ]
    params: list[Any] = [start_date, end_date]
    if feature_set_id and "feature_set_id" in panel_cols:
        where.append("p.feature_set_id = ?")
        params.append(feature_set_id)
    return _records_from_cursor(
        duck.execute(
            f"""
            SELECT {', '.join(select_cols)}
            FROM {_quote_ident(feature_table)} p
            WHERE {' AND '.join(where)}
            """,
            params,
        )
    )


def resolve_tdx_keep_features(rows: list[dict[str, Any]]) -> tuple[list[str], str]:
    panel_cols = set(rows[0].keys()) if rows else set()
    missing = [col for col in TDX_KEEP_FEATURE_COLS if col not in panel_cols]
    if missing:
        raise RuntimeError(f"tdx_keep_v1 缺少 keep 特征: {missing}")
    cols = [
        col for col in [*BASE_FEATURE_COLS, *DENSE_V2_FEATURE_COLS, *TDX_KEEP_FEATURE_COLS]
        if col in panel_cols
    ]
    return cols, TDX_KEEP_CHALLENGER_SCHEMA_VERSION


def train_lgb(
    train_rows: list[dict[str, Any]],
    valid_rows: list[dict[str, Any]],
    feature_cols: list[str],
    params: dict,
    *,
    num_round: int = 400,
) -> lgb.Booster:
    dt = lgb.Dataset(
        _matrix(train_rows, feature_cols),
        label=_values(train_rows, "label_value"),
        feature_name=feature_cols,
    )
    dv = lgb.Dataset(
        _matrix(valid_rows, feature_cols),
        label=_values(valid_rows, "label_value"),
        reference=dt,
        feature_name=feature_cols,
    )
    return lgb.train(
        params,
        dt,
        num_boost_round=num_round,
        valid_sets=[dv],
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
    )


def make_objective(train_rows, valid_rows, feature_cols):
    def objective(trial):
        params = {
            "objective": "regression",
            "metric": "rmse",
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 127),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 50, 500),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
            "bagging_freq": trial.suggest_int("bagging_freq", 0, 10),
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-4, 1.0, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-4, 1.0, log=True),
            "max_depth": trial.suggest_int("max_depth", 4, 10),
            "verbose": -1,
        }
        model = train_lgb(train_rows, valid_rows, feature_cols, params, num_round=400)
        pred = model.predict(
            _matrix(valid_rows, feature_cols),
            num_iteration=model.best_iteration,
        )
        _, rank_ic = compute_ic(_values(valid_rows, "label_value"), pred, _dates(valid_rows))
        return rank_ic
    return objective


def _date_bounds(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    dates = [str(row.get("date")) for row in rows if row.get("date") is not None]
    return (min(dates), max(dates)) if dates else (None, None)


def _prediction_rows(model_id: str, holdout: list[dict[str, Any]], pred) -> list[tuple]:
    rows = []
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for row, score in zip(holdout, pred):
        grouped.setdefault(str(row.get("date")), []).append((row, float(score)))
    for date, items in grouped.items():
        scores = [score for _, score in items]
        percentiles = _rank_percentiles(scores)
        for idx, (row, score) in enumerate(items):
            rank_in_date = 1 + sum(1 for other in scores if other > score)
            rows.append((
                model_id,
                row.get("stock_code"),
                date,
                score,
                rank_in_date,
                percentiles[idx],
            ))
    return rows


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
    records = load_panel_records(
        conn,
        start,
        end,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
    )
    conn.close()
    if not records:
        raise RuntimeError(f"{feature_table} 无可训练数据")

    feature_cols, resolved_schema = resolve_tdx_keep_features(records)
    if schema_version != resolved_schema:
        logger.warning(
            "requested schema=%s resolved=%s; using requested in registry",
            schema_version,
            resolved_schema,
        )

    train, valid, holdout = split_time_series_records(records)
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

    train_valid = [*train, *valid]
    model = lgb.train(
        best,
        lgb.Dataset(
            _matrix(train_valid, feature_cols),
            label=_values(train_valid, "label_value"),
            feature_name=feature_cols,
        ),
        num_boost_round=num_round,
    )
    pred = model.predict(_matrix(holdout, feature_cols))
    ic, rank_ic = compute_ic(_values(holdout, "label_value"), pred, _dates(holdout))
    dec = decile_metrics(_values(holdout, "label_value"), pred, _dates(holdout))
    fi = dict(zip(feature_cols, model.feature_importance(importance_type="gain").tolist()))
    model_id = f"{model_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    created_at = datetime.utcnow().isoformat()
    train_start, train_end = _date_bounds(train)
    valid_start, valid_end = _date_bounds(valid)
    holdout_start, holdout_end = _date_bounds(holdout)

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
            train_start,
            train_end,
            valid_start,
            valid_end,
            holdout_start,
            holdout_end,
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
    conn.executemany(
        """
        INSERT INTO mart_multidim_prediction
        (model_id, stock_code, date, pred_score, rank_in_date, percentile)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        _prediction_rows(model_id, holdout, pred),
    )
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
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))  # Phase ψ.5 allowlist: 实验脚本
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
