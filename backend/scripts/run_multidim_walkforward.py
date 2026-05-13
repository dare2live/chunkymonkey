#!/usr/bin/env python3
"""Fixed-parameter walk-forward validation for the multidim model."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import numpy as np
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
    BASE_FEATURE_COLS,
    DEFAULT_LABEL_NAME,
    DENSE_V2_FEATURE_COLS,
    REGIME_FEATURE_COLS,
    TDX_KEEP_CHALLENGER_SCHEMA_VERSION,
    TDX_KEEP_FEATURE_COLS,
    holding_period_from_label,
    ordered_feature_cols,
)
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.ml_lifecycle.registry import select_default_model_id
from services.feature_retention import load_production_keep_features
from scripts.run_feature_ablation import (
    compute_ic,
    decile_metrics,
    _quote_ident,
    _rank_percentiles,
    _records_from_cursor,
    _values,
)
from scripts.train_multidim_model import (
    STRICT_FEATURE_CONTRACT_GROUPS,
    _date_bounds_array,
    _selection_schema_tag,
    apply_production_feature_contract,
    _prediction_column_arrays,
    load_model_selection_run,
    load_panel_arrays,
    resolve_feature_group_from_columns,
)


logger = logging.getLogger("multidim_walkforward")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_model_walkforward_fold (
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
    test_market_state TEXT,
    test_mean_forward_ret REAL,
    best_iteration INTEGER,
    daily_distinct_score_median REAL,
    daily_distinct_score_min INTEGER,
    quality_flag TEXT,
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

# M8.0: distinct pred_score median 阈值. 低于此判 degenerate, 不进入下游 portfolio.
# 5048 票 / 日的样本下, 100 是 ~2% 多样性, 已经远高于早停 bug 的 ~5-9 桶.
DEGENERATE_DAILY_DISTINCT_THRESHOLD = 100


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
    "feature_pre_filter": False,
}


FEATURE_COLS = ordered_feature_cols(include_dense_v2=True)
ALPHA158_FEATURE_GROUPS = {"base_alpha158", "base_dense_v2_alpha158", "legacy_full"}


def feature_group_uses_alpha158(feature_group: str) -> bool:
    return feature_group in ALPHA158_FEATURE_GROUPS


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


def ensure_model_schema(conn) -> None:
    conn.executescript(MODEL_DDL)
    cols = {row[0] for row in conn.execute("DESCRIBE mart_multidim_model").fetchall()}
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


def latest_params(conn, model_id: str | None) -> tuple[str | None, dict]:
    if model_id:
        row = conn.execute(
            "SELECT model_id, best_params_json FROM mart_multidim_model WHERE model_id = ?",
            (model_id,),
        ).fetchone()
    else:
        default_model_id, _fallback = select_default_model_id(conn)
        row = None
        if default_model_id:
            row = conn.execute(
                "SELECT model_id, best_params_json FROM mart_multidim_model WHERE model_id = ?",
                (default_model_id,),
            ).fetchone()
        if row is None:
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


def _resolve_model_family(requested: str, params: dict[str, Any] | None) -> str:
    if requested != "auto":
        return requested
    return str((params or {}).get("model_family") or "lightgbm")


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


def _table_columns(conn, table: str) -> set[str]:
    duck = conn.raw if hasattr(conn, "raw") else conn
    return {
        row[0]
        for row in duck.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def load_label_dates(
    conn,
    start: str,
    end: str,
    label_name: str,
    *,
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
) -> list[str]:
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        (feature_table,),
    ).fetchall()}
    if label_name not in cols:
        raise RuntimeError(f"{feature_table} 缺少 label 列: {label_name}")
    where = [f"date >= ? AND date <= ? AND {label_name} IS NOT NULL"]
    params = [start, end]
    if feature_set_id and "feature_set_id" in cols:
        where.append("feature_set_id = ?")
        params.append(feature_set_id)
    rows = conn.execute(
        f"""
        SELECT DISTINCT date
        FROM {_quote_ident(feature_table)}
        WHERE {' AND '.join(where)}
        ORDER BY date
        """,
        params,
    ).fetchall()
    return [r[0] for r in rows]


def load_panel_records(
    conn,
    start: str,
    end: str,
    *,
    label_name: str,
    feature_table: str,
    feature_set_id: str | None,
    with_alpha158: bool = True,
) -> list[dict[str, Any]]:
    duck = conn.raw if hasattr(conn, "raw") else conn
    panel_cols = _table_columns(conn, feature_table)
    if label_name not in panel_cols:
        raise RuntimeError(f"{feature_table} 缺少 label 列: {label_name}")

    alpha158_cols: list[str] = []
    alpha158_join = ""
    alpha158_db = Path(__file__).resolve().parent.parent.parent / "data" / "alpha158.duckdb"
    if with_alpha158 and feature_table == "fact_feature_panel" and alpha158_db.exists():
        try:
            duck.execute(f"ATTACH IF NOT EXISTS '{alpha158_db}' AS a158 (READ_ONLY)")
            alpha158_cols = [
                row[0]
                for row in duck.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_catalog='a158'
                      AND table_name='fact_alpha158_panel'
                      AND column_name LIKE 'a158_%'
                    """
                ).fetchall()
            ]
            alpha158_join = (
                "LEFT JOIN a158.fact_alpha158_panel a "
                "ON a.stock_code = p.stock_code AND a.date = CAST(p.date AS DATE)"
            )
            logger.info("Alpha158 join 启用, 增补 %d 列", len(alpha158_cols))
        except Exception as exc:
            logger.warning("Alpha158 attach failed: %s", exc)

    schema_feature_cols = list(FEATURE_COLS)
    for col in TDX_KEEP_FEATURE_COLS:
        if col not in schema_feature_cols:
            schema_feature_cols.append(col)
    feature_cols = [col for col in schema_feature_cols if col in panel_cols]
    regime_expr = "p.regime_flag" if "regime_flag" in panel_cols else "NULL AS regime_flag"
    select_cols = [
        "p.stock_code",
        "p.date",
        regime_expr,
        f"p.{_quote_ident(label_name)} AS label_value",
        *[f"CAST(p.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}" for col in feature_cols],
        *[f"CAST(a.{_quote_ident(col)} AS DOUBLE) AS {_quote_ident(col)}" for col in alpha158_cols],
    ]
    where = [f"p.date >= ? AND p.date <= ? AND p.{_quote_ident(label_name)} IS NOT NULL"]
    params: list[Any] = [start, end]
    if feature_set_id and "feature_set_id" in panel_cols:
        where.append("p.feature_set_id = ?")
        params.append(feature_set_id)
    rows = _records_from_cursor(
        duck.execute(
            f"""
            SELECT {', '.join(select_cols)}
            FROM {_quote_ident(feature_table)} p
            {alpha158_join}
            WHERE {' AND '.join(where)}
            """,
            params,
        )
    )
    for row in rows:
        regime = row.get("regime_flag")
        row["regime_up"] = 1 if regime == "up" else 0
        row["regime_flat"] = 1 if regime == "flat" else 0
        row["regime_down"] = 1 if regime == "down" else 0
    return rows


