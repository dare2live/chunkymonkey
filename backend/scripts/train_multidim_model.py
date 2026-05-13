#!/usr/bin/env python3
"""Phase 3: LightGBM + Optuna 多维评分建模 (基于 fact_feature_panel)

管道
  1. 从 fact_feature_panel 读 panel (stock × date × 36 features + label)
  2. 时序切分 train/valid/holdout (70/15/15 date-ordered)
  3. LightGBM + Optuna 搜参 (optimize val IC)
  4. holdout 评估 (IC / RankIC / top-decile lift / winrate)
  5. 模型 + metrics + feature_importance 落到 mart_multidim_model

Optuna 搜参:
  lgb: num_leaves, learning_rate, min_data_in_leaf, feature_fraction, bagging_fraction,
       lambda_l1, lambda_l2, max_depth, num_boost_round
  regime interaction: 是否加 regime_flag 哑变量 + 与关键特征的交互项
  event window: exec_buy_count_N 中 N 的选择 (60 / 90 / 120)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import numpy as np
import optuna
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from services.db import get_conn
from services.model_artifacts import (
    CrossSectionalLightGBMRidgeBlend,
    DEFAULT_RIDGE_ALPHA,
    DEFAULT_RIDGE_WEIGHT,
    lightgbm_regression_params,
)
from services.model_feature_schema import (
    DEFAULT_LABEL_NAME,
    REGIME_FEATURE_COLS,
    TDX_KEEP_CHALLENGER_SCHEMA_VERSION,
    TDX_KEEP_FEATURE_COLS,
    feature_cols_from_json,
    feature_cols_to_json,
    holding_period_from_label,
    normalize_feature_cols,
    ordered_feature_cols,
)
from services.feature_retention import load_production_keep_features
from services.feature_registry import load_feature_registry
from services.pipeline_manifest import (
    git_commit_sha,
    record_pipeline_run,
    utc_now_iso,
)
from services.pricing_policy import load_pricing_label_policy
from scripts.run_feature_ablation import (
    compute_ic,
    decile_metrics,
    _quote_ident,
    _rank_percentiles,
    split_time_series_records,
)

logger = logging.getLogger("train_multidim")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
optuna.logging.set_verbosity(optuna.logging.WARNING)


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
    pricing_policy_id TEXT,
    pricing_policy_hash TEXT,
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


FEATURE_COLS = ordered_feature_cols(include_dense_v2=True)
ALPHA158_FEATURE_GROUPS = {"base_alpha158", "base_dense_v2_alpha158", "legacy_full"}
STRICT_FEATURE_CONTRACT_GROUPS = {"model_selection_run", "base_retention_keep", "tdx_keep_v1"}


def feature_group_uses_alpha158(feature_group: str) -> bool:
    return feature_group in ALPHA158_FEATURE_GROUPS


def _feature_contract_exclusion_reason(feature_name: str, spec: Any | None, excluded: set[str]) -> str | None:
    if feature_name in REGIME_FEATURE_COLS:
        return None
    if feature_name in excluded:
        return "model_input_excluded"
    if spec is None:
        return "missing_feature_registry_contract"
    if spec.label:
        return "label_column"
    if not spec.enabled:
        return "feature_disabled"
    if not spec.model_input:
        return "not_model_input"
    if not spec.production_ready:
        return "not_production_ready"
    if str(spec.null_policy) == "excluded_until_backfilled":
        return "excluded_until_backfilled"
    return None


def apply_production_feature_contract(
    feature_cols: list[str],
    *,
    feature_group: str,
    strict: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    registry = load_feature_registry()
    excluded_names = set(registry.model_input_excluded)
    allowed: list[str] = []
    excluded: list[dict[str, Any]] = []
    for feature_name in feature_cols:
        spec = registry.features.get(feature_name)
        reason = _feature_contract_exclusion_reason(feature_name, spec, excluded_names)
        if reason is None:
            allowed.append(feature_name)
            continue
        if feature_name.startswith("a158_") and feature_group in ALPHA158_FEATURE_GROUPS:
            allowed.append(feature_name)
            continue
        excluded.append(
            {
                "feature_name": feature_name,
                "reason": reason,
                "feature_group": getattr(spec, "group", None),
                "feature_role": getattr(spec, "feature_role", None),
                "null_policy": getattr(spec, "null_policy", None),
            }
        )
    if strict and excluded:
        sample = ", ".join(f"{row['feature_name']}:{row['reason']}" for row in excluded[:20])
        raise RuntimeError(f"feature contract disallows explicit training features: {sample}")
    if not allowed:
        raise RuntimeError("feature contract left no eligible model-input features")
    return normalize_feature_cols(allowed), excluded


def ensure_model_schema(conn) -> None:
    conn.executescript(MODEL_DDL)
    cols = {r[0] for r in conn.execute("DESCRIBE mart_multidim_model").fetchall()}
    for col in (
        "feature_cols_json",
        "label_name",
        "feature_schema_version",
        "pricing_policy_id",
        "pricing_policy_hash",
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE mart_multidim_model ADD COLUMN {col} TEXT")
    conn.commit()


def _table_columns(duck, table: str) -> set[str]:
    return {
        row[0]
        for row in duck.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _table_exists(duck, table: str) -> bool:
    row = duck.execute(
        """
        SELECT COUNT(*)
          FROM information_schema.tables
         WHERE table_name = ?
        """,
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _as_text_array(values) -> np.ndarray:
    if np.ma.isMaskedArray(values):
        values = values.filled("")
    return np.asarray(["" if value is None else str(value) for value in values], dtype=object)


def _as_float_array(values) -> np.ndarray:
    if np.ma.isMaskedArray(values):
        values = values.filled(np.nan)
    arr = np.asarray(values, dtype=np.float32)
    return np.nan_to_num(arr, nan=0.0, copy=False)


@dataclass
class PanelData:
    stock_codes: np.ndarray
    dates: np.ndarray
    labels: np.ndarray
    features: dict[str, np.ndarray]
    row_count: int

    @property
    def columns(self) -> set[str]:
        return {"stock_code", "date", "label_value", *self.features.keys()}

    def matrix(self, feature_cols: list[str], indices: np.ndarray | None = None) -> np.ndarray:
        arrays = []
        for col in feature_cols:
            if col not in self.features:
                raise KeyError(f"feature column missing from panel arrays: {col}")
            arr = self.features[col]
            arrays.append(arr if indices is None else arr[indices])
        if not arrays:
            return np.empty((0 if indices is None else len(indices), 0), dtype=np.float32)
        return np.column_stack(arrays).astype(np.float32, copy=False)

    def labels_for(self, indices: np.ndarray) -> np.ndarray:
        return self.labels[indices]

    def dates_for(self, indices: np.ndarray) -> np.ndarray:
        return self.dates[indices]

    def codes_for(self, indices: np.ndarray) -> np.ndarray:
        return self.stock_codes[indices]

    def to_records(self, indices: np.ndarray | None = None) -> list[dict[str, Any]]:
        row_indices = range(self.row_count) if indices is None else indices.tolist()
        records: list[dict[str, Any]] = []
        for idx in row_indices:
            row = {
                "stock_code": self.stock_codes[idx],
                "date": self.dates[idx],
                "label_value": float(self.labels[idx]),
            }
            for col, values in self.features.items():
                value = values[idx]
                row[col] = value.item() if hasattr(value, "item") else value
            records.append(row)
        return records


def _panel_query_parts(
    duck,
    *,
    label_name: str,
    with_alpha158: bool,
    feature_table: str,
    requested_feature_cols: list[str] | None = None,
    only_requested_feature_cols: bool = False,
) -> tuple[list[str], list[str], str, set[str]]:
    alpha158_db = Path(__file__).resolve().parent.parent.parent / "data" / "alpha158.duckdb"
    a158_col_list: list[str] = []
    if with_alpha158 and feature_table == "fact_feature_panel" and alpha158_db.exists():
        try:
            duck.execute(f"ATTACH IF NOT EXISTS '{alpha158_db}' AS a158 (READ_ONLY)")
            a158_col_list = [
                row[0]
                for row in duck.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_catalog='a158' AND table_name='fact_alpha158_panel' "
                    "AND column_name LIKE 'a158_%'"
                ).fetchall()
            ]
            logger.info("Alpha158 join 启用, 增补 %d 列", len(a158_col_list))
        except Exception as e:
            logger.warning("Alpha158 attach failed: %s", e)

    panel_cols = _table_columns(duck, feature_table)
    if label_name not in panel_cols:
        raise RuntimeError(f"fact_feature_panel 缺少 label 列: {label_name}")
    schema_feature_cols = list(FEATURE_COLS)
    for col in TDX_KEEP_FEATURE_COLS:
        if col not in schema_feature_cols:
            schema_feature_cols.append(col)
    requested_cols = normalize_feature_cols(requested_feature_cols or [])
    base_cols = [] if only_requested_feature_cols else [col for col in schema_feature_cols if col in panel_cols]
    for col in requested_cols:
        if col in panel_cols and col not in base_cols:
            base_cols.append(col)
    if not only_requested_feature_cols:
        missing_cols = [col for col in FEATURE_COLS if col not in panel_cols]
        if missing_cols:
            logger.warning("fact_feature_panel 缺少 %d 个 schema 特征, 本次跳过: %s", len(missing_cols), missing_cols)
    return base_cols, a158_col_list, ("p.regime_flag" if "regime_flag" in panel_cols else "CAST(NULL AS VARCHAR)"), panel_cols


def load_panel_arrays(
    conn,
    start_date: str,
    end_date: str,
    *,
    label_name: str = DEFAULT_LABEL_NAME,
    with_alpha158: bool = True,
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    requested_feature_cols: list[str] | None = None,
    only_requested_feature_cols: bool = False,
) -> PanelData:
    """Load the training panel as columnar NumPy arrays, not row dicts."""

    duck = conn.raw if hasattr(conn, 'raw') else conn
    logger.info("DuckDB NumPy 加载 %s %s ~ %s", feature_table, start_date, end_date)
    base_cols, a158_col_list, regime_expr, panel_cols = _panel_query_parts(
        duck,
        label_name=label_name,
        with_alpha158=with_alpha158,
        feature_table=feature_table,
        requested_feature_cols=requested_feature_cols,
        only_requested_feature_cols=only_requested_feature_cols,
    )
    feature_cols = [*base_cols, *a158_col_list]
    select_cols = [
        "p.stock_code AS stock_code",
        "CAST(p.date AS VARCHAR) AS date",
        f"{regime_expr} AS regime_flag",
        f"CAST(p.{_quote_ident(label_name)} AS DOUBLE) AS label_value",
    ] + [f"CAST(p.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}" for col in base_cols]
    alpha158_join = ""
    if a158_col_list:
        select_cols.extend(
            f"CAST(a.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}"
            for col in a158_col_list
        )
        alpha158_join = "LEFT JOIN a158.fact_alpha158_panel a ON a.stock_code = p.stock_code AND a.date = CAST(p.date AS DATE)"

    where = [f"p.date >= ? AND p.date <= ? AND p.{_quote_ident(label_name)} IS NOT NULL"]
    params = [start_date, end_date]
    if feature_set_id and "feature_set_id" in panel_cols:
        where.append("p.feature_set_id = ?")
        params.append(feature_set_id)

    query = f"""
        SELECT {', '.join(select_cols)}
        FROM {_quote_ident(feature_table)} p
        {alpha158_join}
        WHERE {' AND '.join(where)}
        ORDER BY p.date, p.stock_code
    """
    raw = duck.execute(query, params).fetchnumpy()
    row_count = len(raw.get("stock_code", []))
    stock_codes = _as_text_array(raw.get("stock_code", []))
    dates = _as_text_array(raw.get("date", []))
    labels = _as_float_array(raw.get("label_value", []))
    regime_flags = _as_text_array(raw.get("regime_flag", []))
    features = {col: _as_float_array(raw[col]) for col in feature_cols if col in raw}
    for flag in ("up", "flat", "down"):
        features[f"regime_{flag}"] = (regime_flags == flag).astype(np.float32)

    logger.info(
        "rows=%d codes=%d dates=%d feature_cols=%d total_cols=%d",
        row_count,
        len(np.unique(stock_codes)) if row_count else 0,
        len(np.unique(dates)) if row_count else 0,
        len(feature_cols),
        len(raw),
    )
    return PanelData(
        stock_codes=stock_codes,
        dates=dates,
        labels=labels,
        features=features,
        row_count=row_count,
    )


def load_panel(
    conn,
    start_date: str,
    end_date: str,
    *,
    label_name: str = DEFAULT_LABEL_NAME,
    with_alpha158: bool = True,
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    requested_feature_cols: list[str] | None = None,
    only_requested_feature_cols: bool = False,
) -> list[dict[str, Any]]:
    """Compatibility helper for tests/debugging; production training uses arrays."""

    return load_panel_arrays(
        conn,
        start_date,
        end_date,
        label_name=label_name,
        with_alpha158=with_alpha158,
        feature_table=feature_table,
        feature_set_id=feature_set_id,
        requested_feature_cols=requested_feature_cols,
        only_requested_feature_cols=only_requested_feature_cols,
    ).to_records()


def load_model_selection_run(conn, run_id: str) -> dict[str, Any]:
    """Load an Optuna/selection run as a concrete feature list for training."""

    duck = conn.raw if hasattr(conn, "raw") else conn
    if not _table_exists(duck, "mart_model_selection_run"):
        raise RuntimeError("mart_model_selection_run 不存在; 请先运行 feature search/selection")
    row = conn.execute(
        """
        SELECT run_id, feature_set_id, method, label_name, objective_score,
               selected_features_json, rejected_features_json, trials, notes, built_at
          FROM mart_model_selection_run
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"未找到 model selection run: {run_id}")
    selected = normalize_feature_cols(feature_cols_from_json(row["selected_features_json"]))
    if not selected:
        raise RuntimeError(f"model selection run 没有 selected_features: {run_id}")
    return {
        "run_id": row["run_id"],
        "feature_set_id": row["feature_set_id"],
        "method": row["method"],
        "label_name": row["label_name"],
        "objective_score": row["objective_score"],
        "selected_features": selected,
        "rejected_features_json": row["rejected_features_json"],
        "trials": row["trials"],
        "notes": row["notes"],
        "built_at": row["built_at"],
    }


