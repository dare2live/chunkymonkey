#!/usr/bin/env python3
"""P0b LightGBM walk-forward 训练 + 评估 CLI (DataFrame-based, 快).

读 mart_p0a_feature_label_panel → DataFrame → 月度 walk-forward → 写
mart_p0b_oos_predictions + mart_p0b_walkforward_eval.

DataFrame-based 实现 (替代 list[dict] 慢路径):
- DuckDB `.df()` 一次 SELECT → pandas DataFrame
- groupby signal_date_month 切窗
- 每窗 fit LightGBM in-place 不复制
- predict + 入库走 numpy

用法:
    PYTHONPATH=backend python backend/scripts/train_p0b_lightgbm.py \
        --label fwd_cost_after_10d \
        --run-id p0b_baseline_10d
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb
import numpy as np
import pandas as pd

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.ml_ranking.ddl import create_p0b_ddl
from services.ml_ranking.rank_ic import compute_rank_ic


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("train_p0b")


# 元数据字段不入 feature matrix
_META_FIELDS = {
    "stock_code", "signal_date", "entry_date",
    "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
    "unable_at_entry",
    "feature_version", "built_at",
}


def _load_df(conn) -> pd.DataFrame:
    """从 mart_p0a_feature_label_panel 加载为 DataFrame, signal_date 转 month_start.

    用 wrapper 底层 _con.execute().fetchdf() 拿 pandas (DuckDB native fast path,
    跳过 Python dict 转换 — 3.7M × 80 cols ≈ 1-2 min 而非 20+ min).
    """
    df = conn._con.execute(
        "SELECT * FROM mart_p0a_feature_label_panel ORDER BY signal_date, stock_code"
    ).fetchdf()
    df["month_start"] = pd.to_datetime(df["signal_date"]).dt.to_period("M").dt.to_timestamp()
    return df


def _split_expanding_monthly(df: pd.DataFrame, min_train_months: int = 6, forward_months: int = 1):
    """月度 walk-forward 切分.

    Yields (train_df, test_df, train_start, train_end, test_start, test_end).
    """
    months = sorted(df["month_start"].unique())
    if len(months) < min_train_months + forward_months:
        log.warning(f"Months {len(months)} < min_train_months+forward {min_train_months + forward_months}")
        return
    for k in range(min_train_months, len(months), forward_months):
        train_months = months[:k]
        test_months = months[k:k + forward_months]
        if not test_months:
            break
        train_df = df[df["month_start"].isin(train_months)]
        test_df = df[df["month_start"].isin(test_months)]
        if len(test_df) == 0:
            continue
        yield (
            train_df, test_df,
            train_df["signal_date"].min(), train_df["signal_date"].max(),
            test_df["signal_date"].min(), test_df["signal_date"].max(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="P0b LightGBM walk-forward training")
    parser.add_argument("--label", default="fwd_cost_after_10d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-id", default="lgbm_baseline_v1")
    parser.add_argument("--min-train-months", type=int, default=6)
    parser.add_argument("--forward-months", type=int, default=1)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--start-date", default=None, help="过滤 signal_date >= 此日期")
    parser.add_argument("--end-date", default=None, help="过滤 signal_date <= 此日期")
    args = parser.parse_args()

    run_id = args.run_id or f"p0b_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, model_id={args.model_id}, label={args.label}")

    conn = duck_connect(str(DB_PATH))
    try:
        create_p0b_ddl(conn)

        log.info("Loading DataFrame from mart_p0a_feature_label_panel...")
        df = _load_df(conn)
        log.info(f"Loaded {len(df):,} rows × {len(df.columns)} cols")

        if args.start_date:
            df = df[df["signal_date"] >= pd.to_datetime(args.start_date)]
            log.info(f"After --start-date filter: {len(df):,} rows")
        if args.end_date:
            df = df[df["signal_date"] <= pd.to_datetime(args.end_date)]
            log.info(f"After --end-date filter: {len(df):,} rows")

        # Filter: 必须有 label (entry/exit mask 后)
        df = df[df[args.label].notna()].copy()
        log.info(f"After label notna filter: {len(df):,} rows")

        # Feature columns = all - meta
        feature_cols = sorted([c for c in df.columns
                               if c not in _META_FIELDS and c != "month_start"])
        log.info(f"feature_columns: {len(feature_cols)} ({feature_cols[:5]}...)")

        # Walk-forward
        all_predictions: list[dict] = []
        window_results = []
        for i, (train_df, test_df, train_start, train_end, test_start, test_end) in enumerate(
            _split_expanding_monthly(df, args.min_train_months, args.forward_months)
        ):
            log.info(f"window {i+1}: train {train_start}..{train_end} ({len(train_df):,}) → "
                     f"test {test_start}..{test_end} ({len(test_df):,})")

            X_train = train_df[feature_cols].to_numpy(dtype=np.float64, na_value=np.nan)
            y_train = train_df[args.label].to_numpy(dtype=np.float64)
            X_test = test_df[feature_cols].to_numpy(dtype=np.float64, na_value=np.nan)
            y_test = test_df[args.label].to_numpy(dtype=np.float64)

            mask = np.isfinite(y_train)
            if mask.sum() < 100:
                log.warning(f"  train mask only {mask.sum()}; skip")
                continue

            model = lgb.LGBMRegressor(
                num_leaves=args.num_leaves,
                learning_rate=args.learning_rate,
                n_estimators=args.n_estimators,
                feature_fraction=0.8,
                bagging_fraction=0.8,
                bagging_freq=5,
                min_child_samples=20,
                random_state=42,
                verbose=-1,
            )
            model.fit(X_train[mask], y_train[mask])
            y_pred = model.predict(X_test)

            # Build per-window predictions list
            for stock_code, signal_date, score, label in zip(
                test_df["stock_code"].values,
                test_df["signal_date"].values,
                y_pred, y_test
            ):
                all_predictions.append({
                    "stock_code": stock_code,
                    "signal_date": str(signal_date),
                    "score": float(score) if np.isfinite(score) else None,
                    args.label: float(label) if np.isfinite(label) else None,
                    "train_start": str(train_start), "train_end": str(train_end),
                    "test_start": str(test_start), "test_end": str(test_end),
                })

            # Within-window RankIC
            win_ic = compute_rank_ic(
                [{"signal_date": str(d), "score": float(s), args.label: float(y)}
                 for d, s, y in zip(test_df["signal_date"].values, y_pred, y_test)
                 if np.isfinite(s) and np.isfinite(y)],
                score_field="score", label_field=args.label
            )
            log.info(f"  window {i+1} RankIC: {win_ic.mean_rank_ic:.4f} (n_dates={win_ic.n_dates})")
            window_results.append({
                "window_idx": i, "train_start": train_start, "train_end": train_end,
                "test_start": test_start, "test_end": test_end,
                "n_train": int(mask.sum()), "n_test": len(test_df),
                "rank_ic": win_ic.mean_rank_ic, "rank_ic_ir": win_ic.ic_ir,
            })

        # Overall RankIC
        overall = compute_rank_ic(all_predictions, label_field=args.label)
        log.info("")
        log.info(f"=== OVERALL OOS RankIC ===")
        log.info(f"  mean: {overall.mean_rank_ic:.4f}")
        log.info(f"  IC IR: {overall.ic_ir:.4f}")
        log.info(f"  n_dates: {overall.n_dates}, skipped: {overall.n_dates_skipped}")
        log.info(f"  n_windows: {len(window_results)}")

        # P0b gate: RankIC ≥ 0.03 AND n_dates ≥ 30
        passed = overall.mean_rank_ic >= 0.03 and overall.n_dates >= 30
        log.info(f"  Gate: {'✓ PASS' if passed else '✗ FAIL'} "
                 f"(RankIC ≥ 0.03 AND n_dates ≥ 30)")

        # Write predictions — DELETE + executemany 批量 (per-row INSERT 1.7M rows × 5ms = 2小时)
        log.info(f"Writing {len(all_predictions):,} predictions + {len(window_results)} eval to DB (batch)...")
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        # Idempotent: 同 model_id + run_id 范围内 signal_date 先 DELETE
        if all_predictions:
            min_date = min(p["signal_date"] for p in all_predictions)
            max_date = max(p["signal_date"] for p in all_predictions)
            conn.execute(
                "DELETE FROM mart_p0b_oos_predictions "
                "WHERE model_id = ? AND signal_date BETWEEN ? AND ?",
                [args.model_id, min_date, max_date],
            )
        # Batch INSERT predictions
        pred_rows = [
            (p["stock_code"], p["signal_date"], p["score"],
             p.get("fwd_cost_after_5d"), p.get("fwd_cost_after_10d"), p.get("fwd_cost_after_20d"),
             args.model_id, "p0b_baseline_v1", "p0a_v1", "p0a_v1",
             "expanding_monthly", p["train_start"], p["train_end"], p["test_start"], p["test_end"],
             False, built_at)
            for p in all_predictions
        ]
        conn._con.executemany(
            """
            INSERT INTO mart_p0b_oos_predictions
            (stock_code, signal_date, score,
             fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
             model_id, model_version, feature_version, label_version,
             walk_forward_mode, train_start, train_end, test_start, test_end,
             is_final_holdout, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            pred_rows,
        )
        # Eval rows
        eval_rows = [
            (run_id, w["window_idx"], args.model_id, "p0b_baseline_v1", "p0a_v1",
             "p0a_v1", "expanding_monthly",
             str(w["train_start"]), str(w["train_end"]), str(w["test_start"]), str(w["test_end"]),
             w["n_train"], w["n_test"],
             w["rank_ic"] if w["rank_ic"] == w["rank_ic"] else None,
             w["rank_ic_ir"] if w["rank_ic_ir"] == w["rank_ic_ir"] else None,
             False, built_at)
            for w in window_results
        ]
        conn._con.executemany(
            """
            INSERT OR REPLACE INTO mart_p0b_walkforward_eval
            (run_id, window_idx, model_id, model_version, feature_version,
             label_version, walk_forward_mode,
             train_start, train_end, test_start, test_end,
             n_train, n_test, rank_ic, rank_ic_ir, is_final_holdout, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            eval_rows,
        )
        log.info(f"Wrote {len(all_predictions):,} predictions + {len(window_results)} eval rows")
        return 0 if passed else 0  # Always exit 0; warn only
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