def resolve_feature_group(name: str, rows: list[dict[str, Any]], *, regime_aware: bool) -> tuple[list[str], str]:
    panel_cols = set(rows[0].keys()) if rows else set()
    a158 = [col for col in panel_cols if col.startswith("a158_")]
    base = [col for col in BASE_FEATURE_COLS if col in panel_cols]
    dense = [col for col in DENSE_V2_FEATURE_COLS if col in panel_cols]
    if name == "base":
        cols = list(base)
        tag = "m7_base_v1"
    elif name == "base_dense_v2":
        cols = base + dense
        tag = "m7_base_dense_v2_v1"
    elif name == "base_alpha158":
        cols = base + a158
        tag = "m7_base_alpha158_v1"
    elif name == "base_dense_v2_alpha158":
        cols = base + dense + a158
        tag = "m7_base_dense_v2_alpha158_v1"
    elif name == "tdx_keep_v1":
        keep = [col for col in TDX_KEEP_FEATURE_COLS if col in panel_cols]
        missing = [col for col in TDX_KEEP_FEATURE_COLS if col not in panel_cols]
        if missing:
            raise RuntimeError(f"tdx_keep_v1 缺少 keep 特征: {missing}")
        cols = base + dense + keep
        tag = TDX_KEEP_CHALLENGER_SCHEMA_VERSION
    elif name == "legacy_full":
        cols = [col for col in FEATURE_COLS if col in panel_cols]
        if a158:
            cols += a158
        tag = "legacy_v0"
    else:
        raise ValueError(f"未知 feature group: {name}")
    if regime_aware:
        for col in REGIME_FEATURE_COLS:
            if col in panel_cols and col not in cols:
                cols.append(col)
        tag = tag + "_regime"
    return cols, tag


