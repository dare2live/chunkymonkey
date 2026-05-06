#!/usr/bin/env python3
"""Optuna search for deployable LightGBM parameter stability.

This runner evaluates real holdout and rolling walk-forward metrics for a
selected feature set. It does not train or promote a lifecycle model; it writes
search evidence and the best fixed parameter set for later `train_multidim_model`
execution.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import numpy as np
import optuna
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from services.db import get_conn
from services.model_feature_schema import holding_period_from_label
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.schema_versions import record_actual_version
from scripts.run_feature_ablation import compute_ic, decile_metrics
from scripts.run_multidim_walkforward import (
    DEFAULT_PARAMS,
    DEGENERATE_DAILY_DISTINCT_THRESHOLD,
    _prediction_columns,
    _score_profile_columns,
    _slice_indices,
    build_folds,
)
from scripts.train_multidim_model import (
    _selection_schema_tag,
    load_model_selection_run,
    load_panel_arrays,
    resolve_feature_group_from_columns,
    split_time_series_indices,
)

optuna.logging.set_verbosity(optuna.logging.WARNING)


DEFAULT_STABLE_PARAMS = {
    "learning_rate": 0.035,
    "num_leaves": 31,
    "min_data_in_leaf": 1000,
    "feature_fraction": 0.85,
    "bagging_fraction": 0.85,
    "bagging_freq": 1,
    "lambda_l1": 0.3,
    "lambda_l2": 0.8,
    "max_depth": 5,
}
DEFAULT_RIDGE_PARAMS = {"alpha": 1.0}
DEFAULT_ELASTIC_NET_PARAMS = {
    "alpha": 0.01,
    "l1_ratio": 0.15,
    "max_iter": 2000,
}
DEFAULT_BLEND_PARAMS = {
    "ridge_weight": 0.70,
    "ridge_alpha": 1.0,
}
MODEL_FAMILIES = {"lightgbm", "lightgbm_ranker", "lightgbm_ridge_blend", "ridge", "elastic_net"}

DDL = """
CREATE TABLE IF NOT EXISTS mart_model_stability_search_trial (
    run_id TEXT NOT NULL,
    model_selection_run_id TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    model_family TEXT,
    topk_size INTEGER,
    objective_value DOUBLE,
    status TEXT,
    params_json TEXT,
    holdout_rank_ic DOUBLE,
    holdout_long_short_spread DOUBLE,
    holdout_winrate_top DOUBLE,
    holdout_topk_net_return DOUBLE,
    holdout_topk_turnover DOUBLE,
    holdout_topk_max_drawdown DOUBLE,
    walkforward_avg_rank_ic DOUBLE,
    walkforward_std_rank_ic DOUBLE,
    walkforward_avg_spread DOUBLE,
    walkforward_avg_topk_net_return DOUBLE,
    walkforward_avg_topk_turnover DOUBLE,
    walkforward_worst_topk_drawdown DOUBLE,
    holdout_feature_drift_psi_avg DOUBLE,
    holdout_feature_drift_psi_max DOUBLE,
    walkforward_avg_feature_drift_psi DOUBLE,
    walkforward_worst_feature_drift_psi DOUBLE,
    ok_folds INTEGER,
    fold_metrics_json TEXT,
    rejection_reason TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, trial_number)
);
CREATE INDEX IF NOT EXISTS idx_model_stability_search_run
    ON mart_model_stability_search_trial(run_id);
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS holdout_topk_net_return DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS holdout_topk_turnover DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS holdout_topk_max_drawdown DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS walkforward_avg_topk_net_return DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS walkforward_avg_topk_turnover DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS walkforward_worst_topk_drawdown DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS holdout_feature_drift_psi_avg DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS holdout_feature_drift_psi_max DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS walkforward_avg_feature_drift_psi DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS walkforward_worst_feature_drift_psi DOUBLE;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS model_family TEXT;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS topk_size INTEGER;
ALTER TABLE mart_model_stability_search_trial ADD COLUMN IF NOT EXISTS perf_summary_json TEXT;

CREATE TABLE IF NOT EXISTS mart_model_stability_search_summary (
    run_id TEXT PRIMARY KEY,
    model_selection_run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    feature_set_id TEXT,
    label_name TEXT,
    selected_features_json TEXT,
    best_trial_number INTEGER,
    best_params_json TEXT,
    objective_score DOUBLE,
    trials INTEGER,
    study_name TEXT,
    study_total_trials INTEGER,
    config_json TEXT,
    built_at TEXT
);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def default_optuna_storage_url() -> str:
    storage_dir = Path(__file__).resolve().parent.parent.parent / "data" / "optuna"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{storage_dir / 'model_stability_studies.sqlite3'}"


def _ensure_sqlite_storage_parent(storage_url: str | None) -> str | None:
    """Create parent directories for SQLite Optuna storage URLs."""
    if not storage_url or not storage_url.startswith("sqlite:///"):
        return storage_url
    raw_path = unquote(storage_url[len("sqlite:///") :])
    if not raw_path or raw_path == ":memory:":
        return storage_url
    Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return storage_url


def _parse_int_csv(value: str | None) -> list[int]:
    if not value:
        return []
    out = []
    for item in value.split(","):
        item = item.strip()
        if item:
            out.append(int(item))
    return out


