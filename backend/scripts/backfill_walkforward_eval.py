#!/usr/bin/env python3
"""Backfill mart_p0b_walkforward_eval per-window RankIC from mart_p0b_oos_predictions.

解锁 promote_champion 阻断 (rank_ic NULL 拒绝 register).

逻辑:
- 按 mart_p0b_oos_predictions 取每个 (model_id, test_start, test_end) window
- JOIN mart_p0a_label_panel 拿 fwd_cost_after_20d
- 计算 Spearman rank IC per window
- INSERT mart_p0b_walkforward_eval (model_id, window_idx, rank_ic, ...)

Usage:
    PYTHONPATH=backend python backend/scripts/backfill_walkforward_eval.py \
        --model-id lgbm_20260517_governance_v1_20d --run-id walkforward_eval_session
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
from scipy.stats import spearmanr

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_walkforward_eval")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill walkforward eval RankIC")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--run-id", default=f"walkforward_eval_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    args = parser.parse_args()

    log.info(f"=== Backfill walkforward eval RankIC ===")
    log.info(f"  model_id: {args.model_id}, run_id: {args.run_id}")

    con = duckdb.connect(args.smartmoney_db)
    try:
        # 取每个 (test_start, test_end) window 列表
        windows = con.execute("""
            SELECT DISTINCT test_start, test_end, train_start, train_end,
                   model_version, feature_version, label_version, walk_forward_mode
            FROM mart_p0b_oos_predictions
            WHERE model_id = ?
            ORDER BY test_start
        """, [args.model_id]).fetchall()
        log.info(f"  {len(windows)} OOS windows found")

        # Calc Spearman IC per window
        rows_to_insert = []
        rank_ics = []
        for idx, (test_start, test_end, train_start, train_end, mv, fv, lv, wfm) in enumerate(windows):
            df = con.execute("""
                SELECT p.score, l.fwd_cost_after_20d AS fwd_ret
                FROM mart_p0b_oos_predictions p
                JOIN mart_p0a_label_panel l
                  ON p.signal_date = l.signal_date AND p.stock_code = l.stock_code
                WHERE p.model_id = ? AND p.test_start = ? AND p.test_end = ?
                  AND p.score IS NOT NULL AND l.fwd_cost_after_20d IS NOT NULL
            """, [args.model_id, test_start, test_end]).fetchdf()
            if len(df) < 30:
                log.warning(f"  window {idx} ({test_start}~{test_end}): n={len(df)} < 30, skip")
                continue
            ic, _ = spearmanr(df["score"], df["fwd_ret"])
            n_test = len(df)
            n_train = con.execute("""
                SELECT COUNT(*) FROM mart_p0a_label_panel
                WHERE signal_date >= ? AND signal_date <= ?
            """, [train_start, train_end]).fetchone()[0]
            rows_to_insert.append((
                args.run_id, idx, args.model_id, mv, fv, lv, wfm,
                train_start, train_end, test_start, test_end,
                n_train, n_test, float(ic), 0.0,  # rank_ic_ir 后续 batch 算
                False, datetime.now(timezone.utc).isoformat(),
            ))
            rank_ics.append(float(ic))
            log.info(f"  window {idx} ({test_start} ~ {test_end}): n={n_test}, rank_ic={ic:+.4f}")

        # Compute rank_ic_ir = mean / std × sqrt(n_windows) — 跨 window 信号一致性
        if len(rank_ics) >= 2:
            arr = np.array(rank_ics)
            ir = float(arr.mean() / arr.std(ddof=1) * np.sqrt(len(arr)))
            log.info(f"  rank_ic mean: {arr.mean():+.4f}, std: {arr.std(ddof=1):.4f}, IR: {ir:+.3f}")
            # Update rank_ic_ir in rows
            rows_to_insert = [r[:14] + (ir,) + r[15:] for r in rows_to_insert]

        # Insert
        if rows_to_insert:
            con.executemany("""
                INSERT OR REPLACE INTO mart_p0b_walkforward_eval (
                    run_id, window_idx, model_id, model_version, feature_version, label_version,
                    walk_forward_mode, train_start, train_end, test_start, test_end,
                    n_train, n_test, rank_ic, rank_ic_ir, is_final_holdout, built_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows_to_insert)
            log.info(f"  inserted: {len(rows_to_insert)} rows into mart_p0b_walkforward_eval")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