def _slice(rows: list[dict[str, Any]], bounds: tuple) -> list[dict[str, Any]]:
    return [row for row in rows if bounds[0] <= row["date"] <= bounds[1]]


def _slice_indices(dates: np.ndarray, bounds: tuple[str, str]) -> np.ndarray:
    return np.flatnonzero((dates >= bounds[0]) & (dates <= bounds[1]))


def _date_bounds(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    dates = [str(row.get("date")) for row in rows if row.get("date") is not None]
    return (min(dates), max(dates)) if dates else (None, None)


def _mean_label(rows: list[dict[str, Any]]) -> float | None:
    values = _values(rows, "label_value")
    return float(sum(values) / len(values)) if values else None


def _mean_label_array(values: np.ndarray) -> float | None:
    return float(np.mean(values)) if len(values) else None


def _score_profile(pred_rows: list[tuple]) -> tuple[float, int]:
    by_date: dict[str, set[float]] = {}
    for row in pred_rows:
        by_date.setdefault(str(row[3]), set()).add(float(row[4]))
    counts = sorted(len(values) for values in by_date.values())
    if not counts:
        return 0.0, 0
    mid = len(counts) // 2
    median = counts[mid] if len(counts) % 2 else (counts[mid - 1] + counts[mid]) / 2.0
    return float(median), int(counts[0])


def _score_profile_columns(pred_columns: dict[str, np.ndarray]) -> tuple[float, int]:
    by_date: dict[str, set[float]] = {}
    for date, score in zip(pred_columns["date"], pred_columns["pred_score"]):
        by_date.setdefault(str(date), set()).add(float(score))
    counts = sorted(len(values) for values in by_date.values())
    if not counts:
        return 0.0, 0
    mid = len(counts) // 2
    median = counts[mid] if len(counts) % 2 else (counts[mid - 1] + counts[mid]) / 2.0
    return float(median), int(counts[0])


def _prediction_rows(run_id: str, fold_id: int, test_rows: list[dict[str, Any]], pred) -> list[tuple]:
    rows = []
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for row, score in zip(test_rows, pred):
        grouped.setdefault(str(row.get("date")), []).append((row, float(score)))
    for date, items in grouped.items():
        scores = [score for _, score in items]
        percentiles = _rank_percentiles(scores)
        rank_by_idx = _rank_desc_min(scores)
        for idx, (row, score) in enumerate(items):
            rows.append((
                run_id,
                fold_id,
                row.get("stock_code"),
                date,
                score,
                rank_by_idx[idx],
                percentiles[idx],
            ))
    return rows


def _prediction_columns(
    run_id: str,
    fold_id: int,
    stock_codes: np.ndarray,
    dates: np.ndarray,
    pred,
) -> dict[str, np.ndarray]:
    ranked = _prediction_column_arrays("_walkforward", stock_codes, dates, pred)
    n = len(ranked["pred_score"])
    return {
        "run_id": np.full(n, run_id, dtype=object),
        "fold_id": np.full(n, int(fold_id), dtype=np.int32),
        "stock_code": ranked["stock_code"],
        "date": ranked["date"],
        "pred_score": ranked["pred_score"],
        "rank_in_date": ranked["rank_in_date"],
        "percentile": ranked["percentile"],
    }


def _filter_prediction_columns_topk(pred_columns: dict[str, np.ndarray], top_k: int) -> dict[str, np.ndarray]:
    if top_k <= 0 or len(pred_columns["pred_score"]) == 0:
        return pred_columns
    keep: list[int] = []
    dates = pred_columns["date"]
    ranks = pred_columns["rank_in_date"]
    for date in np.unique(dates):
        idxs = np.flatnonzero(dates == date)
        order = np.argsort(ranks[idxs], kind="mergesort")
        keep.extend(idxs[order[:top_k]].tolist())
    keep_idx = np.array(sorted(keep), dtype=np.int64)
    return {key: values[keep_idx] for key, values in pred_columns.items()}


def _rank_desc_min(values: list[float]) -> list[int]:
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


def classify_market_state(mean_ret: float | None) -> str | None:
    """M6.3: 按 fold test 期 mean forward_ret_20d 分 up/flat/down.
    阈值: > 3% = up, < -1% = down, else flat (与 §4.5 M6.3 一致)."""
    if mean_ret is None:
        return None
    if mean_ret > 0.03:
        return "up"
    if mean_ret < -0.01:
        return "down"
    return "flat"


def ensure_walkforward_schema(conn) -> None:
    """Schema migration: M8.0 加 quality_flag + score profile 列, 兼容旧表."""
    conn.executescript(DDL)
    duck = conn.raw if hasattr(conn, "raw") else conn
    for col, ddl in [
        ("daily_distinct_score_median", "REAL"),
        ("daily_distinct_score_min", "INTEGER"),
        ("quality_flag", "TEXT"),
    ]:
        try:
            duck.execute(
                f"ALTER TABLE mart_model_walkforward_fold ADD COLUMN IF NOT EXISTS {col} {ddl}"
            )
        except Exception as e:
            logger.warning("ALTER add %s 失败 (大概率已存在): %s", col, e)


def _insert_walkforward_prediction_columns(conn, pred_columns: dict[str, np.ndarray]) -> None:
    if not pred_columns or len(pred_columns["pred_score"]) == 0:
        return
    duck = conn.raw if hasattr(conn, "raw") else conn
    view_name = "tmp_walkforward_prediction_numpy"
    duck.register(view_name, pred_columns)
    try:
        duck.execute(
            f"""
            INSERT OR REPLACE INTO mart_model_walkforward_prediction
            (run_id, fold_id, stock_code, date, pred_score, rank_in_date, percentile)
            SELECT run_id, fold_id, stock_code, date, pred_score, rank_in_date, percentile
            FROM {view_name}
            """
        )
    finally:
        try:
            duck.unregister(view_name)
        except Exception:
            pass


def write_fold(conn, row: dict, pred_rows: list[tuple] | None = None,
               pred_columns: dict[str, np.ndarray] | None = None) -> None:
    ensure_walkforward_schema(conn)
    cols = [
        "run_id", "fold_id", "model_id", "feature_schema_version", "label_name",
        "train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end",
        "n_train", "n_valid", "n_test", "n_features", "params_json",
        "test_ic", "test_rank_ic", "test_top_decile_avg", "test_bottom_decile_avg",
        "test_long_short_spread", "test_winrate_top",
        "test_market_state", "test_mean_forward_ret",
        "best_iteration", "daily_distinct_score_median", "daily_distinct_score_min",
        "quality_flag", "built_at",
    ]
    conn.execute(
        f"INSERT OR REPLACE INTO mart_model_walkforward_fold ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        tuple(row.get(c) for c in cols),
    )
    if pred_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_model_walkforward_prediction
            (run_id, fold_id, stock_code, date, pred_score, rank_in_date, percentile)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            pred_rows,
        )
    if pred_columns:
        _insert_walkforward_prediction_columns(conn, pred_columns)
    conn.commit()



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None, help="复用哪个模型的 best_params; 默认最新")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))  # Phase ψ.5 allowlist: 实验脚本
    parser.add_argument("--label-name", default=DEFAULT_LABEL_NAME)
    parser.add_argument("--train-days", type=int, default=378)
    parser.add_argument("--valid-days", type=int, default=63)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument("--step-days", type=int, default=63)
    parser.add_argument("--max-folds", type=int, default=0)
    parser.add_argument("--regime-aware", action="store_true")
    parser.add_argument("--save-predictions", action="store_true")
    parser.add_argument(
        "--prediction-mode",
        choices=["metrics-only", "topk", "full"],
        default="metrics-only",
        help="metrics-only 不落逐股预测; topk 每日仅落前 K; full 落全部预测",
    )
    parser.add_argument("--prediction-top-k", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--feature-group",
        choices=[
            "base", "base_dense_v2", "base_alpha158", "base_dense_v2_alpha158",
            "tdx_keep_v1", "base_retention_keep", "model_selection_run",
            "legacy_full",
        ],
        default="base_dense_v2",
        help="M7/M9: 显式特征组, 默认 base_dense_v2; legacy_full 仅显式研究使用",
    )
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--retention-decision-run-id", default=None)
    parser.add_argument("--retention-feature-set-id", default=None)
    parser.add_argument(
        "--model-selection-run-id",
        default=None,
        help="feature_group=model_selection_run 时读取 mart_model_selection_run.selected_features_json",
    )
    parser.add_argument(
        "--walkforward-num-round",
        type=int,
        default=400,
        help=("M8.0: 每折 lgb.train 的 num_boost_round. 不再用 valid 段早停 "
              "(对齐 baseline final fit + M6.1 ablation 口径)"),
    )
    parser.add_argument(
        "--model-family",
        choices=["auto", "lightgbm", "lightgbm_ridge_blend"],
        default="auto",
        help="walk-forward 模型族; auto 会从模型 best_params_json 推断",
    )
    parser.add_argument(
        "--update-lifecycle",
        action="store_true",
        help="仅正式研究 run 使用: 所有 fold quality=ok 时才把本次 walk-forward 汇总写回 lifecycle",
    )
    args = parser.parse_args()
    run_started_at = utc_now_iso()
    run_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    conn = get_conn()
    try:
        ensure_model_schema(conn)
        source_model_id, params = latest_params(conn, args.model_id)
        model_family = _resolve_model_family(args.model_family, params)
        selected_feature_cols: list[str] | None = None
        if args.feature_group == "model_selection_run":
            if not args.model_selection_run_id:
                raise RuntimeError("feature_group=model_selection_run 必须指定 --model-selection-run-id")
            model_selection = load_model_selection_run(conn, args.model_selection_run_id)
            selected_feature_cols = list(model_selection["selected_features"])
            selected_label = model_selection.get("label_name")
            if selected_label and selected_label != args.label_name:
                logger.warning(
                    "model_selection_run label_name=%s differs from --label-name=%s; using explicit walk-forward label",
                    selected_label,
                    args.label_name,
                )
        uses_alpha158_features = (
            feature_group_uses_alpha158(args.feature_group)
            or any(col.startswith("a158_") for col in (selected_feature_cols or []))
        )
        if args.dry_run:
            dates = load_label_dates(
                conn,
                args.start,
                args.end,
                args.label_name,
                feature_table=args.feature_table,
                feature_set_id=args.feature_set_id,
            )
        else:
            t_load = time.perf_counter()
            panel = load_panel_arrays(
                conn,
                args.start,
                args.end,
                label_name=args.label_name,
                feature_table=args.feature_table,
                feature_set_id=args.feature_set_id,
                with_alpha158=uses_alpha158_features,
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
            dates = sorted(np.unique(panel.dates).tolist())
        folds = build_folds(dates, args.train_days, args.valid_days, args.test_days, args.step_days)
        if args.max_folds > 0:
            folds = folds[:args.max_folds]
        logger.info("walk-forward folds=%d source_model=%s", len(folds), source_model_id)
        for fold in folds:
            logger.info("fold %d train=%s valid=%s test=%s", fold["fold_id"], fold["train"], fold["valid"], fold["test"])
        if args.dry_run:
            return

        # M7/M9: production 默认走 compact base_dense_v2; Alpha158 组只在显式指定时加载。
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
        logger.info(
            "walkforward feature_group=%s schema_tag=%s eligible_features=%d excluded_features=%d",
            args.feature_group, schema_tag, len(feature_cols), len(excluded_feature_contracts),
        )

        run_id = f"walkforward_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat()
        fold_metrics: list[dict[str, Any]] = []
        prediction_rows_written = 0
        prediction_write_s = 0.0
        for fold in folds:
            fold_t0 = time.perf_counter()
            train_idx = _slice_indices(panel.dates, fold["train"])
            valid_idx = _slice_indices(panel.dates, fold["valid"])
            test_idx = _slice_indices(panel.dates, fold["test"])
            train_valid_idx = np.concatenate([train_idx, valid_idx])
            X_train_valid = panel.matrix(feature_cols, train_valid_idx)
            y_train_valid = panel.labels_for(train_valid_idx)
            X_test = panel.matrix(feature_cols, test_idx)
            y_test = panel.labels_for(test_idx)
            test_dates = panel.dates_for(test_idx)
            dataset = lgb.Dataset(
                X_train_valid,
                label=y_train_valid,
                feature_name=feature_cols,
            )
            if model_family == "lightgbm_ridge_blend":
                lightgbm_model = lgb.train(
                    lightgbm_regression_params(params),
                    dataset,
                    num_boost_round=args.walkforward_num_round,
                )
                ridge_model = make_pipeline(
                    StandardScaler(),
                    Ridge(alpha=float(params.get("ridge_alpha", DEFAULT_RIDGE_ALPHA))),
                )
                ridge_model.fit(X_train_valid, y_train_valid)
                model = CrossSectionalLightGBMRidgeBlend(
                    lightgbm_model=lightgbm_model,
                    ridge_model=ridge_model,
                    ridge_weight=float(params.get("ridge_weight", DEFAULT_RIDGE_WEIGHT)),
                    ridge_alpha=float(params.get("ridge_alpha", DEFAULT_RIDGE_ALPHA)),
                    feature_names=feature_cols,
                )
                pred = model.predict(X_test, dates=test_dates)
            else:
                model = lgb.train(
                    lightgbm_regression_params(params),
                    dataset,
                    num_boost_round=args.walkforward_num_round,
                )
                pred = model.predict(X_test)
            ic, rank_ic = compute_ic(y_test, pred, test_dates)
            dec = decile_metrics(y_test, pred, test_dates)
            test_mean_ret = _mean_label_array(y_test)

            # M8.0: score profile - per test date 的 distinct pred_score 数量
            pred_out_all = _prediction_columns(
                run_id,
                fold["fold_id"],
                panel.codes_for(test_idx),
                panel.dates_for(test_idx),
                pred,
            )
            pred_out = pred_out_all
            if args.prediction_mode == "topk":
                pred_out = _filter_prediction_columns_topk(pred_out_all, args.prediction_top_k)
            distinct_median, distinct_min = _score_profile_columns(pred_out_all)
            quality = "ok" if distinct_median >= DEGENERATE_DAILY_DISTINCT_THRESHOLD else "degenerate"
            train_start, train_end = _date_bounds_array(panel.dates_for(train_idx))
            valid_start, valid_end = _date_bounds_array(panel.dates_for(valid_idx))
            test_start, test_end = _date_bounds_array(panel.dates_for(test_idx))

            row = {
                "run_id": run_id,
                "fold_id": fold["fold_id"],
                "model_id": source_model_id,
                "feature_schema_version": f"walkforward_{schema_tag}",
                "label_name": args.label_name,
                "train_start": train_start, "train_end": train_end,
                "valid_start": valid_start, "valid_end": valid_end,
                "test_start": test_start, "test_end": test_end,
                "n_train": int(len(train_idx)), "n_valid": int(len(valid_idx)), "n_test": int(len(test_idx)),
                "n_features": len(feature_cols),
                "params_json": json.dumps(params, ensure_ascii=False),
                "test_ic": ic, "test_rank_ic": rank_ic,
                "test_top_decile_avg": dec["top_avg"],
                "test_bottom_decile_avg": dec["bot_avg"],
                "test_long_short_spread": dec["spread"],
                "test_winrate_top": dec["winrate_top"],
                "test_mean_forward_ret": test_mean_ret,
                "test_market_state": classify_market_state(test_mean_ret),
                # M8.0: best_iteration 暂存 walkforward_num_round, 表语义即"实际训练轮数"
                "best_iteration": int(args.walkforward_num_round),
                "daily_distinct_score_median": distinct_median,
                "daily_distinct_score_min": distinct_min,
                "quality_flag": quality,
                "built_at": built_at,
            }
            should_write_predictions = args.save_predictions or args.prediction_mode in {"topk", "full"}
            t_write = time.perf_counter()
            write_fold(conn, row, pred_columns=pred_out if should_write_predictions else None)
            write_elapsed = time.perf_counter() - t_write
            if should_write_predictions:
                prediction_write_s += write_elapsed
                prediction_rows_written += int(len(pred_out.get("pred_score", [])))
            fold_duration = time.perf_counter() - fold_t0
            fold_metrics.append({
                "fold_id": fold["fold_id"],
                "rank_ic": rank_ic,
                "ic": ic,
                "spread": dec["spread"],
                "quality": quality,
                "n_train": int(len(train_idx)),
                "n_valid": int(len(valid_idx)),
                "n_test": int(len(test_idx)),
                "duration_s": round(fold_duration, 3),
            })
            logger.info(
                "fold %d IC=%.4f RankIC=%.4f spread=%.4f WR=%.3f distinct_median=%.0f min=%d quality=%s duration=%.1fs",
                fold["fold_id"], ic, rank_ic, dec["spread"], dec["winrate_top"],
                distinct_median, distinct_min, quality, fold_duration,
            )
            if quality == "degenerate":
                logger.warning(
                    "fold %d quality=degenerate (median distinct=%.0f < %d). "
                    "下游 portfolio backtest 应跳过此 run.",
                    fold["fold_id"], distinct_median,
                    DEGENERATE_DAILY_DISTINCT_THRESHOLD,
                )
        if source_model_id and args.update_lifecycle:
            has_degenerate = any(metric.get("quality") != "ok" for metric in fold_metrics)
            if has_degenerate:
                logger.warning("本次 walk-forward 含 degenerate fold, 跳过 lifecycle 汇总更新")
            elif fold_metrics:
                conn.execute(
                    """
                    UPDATE mart_model_lifecycle
                       SET ic_walkforward_avg = (
                           SELECT AVG(test_rank_ic)
                             FROM mart_model_walkforward_fold
                            WHERE run_id = ? AND quality_flag = 'ok'
                       ),
                           ic_walkforward_std = (
                           SELECT STDDEV(test_rank_ic)
                             FROM mart_model_walkforward_fold
                            WHERE run_id = ? AND quality_flag = 'ok'
                       ),
                           updated_at = now()
                     WHERE model_id = ?
                    """,
                    (run_id, run_id, source_model_id),
                )
                conn.commit()
        elif source_model_id:
            logger.info("未传 --update-lifecycle, 本次 walk-forward 不改 lifecycle 汇总")
        duration_s = time.perf_counter() - run_t0
        timings["total_s"] = round(duration_s, 3)
        if prediction_rows_written:
            timings["prediction_write_s"] = round(prediction_write_s, 3)
        rank_ics = [m["rank_ic"] for m in fold_metrics if m.get("rank_ic") is not None]
        avg_rank_ic = sum(rank_ics) / len(rank_ics) if rank_ics else None
        record_pipeline_run(
            conn,
            run_id=run_id,
            pipeline_name="run_multidim_walkforward",
            status="success",
            started_at=run_started_at,
            ended_at=utc_now_iso(),
            duration_s=duration_s,
            commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
            input_tables=[
                args.feature_table,
                "mart_multidim_model",
                *(["mart_model_selection_run"] if args.model_selection_run_id else []),
                *(
                    ["data/alpha158.duckdb:fact_alpha158_panel"]
                    if uses_alpha158_features
                    else []
                ),
            ],
            output_tables=[
                "mart_model_walkforward_fold",
                *(
                    ["mart_model_walkforward_prediction"]
                    if (args.save_predictions or args.prediction_mode in {"topk", "full"})
                    else []
                ),
            ],
            model_id=source_model_id,
            feature_group=args.feature_group,
            label_name=args.label_name,
            holding_period=holding_period_from_label(args.label_name),
            perf_summary={
                "folds": len(fold_metrics),
                "avg_rank_ic": avg_rank_ic,
                "n_features": len(feature_cols),
                "model_family": model_family,
                "model_selection_run_id": args.model_selection_run_id,
                "prediction_mode": args.prediction_mode,
                "save_predictions": bool(args.save_predictions),
                "prediction_top_k": args.prediction_top_k if args.prediction_mode == "topk" else None,
                "prediction_rows_written": prediction_rows_written,
                "cpu_count": os.cpu_count(),
                "timings": timings,
                "fold_metrics": fold_metrics,
                "excluded_feature_contracts": excluded_feature_contracts,
            },
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