def _mean(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(values) / len(values) if values else None


def _sample_std(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(values) < 2:
        return 0.0 if values else None
    avg = sum(values) / len(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


class PerfTimer:
    """Small cumulative timer for model-search bottleneck attribution."""

    def __init__(self) -> None:
        self.seconds: Counter[str] = Counter()
        self.counts: Counter[str] = Counter()

    @contextmanager
    def measure(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] += time.perf_counter() - start
            self.counts[name] += 1

    def add_count(self, name: str, value: int = 1) -> None:
        self.counts[name] += int(value)

    def merge(self, other: "PerfTimer") -> None:
        self.seconds.update(other.seconds)
        self.counts.update(other.counts)

    def summary(self) -> dict[str, Any]:
        return {
            "seconds": {
                f"{key}_s": round(float(value), 6)
                for key, value in sorted(self.seconds.items())
            },
            "counts": {key: int(value) for key, value in sorted(self.counts.items())},
        }


@dataclass
class RankerArtifacts:
    labels: np.ndarray
    dates: np.ndarray
    relevance: np.ndarray
    order: np.ndarray
    groups: list[int]


class RankerArtifactsCache:
    """Cache ranker labels, relevance bins, sorted indices, and group sizes."""

    def __init__(self) -> None:
        self._cache: dict[str, RankerArtifacts] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(indices: np.ndarray) -> str:
        arr = np.ascontiguousarray(np.asarray(indices, dtype=np.int64))
        digest = hashlib.blake2b(arr.tobytes(), digest_size=16).hexdigest()
        return f"{arr.size}:{digest}"

    def get(self, panel: Any, indices: np.ndarray) -> RankerArtifacts:
        key = self._key(indices)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        labels = panel.labels_for(indices)
        dates = panel.dates_for(indices)
        relevance = _ranker_relevance_by_date(labels, dates)
        order, groups = _ranker_group_order(dates)
        artifacts = RankerArtifacts(
            labels=labels,
            dates=dates,
            relevance=relevance,
            order=order,
            groups=groups,
        )
        self._cache[key] = artifacts
        return artifacts

    def summary(self) -> dict[str, Any]:
        group_counts = [len(item.groups) for item in self._cache.values()]
        max_group_sizes = [max(item.groups) for item in self._cache.values() if item.groups]
        row_counts = [int(item.labels.shape[0]) for item in self._cache.values()]
        return {
            "enabled": True,
            "entries": len(self._cache),
            "hits": self.hits,
            "misses": self.misses,
            "cached_rows": int(sum(row_counts)),
            "max_cached_rows": int(max(row_counts)) if row_counts else 0,
            "avg_group_count": _mean(group_counts),
            "max_group_size": int(max(max_group_sizes)) if max_group_sizes else 0,
        }


class EvaluationArtifactsCache:
    """Cache split-level arrays and parameter-independent metrics inside one run."""

    def __init__(
        self,
        *,
        cache_matrices: bool = False,
        cache_vectors: bool = True,
        cache_feature_drift: bool = True,
    ) -> None:
        self.cache_matrices = bool(cache_matrices)
        self.cache_vectors = bool(cache_vectors)
        self.cache_feature_drift = bool(cache_feature_drift)
        self._matrices: dict[tuple[tuple[str, ...], str], np.ndarray] = {}
        self._labels: dict[str, np.ndarray] = {}
        self._dates: dict[str, np.ndarray] = {}
        self._codes: dict[str, np.ndarray] = {}
        self._feature_drift: dict[tuple[tuple[str, ...], str, str, int], dict[str, Any]] = {}
        self.hits: Counter[str] = Counter()
        self.misses: Counter[str] = Counter()
        self.cached_matrix_rows = 0

    @staticmethod
    def _index_key(indices: np.ndarray) -> str:
        return RankerArtifactsCache._key(indices)

    @staticmethod
    def _feature_key(feature_cols: list[str]) -> tuple[str, ...]:
        return tuple(str(col) for col in feature_cols)

    def matrix(self, panel: Any, feature_cols: list[str], indices: np.ndarray) -> np.ndarray:
        if not self.cache_matrices:
            return panel.matrix(feature_cols, indices)
        key = (self._feature_key(feature_cols), self._index_key(indices))
        cached = self._matrices.get(key)
        if cached is not None:
            self.hits["matrix"] += 1
            return cached
        self.misses["matrix"] += 1
        matrix = panel.matrix(feature_cols, indices)
        self.cached_matrix_rows += int(matrix.shape[0])
        self._matrices[key] = matrix
        return matrix

    def labels_for(self, panel: Any, indices: np.ndarray) -> np.ndarray:
        if not self.cache_vectors:
            return panel.labels_for(indices)
        key = self._index_key(indices)
        cached = self._labels.get(key)
        if cached is not None:
            self.hits["labels"] += 1
            return cached
        self.misses["labels"] += 1
        labels = panel.labels_for(indices)
        self._labels[key] = labels
        return labels

    def dates_for(self, panel: Any, indices: np.ndarray) -> np.ndarray:
        if not self.cache_vectors:
            return panel.dates_for(indices)
        key = self._index_key(indices)
        cached = self._dates.get(key)
        if cached is not None:
            self.hits["dates"] += 1
            return cached
        self.misses["dates"] += 1
        dates = panel.dates_for(indices)
        self._dates[key] = dates
        return dates

    def codes_for(self, panel: Any, indices: np.ndarray) -> np.ndarray:
        if not self.cache_vectors:
            return panel.codes_for(indices)
        key = self._index_key(indices)
        cached = self._codes.get(key)
        if cached is not None:
            self.hits["codes"] += 1
            return cached
        self.misses["codes"] += 1
        codes = panel.codes_for(indices)
        self._codes[key] = codes
        return codes

    def feature_drift_metrics(
        self,
        panel: Any,
        feature_cols: list[str],
        reference_indices: np.ndarray,
        target_indices: np.ndarray,
        *,
        bins: int = 10,
    ) -> dict[str, Any]:
        if not self.cache_feature_drift:
            return _compute_feature_drift_metrics(
                panel,
                feature_cols,
                reference_indices,
                target_indices,
                bins=bins,
            )
        key = (
            self._feature_key(feature_cols),
            self._index_key(reference_indices),
            self._index_key(target_indices),
            int(bins),
        )
        cached = self._feature_drift.get(key)
        if cached is not None:
            self.hits["feature_drift"] += 1
            return cached
        self.misses["feature_drift"] += 1
        reference = self.matrix(panel, feature_cols, reference_indices)
        target = self.matrix(panel, feature_cols, target_indices)
        metrics = _feature_drift_from_matrices(reference, target, feature_cols, bins=bins)
        self._feature_drift[key] = metrics
        return metrics

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "cache_matrices": self.cache_matrices,
            "cache_vectors": self.cache_vectors,
            "cache_feature_drift": self.cache_feature_drift,
            "entries": {
                "matrix": len(self._matrices),
                "labels": len(self._labels),
                "dates": len(self._dates),
                "codes": len(self._codes),
                "feature_drift": len(self._feature_drift),
            },
            "hits": {key: int(value) for key, value in sorted(self.hits.items())},
            "misses": {key: int(value) for key, value in sorted(self.misses.items())},
            "cached_matrix_rows": int(self.cached_matrix_rows),
        }


def _default_params_for_family(model_family: str) -> dict[str, Any]:
    if model_family in {"lightgbm", "lightgbm_ranker"}:
        return dict(DEFAULT_STABLE_PARAMS)
    if model_family == "lightgbm_ridge_blend":
        params = dict(DEFAULT_STABLE_PARAMS)
        params.update(DEFAULT_BLEND_PARAMS)
        return params
    if model_family == "ridge":
        return dict(DEFAULT_RIDGE_PARAMS)
    if model_family == "elastic_net":
        return dict(DEFAULT_ELASTIC_NET_PARAMS)
    raise ValueError(f"unsupported model_family={model_family}")


def _runtime_params(params: dict[str, Any], *, model_family: str = "lightgbm", num_threads: int = 0) -> dict[str, Any]:
    if model_family not in MODEL_FAMILIES:
        raise ValueError(f"unsupported model_family={model_family}")
    if model_family == "ridge":
        return {
            "model_family": "ridge",
            "alpha": float(params.get("alpha", DEFAULT_RIDGE_PARAMS["alpha"])),
        }
    if model_family == "elastic_net":
        return {
            "model_family": "elastic_net",
            "alpha": float(params.get("alpha", DEFAULT_ELASTIC_NET_PARAMS["alpha"])),
            "l1_ratio": float(params.get("l1_ratio", DEFAULT_ELASTIC_NET_PARAMS["l1_ratio"])),
            "max_iter": int(params.get("max_iter", DEFAULT_ELASTIC_NET_PARAMS["max_iter"])),
        }
    if model_family == "lightgbm_ridge_blend":
        out = dict(DEFAULT_PARAMS)
        out.update({
            key: value
            for key, value in params.items()
            if key not in {"model_family", "ridge_weight", "ridge_alpha"}
        })
        out.update({
            "model_family": "lightgbm_ridge_blend",
            "objective": "regression",
            "metric": "rmse",
            "verbose": -1,
            "seed": 42,
            "feature_fraction_seed": 42,
            "bagging_seed": 42,
            "data_random_seed": 42,
            "feature_pre_filter": False,
            "ridge_weight": float(params.get("ridge_weight", DEFAULT_BLEND_PARAMS["ridge_weight"])),
            "ridge_alpha": float(params.get("ridge_alpha", DEFAULT_BLEND_PARAMS["ridge_alpha"])),
        })
        if num_threads > 0:
            out["num_threads"] = int(num_threads)
        return out
    out = dict(DEFAULT_PARAMS)
    out.update(params)
    if model_family == "lightgbm_ranker":
        out.update({
            "model_family": "lightgbm_ranker",
            "objective": "lambdarank",
            "metric": "ndcg",
            "label_gain": [0, 1, 3, 7, 15],
            "eval_at": [10],
            "verbose": -1,
            "seed": 42,
            "feature_fraction_seed": 42,
            "bagging_seed": 42,
            "data_random_seed": 42,
            "feature_pre_filter": False,
        })
        if num_threads > 0:
            out["num_threads"] = int(num_threads)
        return out
    out.update({
        "model_family": "lightgbm",
        "objective": "regression",
        "metric": "rmse",
        "verbose": -1,
        "seed": 42,
        "feature_fraction_seed": 42,
        "bagging_seed": 42,
        "data_random_seed": 42,
        "feature_pre_filter": False,
    })
    if num_threads > 0:
        out["num_threads"] = int(num_threads)
    return out


def _suggest_params(trial: optuna.Trial, *, model_family: str = "lightgbm") -> dict[str, Any]:
    if model_family == "ridge":
        return {"alpha": trial.suggest_float("alpha", 1e-3, 100.0, log=True)}
    if model_family == "elastic_net":
        return {
            "alpha": trial.suggest_float("alpha", 1e-4, 10.0, log=True),
            "l1_ratio": trial.suggest_float("l1_ratio", 0.05, 0.95),
            "max_iter": trial.suggest_categorical("max_iter", [1000, 2000, 4000]),
        }
    params = {
        "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.06),
        "num_leaves": trial.suggest_categorical("num_leaves", [15, 31, 45, 63]),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 600, 1400, step=100),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.70, 0.95, step=0.05),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.70, 0.95, step=0.05),
        "bagging_freq": trial.suggest_int("bagging_freq", 1, 3),
        "lambda_l1": trial.suggest_float("lambda_l1", 0.05, 1.0, log=True),
        "lambda_l2": trial.suggest_float("lambda_l2", 0.2, 2.0, log=True),
        "max_depth": trial.suggest_int("max_depth", 3, 6),
    }
    if model_family == "lightgbm_ridge_blend":
        params.update({
            "ridge_weight": trial.suggest_float("ridge_weight", 0.45, 0.85, step=0.05),
            "ridge_alpha": trial.suggest_float("ridge_alpha", 0.1, 10.0, log=True),
        })
    return params


