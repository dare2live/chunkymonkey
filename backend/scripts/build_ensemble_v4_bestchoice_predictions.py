#!/usr/bin/env python3
"""Ensemble V4 champion + BestChoice predictions for paper_sim alpha-additivity test.

Method:
- Per (signal_date, stock_code): rank-percentile V4 score + rank-percentile BC confidence
- Combined score = V4_rank_pct + BC_rank_pct (max 2.0)
- Stocks with no BC signal: BC_rank_pct = 0 (only V4 ranking counts)
- INSERT as new model_id=ensemble_v4_bestchoice_v1 in mart_p0b_lambdamart_v6_predictions

Then run:
  PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py \\
    --lambdamart-model-id ensemble_v4_bestchoice_v1

Compare Sharpe(ensemble) vs Sharpe(V4)=0.65 vs Sharpe(BC)=1.10.
If Sharpe(ensemble) > max(V4, BC) → BestChoice 真给主项目添 alpha.
If Sharpe(ensemble) ≈ V4 or BC → 不添 / selection bias artifact.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402
from services.bestchoice_config import DEFAULT_BESTCHOICE_PIPELINE_CONFIG  # noqa: E402

# rule-compliance: ok evidence=plan §5 Phase 5+ ensemble naming
ENSEMBLE_MODEL_ID = "ensemble_v4_bestchoice_v1"
V4_MODEL_ID = "lgbm_20260517_governance_v1_20d"
BC_RUN_ID = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.bc_run_id
ENSEMBLE_TRAIN_START = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.ensemble_train_start_date
ENSEMBLE_TRAIN_END = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.ensemble_train_end_date
ENSEMBLE_TEST_START = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.ensemble_test_start_date
ENSEMBLE_TEST_END = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.ensemble_test_end_date


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    args = p.parse_args()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with connect(args.db_path, read_only=False) as conn:
        # Delete existing ensemble rows (idempotent)
        conn.execute(
            "DELETE FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
            [ENSEMBLE_MODEL_ID],
        )

        # Build ensemble via SQL: rank-percentile combine V4 + BC per signal_date
        # rule-compliance: ok evidence=ensemble naming + period from V4 OOS / BC feed overlap
        conn.execute(
            f"""
            INSERT INTO mart_p0b_lambdamart_v6_predictions (
                stock_code, signal_date, score,
                fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                model_id, model_version, feature_version, label_version,
                walk_forward_mode, train_start, train_end, test_start, test_end,
                is_final_holdout, built_at, trade_date_dt
            )
            WITH v4 AS (
              SELECT signal_date, stock_code, score AS v4_score,
                     fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d
                FROM mart_p0b_oos_predictions
               WHERE model_id = '{V4_MODEL_ID}'
                 AND score IS NOT NULL
            ),
            bc AS (
              SELECT signal_date, stock_code, confidence_score AS bc_score, buy_date
                FROM mart_daily_formula_candidate_bestchoice_v1
               WHERE run_id = '{BC_RUN_ID}'
            ),
            joined AS (
              SELECT v4.signal_date, v4.stock_code,
                     v4.v4_score,
                     COALESCE(bc.bc_score, 0) AS bc_score,
                     v4.fwd_cost_after_5d, v4.fwd_cost_after_10d, v4.fwd_cost_after_20d,
                     v4.signal_date AS trade_date_dt
                FROM v4
                LEFT JOIN bc ON bc.signal_date = v4.signal_date AND bc.stock_code = v4.stock_code
            ),
            ranked AS (
              SELECT signal_date, stock_code,
                     fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                     trade_date_dt,
                     PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY v4_score) AS v4_rank_pct,
                     CASE WHEN bc_score > 0 THEN PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY bc_score)
                          ELSE 0 END AS bc_rank_pct
                FROM joined
            )
            SELECT stock_code, signal_date,
                   (v4_rank_pct + bc_rank_pct) AS score,
                   fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                   '{ENSEMBLE_MODEL_ID}' AS model_id,
                   'ensemble_v1' AS model_version,
                   'p0a_v3+formula_engine_v1' AS feature_version,
                   'p0a_v2_governance_v1' AS label_version,
                   'NA' AS walk_forward_mode,
                   '{ENSEMBLE_TRAIN_START}' AS train_start,
                   '{ENSEMBLE_TRAIN_END}' AS train_end,
                   '{ENSEMBLE_TEST_START}' AS test_start,
                   '{ENSEMBLE_TEST_END}' AS test_end,
                   FALSE AS is_final_holdout,
                   '{now_utc}' AS built_at,
                   trade_date_dt
              FROM ranked
            """
        )
        conn.commit()

        audit = conn.execute(
            """
            SELECT COUNT(*) AS n_rows,
                   COUNT(DISTINCT signal_date) AS n_dates,
                   COUNT(DISTINCT stock_code) AS n_stocks,
                   MIN(score), MAX(score), AVG(score),
                   COUNT(*) FILTER (WHERE score > 1.0) AS n_dual_signal
              FROM mart_p0b_lambdamart_v6_predictions
             WHERE model_id = ?
            """,
            [ENSEMBLE_MODEL_ID],
        ).fetchone()
        print(f"[OK] ensemble predictions imported (model_id={ENSEMBLE_MODEL_ID})")
        print(f"  rows={audit[0]:,} dates={audit[1]} stocks={audit[2]}")
        print(f"  score range [{audit[3]:.4f}, {audit[4]:.4f}] mean={audit[5]:.4f}")
        print(f"  rows with BOTH V4 + BC signal (score>1.0): {audit[6]:,} (~bc_overlap)")
        print()
        print("Next: PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py \\")
        print(f"        --lambdamart-model-id {ENSEMBLE_MODEL_ID}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
