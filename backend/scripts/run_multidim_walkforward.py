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
from services.ml_lifecycle.registry import select_default_model_id
from scripts.train_multidim_model import (
    FEATURE_COLS,
    compute_ic,
    decile_metrics,
    ensure_model_schema,
    load_panel,
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
        FROM {feature_table}
        WHERE {' AND '.join(where)}
        ORDER BY date
        """,
        params,
    ).fetchall()
    return [r[0] for r in rows]


def _slice(df: pd.DataFrame, bounds: tuple) -> pd.DataFrame:
    return df[(df["date"] >= bounds[0]) & (df["date"] <= bounds[1])].copy()


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


def write_fold(conn, row: dict, pred_df: pd.DataFrame | None) -> None:
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
    parser.add_argument(
        "--feature-group",
        choices=["base", "base_dense_v2", "base_alpha158", "base_dense_v2_alpha158", "tdx_keep_v1", "legacy_full"],
        default="legacy_full",
        help="M7: 显式特征组, 与 train_multidim_model 同名同义",
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
            df = load_panel(
                conn,
                args.start,
                args.end,
                label_name=args.label_name,
                feature_table=args.feature_table,
                feature_set_id=args.feature_set_id,
            )
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
        # M7: 显式 feature group, legacy_full = 旧默认 (BASE+a158+regime), 与原 walkforward 一致
        from scripts.train_multidim_model import resolve_feature_group  # 复用同一定义, 避免漂移
        feature_cols, schema_tag = resolve_feature_group(
            args.feature_group, df, regime_aware=args.regime_aware
        )
        logger.info(
            "walkforward feature_group=%s schema_tag=%s 特征数=%d",
            args.feature_group, schema_tag, len(feature_cols),
        )

        run_id = f"walkforward_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat()
        for fold in folds:
            train = _slice(df, fold["train"])
            valid = _slice(df, fold["valid"])
            test = _slice(df, fold["test"])
            # M8.0: train + valid 合并, 无 valid_sets / 无 early_stopping. 对齐 baseline final fit.
            train_valid = pd.concat([train, valid], ignore_index=True)
            dataset = lgb.Dataset(
                train_valid[feature_cols].values,
                label=train_valid["label_value"].values,
                feature_name=feature_cols,
            )
            model = lgb.train(
                params,
                dataset,
                num_boost_round=args.walkforward_num_round,
            )
            pred = model.predict(test[feature_cols].values)
            ic, rank_ic = compute_ic(test["label_value"].values, pred, test["date"].values)
            dec = decile_metrics(test["label_value"].values, pred, test["date"].values)
            test_mean_ret = float(test["label_value"].mean()) if len(test) else None

            # M8.0: score profile - per test date 的 distinct pred_score 数量
            test_pred_df = pd.DataFrame({
                "date": test["date"].values,
                "pred_score": pred,
            })
            distinct_per_day = test_pred_df.groupby("date")["pred_score"].nunique()
            distinct_median = float(distinct_per_day.median()) if len(distinct_per_day) else 0.0
            distinct_min = int(distinct_per_day.min()) if len(distinct_per_day) else 0
            quality = "ok" if distinct_median >= DEGENERATE_DAILY_DISTINCT_THRESHOLD else "degenerate"

            row = {
                "run_id": run_id,
                "fold_id": fold["fold_id"],
                "model_id": source_model_id,
                "feature_schema_version": f"walkforward_{schema_tag}",
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
                "test_mean_forward_ret": test_mean_ret,
                "test_market_state": classify_market_state(test_mean_ret),
                # M8.0: best_iteration 暂存 walkforward_num_round, 表语义即"实际训练轮数"
                "best_iteration": int(args.walkforward_num_round),
                "daily_distinct_score_median": distinct_median,
                "daily_distinct_score_min": distinct_min,
                "quality_flag": quality,
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
                "fold %d IC=%.4f RankIC=%.4f spread=%.4f WR=%.3f distinct_median=%.0f min=%d quality=%s",
                fold["fold_id"], ic, rank_ic, dec["spread"], dec["winrate_top"],
                distinct_median, distinct_min, quality,
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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