def _ranker_relevance_by_date(
    labels: np.ndarray,
    dates: np.ndarray,
    *,
    bins: int = 5,
) -> np.ndarray:
    labels = np.asarray(labels, dtype=np.float64)
    dates = np.asarray(dates, dtype=object)
    bins = max(int(bins), 2)
    relevance = np.zeros(labels.shape[0], dtype=np.int32)
    if labels.size == 0:
        return relevance
    order_by_date = np.argsort(dates.astype(str), kind="mergesort")
    ordered_dates = dates[order_by_date].astype(str)
    starts = np.r_[0, np.flatnonzero(ordered_dates[1:] != ordered_dates[:-1]) + 1]
    ends = np.r_[starts[1:], len(order_by_date)]
    for start, end in zip(starts, ends):
        idx = order_by_date[start:end]
        values = labels[idx]
        finite = np.isfinite(values)
        if finite.sum() < 2:
            continue
        finite_idx = idx[finite]
        order = finite_idx[np.argsort(values[finite], kind="mergesort")]
        if float(values[finite].max()) == float(values[finite].min()):
            continue
        denom = max(len(order) - 1, 1)
        for rank_pos, row_idx in enumerate(order):
            relevance[row_idx] = int(round(rank_pos / denom * (bins - 1)))
    return relevance


def _ranker_group_order(dates: np.ndarray) -> tuple[np.ndarray, list[int]]:
    dates = np.asarray(dates, dtype=object)
    order = np.argsort(dates.astype(str), kind="mergesort")
    ordered_dates = dates[order]
    _unique_dates, counts = np.unique(ordered_dates.astype(str), return_counts=True)
    groups = counts.astype(int).tolist()
    return order, groups


