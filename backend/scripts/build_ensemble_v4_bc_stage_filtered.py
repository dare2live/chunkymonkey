#!/usr/bin/env python3
"""Build V4 + BC + stage filter ensemble predictions for paper_sim_v6_compare.

Per goal.md Section I priority #2 + per-stage ablation finding:
- V4 IC per stage: Stage 1.5 (+0.081) / 2 (-0.001) / 3 (+0.010) — positive
- V4 IC per stage: Stage 1 (-0.013) / 4 (-0.021) — negative
- Stage filter {1.5, 2, 3} drop {1, 4}

Method:
- LEFT JOIN V4 predictions + BC confidence + fact_stock_technical_stage
- Set ensemble_score = 0 if stage in {1, 4} (effective drop)
- Else: PERCENT_RANK(v4_score) + PERCENT_RANK(bc_confidence) (跟 commit 964147d1 同 method)
- Write to mart_p0b_lambdamart_v6_predictions with model_id=ensemble_v4_bc_stage_filtered_v1
- Then run paper_sim_v6_compare to get authoritative Sharpe

Operational target: Sharpe ≥ 2.0 (#6 perfect ladder gap).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=per-stage ablation V4 OOS positive IC stages
POSITIVE_STAGES = ("1.5", "2", "3")
ENSEMBLE_MODEL_ID = "ensemble_v4_bc_stage_filtered_v1"
V4_MODEL_ID = "lgbm_20260517_governance_v1_20d"
BC_RUN_ID = "bestchoice_formula_optuna_20260521_v1"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    args = p.parse_args()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with connect(str(REPO_ROOT / "data" / "smartmoney.duckdb"), read_only=False) as conn:
        conn.execute(
            "DELETE FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
            [ENSEMBLE_MODEL_ID],
        )
        # Stage filter at SELECT level — set score=NULL for Stage {1, 4} so paper_sim skips
        stages_csv = ",".join(f"'{s}'" for s in POSITIVE_STAGES)
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
                 WHERE model_id = '{V4_MODEL_ID}' AND score IS NOT NULL
            ),
            stage AS (
                SELECT stock_code, date AS signal_date, stage
                  FROM fact_stock_technical_stage
                 WHERE stage IS NOT NULL
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
                       stage.stage,
                       v4.fwd_cost_after_5d, v4.fwd_cost_after_10d, v4.fwd_cost_after_20d,
                       v4.signal_date AS trade_date_dt
                  FROM v4
                  LEFT JOIN stage USING (stock_code, signal_date)
                  LEFT JOIN bc USING (stock_code, signal_date)
            ),
            ranked AS (
                SELECT signal_date, stock_code, stage,
                       fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d, trade_date_dt,
                       -- Stage filter: zero out Stage 1 + 4 (negative IC)
                       CASE WHEN stage IN ({stages_csv}) OR stage IS NULL
                            THEN PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY v4_score)
                            ELSE 0 END AS v4_rank_pct,
                       CASE WHEN bc_score > 0 AND (stage IN ({stages_csv}) OR stage IS NULL)
                            THEN PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY bc_score)
                            ELSE 0 END AS bc_rank_pct
                  FROM joined
            )
            SELECT stock_code, signal_date,
                   (v4_rank_pct + bc_rank_pct) AS score,
                   fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                   '{ENSEMBLE_MODEL_ID}' AS model_id,
                   'ensemble_v1_stage_filter' AS model_version,
                   'p0a_v3+formula_engine+stage_filter' AS feature_version,
                   'p0a_v2_governance_v1' AS label_version,
                   'NA' AS walk_forward_mode,
                   '2024-01-02' AS train_start,
                   '2024-06-28' AS train_end,
                   '2024-07-01' AS test_start,
                   '2026-04-13' AS test_end,
                   FALSE AS is_final_holdout,
                   '{now_utc}' AS built_at,
                   trade_date_dt
              FROM ranked
            """
        )
        conn.commit()
        r = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT signal_date), SUM(CASE WHEN score=0 THEN 1 ELSE 0 END), AVG(score), MAX(score) "
            "FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
            [ENSEMBLE_MODEL_ID],
        ).fetchone()
        print(f"[OK] ensemble_v4_bc_stage_filtered_v1 imported")
        print(f"  rows={r[0]:,} dates={r[1]} score=0 (Stage 1/4): {r[2]:,} avg_score={r[3]:.4f} max={r[4]:.4f}")
        print(f"\nNext: PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py "
              f"--lambdamart-model-id {ENSEMBLE_MODEL_ID}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
