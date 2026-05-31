#!/usr/bin/env python3
"""P0b LambdaMART v6 — top-K cost-aware ranker.

This runner keeps the v4 prepared-array path, but trains LightGBM with a
LambdaRank objective and evaluates the OOS top-K surface directly.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import os
import sys
import time
import uuid
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.optimization.walk_forward import split_expanding_monthly
from services.perf.prepared_panel import make_lambdarank_groups

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p0b_lambdamart_v6")

warnings.filterwarnings("ignore", message="Found 'ndcg_eval_at' in params.*")
warnings.filterwarnings("ignore", message="X does not have valid feature names.*")


_META_COLS = {
    "stock_code", "signal_date", "entry_date", "unable_at_entry",
    "month_start", "built_at",
    "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
    "feature_version", "label_version", "industry_pit_confidence",
    "industry_pit_l1_name", "industry_pit_l2_name",
    "industry_pit_l1_code", "industry_pit_l2_code",
    "sector_name",
    # Latest-snapshot / fallback contamination exclusions inherited from v4.
    "inst_quality_wavg", "inst_quality_max", "inst_total_holding_ratio",
    "inst_holder_cnt", "top_inst_holding_ratio",
    "sector_ret_5d", "sector_ret_20d", "sector_ret_60d",
    "sector_excess_20d", "sector_excess_60d",
    "holder_count_q_report_date",
}


@dataclass(frozen=True)
class RankPanel:
    X: np.ndarray
    y_raw: np.ndarray
    y_relevance: np.ndarray
    signal_dates: np.ndarray
    stock_codes: np.ndarray
    feature_columns: list[str]


@dataclass(frozen=True)
class WindowSpec:
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class ModelRunResult:
    model_name: str
    best_value: float
    best_params: dict[str, Any]
    metrics: dict[str, float]
    n_trials: int
    n_windows: int


def assert_pit_strict(train_signal_dates, test_signal_dates) -> None:
    """Require every train signal_date to be strictly before the test start.

    Fix 1 (Claude general aacdbf94, 2026-05-19): 若输入是 int64 ndarray (day-level epoch),
    走 fast-path O(1) max/min; legacy string path 保留兼容 (test_lambdamart_v6.py:112 老调用).
    Speedup: 30 expanding window 累积 3.5M strings pd.to_datetime 重复 parse ~5-8 min → 0 sec.
    """
    if len(train_signal_dates) == 0 or len(test_signal_dates) == 0:
        raise AssertionError("PIT strict check requires non-empty train and test dates")
    # Fix 1 fast-path: int64 ndarray (epoch day)
    if (
        isinstance(train_signal_dates, np.ndarray)
        and train_signal_dates.dtype.kind == "i"
        and isinstance(test_signal_dates, np.ndarray)
        and test_signal_dates.dtype.kind == "i"
    ):
        last_train_int = int(train_signal_dates.max())
        first_test_int = int(test_signal_dates.min())
        if last_train_int >= first_test_int:
            raise AssertionError(
                f"PIT leak detected: last_train_epoch={last_train_int} >= "
                f"first_test_epoch={first_test_int} (day-level int)"
            )
        return
    # Legacy string path (test fixture 老调用兼容)
    train_dates = pd.to_datetime(pd.Series(train_signal_dates))
    test_dates = pd.to_datetime(pd.Series(test_signal_dates))
    last_train = train_dates.max()
    first_test = test_dates.min()
    if last_train >= first_test:
        raise AssertionError(
            f"PIT leak detected: last_train_date={last_train.date()} >= "
            f"first_test_date={first_test.date()}"
        )


def _finite(value: float | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _select_feature_columns(df: pd.DataFrame, exclude_cols: str = "") -> list[str]:
    meta_cols = set(_META_COLS)
    if exclude_cols:
        meta_cols.update(c.strip() for c in exclude_cols.split(",") if c.strip())
    return [
        c for c in df.columns
        if c not in meta_cols and pd.api.types.is_numeric_dtype(df[c])
    ]


def load_rank_panel(
    *,
    db_path: str,
    feature_panel: str,
    label_col: str,
    start_date: str,
    end_date: str,
    exclude_cols: str = "",
) -> RankPanel:
    conn = duck_connect(db_path, read_only=True)
    try:
        log.info("Loading DataFrame from %s ...", feature_panel)
        t0 = time.time()
        df = conn._con.execute(
            f"SELECT * FROM {feature_panel} ORDER BY signal_date, stock_code"
        ).fetchdf()
        log.info("  Loaded %s rows x %s cols (%.1fs)", f"{len(df):,}", len(df.columns), time.time() - t0)
    finally:
        conn.close()

    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df[df["signal_date"] >= pd.to_datetime(start_date)]
    df = df[df["signal_date"] <= pd.to_datetime(end_date)]
    df = df[df[label_col].notna()].copy()
    df = df.sort_values(["signal_date", "stock_code"]).reset_index(drop=True)
    log.info("  After date+label filter: %s rows", f"{len(df):,}")

    feature_columns = _select_feature_columns(df, exclude_cols)
    if not feature_columns:
        raise ValueError("No numeric feature columns selected")
    log.info("  feature_columns (%d): %s...", len(feature_columns), feature_columns[:10])

    signal_dates = df["signal_date"].drop_duplicates().tolist()
    X, y_relevance, groups = make_lambdarank_groups(
        df,
        signal_dates,
        label_col=label_col,
        feature_cols=feature_columns,
        meta_cols=_META_COLS,
        fill_value=-9999.0,
    )
    if int(groups.sum()) != len(df):
        raise AssertionError(f"full-panel group sum mismatch: {groups.sum()} != {len(df)}")

    panel = RankPanel(
        X=X,
        y_raw=df[label_col].to_numpy(dtype=np.float32, copy=True),
        y_relevance=y_relevance.astype(np.int32, copy=False),
        signal_dates=df["signal_date"].dt.strftime("%Y-%m-%d").to_numpy(copy=True),
        stock_codes=df["stock_code"].astype(str).to_numpy(copy=True),
        feature_columns=feature_columns,
    )
    del df
    gc.collect()
    log.info("  RankPanel built: X=%s %s, y_relevance=%s", panel.X.shape, panel.X.dtype, panel.y_relevance.shape)
    return panel


def build_walk_forward_windows(
    panel: RankPanel,
    *,
    min_train_months: int,
    forward_months: int,
    max_windows: int | None = None,
) -> list[WindowSpec]:
    # Fix 1 (Claude general aacdbf94, 2026-05-19): panel.signal_dates 一次性转 int64 day-epoch,
    # 后续 np.isin / assert_pit_strict 全 int 比较, 估 30-60× 加速 (15 min → 20-30 sec).
    # 文件: docs/strategy_validation_contract.md
    # rule-compliance: ok evidence=claude-general-aacdbf94-retrain-stall-fix1-int64
    panel_dates_str = panel.signal_dates  # original <U10 ndarray (kept for WindowSpec str fields)
    panel_dates_int = (
        pd.to_datetime(pd.Series(panel_dates_str))
        .values.astype("datetime64[D]")
        .astype("int64")
    )

    unique_dates = pd.Series(panel_dates_str).drop_duplicates().tolist()
    date_signals = [{"stock_code": "__date__", "signal_date": d} for d in unique_dates]
    splits = split_expanding_monthly(
        date_signals,
        min_train_months=min_train_months,
        forward_months=forward_months,
        min_test=1,
    )

    def _dates_to_int64(date_strs: set[str]) -> np.ndarray:
        # str "2024-01-02" → int64 day-epoch
        return np.array(
            [
                pd.Timestamp(d).to_datetime64().astype("datetime64[D]").astype("int64")
                for d in date_strs
            ],
            dtype=np.int64,
        )

    windows: list[WindowSpec] = []
    for sp in splits:
        train_dates = {str(r["signal_date"])[:10] for r in sp.train}
        test_dates = {str(r["signal_date"])[:10] for r in sp.test}
        train_dates_int = _dates_to_int64(train_dates)
        test_dates_int = _dates_to_int64(test_dates)
        train_idx = np.where(np.isin(panel_dates_int, train_dates_int))[0].astype(np.int32)
        test_idx = np.where(np.isin(panel_dates_int, test_dates_int))[0].astype(np.int32)
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        # Fix 1 fast-path: 传 int64 ndarray, assert_pit_strict 不再跑 pd.to_datetime on累积 3.5M strings
        assert_pit_strict(panel_dates_int[train_idx], panel_dates_int[test_idx])
        windows.append(WindowSpec(
            train_idx=train_idx,
            test_idx=test_idx,
            train_start=str(panel_dates_str[train_idx[0]]),
            train_end=str(panel_dates_str[train_idx[-1]]),
            test_start=str(panel_dates_str[test_idx[0]]),
            test_end=str(panel_dates_str[test_idx[-1]]),
        ))

    if max_windows is not None:
        windows = windows[:max_windows]
    return windows


def _group_sizes_for_contiguous_dates(signal_dates: np.ndarray) -> np.ndarray:
    if len(signal_dates) == 0:
        return np.empty((0,), dtype=np.int32)
    boundaries = np.flatnonzero(signal_dates[1:] != signal_dates[:-1]) + 1
    bounds = np.concatenate(([0], boundaries, [len(signal_dates)]))
    groups = np.diff(bounds).astype(np.int32)
    if int(groups.sum()) != len(signal_dates):
        raise AssertionError(f"group sum mismatch: {groups.sum()} != {len(signal_dates)}")
    return groups


def _dcg(relevance: np.ndarray, k: int) -> float:
    rel = relevance[:k].astype(float)
    if len(rel) == 0:
        return float("nan")
    gains = np.power(2.0, rel) - 1.0
    discounts = np.log2(np.arange(2, len(rel) + 2, dtype=float))
    return float(np.sum(gains / discounts))


def _mean_ndcg(df: pd.DataFrame, k: int) -> float:
    values: list[float] = []
    for _, g in df.groupby("signal_date", sort=True):
        if len(g) < 2:
            continue
        rel = g["relevance"].to_numpy(dtype=float)
        score = g["score"].to_numpy(dtype=float)
        order = np.argsort(-score, kind="mergesort")
        ideal_order = np.argsort(-rel, kind="mergesort")
        ideal = _dcg(rel[ideal_order], k)
        if not math.isfinite(ideal) or ideal <= 0:
            continue
        actual = _dcg(rel[order], k)
        if math.isfinite(actual):
            values.append(actual / ideal)
    return float(np.mean(values)) if values else float("nan")


def _mean_rank_ic(df: pd.DataFrame, label_col: str) -> float:
    values: list[float] = []
    for _, g in df.groupby("signal_date", sort=True):
        valid = g[["score", label_col]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(valid) < 2 or valid["score"].nunique() < 2 or valid[label_col].nunique() < 2:
            continue
        rho = valid["score"].rank().corr(valid[label_col].rank())
        if rho is not None and math.isfinite(float(rho)):
            values.append(float(rho))
    return float(np.mean(values)) if values else float("nan")


def _mean_top_bottom_spread(df: pd.DataFrame, label_col: str, k: int) -> float:
    values: list[float] = []
    for _, g in df.groupby("signal_date", sort=True):
        valid = g[["score", label_col]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(valid) < 2:
            continue
        ordered = valid.sort_values("score", ascending=False, kind="mergesort")
        kk = min(k, len(ordered))
        top_avg = float(ordered.head(kk)[label_col].mean())
        bottom_avg = float(ordered.tail(kk)[label_col].mean())
        values.append(top_avg - bottom_avg)
    return float(np.mean(values)) if values else float("nan")


def _mean_topk_turnover_count(df: pd.DataFrame, k: int) -> float:
    top_sets: list[set[str]] = []
    for _, g in df.groupby("signal_date", sort=True):
        ordered = g.sort_values("score", ascending=False, kind="mergesort")
        top_sets.append(set(ordered.head(k)["stock_code"].astype(str)))
    if len(top_sets) < 2:
        return 0.0
    changed_counts = []
    for prev, cur in zip(top_sets, top_sets[1:]):
        changed_counts.append(max(0, len(cur) - len(prev & cur)))
    return float(np.mean(changed_counts)) if changed_counts else 0.0


def _prediction_frame(
    panel: RankPanel,
    idx: np.ndarray,
    pred: np.ndarray,
    *,
    label_col: str,
) -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": panel.stock_codes[idx],
        "signal_date": panel.signal_dates[idx],
        "score": pred.astype(float, copy=False),
        label_col: panel.y_raw[idx].astype(float, copy=False),
        "relevance": panel.y_relevance[idx].astype(int, copy=False),
    })


def evaluate_predictions(df: pd.DataFrame, *, label_col: str, top_k: int = 5) -> dict[str, float]:
    if df.empty:
        return {
            "rank_ic": float("nan"),
            "ndcg5": float("nan"),
            "ndcg10": float("nan"),
            "ndcg20": float("nan"),
            "top5_spread": float("nan"),
            "top10_spread": float("nan"),
            "top5_turnover": 0.0,
        }
    return {
        "rank_ic": _mean_rank_ic(df, label_col),
        "ndcg5": _mean_ndcg(df, 5),
        "ndcg10": _mean_ndcg(df, 10),
        "ndcg20": _mean_ndcg(df, 20),
        "top5_spread": _mean_top_bottom_spread(df, label_col, 5),
        "top10_spread": _mean_top_bottom_spread(df, label_col, 10),
        "top5_turnover": _mean_topk_turnover_count(df, top_k),
    }


def _cost_aware_score(metrics: dict[str, float], *, turnover_limit: float, turnover_penalty_weight: float) -> tuple[float, float]:
    base = (
        0.50 * _finite(metrics.get("ndcg5"))
        + 0.30 * _finite(metrics.get("ndcg10"))
        + 0.20 * _finite(metrics.get("ndcg20"))
    )
    penalty = max(0.0, _finite(metrics.get("top5_turnover")) - turnover_limit) * turnover_penalty_weight
    return base - penalty, penalty


def _rank_ic_stability_adjustment(
    rank_ics: list[float],
    *,
    std_penalty_weight: float = 0.0,
    negative_rate_penalty_weight: float = 0.0,
) -> dict[str, float]:
    """Return opt-in window RankIC stability diagnostics and objective adjustment."""

    clean: list[float] = []
    for value in rank_ics:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            clean.append(numeric)
    if not clean:
        return {
            "window_rank_ic_mean": float("nan"),
            "window_rank_ic_std": float("nan"),
            "window_rank_ic_positive_rate": float("nan"),
            "window_rank_ic_negative_rate": float("nan"),
            "rank_ic_stability_penalty": 0.0,
        }
    mean_ic = float(np.mean(clean))
    std_ic = float(np.std(clean, ddof=1)) if len(clean) > 1 else 0.0
    positive_rate = float(sum(1 for value in clean if value > 0) / len(clean))
    negative_rate = float(sum(1 for value in clean if value < 0) / len(clean))
    penalty = std_penalty_weight * std_ic + negative_rate_penalty_weight * negative_rate
    return {
        "window_rank_ic_mean": mean_ic,
        "window_rank_ic_std": std_ic,
        "window_rank_ic_positive_rate": positive_rate,
        "window_rank_ic_negative_rate": negative_rate,
        "rank_ic_stability_penalty": float(penalty),
    }


def _suggest_common_params(trial: optuna.Trial, *, seed: int, n_estimators: int) -> dict[str, Any]:
    max_depth = trial.suggest_int("max_depth", 3, 8)
    num_leaves_high = max(2, min(127, 2 ** max_depth - 1))
    n_jobs = int(os.environ.get("OMP_NUM_THREADS", "8"))
    return {
        "max_depth": max_depth,
        "num_leaves": trial.suggest_int("num_leaves", 2, num_leaves_high, log=True),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.08, log=True),
        "min_child_samples": trial.suggest_int("min_child_samples", 20, 300, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.55, 0.95),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.60, 1.00),
        "bagging_freq": trial.suggest_int("bagging_freq", 1, 5),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 50.0, log=True),
        "min_split_gain": trial.suggest_float("min_split_gain", 0.0, 0.2),
        "n_estimators": n_estimators,
        "random_state": seed,
        "verbose": -1,
        "n_jobs": n_jobs,
        "num_threads": n_jobs,
    }


def completed_trial_count(study: optuna.Study) -> int:
    """Return completed Optuna trials, excluding pruned/failed/running leftovers."""

    return sum(1 for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE)


def remaining_trials_for_target(study: optuna.Study, target_trials: int) -> int:
    """Return trials still needed when resuming a persistent study.

    Optuna's ``study.optimize(n_trials=N)`` appends N new trials to an existing
    SQLite study. For GCP spot resume we need N to mean total target trials,
    otherwise each restart burns another full batch.
    """

    if target_trials < 0:
        raise ValueError(f"target_trials must be non-negative, got {target_trials}")
    return max(0, int(target_trials) - completed_trial_count(study))


def load_warm_start_params(path: str | Path) -> dict[str, Any]:
    """Load reusable Optuna params from a previous best-trial checkpoint."""

    checkpoint = Path(path)
    if not checkpoint.exists():
        raise FileNotFoundError(f"warm-start checkpoint not found: {checkpoint}")
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    params = payload.get("best_params")
    if not isinstance(params, dict) or not params:
        raise ValueError(f"warm-start checkpoint missing non-empty best_params: {checkpoint}")
    return dict(params)


def enqueue_warm_start_trial(
    study: optuna.Study,
    params: dict[str, Any],
    *,
    source: str | None = None,
) -> None:
    """Queue a previous best param set as a pending trial for Layer 4 reuse."""

    if not params:
        raise ValueError("warm-start params must be non-empty")
    user_attrs = {"warm_start": True}
    if source:
        user_attrs["warm_start_source"] = source
    study.enqueue_trial(dict(params), user_attrs=user_attrs, skip_if_exists=True)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return int(default)
    try:
        value = int(raw)
    except ValueError:
        log.warning("invalid %s=%r; using %d", name, raw, default)
        return int(default)
    if value < 1:
        log.warning("invalid %s=%r; using %d", name, raw, default)
        return int(default)
    return value


def _configure_optuna_parallelism() -> tuple[int, int]:
    """Return outer Optuna jobs and capped inner LightGBM threads."""

    cpu_count = max(1, os.cpu_count() or 1)
    n_jobs_default = max(1, min(4, cpu_count // 2))
    n_jobs = _read_positive_int_env("OPTUNA_N_JOBS", n_jobs_default)
    if n_jobs <= 1:
        inner_threads = _read_positive_int_env("OMP_NUM_THREADS", min(8, cpu_count))
        os.environ["OMP_NUM_THREADS"] = str(inner_threads)
        os.environ["LIGHTGBM_NUM_THREADS"] = str(inner_threads)
        return n_jobs, inner_threads

    max_inner_threads = max(1, cpu_count // n_jobs)
    requested_inner = _read_positive_int_env("OMP_NUM_THREADS", max_inner_threads)
    inner_threads = min(requested_inner, max_inner_threads)
    if inner_threads != requested_inner:
        log.warning(
            "capping inner LightGBM threads: OPTUNA_N_JOBS=%d OMP_NUM_THREADS=%d cpu_count=%d -> inner=%d",
            n_jobs,
            requested_inner,
            cpu_count,
            inner_threads,
        )
    os.environ["OMP_NUM_THREADS"] = str(inner_threads)
    os.environ["LIGHTGBM_NUM_THREADS"] = str(inner_threads)
    return n_jobs, inner_threads


def _fit_lambdamart_window_model(panel: RankPanel, window: WindowSpec, params: dict[str, Any]) -> lgb.LGBMRanker:
    train_groups = _group_sizes_for_contiguous_dates(panel.signal_dates[window.train_idx])
    model = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        ndcg_eval_at=[5, 10, 20],
        label_gain=[0, 1, 3, 7, 15],
        **params,
    )
    model.fit(
        panel.X[window.train_idx],
        panel.y_relevance[window.train_idx],
        group=train_groups,
    )
    return model


def _run_lambdamart_window(panel: RankPanel, window: WindowSpec, params: dict[str, Any], *, label_col: str) -> pd.DataFrame:
    model = _fit_lambdamart_window_model(panel, window, params)
    pred = model.predict(panel.X[window.test_idx])
    return _prediction_frame(panel, window.test_idx, pred, label_col=label_col)


def _run_regressor_window(panel: RankPanel, window: WindowSpec, params: dict[str, Any], *, label_col: str) -> pd.DataFrame:
    model = lgb.LGBMRegressor(**params)
    model.fit(panel.X[window.train_idx], panel.y_raw[window.train_idx])
    pred = model.predict(panel.X[window.test_idx])
    return _prediction_frame(panel, window.test_idx, pred, label_col=label_col)


def run_optuna(
    *,
    model_name: str,
    panel: RankPanel,
    windows: list[WindowSpec],
    label_col: str,
    n_trials: int,
    n_estimators: int,
    seed: int,
    turnover_limit: float,
    turnover_penalty_weight: float,
    top_k: int,
    study_storage: str | None = None,
    study_name: str | None = None,
    checkpoint_path: str | None = None,
    warm_start_params: dict[str, Any] | None = None,
    warm_start_source: str | None = None,
    window_rank_ic_std_penalty_weight: float = 0.0,
    window_rank_ic_negative_rate_penalty_weight: float = 0.0,
) -> ModelRunResult:
    if model_name not in {"lambdamart", "regressor"}:
        raise ValueError(f"unknown model_name={model_name}")

    sampler = optuna.samplers.TPESampler(seed=seed)
    pruner = optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=2, n_min_trials=2)
    # F1 (Codex bocq8b60j 2026-05-20): Optuna SQLite persistent storage 防 spot preempt 浪费.
    # interrupted 后 重启同名 study 可 resume (load_if_exists=True), 已 COMPLETE trials 不重算.
    # rule-compliance: ok evidence=codex-bocq8b60j-gcp-reliability-f1-sqlite-storage
    resolved_name = study_name or (
        f"p0b_{model_name}_v6_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"  # Phase ψ.5 allowlist: study_name wall-clock identifier
    )
    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
        pruner=pruner,
        study_name=resolved_name,
        storage=study_storage,
        load_if_exists=study_storage is not None,
    )
    if study_storage:
        log.info("optuna study storage=%s name=%s load_if_exists=True", study_storage, resolved_name)
    else:
        log.warning("optuna in-memory storage (interrupted = 全丢, F1 fix 建议 --study-storage sqlite:///path)")
    if warm_start_params:
        enqueue_warm_start_trial(study, warm_start_params, source=warm_start_source)
        log.info(
            "optuna warm-start queued: source=%s params=%s",
            warm_start_source or "(inline)",
            sorted(warm_start_params.keys()),
        )

    def objective(trial: optuna.Trial) -> float:
        params = _suggest_common_params(trial, seed=seed, n_estimators=n_estimators)
        pred_frames: list[pd.DataFrame] = []
        window_scores: list[float] = []
        rank_ics: list[float] = []

        for win_i, window in enumerate(windows):
            if model_name == "lambdamart":
                pred_df = _run_lambdamart_window(panel, window, params, label_col=label_col)
            else:
                pred_df = _run_regressor_window(panel, window, params, label_col=label_col)

            metrics = evaluate_predictions(pred_df, label_col=label_col, top_k=top_k)
            pred_frames.append(pred_df)
            rank_ic_value = _finite(metrics.get("rank_ic"), float("nan"))
            if math.isfinite(rank_ic_value):
                rank_ics.append(float(rank_ic_value))

            if model_name == "lambdamart":
                score, _ = _cost_aware_score(
                    metrics,
                    turnover_limit=turnover_limit,
                    turnover_penalty_weight=turnover_penalty_weight,
                )
                window_scores.append(score)
                trial.report(float(np.mean(window_scores)), step=win_i)
            else:
                if rank_ics:
                    mean_ic = float(np.mean(rank_ics))
                    std_ic = float(np.std(rank_ics, ddof=1)) if len(rank_ics) > 1 else 0.0
                    trial.report(mean_ic - 0.5 * std_ic, step=win_i)

            if trial.should_prune():
                raise optuna.TrialPruned()

        all_pred = pd.concat(pred_frames, ignore_index=True) if pred_frames else pd.DataFrame()
        final_metrics = evaluate_predictions(all_pred, label_col=label_col, top_k=top_k)
        if model_name == "lambdamart":
            final_score, penalty = _cost_aware_score(
                final_metrics,
                turnover_limit=turnover_limit,
                turnover_penalty_weight=turnover_penalty_weight,
            )
        else:
            if rank_ics:
                mean_ic = float(np.mean(rank_ics))
                std_ic = float(np.std(rank_ics, ddof=1)) if len(rank_ics) > 1 else 0.0
                final_score = mean_ic - 0.5 * std_ic
            else:
                final_score = -10.0
            penalty = 0.0

        rank_ic_stability = _rank_ic_stability_adjustment(
            rank_ics,
            std_penalty_weight=window_rank_ic_std_penalty_weight,
            negative_rate_penalty_weight=window_rank_ic_negative_rate_penalty_weight,
        )
        final_score -= rank_ic_stability["rank_ic_stability_penalty"]

        for key, value in final_metrics.items():
            trial.set_user_attr(key, None if not math.isfinite(_finite(value, float("nan"))) else float(value))
        trial.set_user_attr("turnover_penalty", float(penalty))
        for key, value in rank_ic_stability.items():
            trial.set_user_attr(key, None if not math.isfinite(value) else float(value))
        trial.set_user_attr("n_windows", len(windows))
        trial.set_user_attr("n_oos_rows", int(len(all_pred)))
        log.info(
            "%s trial %d: score=%.6f RankIC=%.4f NDCG@5=%.4f NDCG@10=%.4f NDCG@20=%.4f "
            "top5_spread=%.4f top10_spread=%.4f turnover=%.2f penalty=%.4f rank_ic_stability_penalty=%.4f",
            model_name,
            trial.number,
            final_score,
            _finite(final_metrics.get("rank_ic"), float("nan")),
            _finite(final_metrics.get("ndcg5"), float("nan")),
            _finite(final_metrics.get("ndcg10"), float("nan")),
            _finite(final_metrics.get("ndcg20"), float("nan")),
            _finite(final_metrics.get("top5_spread"), float("nan")),
            _finite(final_metrics.get("top10_spread"), float("nan")),
            _finite(final_metrics.get("top5_turnover"), float("nan")),
            penalty,
            rank_ic_stability["rank_ic_stability_penalty"],
        )
        return final_score

    # Codex codegraph audit P-2 (aa94bbab 2026-05-19): 加 n_jobs=4 parallel trials
    # (Mac 8C 估 2-3× 加速, GCP 32C 估 4-8× 加速). 配 OMP_NUM_THREADS=2 限内层 LightGBM
    # 防 thread oversubscription. 必须用 InMemoryStorage 或 SQLite study DB 防 race;
    # 当前 study 是 in-memory single-process safe (Optuna 内部 multi-thread 锁).
    # rule-compliance: ok evidence=codex-codegraph-P-2-parallel-trials
    n_jobs, inner_threads = _configure_optuna_parallelism()
    log.info("optuna parallelism: outer_jobs=%d inner_lightgbm_threads=%d", n_jobs, inner_threads)

    # F2 (Codex bocq8b60j 2026-05-20): 每 COMPLETE trial 写 best_params checkpoint json.
    # spot preempt 时 interrupted, best params 已落盘可救回. atomic write (tmp + replace).
    # rule-compliance: ok evidence=codex-bocq8b60j-gcp-reliability-f2-checkpoint-best
    _callbacks: list = []
    if checkpoint_path:
        from pathlib import Path
        import json as _json
        _ckpt = Path(checkpoint_path)
        _ckpt.parent.mkdir(parents=True, exist_ok=True)

        def _checkpoint_best(study_obj, frozen_trial) -> None:
            if frozen_trial.state != optuna.trial.TrialState.COMPLETE:
                return
            try:
                best_trial = study_obj.best_trial
            except ValueError:
                return  # no completed trial yet
            payload = {
                "best_trial_number": best_trial.number,
                "best_value": best_trial.value,
                "best_params": best_trial.params,
                "best_user_attrs": {k: v for k, v in best_trial.user_attrs.items()},
                "study_name": study_obj.study_name,
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            tmp = _ckpt.with_suffix(".tmp")
            tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(_ckpt)

        _callbacks.append(_checkpoint_best)
        log.info("optuna checkpoint enabled: %s (每 COMPLETE trial atomic write best params)", _ckpt)

    trials_to_run = n_trials
    if study_storage is not None:
        completed_before = completed_trial_count(study)
        trials_to_run = remaining_trials_for_target(study, n_trials)
        log.info(
            "optuna resume budget: target_total_trials=%d completed=%d remaining=%d",
            n_trials,
            completed_before,
            trials_to_run,
        )

    if trials_to_run > 0:
        study.optimize(
            objective,
            n_trials=trials_to_run,
            gc_after_trial=True,
            n_jobs=n_jobs,
            callbacks=_callbacks or None,
        )
    else:
        log.info("optuna target already satisfied; skipping optimize and using existing best trial")

    best = study.best_trial
    metrics = {
        "rank_ic": _finite(best.user_attrs.get("rank_ic"), float("nan")),
        "ndcg5": _finite(best.user_attrs.get("ndcg5"), float("nan")),
        "ndcg10": _finite(best.user_attrs.get("ndcg10"), float("nan")),
        "ndcg20": _finite(best.user_attrs.get("ndcg20"), float("nan")),
        "top5_spread": _finite(best.user_attrs.get("top5_spread"), float("nan")),
        "top10_spread": _finite(best.user_attrs.get("top10_spread"), float("nan")),
        "top5_turnover": _finite(best.user_attrs.get("top5_turnover"), float("nan")),
        "turnover_penalty": _finite(best.user_attrs.get("turnover_penalty"), 0.0),
        "window_rank_ic_mean": _finite(best.user_attrs.get("window_rank_ic_mean"), float("nan")),
        "window_rank_ic_std": _finite(best.user_attrs.get("window_rank_ic_std"), float("nan")),
        "window_rank_ic_positive_rate": _finite(best.user_attrs.get("window_rank_ic_positive_rate"), float("nan")),
        "window_rank_ic_negative_rate": _finite(best.user_attrs.get("window_rank_ic_negative_rate"), float("nan")),
        "rank_ic_stability_penalty": _finite(best.user_attrs.get("rank_ic_stability_penalty"), 0.0),
    }
    return ModelRunResult(
        model_name=model_name,
        best_value=float(best.value),
        best_params=dict(best.params),
        metrics=metrics,
        n_trials=completed_trial_count(study),
        n_windows=len(windows),
    )


def _print_result(result: ModelRunResult) -> None:
    m = result.metrics
    print(f"\n=== {result.model_name} best trial ===")
    print(f"best_value: {result.best_value:.6f}")
    print(f"n_trials: {result.n_trials}")
    print(f"n_windows: {result.n_windows}")
    print(f"RankIC: {m['rank_ic']:.6f}")
    print(f"NDCG@5: {m['ndcg5']:.6f}")
    print(f"NDCG@10: {m['ndcg10']:.6f}")
    print(f"NDCG@20: {m['ndcg20']:.6f}")
    print(f"top-5 spread: {m['top5_spread']:.6f}")
    print(f"top-10 spread: {m['top10_spread']:.6f}")
    print(f"top-5 turnover count: {m['top5_turnover']:.6f}")
    print(f"turnover penalty: {m['turnover_penalty']:.6f}")
    print(f"window RankIC mean: {m['window_rank_ic_mean']:.6f}")
    print(f"window RankIC std: {m['window_rank_ic_std']:.6f}")
    print(f"window RankIC positive rate: {m['window_rank_ic_positive_rate']:.6f}")
    print(f"window RankIC negative rate: {m['window_rank_ic_negative_rate']:.6f}")
    print(f"RankIC stability penalty: {m['rank_ic_stability_penalty']:.6f}")
    print(f"params: {result.best_params}")


def _print_comparison(results: list[ModelRunResult]) -> None:
    print("\n=== quick comparison ===")
    print("| model | RankIC | NDCG@5 | NDCG@10 | NDCG@20 | top-5 spread | top-10 spread |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for result in results:
        m = result.metrics
        print(
            f"| {result.model_name} | {m['rank_ic']:.6f} | {m['ndcg5']:.6f} | "
            f"{m['ndcg10']:.6f} | {m['ndcg20']:.6f} | "
            f"{m['top5_spread']:.6f} | {m['top10_spread']:.6f} |"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="P0b LambdaMART v6 top-K cost-aware ranker")
    parser.add_argument("--label", default="fwd_cost_after_20d")
    parser.add_argument("--n-trials", type=int, default=50)
    parser.add_argument("--full", action="store_true", help="use n_estimators=2000")
    parser.add_argument("--n-estimators", type=int, default=None)
    parser.add_argument("--start-date", default="2024-01-01")   # rule-compliance: ok evidence=alpha158-panel-起始日
    parser.add_argument("--end-date", default="2026-04-13")     # rule-compliance: ok evidence=panel-cutoff-避免-fwd-20d-超出-K-line
    parser.add_argument("--min-train-months", type=int, default=12)
    parser.add_argument("--forward-months", type=int, default=1)
    parser.add_argument("--max-windows", type=int, default=None, help="limit walk-forward windows for smoke tests")
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v4")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--exclude-cols", default="")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--turnover-limit", type=float, default=3.0,
                        help="average top-5 changed-name count allowed before penalty")
    parser.add_argument("--turnover-penalty-weight", type=float, default=0.02)
    parser.add_argument("--window-rank-ic-std-penalty-weight", type=float, default=0.0,
                        help="Opt-in objective penalty on per-window RankIC std; default 0 preserves existing behavior")
    parser.add_argument("--window-rank-ic-negative-rate-penalty-weight", type=float, default=0.0,
                        help="Opt-in objective penalty on negative per-window RankIC rate; default 0 preserves existing behavior")
    parser.add_argument(
        "--warm-start-checkpoint",
        default=None,
        help="Seed Optuna with best_params from a previous checkpoint JSON (Layer 4 warm-start)",
    )
    parser.add_argument("--compare-regressor", action="store_true",
                        help="also run a v4-style LGBMRegressor Optuna baseline")
    args = parser.parse_args()

    t0 = time.time()
    n_estimators = args.n_estimators if args.n_estimators is not None else (2000 if args.full else 300)
    log.info(
        "label=%s n_trials=%d full=%s n_estimators=%d feature_panel=%s",
        args.label,
        args.n_trials,
        args.full,
        n_estimators,
        args.feature_panel,
    )

    panel = load_rank_panel(
        db_path=args.db_path or str(DB_PATH),
        feature_panel=args.feature_panel,
        label_col=args.label,
        start_date=args.start_date,
        end_date=args.end_date,
        exclude_cols=args.exclude_cols,
    )
    windows = build_walk_forward_windows(
        panel,
        min_train_months=args.min_train_months,
        forward_months=args.forward_months,
        max_windows=args.max_windows,
    )
    if not windows:
        log.error("No walk-forward windows produced")
        return 1
    warm_start_params = load_warm_start_params(args.warm_start_checkpoint) if args.warm_start_checkpoint else None
    log.info("walk-forward windows: %d", len(windows))
    for i, w in enumerate(windows[:5]):
        log.info(
            "  window %d: train %s..%s (%s rows) -> test %s..%s (%s rows)",
            i,
            w.train_start,
            w.train_end,
            f"{len(w.train_idx):,}",
            w.test_start,
            w.test_end,
            f"{len(w.test_idx):,}",
        )

    results = [
        run_optuna(
            model_name="lambdamart",
            panel=panel,
            windows=windows,
            label_col=args.label,
            n_trials=args.n_trials,
            n_estimators=n_estimators,
            seed=args.seed,
            turnover_limit=args.turnover_limit,
            turnover_penalty_weight=args.turnover_penalty_weight,
            top_k=args.top_k,
            warm_start_params=warm_start_params,
            warm_start_source=args.warm_start_checkpoint,
            window_rank_ic_std_penalty_weight=args.window_rank_ic_std_penalty_weight,
            window_rank_ic_negative_rate_penalty_weight=args.window_rank_ic_negative_rate_penalty_weight,
        )
    ]
    _print_result(results[0])

    if args.compare_regressor:
        results.append(
            run_optuna(
                model_name="regressor",
                panel=panel,
                windows=windows,
                label_col=args.label,
                n_trials=args.n_trials,
                n_estimators=n_estimators,
                seed=args.seed,
                turnover_limit=args.turnover_limit,
                turnover_penalty_weight=args.turnover_penalty_weight,
                top_k=args.top_k,
                window_rank_ic_std_penalty_weight=args.window_rank_ic_std_penalty_weight,
                window_rank_ic_negative_rate_penalty_weight=args.window_rank_ic_negative_rate_penalty_weight,
            )
        )
        _print_result(results[1])
        _print_comparison(results)

    log.info("Total: %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