def _cross_sectional_zscore(values: np.ndarray, dates: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    dates = np.asarray(dates, dtype=object)
    date_keys = dates.astype(str)
    out = np.zeros(values.shape[0], dtype=np.float64)
    for day in np.unique(date_keys):
        idx = np.flatnonzero(date_keys == day)
        if idx.size == 0:
            continue
        day_values = values[idx]
        finite = np.isfinite(day_values)
        if finite.sum() < 2:
            continue
        mean = float(np.mean(day_values[finite]))
        std = float(np.std(day_values[finite]))
        if not math.isfinite(std) or std < 1e-12:
            continue
        out[idx[finite]] = (day_values[finite] - mean) / std
    return out


def _blend_predictions_by_date(
    lightgbm_pred: np.ndarray,
    ridge_pred: np.ndarray,
    dates: np.ndarray,
    *,
    ridge_weight: float,
) -> np.ndarray:
    """Blend two score vectors after per-date cross-sectional normalization."""

    weight = min(max(float(ridge_weight), 0.0), 1.0)
    lgb_z = _cross_sectional_zscore(lightgbm_pred, dates)
    ridge_z = _cross_sectional_zscore(ridge_pred, dates)
    return (1.0 - weight) * lgb_z + weight * ridge_z


def _fit_predict(
    panel: Any,
    feature_cols: list[str],
    train_indices: np.ndarray,
    test_indices: np.ndarray,
    params: dict[str, Any],
    *,
    num_round: int,
    model_family: str = "lightgbm",
    ranker_cache: RankerArtifactsCache | None = None,
    eval_cache: EvaluationArtifactsCache | None = None,
    perf_timer: PerfTimer | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    timer = perf_timer or PerfTimer()
    precomputed_test_dates: np.ndarray | None = None

    def matrix(indices: np.ndarray) -> np.ndarray:
        if eval_cache is not None:
            return eval_cache.matrix(panel, feature_cols, indices)
        return panel.matrix(feature_cols, indices)

    def labels(indices: np.ndarray) -> np.ndarray:
        if eval_cache is not None:
            return eval_cache.labels_for(panel, indices)
        return panel.labels_for(indices)

    def dates(indices: np.ndarray) -> np.ndarray:
        if eval_cache is not None:
            return eval_cache.dates_for(panel, indices)
        return panel.dates_for(indices)

    with timer.measure("matrix_test"):
        x_test = matrix(test_indices)
    if model_family == "lightgbm":
        with timer.measure("matrix_train"):
            x_train = matrix(train_indices)
        with timer.measure("label_train"):
            y_train = labels(train_indices)
        lgb_params = {key: value for key, value in params.items() if key != "model_family"}
        with timer.measure("train"):
            model = lgb.train(
                lgb_params,
                lgb.Dataset(
                    x_train,
                    label=y_train,
                    feature_name=feature_cols,
                ),
                num_boost_round=num_round,
            )
        with timer.measure("predict"):
            pred = model.predict(x_test)
    elif model_family == "lightgbm_ridge_blend":
        with timer.measure("matrix_train"):
            x_train = matrix(train_indices)
        with timer.measure("label_train"):
            y_train = labels(train_indices)
        lgb_params = {
            key: value
            for key, value in params.items()
            if key not in {"model_family", "ridge_weight", "ridge_alpha"}
        }
        with timer.measure("train"):
            lgb_model = lgb.train(
                lgb_params,
                lgb.Dataset(
                    x_train,
                    label=y_train,
                    feature_name=feature_cols,
                ),
                num_boost_round=num_round,
            )
            ridge_model = make_pipeline(
                StandardScaler(),
                Ridge(alpha=float(params.get("ridge_alpha", DEFAULT_BLEND_PARAMS["ridge_alpha"]))),
            )
            ridge_model.fit(x_train, y_train)
        with timer.measure("predict"):
            lightgbm_pred = lgb_model.predict(x_test)
            ridge_pred = ridge_model.predict(x_test)
        with timer.measure("date_test"):
            precomputed_test_dates = dates(test_indices)
        pred = _blend_predictions_by_date(
            lightgbm_pred,
            ridge_pred,
            precomputed_test_dates,
            ridge_weight=float(params.get("ridge_weight", DEFAULT_BLEND_PARAMS["ridge_weight"])),
        )
    elif model_family == "lightgbm_ranker":
        lgb_params = {key: value for key, value in params.items() if key != "model_family"}
        cache = ranker_cache or RankerArtifactsCache()
        with timer.measure("ranker_artifacts"):
            artifacts = cache.get(panel, train_indices)
        with timer.measure("ranker_matrix_order"):
            ordered_label = artifacts.relevance[artifacts.order]
            ordered_indices = np.asarray(train_indices)[artifacts.order]
        with timer.measure("matrix_train"):
            ordered_train = matrix(ordered_indices)
        with timer.measure("train"):
            model = lgb.train(
                lgb_params,
                lgb.Dataset(
                    ordered_train,
                    label=ordered_label,
                    group=artifacts.groups,
                    feature_name=feature_cols,
                ),
                num_boost_round=num_round,
            )
        with timer.measure("predict"):
            pred = model.predict(x_test)
    elif model_family == "ridge":
        with timer.measure("matrix_train"):
            x_train = matrix(train_indices)
        with timer.measure("label_train"):
            y_train = labels(train_indices)
        model = make_pipeline(
            StandardScaler(),
            Ridge(alpha=float(params.get("alpha", DEFAULT_RIDGE_PARAMS["alpha"]))),
        )
        with timer.measure("train"):
            model.fit(x_train, y_train)
        with timer.measure("predict"):
            pred = model.predict(x_test)
    elif model_family == "elastic_net":
        with timer.measure("matrix_train"):
            x_train = matrix(train_indices)
        with timer.measure("label_train"):
            y_train = labels(train_indices)
        model = make_pipeline(
            StandardScaler(),
            ElasticNet(
                alpha=float(params.get("alpha", DEFAULT_ELASTIC_NET_PARAMS["alpha"])),
                l1_ratio=float(params.get("l1_ratio", DEFAULT_ELASTIC_NET_PARAMS["l1_ratio"])),
                max_iter=int(params.get("max_iter", DEFAULT_ELASTIC_NET_PARAMS["max_iter"])),
                random_state=42,
            ),
        )
        with timer.measure("train"):
            model.fit(x_train, y_train)
        with timer.measure("predict"):
            pred = model.predict(x_test)
    else:
        raise ValueError(f"unsupported model_family={model_family}")
    with timer.measure("label_test"):
        y_test = labels(test_indices)
    if precomputed_test_dates is None:
        with timer.measure("date_test"):
            test_dates = dates(test_indices)
    else:
        test_dates = precomputed_test_dates
    return pred, y_test, test_dates


def _quality_from_predictions(
    pred: np.ndarray,
    codes: np.ndarray,
    dates: np.ndarray,
    distinct_threshold: int,
) -> tuple[str, float, int]:
    pred_columns = _prediction_columns("_model_stability", 0, codes, dates, pred)
    distinct_median, distinct_min = _score_profile_columns(pred_columns)
    quality = "ok" if distinct_median >= distinct_threshold else "degenerate"
    return quality, float(distinct_median), int(distinct_min)


def _topk_portfolio_metrics(
    pred: np.ndarray,
    y: np.ndarray,
    dates: np.ndarray,
    codes: np.ndarray,
    *,
    topk_size: int,
    cost_bps: float,
) -> dict[str, Any]:
    """Equal-weight per-date topK return, turnover cost, and drawdown."""

    topk_size = max(int(topk_size), 1)
    cost_rate = max(float(cost_bps), 0.0) / 10000.0
    unique_dates = sorted(np.unique(dates).tolist())
    previous_codes: set[str] | None = None
    daily_rows = []
    cumulative = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for day in unique_dates:
        idx = np.flatnonzero(dates == day)
        if len(idx) == 0:
            continue
        order = idx[np.argsort(pred[idx])[::-1]][: min(topk_size, len(idx))]
        selected_codes = {str(code) for code in codes[order]}
        gross = float(np.mean(y[order])) if len(order) else 0.0
        if previous_codes is None:
            turnover = 1.0
        else:
            overlap = len(previous_codes.intersection(selected_codes))
            denom = max(len(previous_codes), len(selected_codes), 1)
            turnover = 1.0 - overlap / denom
        net = gross - turnover * cost_rate
        cumulative *= 1.0 + net
        peak = max(peak, cumulative)
        drawdown = cumulative / peak - 1.0 if peak > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        daily_rows.append(
            {
                "date": str(day),
                "gross_return": gross,
                "turnover": turnover,
                "net_return": net,
                "cumulative": cumulative,
                "drawdown": drawdown,
            }
        )
        previous_codes = selected_codes
    return {
        "topk_size": topk_size,
        "cost_bps": float(cost_bps),
        "periods": len(daily_rows),
        "topk_gross_return": _mean(row["gross_return"] for row in daily_rows),
        "topk_net_return": _mean(row["net_return"] for row in daily_rows),
        "topk_turnover": _mean(row["turnover"] for row in daily_rows),
        "topk_max_drawdown": max_drawdown,
        "topk_daily_json": daily_rows,
    }


def _psi_1d(reference: np.ndarray, target: np.ndarray, *, bins: int = 10) -> float | None:
    reference = np.asarray(reference, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    reference = reference[np.isfinite(reference)]
    target = target[np.isfinite(target)]
    if reference.size < 2 or target.size < 2:
        return None
    quantiles = np.linspace(0.0, 1.0, max(int(bins), 2) + 1)
    edges = np.unique(np.quantile(reference, quantiles))
    if edges.size < 2:
        return 0.0
    edges = edges.astype(np.float64, copy=True)
    edges[0] = -np.inf
    edges[-1] = np.inf
    ref_counts, _ = np.histogram(reference, bins=edges)
    tgt_counts, _ = np.histogram(target, bins=edges)
    eps = 1e-6
    ref_pct = np.maximum(ref_counts.astype(np.float64) / max(float(ref_counts.sum()), eps), eps)
    tgt_pct = np.maximum(tgt_counts.astype(np.float64) / max(float(tgt_counts.sum()), eps), eps)
    return float(np.sum((tgt_pct - ref_pct) * np.log(tgt_pct / ref_pct)))


def _feature_drift_from_matrices(
    reference: np.ndarray,
    target: np.ndarray,
    feature_cols: list[str],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    psi_by_feature: dict[str, float] = {}
    for idx, feature in enumerate(feature_cols):
        psi = _psi_1d(reference[:, idx], target[:, idx], bins=bins)
        if psi is not None and math.isfinite(psi):
            psi_by_feature[feature] = psi
    values = list(psi_by_feature.values())
    return {
        "feature_drift_psi_avg": _mean(values),
        "feature_drift_psi_max": max(values) if values else None,
        "feature_drift_psi_by_feature": psi_by_feature,
    }


def _compute_feature_drift_metrics(
    panel: Any,
    feature_cols: list[str],
    reference_indices: np.ndarray,
    target_indices: np.ndarray,
    *,
    bins: int = 10,
) -> dict[str, Any]:
    reference = panel.matrix(feature_cols, reference_indices)
    target = panel.matrix(feature_cols, target_indices)
    return _feature_drift_from_matrices(reference, target, feature_cols, bins=bins)


def _feature_drift_metrics(
    panel: Any,
    feature_cols: list[str],
    reference_indices: np.ndarray,
    target_indices: np.ndarray,
    *,
    bins: int = 10,
    eval_cache: EvaluationArtifactsCache | None = None,
) -> dict[str, Any]:
    if eval_cache is not None:
        return eval_cache.feature_drift_metrics(
            panel,
            feature_cols,
            reference_indices,
            target_indices,
            bins=bins,
        )
    return _compute_feature_drift_metrics(
        panel,
        feature_cols,
        reference_indices,
        target_indices,
        bins=bins,
    )


def _evaluation_splits(
    panel: Any,
    folds: list[dict[str, Any]],
    eval_plan: dict[str, Any] | None = None,
    perf_timer: PerfTimer | None = None,
) -> dict[str, Any]:
    if eval_plan is not None:
        return eval_plan
    timer = perf_timer or PerfTimer()
    with timer.measure("split_indices"):
        train_idx, valid_idx, holdout_idx = split_time_series_indices(panel.dates)
        fold_splits = []
        for fold in folds:
            train = _slice_indices(panel.dates, fold["train"])
            valid = _slice_indices(panel.dates, fold["valid"])
            test = _slice_indices(panel.dates, fold["test"])
            fold_splits.append(
                {
                    "fold_id": int(fold["fold_id"]),
                    "train_valid": np.concatenate([train, valid]),
                    "test": test,
                    "test_start": fold["test"][0],
                    "test_end": fold["test"][1],
                }
            )
    return {
        "holdout_train_valid": np.concatenate([train_idx, valid_idx]),
        "holdout": holdout_idx,
        "folds": fold_splits,
    }


def prepare_evaluation_plan(
    panel: Any,
    folds: list[dict[str, Any]],
    *,
    model_family: str = "lightgbm",
    ranker_cache: RankerArtifactsCache | None = None,
    perf_timer: PerfTimer | None = None,
) -> dict[str, Any]:
    timer = perf_timer or PerfTimer()
    plan = _evaluation_splits(panel, folds, perf_timer=timer)
    if model_family == "lightgbm_ranker" and ranker_cache is not None:
        with timer.measure("ranker_cache_warmup"):
            ranker_cache.get(panel, plan["holdout_train_valid"])
            for fold in plan["folds"]:
                ranker_cache.get(panel, fold["train_valid"])
    return plan


def evaluate_params(
    panel: Any,
    feature_cols: list[str],
    params: dict[str, Any],
    *,
    folds: list[dict[str, Any]],
    num_round: int,
    distinct_threshold: int = DEGENERATE_DAILY_DISTINCT_THRESHOLD,
    topk_size: int = 50,
    cost_bps: float = 10.0,
    drift_bins: int = 10,
    model_family: str = "lightgbm",
    eval_plan: dict[str, Any] | None = None,
    ranker_cache: RankerArtifactsCache | None = None,
    eval_cache: EvaluationArtifactsCache | None = None,
    perf_timer: PerfTimer | None = None,
) -> dict[str, Any]:
    timer = perf_timer or PerfTimer()
    splits = _evaluation_splits(panel, folds, eval_plan=eval_plan, perf_timer=timer)
    train_valid_idx = splits["holdout_train_valid"]
    holdout_idx = splits["holdout"]
    holdout_pred, holdout_y, holdout_dates = _fit_predict(
        panel,
        feature_cols,
        train_valid_idx,
        holdout_idx,
        params,
        num_round=num_round,
        model_family=model_family,
        ranker_cache=ranker_cache,
        eval_cache=eval_cache,
        perf_timer=timer,
    )
    with timer.measure("metrics_ic"):
        holdout_ic, holdout_rank_ic = compute_ic(holdout_y, holdout_pred, holdout_dates)
    with timer.measure("metrics_decile"):
        holdout_dec = decile_metrics(holdout_y, holdout_pred, holdout_dates)
    with timer.measure("codes_test"):
        holdout_codes = (
            eval_cache.codes_for(panel, holdout_idx)
            if eval_cache is not None
            else panel.codes_for(holdout_idx)
        )
    with timer.measure("metrics_topk"):
        holdout_topk = _topk_portfolio_metrics(
            holdout_pred,
            holdout_y,
            holdout_dates,
            holdout_codes,
            topk_size=topk_size,
            cost_bps=cost_bps,
        )
    with timer.measure("metrics_feature_drift"):
        holdout_drift = _feature_drift_metrics(
            panel,
            feature_cols,
            train_valid_idx,
            holdout_idx,
            bins=drift_bins,
            eval_cache=eval_cache,
        )
    fold_metrics: list[dict[str, Any]] = []
    for fold in splits["folds"]:
        train_valid = fold["train_valid"]
        test = fold["test"]
        pred, y_test, test_dates = _fit_predict(
            panel,
            feature_cols,
            train_valid,
            test,
            params,
            num_round=num_round,
            model_family=model_family,
            ranker_cache=ranker_cache,
            eval_cache=eval_cache,
            perf_timer=timer,
        )
        with timer.measure("metrics_ic"):
            ic, rank_ic = compute_ic(y_test, pred, test_dates)
        with timer.measure("metrics_decile"):
            dec = decile_metrics(y_test, pred, test_dates)
        with timer.measure("codes_test"):
            test_codes = (
                eval_cache.codes_for(panel, test)
                if eval_cache is not None
                else panel.codes_for(test)
            )
        with timer.measure("metrics_topk"):
            topk = _topk_portfolio_metrics(
                pred,
                y_test,
                test_dates,
                test_codes,
                topk_size=topk_size,
                cost_bps=cost_bps,
            )
        with timer.measure("metrics_feature_drift"):
            drift = _feature_drift_metrics(
                panel,
                feature_cols,
                train_valid,
                test,
                bins=drift_bins,
                eval_cache=eval_cache,
            )
        with timer.measure("metrics_quality"):
            quality, distinct_median, distinct_min = _quality_from_predictions(
                pred,
                test_codes,
                test_dates,
                distinct_threshold,
            )
        fold_metrics.append({
            "fold_id": int(fold["fold_id"]),
            "rank_ic": rank_ic,
            "ic": ic,
            "spread": dec["spread"],
            "winrate_top": dec["winrate_top"],
            "topk_net_return": topk["topk_net_return"],
            "topk_turnover": topk["topk_turnover"],
            "topk_max_drawdown": topk["topk_max_drawdown"],
            "feature_drift_psi_avg": drift["feature_drift_psi_avg"],
            "feature_drift_psi_max": drift["feature_drift_psi_max"],
            "feature_drift_psi_by_feature": drift["feature_drift_psi_by_feature"],
            "quality": quality,
            "daily_distinct_score_median": distinct_median,
            "daily_distinct_score_min": distinct_min,
            "test_start": fold["test_start"],
            "test_end": fold["test_end"],
        })
    ok_rank_ics = [float(row["rank_ic"]) for row in fold_metrics if row.get("quality") == "ok" and row.get("rank_ic") is not None]
    ok_spreads = [float(row["spread"]) for row in fold_metrics if row.get("quality") == "ok" and row.get("spread") is not None]
    ok_topk_returns = [float(row["topk_net_return"]) for row in fold_metrics if row.get("quality") == "ok" and row.get("topk_net_return") is not None]
    ok_turnovers = [float(row["topk_turnover"]) for row in fold_metrics if row.get("quality") == "ok" and row.get("topk_turnover") is not None]
    ok_drawdowns = [float(row["topk_max_drawdown"]) for row in fold_metrics if row.get("quality") == "ok" and row.get("topk_max_drawdown") is not None]
    ok_drift = [float(row["feature_drift_psi_max"]) for row in fold_metrics if row.get("quality") == "ok" and row.get("feature_drift_psi_max") is not None]
    return {
        "holdout_ic": holdout_ic,
        "holdout_rank_ic": holdout_rank_ic,
        "holdout_long_short_spread": holdout_dec["spread"],
        "holdout_winrate_top": holdout_dec["winrate_top"],
        "holdout_topk_net_return": holdout_topk["topk_net_return"],
        "holdout_topk_turnover": holdout_topk["topk_turnover"],
        "holdout_topk_max_drawdown": holdout_topk["topk_max_drawdown"],
        "holdout_feature_drift_psi_avg": holdout_drift["feature_drift_psi_avg"],
        "holdout_feature_drift_psi_max": holdout_drift["feature_drift_psi_max"],
        "holdout_feature_drift_psi_by_feature": holdout_drift["feature_drift_psi_by_feature"],
        "walkforward_avg_rank_ic": _mean(ok_rank_ics),
        "walkforward_std_rank_ic": _sample_std(ok_rank_ics),
        "walkforward_avg_spread": _mean(ok_spreads),
        "walkforward_avg_topk_net_return": _mean(ok_topk_returns),
        "walkforward_avg_topk_turnover": _mean(ok_turnovers),
        "walkforward_worst_topk_drawdown": min(ok_drawdowns) if ok_drawdowns else None,
        "walkforward_avg_feature_drift_psi": _mean(ok_drift),
        "walkforward_worst_feature_drift_psi": max(ok_drift) if ok_drift else None,
        "ok_folds": len(ok_rank_ics),
        "fold_metrics": fold_metrics,
    }


def score_metrics(
    metrics: dict[str, Any],
    *,
    min_holdout_rank_ic: float,
    min_holdout_spread: float,
    min_walkforward_avg_rank_ic: float,
    max_walkforward_std_rank_ic: float,
    min_ok_folds: int,
    max_topk_drawdown: float = 1.00,
    max_feature_drift_psi: float = 1.00,
) -> tuple[float, str, str | None]:
    reasons: list[str] = []
    holdout_rank_ic = float(metrics.get("holdout_rank_ic") or 0.0)
    holdout_spread = float(metrics.get("holdout_long_short_spread") or 0.0)
    holdout_winrate = float(metrics.get("holdout_winrate_top") or 0.0)
    wf_avg = float(metrics.get("walkforward_avg_rank_ic") or 0.0)
    wf_std = float(metrics.get("walkforward_std_rank_ic") or 0.0)
    wf_spread = float(metrics.get("walkforward_avg_spread") or 0.0)
    holdout_topk_net = float(metrics.get("holdout_topk_net_return") or 0.0)
    wf_topk_net = float(metrics.get("walkforward_avg_topk_net_return") or 0.0)
    wf_topk_turnover = float(metrics.get("walkforward_avg_topk_turnover") or 0.0)
    wf_topk_drawdown = float(metrics.get("walkforward_worst_topk_drawdown") or 0.0)
    holdout_drift = float(metrics.get("holdout_feature_drift_psi_max") or 0.0)
    wf_drift = float(metrics.get("walkforward_worst_feature_drift_psi") or 0.0)
    drift_excess = max(0.0, holdout_drift - max_feature_drift_psi, wf_drift - max_feature_drift_psi)
    ok_folds = int(metrics.get("ok_folds") or 0)
    if ok_folds < min_ok_folds:
        reasons.append(f"ok_folds {ok_folds} < {min_ok_folds}")
    if holdout_rank_ic < min_holdout_rank_ic:
        reasons.append(f"holdout_rank_ic {holdout_rank_ic:.6f} < {min_holdout_rank_ic:.6f}")
    if holdout_spread < min_holdout_spread:
        reasons.append(f"holdout_spread {holdout_spread:.6f} < {min_holdout_spread:.6f}")
    if wf_avg < min_walkforward_avg_rank_ic:
        reasons.append(f"walkforward_avg_rank_ic {wf_avg:.6f} < {min_walkforward_avg_rank_ic:.6f}")
    if wf_std > max_walkforward_std_rank_ic:
        reasons.append(f"walkforward_std_rank_ic {wf_std:.6f} > {max_walkforward_std_rank_ic:.6f}")
    if wf_spread <= 0:
        reasons.append(f"walkforward_avg_spread {wf_spread:.6f} <= 0")
    if wf_topk_drawdown < -abs(max_topk_drawdown):
        reasons.append(f"walkforward_topk_drawdown {wf_topk_drawdown:.6f} < -{abs(max_topk_drawdown):.6f}")
    if holdout_drift > max_feature_drift_psi:
        reasons.append(f"holdout_feature_drift_psi {holdout_drift:.6f} > {max_feature_drift_psi:.6f}")
    if wf_drift > max_feature_drift_psi:
        reasons.append(f"walkforward_feature_drift_psi {wf_drift:.6f} > {max_feature_drift_psi:.6f}")
    score = (
        0.30 * wf_avg
        + 0.20 * holdout_rank_ic
        + 0.15 * ((holdout_spread + wf_spread) / 2.0)
        + 0.15 * ((holdout_topk_net + wf_topk_net) / 2.0)
        + 0.10 * holdout_winrate
        - 0.25 * max(0.0, wf_std - max_walkforward_std_rank_ic)
        - 0.10 * max(0.0, abs(min(wf_topk_drawdown, 0.0)) - abs(max_topk_drawdown))
        - 0.08 * drift_excess
        - 0.02 * max(0.0, wf_topk_turnover - 0.5)
        - 0.05 * max(0, min_ok_folds - ok_folds)
    )
    return float(score), ("pass" if not reasons else "fail"), "; ".join(reasons) if reasons else None


def run_optuna_model_stability_search(
    conn: Any,
    *,
    model_selection_run_id: str,
    run_id: str | None = None,
    start: str = "2025-01-01",
    end: str = "2025-12-31",
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    label_name: str = "forward_ret_20d",
    trials: int = 16,
    seed: int = 42,
    num_round: int = 80,
    num_threads: int = 4,
    train_days: int = 60,
    valid_days: int = 20,
    test_days: int = 20,
    step_days: int = 20,
    max_folds: int = 6,
    min_holdout_rank_ic: float = 0.0424,
    min_holdout_spread: float = 0.0092,
    min_walkforward_avg_rank_ic: float = 0.015,
    max_walkforward_std_rank_ic: float = 0.03,
    min_ok_folds: int = 4,
    topk_size: int = 50,
    topk_size_choices: list[int] | None = None,
    cost_bps: float = 10.0,
    max_topk_drawdown: float = 1.00,
    max_feature_drift_psi: float = 1.00,
    drift_bins: int = 10,
    model_family: str = "lightgbm",
    storage_url: str | None = None,
    study_name: str | None = None,
    load_if_exists: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    storage_url = _ensure_sqlite_storage_parent(storage_url)
    run_id = run_id or f"model_stability_search_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    if model_family not in MODEL_FAMILIES:
        raise ValueError(f"unsupported model_family={model_family}")
    study_name = study_name or run_id
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    run_timer = PerfTimer()
    with run_timer.measure("load_model_selection"):
        selection = load_model_selection_run(conn, model_selection_run_id)
    selected_features = list(selection["selected_features"])
    with run_timer.measure("load_panel_arrays"):
        panel = load_panel_arrays(
            conn,
            start,
            end,
            label_name=label_name,
            feature_table=feature_table,
            feature_set_id=feature_set_id,
            with_alpha158=any(col.startswith("a158_") for col in selected_features),
            requested_feature_cols=selected_features,
            only_requested_feature_cols=True,
        )
    with run_timer.measure("resolve_feature_group"):
        feature_cols, schema_tag = resolve_feature_group_from_columns(
            "model_selection_run",
            panel.columns,
            regime_aware=False,
            selection_features=selected_features,
            selection_schema_tag=_selection_schema_tag(model_selection_run_id),
        )
    with run_timer.measure("build_folds"):
        dates = sorted(np.unique(panel.dates).tolist())
        folds = build_folds(dates, train_days, valid_days, test_days, step_days)
        if max_folds > 0:
            folds = folds[:max_folds]
    ranker_cache = RankerArtifactsCache() if model_family == "lightgbm_ranker" else None
    eval_cache = EvaluationArtifactsCache(
        cache_matrices=model_family == "lightgbm_ranker",
        cache_vectors=True,
        cache_feature_drift=True,
    )
    eval_plan = prepare_evaluation_plan(
        panel,
        folds,
        model_family=model_family,
        ranker_cache=ranker_cache,
        perf_timer=run_timer,
    )
    trial_rows: list[tuple] = []
    topk_size_choices = [int(size) for size in (topk_size_choices or []) if int(size) > 0]

    def evaluate_trial(
        trial_number: int,
        params: dict[str, Any],
        *,
        trial_topk_size: int,
    ) -> tuple[float, str, str | None, dict[str, Any], dict[str, Any], int]:
        runtime_params = _runtime_params(params, model_family=model_family, num_threads=num_threads)
        trial_timer = PerfTimer()
        with trial_timer.measure("evaluate_params"):
            metrics = evaluate_params(
                panel,
                feature_cols,
                runtime_params,
                folds=folds,
                num_round=num_round,
                topk_size=trial_topk_size,
                cost_bps=cost_bps,
                drift_bins=drift_bins,
                model_family=model_family,
                eval_plan=eval_plan,
                ranker_cache=ranker_cache,
                eval_cache=eval_cache,
                perf_timer=trial_timer,
            )
        with trial_timer.measure("score_metrics"):
            score, status, reason = score_metrics(
                metrics,
                min_holdout_rank_ic=min_holdout_rank_ic,
                min_holdout_spread=min_holdout_spread,
                min_walkforward_avg_rank_ic=min_walkforward_avg_rank_ic,
                max_walkforward_std_rank_ic=max_walkforward_std_rank_ic,
                min_ok_folds=min_ok_folds,
                max_topk_drawdown=max_topk_drawdown,
                max_feature_drift_psi=max_feature_drift_psi,
            )
        run_timer.merge(trial_timer)
        trial_perf = trial_timer.summary()
        trial_rows.append((
            run_id,
            model_selection_run_id,
            int(trial_number),
            model_family,
            int(trial_topk_size),
            score,
            status,
            json.dumps(runtime_params, ensure_ascii=False, sort_keys=True),
            metrics.get("holdout_rank_ic"),
            metrics.get("holdout_long_short_spread"),
            metrics.get("holdout_winrate_top"),
            metrics.get("holdout_topk_net_return"),
            metrics.get("holdout_topk_turnover"),
            metrics.get("holdout_topk_max_drawdown"),
            metrics.get("walkforward_avg_rank_ic"),
            metrics.get("walkforward_std_rank_ic"),
            metrics.get("walkforward_avg_spread"),
            metrics.get("walkforward_avg_topk_net_return"),
            metrics.get("walkforward_avg_topk_turnover"),
            metrics.get("walkforward_worst_topk_drawdown"),
            metrics.get("holdout_feature_drift_psi_avg"),
            metrics.get("holdout_feature_drift_psi_max"),
            metrics.get("walkforward_avg_feature_drift_psi"),
            metrics.get("walkforward_worst_feature_drift_psi"),
            metrics.get("ok_folds"),
            json.dumps(metrics.get("fold_metrics") or [], ensure_ascii=False),
            json.dumps(trial_perf, ensure_ascii=False, sort_keys=True),
            reason,
            built_at,
        ))
        return score, status, reason, runtime_params, metrics, int(trial_topk_size)

    if trials <= 0:
        policy_sizes = topk_size_choices or [int(topk_size)]
        evaluated = [
            evaluate_trial(
                idx,
                _default_params_for_family(model_family),
                trial_topk_size=size,
            )
            for idx, size in enumerate(policy_sizes)
        ]
        best_idx, best_tuple = max(enumerate(evaluated), key=lambda item: item[1][0])
        best_score, best_status, best_reason, best_params, best_metrics, best_topk_size = best_tuple
        best_trial_number = best_idx
        study_total_trials = len(evaluated)
    else:
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=seed),
            storage=storage_url,
            study_name=study_name if storage_url else None,
            load_if_exists=load_if_exists if storage_url else False,
        )

        def objective(trial: optuna.Trial) -> float:
            trial_topk_size = (
                int(trial.suggest_categorical("topk_size", topk_size_choices))
                if topk_size_choices
                else int(topk_size)
            )
            score, status, reason, params, metrics, trial_topk_size = evaluate_trial(
                trial.number,
                _suggest_params(trial, model_family=model_family),
                trial_topk_size=trial_topk_size,
            )
            trial.set_user_attr("status", status)
            trial.set_user_attr("rejection_reason", reason)
            trial.set_user_attr("runtime_params", params)
            trial.set_user_attr("metrics", metrics)
            trial.set_user_attr("topk_size", trial_topk_size)
            return score

        study.optimize(objective, n_trials=trials, show_progress_bar=False)
        best = study.best_trial
        best_trial_number = int(best.number)
        best_score = float(best.value)
        best_status = str(best.user_attrs.get("status") or "unknown")
        best_reason = best.user_attrs.get("rejection_reason")
        best_params = dict(best.user_attrs.get("runtime_params") or {})
        best_metrics = dict(best.user_attrs.get("metrics") or {})
        best_topk_size = int(best.user_attrs.get("topk_size") or topk_size)
        study_total_trials = len(study.trials)

    if trial_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_model_stability_search_trial
            (run_id, model_selection_run_id, trial_number, model_family, topk_size, objective_value,
             status, params_json, holdout_rank_ic, holdout_long_short_spread,
             holdout_winrate_top, holdout_topk_net_return,
             holdout_topk_turnover, holdout_topk_max_drawdown,
             walkforward_avg_rank_ic, walkforward_std_rank_ic,
             walkforward_avg_spread, walkforward_avg_topk_net_return,
             walkforward_avg_topk_turnover, walkforward_worst_topk_drawdown,
             holdout_feature_drift_psi_avg, holdout_feature_drift_psi_max,
             walkforward_avg_feature_drift_psi, walkforward_worst_feature_drift_psi,
             ok_folds,
             fold_metrics_json, perf_summary_json, rejection_reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            trial_rows,
        )
    config = {
        "start": start,
        "end": end,
        "schema_tag": schema_tag,
        "model_family": model_family,
        "train_days": train_days,
        "valid_days": valid_days,
        "test_days": test_days,
        "step_days": step_days,
        "max_folds": max_folds,
        "num_round": num_round,
        "num_threads": num_threads,
        "topk_size": topk_size,
        "topk_size_choices": topk_size_choices,
        "best_topk_size": best_topk_size,
        "cost_bps": cost_bps,
        "thresholds": {
            "min_holdout_rank_ic": min_holdout_rank_ic,
            "min_holdout_spread": min_holdout_spread,
            "min_walkforward_avg_rank_ic": min_walkforward_avg_rank_ic,
            "max_walkforward_std_rank_ic": max_walkforward_std_rank_ic,
            "min_ok_folds": min_ok_folds,
            "max_topk_drawdown": max_topk_drawdown,
            "max_feature_drift_psi": max_feature_drift_psi,
        },
        "drift_bins": drift_bins,
        "best_status": best_status,
        "best_rejection_reason": best_reason,
        "best_metrics": best_metrics,
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_model_stability_search_summary
        (run_id, model_selection_run_id, feature_table, feature_set_id,
         label_name, selected_features_json, best_trial_number,
         best_params_json, objective_score, trials, study_name,
         study_total_trials, config_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            model_selection_run_id,
            feature_table,
            feature_set_id,
            label_name,
            json.dumps(feature_cols, ensure_ascii=False),
            best_trial_number,
            json.dumps(best_params, ensure_ascii=False, sort_keys=True),
            best_score,
            max(int(trials), 0),
            study_name if storage_url else None,
            study_total_trials,
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    record_actual_version(conn, "mart_model_stability_search_trial")
    record_actual_version(conn, "mart_model_stability_search_summary")
    duration_s = time.perf_counter() - t0
    timing_summary = run_timer.summary()
    ranker_cache_summary = (
        ranker_cache.summary()
        if ranker_cache is not None
        else {"enabled": False, "entries": 0, "hits": 0, "misses": 0}
    )
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="run_optuna_model_stability_search",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[feature_table, "mart_model_selection_run"],
        output_tables=["mart_model_stability_search_trial", "mart_model_stability_search_summary"],
        feature_group="model_selection_run",
        label_name=label_name,
        holding_period=holding_period_from_label(label_name),
        perf_summary={
            "model_selection_run_id": model_selection_run_id,
            "model_family": model_family,
            "selected_features": len(feature_cols),
            "trials": max(int(trials), 0),
            "study_total_trials": study_total_trials,
            "best_trial_number": best_trial_number,
            "best_score": best_score,
            "best_status": best_status,
            "best_metrics": best_metrics,
            "topk_size": topk_size,
            "topk_size_choices": topk_size_choices,
            "best_topk_size": best_topk_size,
            "cost_bps": cost_bps,
            "max_feature_drift_psi": max_feature_drift_psi,
            "drift_bins": drift_bins,
            "timing": timing_summary,
            "ranker_cache": ranker_cache_summary,
            "evaluation_cache": eval_cache.summary(),
            "cpu_count": os.cpu_count(),
            "duration_s": duration_s,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "model_selection_run_id": model_selection_run_id,
        "model_family": model_family,
        "trials": max(int(trials), 0),
        "study_total_trials": study_total_trials,
        "best_trial_number": best_trial_number,
        "best_score": best_score,
        "best_status": best_status,
        "best_rejection_reason": best_reason,
        "best_topk_size": best_topk_size,
        "best_params": best_params,
        "best_metrics": best_metrics,
        "perf_summary": timing_summary,
        "ranker_cache": ranker_cache_summary,
        "evaluation_cache": eval_cache.summary(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-selection-run-id", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--label-name", default="forward_ret_20d")
    parser.add_argument("--model-family", choices=sorted(MODEL_FAMILIES), default="lightgbm")
    parser.add_argument("--trials", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-round", type=int, default=80)
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--valid-days", type=int, default=20)
    parser.add_argument("--test-days", type=int, default=20)
    parser.add_argument("--step-days", type=int, default=20)
    parser.add_argument("--max-folds", type=int, default=6)
    parser.add_argument("--min-holdout-rank-ic", type=float, default=0.0424)
    parser.add_argument("--min-holdout-spread", type=float, default=0.0092)
    parser.add_argument("--min-walkforward-avg-rank-ic", type=float, default=0.015)
    parser.add_argument("--max-walkforward-std-rank-ic", type=float, default=0.03)
    parser.add_argument("--min-ok-folds", type=int, default=4)
    parser.add_argument("--topk-size", type=int, default=50)
    parser.add_argument("--topk-size-choices", default=None)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--max-topk-drawdown", type=float, default=1.00)
    parser.add_argument("--max-feature-drift-psi", type=float, default=1.00)
    parser.add_argument("--drift-bins", type=int, default=10)
    parser.add_argument("--storage", default=None)
    parser.add_argument("--study-name", default=None)
    parser.add_argument("--no-persistent-study", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        storage_url = None if args.no_persistent_study else (args.storage or default_optuna_storage_url())
        result = run_optuna_model_stability_search(
            conn,
            model_selection_run_id=args.model_selection_run_id,
            run_id=args.run_id,
            start=args.start,
            end=args.end,
            feature_table=args.feature_table,
            feature_set_id=args.feature_set_id,
            label_name=args.label_name,
            model_family=args.model_family,
            trials=args.trials,
            seed=args.seed,
            num_round=args.num_round,
            num_threads=args.num_threads,
            train_days=args.train_days,
            valid_days=args.valid_days,
            test_days=args.test_days,
            step_days=args.step_days,
            max_folds=args.max_folds,
            min_holdout_rank_ic=args.min_holdout_rank_ic,
            min_holdout_spread=args.min_holdout_spread,
            min_walkforward_avg_rank_ic=args.min_walkforward_avg_rank_ic,
            max_walkforward_std_rank_ic=args.max_walkforward_std_rank_ic,
            min_ok_folds=args.min_ok_folds,
            topk_size=args.topk_size,
            topk_size_choices=_parse_int_csv(args.topk_size_choices),
            cost_bps=args.cost_bps,
            max_topk_drawdown=args.max_topk_drawdown,
            max_feature_drift_psi=args.max_feature_drift_psi,
            drift_bins=args.drift_bins,
            storage_url=storage_url,
            study_name=args.study_name,
            load_if_exists=not args.no_resume,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