def load_model_stability_search_run(conn, run_id: str) -> dict[str, Any]:
    """Load best fixed params from mart_model_stability_search_summary."""

    duck = conn.raw if hasattr(conn, "raw") else conn
    if not _table_exists(duck, "mart_model_stability_search_summary"):
        raise RuntimeError("mart_model_stability_search_summary 不存在; 请先运行 model stability search")
    row = conn.execute(
        """
        SELECT run_id, model_selection_run_id, feature_table, feature_set_id,
               label_name, selected_features_json, best_trial_number,
               best_params_json, objective_score, config_json, built_at
          FROM mart_model_stability_search_summary
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"未找到 model stability search run: {run_id}")
    params = json.loads(row["best_params_json"] or "{}")
    if not isinstance(params, dict) or not params:
        raise RuntimeError(f"model stability search run 没有 best_params_json: {run_id}")
    return {
        "run_id": row["run_id"],
        "model_selection_run_id": row["model_selection_run_id"],
        "feature_table": row["feature_table"],
        "feature_set_id": row["feature_set_id"],
        "label_name": row["label_name"],
        "selected_features": normalize_feature_cols(feature_cols_from_json(row["selected_features_json"])),
        "best_trial_number": row["best_trial_number"],
        "best_params": params,
        "objective_score": row["objective_score"],
        "config_json": row["config_json"],
        "built_at": row["built_at"],
    }


def split_time_series(rows: list[dict[str, Any]], train_ratio: float = 0.7, valid_ratio: float = 0.15):
    return split_time_series_records(rows, train_ratio=train_ratio, valid_ratio=valid_ratio)


def train_lgb(
    X_train,
    y_train,
    X_valid,
    y_valid,
    params: dict,
    num_round: int = 500,
    feature_name: list | None = None,
) -> lgb.Booster:
    dt = lgb.Dataset(X_train, label=y_train, feature_name=feature_name or 'auto')
    dv = lgb.Dataset(X_valid, label=y_valid, reference=dt, feature_name=feature_name or 'auto')
    model = lgb.train(
        params, dt,
        num_boost_round=num_round,
        valid_sets=[dv],
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
    )
    return model


def make_objective(
    train_dataset,
    valid_dataset,
    X_valid,
    y_valid: list[float],
    valid_dates: list[str],
    feature_cols: list[str],
    objective_num_round: int,
):
    def objective(trial):
        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
            'num_leaves': trial.suggest_int('num_leaves', 15, 127),
            'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 50, 500),
            'feature_fraction': trial.suggest_float('feature_fraction', 0.6, 1.0),
            'bagging_fraction': trial.suggest_float('bagging_fraction', 0.6, 1.0),
            'bagging_freq': trial.suggest_int('bagging_freq', 0, 10),
            'lambda_l1': trial.suggest_float('lambda_l1', 1e-4, 1.0, log=True),
            'lambda_l2': trial.suggest_float('lambda_l2', 1e-4, 1.0, log=True),
            'max_depth': trial.suggest_int('max_depth', 4, 10),
            'feature_pre_filter': False,
            'verbose': -1,
        }
        model = lgb.train(
            params,
            train_dataset,
            num_boost_round=objective_num_round,
            valid_sets=[valid_dataset],
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
        )
        pred = model.predict(X_valid, num_iteration=model.best_iteration)
        # 目标: valid RankIC (正向)
        _, rank_ic = compute_ic(y_valid, pred, valid_dates)
        return rank_ic
    return objective


def _date_bounds(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    dates = [str(row.get("date")) for row in rows if row.get("date") is not None]
    return (min(dates), max(dates)) if dates else (None, None)


def _date_bounds_array(dates: np.ndarray) -> tuple[str | None, str | None]:
    values = [str(value) for value in dates if str(value)]
    return (min(values), max(values)) if values else (None, None)


def _prediction_rows(model_id: str, holdout: list[dict[str, Any]], pred) -> list[tuple]:
    rows = []
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for row, score in zip(holdout, pred):
        grouped.setdefault(str(row.get("date")), []).append((row, float(score)))
    for date, items in grouped.items():
        scores = [score for _, score in items]
        percentiles = _rank_percentiles(scores)
        rank_by_idx = _rank_desc_min(scores)
        for idx, (row, score) in enumerate(items):
            rows.append((
                model_id,
                row.get("stock_code"),
                date,
                score,
                rank_by_idx[idx],
                percentiles[idx],
            ))
    return rows


def _prediction_rows_arrays(model_id: str, stock_codes: np.ndarray, dates: np.ndarray, pred) -> list[tuple]:
    columns = _prediction_column_arrays(model_id, stock_codes, dates, pred)
    return list(zip(
        columns["model_id"].tolist(),
        columns["stock_code"].tolist(),
        columns["date"].tolist(),
        columns["pred_score"].tolist(),
        columns["rank_in_date"].tolist(),
        columns["percentile"].tolist(),
    ))


def _average_rank_percentiles(values: np.ndarray) -> np.ndarray:
    n = len(values)
    if n == 0:
        return np.array([], dtype=np.float32)
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    starts = np.r_[0, np.flatnonzero(sorted_values[1:] != sorted_values[:-1]) + 1]
    ends = np.r_[starts[1:], n]
    out = np.empty(n, dtype=np.float32)
    for start, end in zip(starts, ends):
        percentile = (((start + 1) + end) / 2.0) / n
        out[order[start:end]] = percentile
    return out


def _descending_competition_ranks(values: np.ndarray) -> np.ndarray:
    n = len(values)
    if n == 0:
        return np.array([], dtype=np.int32)
    order = np.argsort(-values, kind="mergesort")
    sorted_values = values[order]
    starts = np.r_[0, np.flatnonzero(sorted_values[1:] != sorted_values[:-1]) + 1]
    ends = np.r_[starts[1:], n]
    out = np.empty(n, dtype=np.int32)
    for start, end in zip(starts, ends):
        out[order[start:end]] = start + 1
    return out


def _prediction_column_arrays(model_id: str, stock_codes: np.ndarray, dates: np.ndarray, pred) -> dict[str, np.ndarray]:
    scores = np.asarray(pred, dtype=np.float64)
    n = len(scores)
    ranks = np.empty(n, dtype=np.int32)
    percentiles = np.empty(n, dtype=np.float32)
    for date in np.unique(dates):
        idxs = np.flatnonzero(dates == date)
        day_scores = scores[idxs]
        ranks[idxs] = _descending_competition_ranks(day_scores)
        percentiles[idxs] = _average_rank_percentiles(day_scores)
    return {
        "model_id": np.full(n, model_id, dtype=object),
        "stock_code": stock_codes.astype(object, copy=False),
        "date": dates.astype(object, copy=False),
        "pred_score": scores.astype(np.float64, copy=False),
        "rank_in_date": ranks,
        "percentile": percentiles,
    }


def _rank_desc_min(values: list[float]) -> list[int]:
    """Competition rank, descending, with ties sharing the best rank."""
    indexed = sorted(enumerate(values), key=lambda item: item[1], reverse=True)
    ranks = [0] * len(values)
    pos = 0
    while pos < len(indexed):
        end = pos + 1
        while end < len(indexed) and indexed[end][1] == indexed[pos][1]:
            end += 1
        rank = pos + 1
        for idx in range(pos, end):
            ranks[indexed[idx][0]] = rank
        pos = end
    return ranks


def _record_columns(rows: list[dict[str, Any]]) -> set[str]:
    cols: set[str] = set()
    for row in rows:
        cols.update(row.keys())
    return cols


_ADDED_A158: list[str] = []


def resolve_feature_group_from_columns(
    name: str,
    panel_cols: set[str],
    *,
    regime_aware: bool,
    retention_keep_features: list[str] | None = None,
    retention_schema_tag: str = "retention_keep_v1",
    selection_features: list[str] | None = None,
    selection_schema_tag: str = "model_selection_run_v1",
) -> tuple[list[str], str]:
    from services.model_feature_schema import BASE_FEATURE_COLS, DENSE_V2_FEATURE_COLS

    a158 = [c for c in panel_cols if c.startswith("a158_")]
    base = [c for c in BASE_FEATURE_COLS if c in panel_cols]
    v2 = [c for c in DENSE_V2_FEATURE_COLS if c in panel_cols]

    if name == "base":
        cols = list(base)
        tag = "m7_base_v1"
    elif name == "base_dense_v2":
        cols = base + v2
        tag = "m7_base_dense_v2_v1"
    elif name == "base_alpha158":
        cols = base + sorted(a158)
        tag = "m7_base_alpha158_v1"
    elif name == "base_dense_v2_alpha158":
        cols = base + v2 + sorted(a158)
        tag = "m7_base_dense_v2_alpha158_v1"
    elif name == "tdx_keep_v1":
        keep = [c for c in TDX_KEEP_FEATURE_COLS if c in panel_cols]
        missing = [c for c in TDX_KEEP_FEATURE_COLS if c not in panel_cols]
        if missing:
            raise RuntimeError(f"tdx_keep_v1 缺少 keep 特征: {missing}")
        cols = base + v2 + keep
        tag = TDX_KEEP_CHALLENGER_SCHEMA_VERSION
    elif name == "base_retention_keep":
        requested = list(retention_keep_features or [])
        if not requested:
            raise RuntimeError("base_retention_keep 需要 retention keep 特征; 请提供 --feature-set-id 和可用 decision_run")
        keep = [c for c in requested if c in panel_cols]
        missing = [c for c in requested if c not in panel_cols]
        if missing:
            raise RuntimeError(f"base_retention_keep 缺少 keep 特征: {missing}")
        cols = base + keep
        tag = retention_schema_tag
    elif name == "model_selection_run":
        requested = normalize_feature_cols(selection_features or [])
        if not requested:
            raise RuntimeError("model_selection_run 需要 --model-selection-run-id 提供 selected features")
        missing = [c for c in requested if c not in panel_cols]
        if missing:
            raise RuntimeError(f"model_selection_run 缺少 selected 特征: {missing}")
        cols = list(requested)
        tag = selection_schema_tag
    elif name == "legacy_full":
        cols = [c for c in FEATURE_COLS if c in panel_cols]
        if a158:
            cols += sorted(a158)
        tag = "legacy_v0"
    else:
        raise ValueError(f"未知 feature group: {name}")

    if regime_aware:
        for col in REGIME_FEATURE_COLS:
            if col in panel_cols and col not in cols:
                cols.append(col)
        tag = tag + "_regime"
    return cols, tag


def resolve_feature_group(name: str, rows: list[dict[str, Any]], *, regime_aware: bool) -> tuple[list[str], str]:
    """M7: feature group 显式选择. 返回 (feature_cols, schema_version_tag).
    base                     - 43 特征 (BASE_FEATURE_COLS)
    base_dense_v2            - 54 特征 (BASE + DENSE_V2)
    tdx_keep_v1              - BASE + DENSE_V2 + 5 validated TDX keep features
    base_alpha158            - 107 特征 (BASE + a158_*) (实验对照)
    base_dense_v2_alpha158   - 118 特征 (BASE + DENSE_V2 + a158_*) (实验对照)
    legacy_full              - 旧 110 特征研究对照 (BASE + DENSE_V2 + a158_*)
    """
    return resolve_feature_group_from_columns(name, _record_columns(rows), regime_aware=regime_aware)


def _persist_predictions(conn, model_id: str, holdout: list[dict[str, Any]], pred) -> None:
    conn.executemany(
        """
        INSERT INTO mart_multidim_prediction
        (model_id, stock_code, date, pred_score, rank_in_date, percentile)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        _prediction_rows(model_id, holdout, pred),
    )


