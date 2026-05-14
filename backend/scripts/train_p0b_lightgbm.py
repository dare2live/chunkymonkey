#!/usr/bin/env python3
"""P0b LightGBM walk-forward 训练 + 评估 CLI.

读 mart_p0a_feature_label_panel → walk-forward → 写 mart_p0b_oos_predictions
+ mart_p0b_walkforward_eval.

用法:
    PYTHONPATH=backend python backend/scripts/train_p0b_lightgbm.py \
        --label fwd_cost_after_10d \
        --run-id p0b_baseline_10d

输出:
    - mart_p0b_oos_predictions: OOS 预测 (供 P0c selector 用)
    - mart_p0b_walkforward_eval: 每 window 的 RankIC
    - stdout: stitched OOS RankIC + Gate PASS/FAIL
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.ml_ranking import train_lightgbm_walkforward
from services.ml_ranking.ddl import create_p0b_ddl
from services.ml_ranking.lightgbm_walkforward import (
    LightGBMWalkForwardConfig,
    WalkForwardResult,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("train_p0b")


def _load_rows(conn) -> list[dict]:
    """从 mart_p0a_feature_label_panel 读所有 row 转 dict."""
    cur = conn.execute("SELECT * FROM mart_p0a_feature_label_panel ORDER BY signal_date, stock_code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _write_predictions(conn, result: WalkForwardResult, model_id: str, run_id: str) -> int:
    """Write OOS predictions to mart_p0b_oos_predictions."""
    n_written = 0
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    # Each window has test_predictions (already stitched in result)
    for w in result.windows:
        for p in w.test_predictions:
            conn.execute(
                """
                INSERT INTO mart_p0b_oos_predictions (
                    stock_code, signal_date, score,
                    fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                    model_id, model_version, feature_version, label_version,
                    walk_forward_mode,
                    train_start, train_end, test_start, test_end,
                    is_final_holdout, built_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (stock_code, signal_date, model_id) DO UPDATE
                  SET score=excluded.score, built_at=excluded.built_at
                """,
                [
                    p["stock_code"], p["signal_date"], p.get("score"),
                    p.get("fwd_cost_after_5d"),
                    p.get(result.config.label_field) if result.config.label_field == "fwd_cost_after_10d" else None,
                    p.get("fwd_cost_after_20d"),
                    model_id, "p0b_baseline_v1", "p0a_v1", "p0a_v1",
                    "expanding_monthly",
                    w.train_start, w.train_end, w.test_start, w.test_end,
                    False, built_at,
                ],
            )
            n_written += 1
    return n_written


def _write_eval(conn, result: WalkForwardResult, model_id: str, run_id: str) -> int:
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    for i, w in enumerate(result.windows):
        conn.execute(
            """
            INSERT INTO mart_p0b_walkforward_eval (
                run_id, window_idx, model_id, model_version, feature_version,
                label_version, walk_forward_mode,
                train_start, train_end, test_start, test_end,
                n_train, n_test,
                rank_ic, rank_ic_ir, is_final_holdout, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id, i, model_id, "p0b_baseline_v1", "p0a_v1",
                "p0a_v1", "expanding_monthly",
                w.train_start, w.train_end, w.test_start, w.test_end,
                w.n_train, w.n_test,
                w.rank_ic if w.rank_ic == w.rank_ic else None,  # NaN→None
                w.rank_ic_ir if w.rank_ic_ir == w.rank_ic_ir else None,
                False, built_at,
            ],
        )
    return len(result.windows)


def main() -> int:
    parser = argparse.ArgumentParser(description="P0b LightGBM walk-forward training")
    parser.add_argument("--label", default="fwd_cost_after_10d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--run-id", default=None, help="Custom run_id (default UUID)")
    parser.add_argument("--model-id", default="lgbm_baseline_v1")
    parser.add_argument("--min-train-months", type=int, default=None)
    parser.add_argument("--forward-months", type=int, default=None)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--num-leaves", type=int, default=31)
    args = parser.parse_args()

    run_id = args.run_id or f"p0b_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, model_id={args.model_id}, label={args.label}")

    # Load rows
    conn = duck_connect(str(DB_PATH))
    try:
        create_p0b_ddl(conn)
        log.info("Loading rows from mart_p0a_feature_label_panel...")
        rows = _load_rows(conn)
        log.info(f"Loaded {len(rows):,} rows")
        if not rows:
            log.error("No rows in mart_p0a_feature_label_panel — run P0a panel build first")
            return 1

        cfg = LightGBMWalkForwardConfig(
            label_field=args.label,
            min_train_months=args.min_train_months,
            forward_months=args.forward_months,
            n_estimators=args.n_estimators,
            learning_rate=args.learning_rate,
            num_leaves=args.num_leaves,
        )

        log.info(f"Training: {cfg}")
        result = train_lightgbm_walkforward(rows, cfg)
        log.info(f"n_windows: {result.n_windows}; feature columns: {len(result.feature_columns)}")
        log.info(f"OOS RankIC mean: {result.overall_rank_ic.mean_rank_ic:.4f}  IR: {result.overall_rank_ic.ic_ir:.4f}")
        log.info(f"OOS n_dates: {result.overall_rank_ic.n_dates}; skipped: {result.overall_rank_ic.n_dates_skipped}")

        # Write outputs
        log.info("Writing predictions + eval...")
        n_preds = _write_predictions(conn, result, args.model_id, run_id)
        n_eval = _write_eval(conn, result, args.model_id, run_id)
        log.info(f"Wrote {n_preds:,} predictions + {n_eval} window eval rows")

        # Gate
        if result.passed_gate:
            log.info("✓ P0b gate PASS (RankIC ≥ 0.03 AND n_dates ≥ 30)")
            return 0
        log.warning(f"✗ P0b gate FAIL (RankIC={result.overall_rank_ic.mean_rank_ic:.4f}, "
                    f"n_dates={result.overall_rank_ic.n_dates})")
        return 0  # Not exit-1, only WARN — let user inspect.
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
