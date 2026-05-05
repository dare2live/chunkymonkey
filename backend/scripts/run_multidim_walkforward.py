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

from services.db import get_conn
from services.model_feature_schema import (
    BASE_FEATURE_COLS,
    DEFAULT_LABEL_NAME,
    DENSE_V2_FEATURE_COLS,
    REGIME_FEATURE_COLS,
    TDX_KEEP_CHALLENGER_SCHEMA_VERSION,
    TDX_KEEP_FEATURE_COLS,
    ordered_feature_cols,
)
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.ml_lifecycle.registry import select_default_model_id
from scripts.run_feature_ablation import (
    compute_ic,
    decile_metrics,
    _dates,
    _matrix,
    _quote_ident,
    _rank_percentiles,
    _records_from_cursor,
    _values,
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
    for col in ("feature_cols_json", "label_name", "feature_schema_version"):
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


def _date_bounds(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    dates = [str(row.get("date")) for row in rows if row.get("date") is not None]
    return (min(dates), max(dates)) if dates else (None, None)


def _mean_label(rows: list[dict[str, Any]]) -> float | None:
    values = _values(rows, "label_value")
    return float(sum(values) / len(values)) if values else None


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


def write_fold(conn, row: dict, pred_rows: list[tuple] | None) -> None:
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
        choices=["base", "base_dense_v2", "base_alpha158", "base_dense_v2_alpha158", "tdx_keep_v1", "legacy_full"],
        default="base_dense_v2",
        help="M7/M9: 显式特征组, 默认 base_dense_v2; legacy_full 仅显式研究使用",
    )
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument(
        "--walkforward-num-round",
        type=int,
        default=400,
        help=("M8.0: 每折 lgb.train 的 num_boost_round. 不再用 valid 段早停 "
              "(对齐 baseline final fit + M6.1 ablation 口径)"),
    )
    args = parser.parse_args()
    run_started_at = utc_now_iso()
    run_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    conn = get_conn()
    try:
        ensure_model_schema(conn)
        source_model_id, params = latest_params(conn, args.model_id)
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
            records = load_panel_records(
                conn,
                args.start,
                args.end,
                label_name=args.label_name,
                feature_table=args.feature_table,
                feature_set_id=args.feature_set_id,
                with_alpha158=feature_group_uses_alpha158(args.feature_group),
            )
            timings["load_panel_s"] = round(time.perf_counter() - t_load, 3)
            dates = sorted({row["date"] for row in records})
        folds = build_folds(dates, args.train_days, args.valid_days, args.test_days, args.step_days)
        if args.max_folds > 0:
            folds = folds[:args.max_folds]
        logger.info("walk-forward folds=%d source_model=%s", len(folds), source_model_id)
        for fold in folds:
            logger.info("fold %d train=%s valid=%s test=%s", fold["fold_id"], fold["train"], fold["valid"], fold["test"])
        if args.dry_run:
            return

        # records is loaded above in non-dry-run branch.
        # M7/M9: production 默认走 compact base_dense_v2; Alpha158 组只在显式指定时加载。
        feature_cols, schema_tag = resolve_feature_group(
            args.feature_group, records, regime_aware=args.regime_aware
        )
        logger.info(
            "walkforward feature_group=%s schema_tag=%s 特征数=%d",
            args.feature_group, schema_tag, len(feature_cols),
        )

        run_id = f"walkforward_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat()
        fold_metrics: list[dict[str, Any]] = []
        for fold in folds:
            fold_t0 = time.perf_counter()
            train = _slice(records, fold["train"])
            valid = _slice(records, fold["valid"])
            test = _slice(records, fold["test"])
            # M8.0: train + valid 合并, 无 valid_sets / 无 early_stopping. 对齐 baseline final fit.
            train_valid = [*train, *valid]
            dataset = lgb.Dataset(
                _matrix(train_valid, feature_cols),
                label=_values(train_valid, "label_value"),
                feature_name=feature_cols,
            )
            model = lgb.train(
                params,
                dataset,
                num_boost_round=args.walkforward_num_round,
            )
            pred = model.predict(_matrix(test, feature_cols))
            ic, rank_ic = compute_ic(_values(test, "label_value"), pred, _dates(test))
            dec = decile_metrics(_values(test, "label_value"), pred, _dates(test))
            test_mean_ret = _mean_label(test)

            # M8.0: score profile - per test date 的 distinct pred_score 数量
            pred_out_all = _prediction_rows(run_id, fold["fold_id"], test, pred)
            pred_out = pred_out_all
            if args.prediction_mode == "topk":
                by_date: dict[str, list[tuple]] = {}
                for item in pred_out_all:
                    by_date.setdefault(str(item[3]), []).append(item)
                pred_out = [
                    item
                    for items in by_date.values()
                    for item in sorted(items, key=lambda row: row[5])[: args.prediction_top_k]
                ]
            distinct_median, distinct_min = _score_profile(pred_out_all)
            quality = "ok" if distinct_median >= DEGENERATE_DAILY_DISTINCT_THRESHOLD else "degenerate"
            train_start, train_end = _date_bounds(train)
            valid_start, valid_end = _date_bounds(valid)
            test_start, test_end = _date_bounds(test)

            row = {
                "run_id": run_id,
                "fold_id": fold["fold_id"],
                "model_id": source_model_id,
                "feature_schema_version": f"walkforward_{schema_tag}",
                "label_name": args.label_name,
                "train_start": train_start, "train_end": train_end,
                "valid_start": valid_start, "valid_end": valid_end,
                "test_start": test_start, "test_end": test_end,
                "n_train": len(train), "n_valid": len(valid), "n_test": len(test),
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
            write_fold(conn, row, pred_out if should_write_predictions else None)
            fold_duration = time.perf_counter() - fold_t0
            fold_metrics.append({
                "fold_id": fold["fold_id"],
                "rank_ic": rank_ic,
                "ic": ic,
                "spread": dec["spread"],
                "quality": quality,
                "n_train": len(train),
                "n_valid": len(valid),
                "n_test": len(test),
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
        if source_model_id:
            conn.execute(
                """
                UPDATE mart_model_lifecycle
                   SET ic_walkforward_avg = (
                       SELECT AVG(test_rank_ic)
                         FROM mart_model_walkforward_fold
                        WHERE run_id = ?
                   ),
                       ic_walkforward_std = (
                       SELECT STDDEV(test_rank_ic)
                         FROM mart_model_walkforward_fold
                        WHERE run_id = ?
                   ),
                       updated_at = now()
                 WHERE model_id = ?
                """,
                (run_id, run_id, source_model_id),
            )
            conn.commit()
        duration_s = time.perf_counter() - run_t0
        timings["total_s"] = round(duration_s, 3)
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
                *(
                    ["data/alpha158.duckdb:fact_alpha158_panel"]
                    if feature_group_uses_alpha158(args.feature_group)
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
            holding_period=20 if args.label_name.endswith("_20d") else None,
            perf_summary={
                "folds": len(fold_metrics),
                "avg_rank_ic": avg_rank_ic,
                "n_features": len(feature_cols),
                "prediction_mode": "full" if args.save_predictions else args.prediction_mode,
                "cpu_count": os.cpu_count(),
                "timings": timings,
                "fold_metrics": fold_metrics,
            },
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