def _persist_prediction_arrays(conn, model_id: str, stock_codes: np.ndarray, dates: np.ndarray, pred) -> None:
    columns = _prediction_column_arrays(model_id, stock_codes, dates, pred)
    if len(columns["pred_score"]) == 0:
        return
    duck = conn.raw if hasattr(conn, "raw") else conn
    view_name = "tmp_multidim_prediction_numpy"
    duck.register(view_name, columns)
    try:
        duck.execute(
            f"""
            INSERT OR REPLACE INTO mart_multidim_prediction
            (model_id, stock_code, date, pred_score, rank_in_date, percentile)
            SELECT model_id, stock_code, date, pred_score, rank_in_date, percentile
            FROM {view_name}
            """
        )
    finally:
        try:
            duck.unregister(view_name)
        except Exception:
            pass


def _ensure_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        logger.error("fact_feature_panel 空或无 label; 先跑 build_feature_panel_duck.py")
        sys.exit(1)


def _ensure_panel(panel: PanelData) -> None:
    if panel.row_count == 0:
        logger.error("fact_feature_panel 空或无 label; 先跑 build_feature_panel_duck.py")
        sys.exit(1)


def _holdout_metrics(holdout: list[dict[str, Any]], pred) -> tuple[float, float, dict[str, float]]:
    values = [float(row.get("label_value") or 0.0) for row in holdout]
    dates = [str(row.get("date")) for row in holdout]
    ic, rank_ic = compute_ic(values, pred, dates)
    dec = decile_metrics(values, pred, dates)
    return ic, rank_ic, dec


def _log_top_features(feature_cols: list[str], model: Any) -> dict[str, float]:
    fi = dict(zip(feature_cols, model.feature_importance(importance_type='gain').tolist()))
    fi_sorted = sorted(fi.items(), key=lambda x: x[1], reverse=True)
    logger.info("top 10 特征: %s", fi_sorted[:10])
    return fi


def _model_dir() -> Path:
    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "multidim_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def _save_model(model_id: str, final_model: Any) -> Path:
    import pickle
    model_dir = _model_dir()
    model_path = model_dir / f"{model_id}.pkl"
    with open(model_path, 'wb') as f:
        pickle.dump(final_model, f)
    return model_path


def _feature_cols_for_log(feature_cols: list[str]) -> str:
    return feature_cols_to_json(feature_cols)


def _notes(args) -> str:
    selection = (
        f" · model_selection_run_id={args.model_selection_run_id}"
        if getattr(args, "model_selection_run_id", None)
        else ""
    )
    fixed_ref = getattr(args, "fixed_params_run_id", None)
    if fixed_ref:
        search_mode = f"fixed_params_run={fixed_ref}"
    else:
        search_mode = "fixed_params" if getattr(args, "fixed_params_json", None) else f"Optuna {args.trials} trials"
    return (
        f"M7/M8 candidate · feature_group={args.feature_group} · feature_table={args.feature_table} · "
        f"feature_set_id={args.feature_set_id} · {search_mode} · "
        f"regime_aware={args.regime_aware} · num_round={args.num_round}{selection}"
    )


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _best_params(study) -> dict:
    best = dict(study.best_params)
    best.update({
        'objective': 'regression', 'metric': 'rmse', 'verbose': -1,
        'seed': 42, 'feature_fraction_seed': 42, 'bagging_seed': 42,
        'data_random_seed': 42, 'feature_pre_filter': False,
    })
    return best


def _fixed_params_from_json(params_json: str | None, *, model_family: str | None = None) -> dict | None:
    if not params_json:
        return None
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--fixed-params-json must be a JSON object: {exc}") from exc
    if not isinstance(params, dict):
        raise ValueError("--fixed-params-json must be a JSON object")
    family = str(model_family or params.get("model_family") or "lightgbm")
    if family not in {"lightgbm", "lightgbm_ridge_blend"}:
        raise ValueError(f"unsupported model_family for train_multidim_model: {family}")
    params.update({
        'objective': 'regression', 'metric': 'rmse', 'verbose': -1,
        'seed': 42, 'feature_fraction_seed': 42, 'bagging_seed': 42,
        'data_random_seed': 42, 'feature_pre_filter': False,
    })
    if family == "lightgbm_ridge_blend":
        params.update({
            "model_family": "lightgbm_ridge_blend",
            "ridge_weight": float(params.get("ridge_weight", DEFAULT_RIDGE_WEIGHT)),
            "ridge_alpha": float(params.get("ridge_alpha", DEFAULT_RIDGE_ALPHA)),
        })
    else:
        params.pop("ridge_weight", None)
        params.pop("ridge_alpha", None)
        params["model_family"] = "lightgbm"
    return params


def _resolve_training_model_family(requested: str, params: dict[str, Any] | None) -> str:
    if requested != "auto":
        return requested
    return str((params or {}).get("model_family") or "lightgbm")


def _selection_schema_tag(run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in run_id).strip("_")
    return f"model_selection_{safe[:80] or 'run'}"


def _split_bounds(train, valid, holdout):
    train_start, train_end = _date_bounds(train)
    valid_start, valid_end = _date_bounds(valid)
    holdout_start, holdout_end = _date_bounds(holdout)
    return train_start, train_end, valid_start, valid_end, holdout_start, holdout_end


def split_time_series_indices(dates: np.ndarray, train_ratio: float = 0.7, valid_ratio: float = 0.15):
    unique_dates = np.array(sorted(np.unique(dates)), dtype=object)
    n = len(unique_dates)
    if n == 0:
        empty = np.array([], dtype=np.int64)
        return empty, empty, empty
    t_end = unique_dates[int(n * train_ratio)]
    v_end = unique_dates[int(n * (train_ratio + valid_ratio))]
    train_idx = np.flatnonzero(dates < t_end)
    valid_idx = np.flatnonzero((dates >= t_end) & (dates < v_end))
    holdout_idx = np.flatnonzero(dates >= v_end)
    logger.info(
        "split: train %s ~ %s (%d)  valid %s ~ %s (%d)  holdout %s ~ %s (%d)",
        *_date_bounds_array(dates[train_idx]), len(train_idx),
        *_date_bounds_array(dates[valid_idx]), len(valid_idx),
        *_date_bounds_array(dates[holdout_idx]), len(holdout_idx),
    )
    return train_idx, valid_idx, holdout_idx


def _insert_model(
    conn,
    *,
    model_id: str,
    train_bounds: tuple[str | None, str | None],
    valid_bounds: tuple[str | None, str | None],
    holdout_bounds: tuple[str | None, str | None],
    feature_cols: list[str],
    best: dict,
    ic: float,
    rank_ic: float,
    dec: dict[str, float],
    fi: dict[str, float],
    label_name: str,
    schema_tag: str,
    pricing_policy_id: str,
    pricing_policy_hash: str,
    notes: str,
) -> None:
    train_start, train_end = train_bounds
    valid_start, valid_end = valid_bounds
    holdout_start, holdout_end = holdout_bounds
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
            pricing_policy_id, pricing_policy_hash, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (model_id, datetime.utcnow().isoformat(),
         train_start, train_end,
         valid_start, valid_end,
         holdout_start, holdout_end,
         len(feature_cols),
         _json(best),
         ic, rank_ic,
         dec['top_avg'], dec['bot_avg'], dec['spread'],
         dec['winrate_top'],
         _json(fi),
         _feature_cols_for_log(feature_cols),
         label_name,
         schema_tag,
         pricing_policy_id,
         pricing_policy_hash,
        notes),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2023-01-01')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))  # Phase ψ.5 allowlist: 实验脚本
    parser.add_argument('--trials', type=int, default=50, help='Optuna 搜参次数')
    parser.add_argument('--label-name', default=DEFAULT_LABEL_NAME,
                        help='训练标签列名, 默认 forward_ret_20d')
    parser.add_argument('--regime-aware', action='store_true',
                        help='加入 regime one-hot 作为特征')
    parser.add_argument('--feature-group',
                        choices=[
                            'base', 'base_dense_v2', 'base_alpha158', 'base_dense_v2_alpha158',
                            'tdx_keep_v1', 'base_retention_keep', 'model_selection_run',
                            'legacy_full',
                        ],
                        default='base_dense_v2',
                        help='M7/M9: 显式特征组. 默认 base_dense_v2 为 compact production candidate; legacy_full 仅显式研究使用')
    parser.add_argument('--feature-table', default='fact_feature_panel',
                        help='训练使用的 feature table')
    parser.add_argument('--feature-set-id', default=None,
                        help='feature_table 有 feature_set_id 列时过滤')
    parser.add_argument('--retention-decision-run-id', default=None,
                        help='base_retention_keep 使用的 mart_feature_retention_decision.decision_run_id; 默认取最新')
    parser.add_argument('--retention-feature-set-id', default=None,
                        help='base_retention_keep 读取 retention decision 的 feature_set_id; 默认等于 --feature-set-id')
    parser.add_argument('--model-selection-run-id', default=None,
                        help='feature_group=model_selection_run 时读取 mart_model_selection_run.selected_features_json')
    parser.add_argument('--num-round', type=int, default=400, help='final fit 轮数')
    parser.add_argument('--objective-num-round', type=int, default=400,
                        help='Optuna 每次 trial 的训练轮数')
    parser.add_argument('--num-threads', type=int, default=0,
                        help='LightGBM num_threads; 0 表示使用 LightGBM 默认')
    parser.add_argument('--fixed-params-json', default=None,
                        help='JSON object of LightGBM params. When set, skip Optuna and train with these params.')
    parser.add_argument('--fixed-params-run-id', default=None,
                        help='读取 mart_model_stability_search_summary.best_params_json 并跳过 Optuna')
    parser.add_argument('--model-family', choices=['auto', 'lightgbm', 'lightgbm_ridge_blend'], default='auto',
                        help='训练模型族; auto 会从 fixed params 的 model_family 推断, 否则默认 lightgbm')
    parser.add_argument('--model-id-prefix', default='multidim_v1',
                        help='模型 ID 前缀, M7 候选可用 multidim_v2_base / multidim_v2_dense 区分')
    args = parser.parse_args()
    run_started_at = utc_now_iso()
    run_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    # 训练分两阶段释放 DuckDB 写锁, 让前端期间可读:
    # 1) 先用 writable 连接确保 DDL + 读 panel, 立即 close 释放锁
    # 2) 训练/调参/评估期间无 DB 连接
    # 3) 最后落库时重新打开 writable connection, 写完 close
    conn = get_conn()
    ensure_model_schema(conn)
    model_selection: dict[str, Any] | None = None
    fixed_params_run: dict[str, Any] | None = None
    fixed_params_json = args.fixed_params_json
    if args.fixed_params_run_id:
        if args.fixed_params_json:
            raise RuntimeError("--fixed-params-json 和 --fixed-params-run-id 只能二选一")
        fixed_params_run = load_model_stability_search_run(conn, args.fixed_params_run_id)
        fixed_params_json = json.dumps(fixed_params_run["best_params"], ensure_ascii=False)
        if args.feature_group == "model_selection_run" and not args.model_selection_run_id:
            args.model_selection_run_id = fixed_params_run["model_selection_run_id"]
    selected_feature_cols: list[str] | None = None
    if args.feature_group == "model_selection_run":
        if not args.model_selection_run_id:
            raise RuntimeError("feature_group=model_selection_run 必须指定 --model-selection-run-id")
        model_selection = load_model_selection_run(conn, args.model_selection_run_id)
        selected_feature_cols = list(model_selection["selected_features"])
        selected_label = model_selection.get("label_name")
        if selected_label and selected_label != args.label_name:
            logger.warning(
                "model_selection_run label_name=%s differs from --label-name=%s; using explicit training label",
                selected_label,
                args.label_name,
            )
        if fixed_params_run and fixed_params_run["model_selection_run_id"] != args.model_selection_run_id:
            raise RuntimeError(
                "fixed params run belongs to model_selection_run_id="
                f"{fixed_params_run['model_selection_run_id']}, not {args.model_selection_run_id}"
            )
    uses_alpha158_features = (
        feature_group_uses_alpha158(args.feature_group)
        or any(col.startswith("a158_") for col in (selected_feature_cols or []))
    )
    t_load = time.perf_counter()
    panel = load_panel_arrays(
        conn,
        args.start,
        args.end,
        label_name=args.label_name,
        with_alpha158=uses_alpha158_features,
        feature_table=args.feature_table,
        feature_set_id=args.feature_set_id,
        requested_feature_cols=selected_feature_cols,
        only_requested_feature_cols=bool(selected_feature_cols),
    )
    retention_keep_features: list[str] | None = None
    retention_decision_run_id: str | None = None
    if args.feature_group == "base_retention_keep":
        retention_feature_set_id = args.retention_feature_set_id or args.feature_set_id
        if not retention_feature_set_id:
            raise RuntimeError("base_retention_keep 必须指定 --feature-set-id 或 --retention-feature-set-id")
        retention_keep_features, retention_decision_run_id = load_production_keep_features(
            conn,
            feature_set_id=retention_feature_set_id,
            decision_run_id=args.retention_decision_run_id,
        )
        if not retention_keep_features:
            raise RuntimeError(
                f"retention_feature_set_id={retention_feature_set_id} decision_run={args.retention_decision_run_id or 'latest'} 无 production keep 特征"
            )
    timings["load_panel_s"] = round(time.perf_counter() - t_load, 3)
    conn.close()
    logger.info("数据加载完成, DuckDB 写锁已释放, 训练期间前端可正常读")
    _ensure_panel(panel)

    feature_cols, schema_tag = resolve_feature_group_from_columns(
        args.feature_group,
        panel.columns,
        regime_aware=args.regime_aware,
        retention_keep_features=retention_keep_features,
        retention_schema_tag=f"retention_keep_{retention_decision_run_id or 'latest'}",
        selection_features=selected_feature_cols,
        selection_schema_tag=_selection_schema_tag(args.model_selection_run_id or "latest"),
    )
    feature_cols, excluded_feature_contracts = apply_production_feature_contract(
        feature_cols,
        feature_group=args.feature_group,
        strict=args.feature_group in STRICT_FEATURE_CONTRACT_GROUPS,
    )
    if excluded_feature_contracts:
        schema_tag = f"{schema_tag}_contract_filtered"
        logger.warning(
            "feature contract excluded %d non-production features: %s",
            len(excluded_feature_contracts),
            excluded_feature_contracts[:20],
        )
    logger.info("feature_group=%s schema_tag=%s eligible_features=%d excluded_features=%d",
                args.feature_group, schema_tag, len(feature_cols), len(excluded_feature_contracts))

    t_split = time.perf_counter()
    train_idx, valid_idx, holdout_idx = split_time_series_indices(panel.dates)
    timings["split_s"] = round(time.perf_counter() - t_split, 3)

    # Phase 3: matrix / label / date arrays are built once and reused by all
    # Optuna trials. The old path rebuilt float32 matrices inside every trial.
    t_matrix = time.perf_counter()
    train_valid_idx = np.concatenate([train_idx, valid_idx])
    X_train = panel.matrix(feature_cols, train_idx)
    y_train = panel.labels_for(train_idx)
    X_valid = panel.matrix(feature_cols, valid_idx)
    y_valid = panel.labels_for(valid_idx)
    valid_dates = panel.dates_for(valid_idx)
    X_train_valid = panel.matrix(feature_cols, train_valid_idx)
    y_train_valid = panel.labels_for(train_valid_idx)
    X_holdout = panel.matrix(feature_cols, holdout_idx)
    y_holdout = panel.labels_for(holdout_idx)
    holdout_dates = panel.dates_for(holdout_idx)
    train_dataset = lgb.Dataset(
        X_train,
        label=y_train,
        feature_name=feature_cols,
        free_raw_data=False,
    )
    valid_dataset = lgb.Dataset(
        X_valid,
        label=y_valid,
        reference=train_dataset,
        feature_name=feature_cols,
        free_raw_data=False,
    )
    timings["matrix_precompute_s"] = round(time.perf_counter() - t_matrix, 3)

    requested_model_family = args.model_family
    raw_fixed_params = json.loads(fixed_params_json) if fixed_params_json else None
    model_family = _resolve_training_model_family(requested_model_family, raw_fixed_params)
    fixed_params = _fixed_params_from_json(fixed_params_json, model_family=model_family)
    if model_family == "lightgbm_ridge_blend" and fixed_params is None:
        raise RuntimeError("lightgbm_ridge_blend training requires --fixed-params-json or --fixed-params-run-id")
    if fixed_params is None:
        logger.info("Optuna 启动 %d 次 trial", args.trials)
        t_optuna = time.perf_counter()
        study = optuna.create_study(direction='maximize')
        study.optimize(
            make_objective(
                train_dataset,
                valid_dataset,
                X_valid,
                y_valid,
                valid_dates,
                feature_cols,
                args.objective_num_round,
            ),
            n_trials=args.trials,
        )
        timings["optuna_s"] = round(time.perf_counter() - t_optuna, 3)
        logger.info("Optuna 完成. best_value=%.4f params=%s", study.best_value, study.best_params)
        # 用 best params 重训 (train + valid 合并) + holdout 评估
        best = _best_params(study)
        best["model_family"] = "lightgbm"
        model_family = "lightgbm"
        model_search_mode = "optuna"
    else:
        timings["optuna_s"] = 0.0
        best = fixed_params
        model_search_mode = "fixed_params"
        logger.info("使用 fixed %s params, 跳过 Optuna: %s", model_family, best)
    if args.num_threads > 0:
        best["num_threads"] = int(args.num_threads)
    logger.info("LightGBM num_threads=%s cpu_count=%s", best.get("num_threads", "default"), os.cpu_count())

    # no early_stopping in final fit - use fixed num_round
    t_fit = time.perf_counter()
    if model_family == "lightgbm_ridge_blend":
        lightgbm_model = lgb.train(
            lightgbm_regression_params(best),
            lgb.Dataset(
                X_train_valid,
                label=y_train_valid,
                feature_name=feature_cols,
                free_raw_data=False,
            ),
            num_boost_round=args.num_round,
        )
        ridge_model = make_pipeline(
            StandardScaler(),
            Ridge(alpha=float(best.get("ridge_alpha", DEFAULT_RIDGE_ALPHA))),
        )
        ridge_model.fit(X_train_valid, y_train_valid)
        final_model = CrossSectionalLightGBMRidgeBlend(
            lightgbm_model=lightgbm_model,
            ridge_model=ridge_model,
            ridge_weight=float(best.get("ridge_weight", DEFAULT_RIDGE_WEIGHT)),
            ridge_alpha=float(best.get("ridge_alpha", DEFAULT_RIDGE_ALPHA)),
            feature_names=feature_cols,
        )
    else:
        final_model = lgb.train(
            lightgbm_regression_params(best),
            lgb.Dataset(
                X_train_valid,
                label=y_train_valid,
                feature_name=feature_cols,
                free_raw_data=False,
            ),
            num_boost_round=args.num_round,
        )
    timings["final_fit_s"] = round(time.perf_counter() - t_fit, 3)
    t_pred = time.perf_counter()
    pred_ho = final_model.predict(X_holdout, dates=holdout_dates) if model_family == "lightgbm_ridge_blend" else final_model.predict(X_holdout)
    timings["holdout_predict_s"] = round(time.perf_counter() - t_pred, 3)

    t_metrics = time.perf_counter()
    ic, rank_ic = compute_ic(y_holdout, pred_ho, holdout_dates)
    dec = decile_metrics(y_holdout, pred_ho, holdout_dates)
    timings["holdout_metrics_s"] = round(time.perf_counter() - t_metrics, 3)

    logger.info("=" * 60)
    logger.info("Holdout: IC=%.4f RankIC=%.4f top-avg=%.4f bot-avg=%.4f spread=%.4f wr_top=%.3f",
                ic, rank_ic, dec['top_avg'], dec['bot_avg'], dec['spread'], dec['winrate_top'])

    # feature importance
    fi = _log_top_features(feature_cols, final_model)

    # 落库: 训练完毕, 重新打开 writable connection
    logger.info("训练完成, 重新打开 DuckDB (writable) 落库...")
    conn = get_conn()
    ensure_model_schema(conn)
    pricing_policy = load_pricing_label_policy()
    model_id = f"{args.model_id_prefix}_{args.feature_group}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    t_write = time.perf_counter()
    _insert_model(
        conn,
        model_id=model_id,
        train_bounds=_date_bounds_array(panel.dates_for(train_idx)),
        valid_bounds=_date_bounds_array(panel.dates_for(valid_idx)),
        holdout_bounds=_date_bounds_array(panel.dates_for(holdout_idx)),
        feature_cols=feature_cols,
        best=best,
        ic=ic,
        rank_ic=rank_ic,
        dec=dec,
        fi=fi,
        label_name=args.label_name,
        schema_tag=schema_tag,
        pricing_policy_id=pricing_policy.policy_id,
        pricing_policy_hash=pricing_policy.policy_hash(),
        notes=_notes(args),
    )

    # 落 predictions
    _persist_prediction_arrays(conn, model_id, panel.codes_for(holdout_idx), panel.dates_for(holdout_idx), pred_ho)
    conn.commit()
    timings["db_write_s"] = round(time.perf_counter() - t_write, 3)

    # 保存 model pkl
    t_save = time.perf_counter()
    model_path = _save_model(model_id, final_model)
    timings["model_save_s"] = round(time.perf_counter() - t_save, 3)

    logger.info("模型保存: %s", model_path)
    duration_s = time.perf_counter() - run_t0
    timings["total_s"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=model_id,
        pipeline_name="train_multidim_model",
        status="success",
        started_at=run_started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            args.feature_table,
            *(["mart_model_selection_run"] if args.model_selection_run_id else []),
            *(
                ["data/alpha158.duckdb:fact_alpha158_panel"]
                if uses_alpha158_features
                else []
            ),
        ],
        output_tables=["mart_multidim_model", "mart_multidim_prediction"],
        model_id=model_id,
        feature_group=args.feature_group,
        label_name=args.label_name,
        holding_period=holding_period_from_label(args.label_name),
        perf_summary={
            "rows": panel.row_count,
            "n_train": int(len(train_idx)),
            "n_valid": int(len(valid_idx)),
            "n_holdout": int(len(holdout_idx)),
            "n_features": len(feature_cols),
            "model_selection_run_id": args.model_selection_run_id,
            "model_search_mode": model_search_mode,
            "model_family": model_family,
            "fixed_params_run_id": args.fixed_params_run_id,
            "trials": args.trials,
            "num_round": args.num_round,
            "objective_num_round": args.objective_num_round,
            "holdout_ic": ic,
            "holdout_rank_ic": rank_ic,
            "holdout_spread": dec["spread"],
            "cpu_count": os.cpu_count(),
            "num_threads": best.get("num_threads", "default"),
            "timings": timings,
            "excluded_feature_contracts": excluded_feature_contracts,
        },
    )
    conn.close()

    from services.ml_lifecycle.registry import propose_challenger

    propose_challenger(
        model_id,
        training_config={
            "feature_group": args.feature_group,
            "feature_table": args.feature_table,
            "feature_set_id": args.feature_set_id,
            "model_selection_run_id": args.model_selection_run_id,
            "source_selection_method": model_selection.get("method") if model_selection else None,
            "model_search_mode": model_search_mode,
            "model_family": model_family,
            "fixed_params_run_id": args.fixed_params_run_id,
            "fixed_params": fixed_params,
            "pricing_policy_id": pricing_policy.policy_id,
            "pricing_policy_hash": pricing_policy.policy_hash(),
            "label_name": args.label_name,
            "start": args.start,
            "end": args.end,
            "trials": args.trials,
            "num_round": args.num_round,
            "objective_num_round": args.objective_num_round,
            "excluded_feature_contracts": excluded_feature_contracts,
        },
        ic_holdout=rank_ic,
    )
    logger.info("训练总耗时 %.1f min", duration_s / 60)


if __name__ == "__main__":
    main()
