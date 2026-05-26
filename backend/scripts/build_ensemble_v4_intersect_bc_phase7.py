#!/usr/bin/env python3
"""V4 ∩ BC + Phase 7 composite as paper_sim_v6 model_id for authoritative portfolio Sharpe.

Per-trade Sharpe 3.17 finding likely optimistic. paper_sim_v6 uses monthly aggregated
portfolio Sharpe (concurrent positions, sizer, tx_cost adv20, exit_rules). To verify
operational Sharpe ≥ 2.0 gate, need apples-to-apples paper_sim_v6 result.

Score = V4 rank percentile × indicator(BC signal that day) × indicator(stage in {1.5,2,3})
      ELSE NULL (paper_sim skips NULL scores)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

ENSEMBLE_MODEL_ID = "ensemble_v7_bc_clean_v1"
V4_MODEL_ID = "lgbm_phase5_v7_20260523T010000Z"
BC_RUN_ID = "bestchoice_formula_optuna_20260521_v1"
# rule-compliance: ok evidence=per-stage ablation V4 positive IC stages
POSITIVE_STAGES = ("1.5", "2", "3")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    args = p.parse_args()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stages_csv = ",".join(f"'{s}'" for s in POSITIVE_STAGES)
    with connect(str(REPO_ROOT / "data" / "smartmoney.duckdb"), read_only=False) as conn:
        conn.execute("DELETE FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
                     [ENSEMBLE_MODEL_ID])
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
                  FROM mart_p0b_lambdamart_v6_predictions
                 WHERE model_id = '{V4_MODEL_ID}' AND score IS NOT NULL
            ),
            stage AS (
                SELECT stock_code, date AS signal_date, stage
                  FROM fact_stock_technical_stage WHERE stage IS NOT NULL
            ),
            bc AS (
                SELECT DISTINCT signal_date, stock_code
                  FROM mart_daily_formula_candidate_bestchoice_v1
                 WHERE run_id = '{BC_RUN_ID}'
            ),
            st_filter AS (
                -- 宪法第一条: ST 过滤统一走 dim_active_a_stock.stock_name
                SELECT stock_code FROM dim_active_a_stock
                 WHERE stock_name NOT LIKE 'ST%' AND stock_name NOT LIKE '*ST%'
                -- rule-compliance: ok evidence=SQL内联ST过滤,无法调Python函数,保留但标注
            ),
            joined AS (
                SELECT v4.signal_date, v4.stock_code, v4.v4_score,
                       stage.stage,
                       CASE WHEN bc.stock_code IS NOT NULL THEN 1 ELSE 0 END AS has_bc,
                       CASE WHEN st_filter.stock_code IS NOT NULL THEN 1 ELSE 0 END AS not_st,
                       v4.fwd_cost_after_5d, v4.fwd_cost_after_10d, v4.fwd_cost_after_20d,
                       v4.signal_date AS trade_date_dt
                  FROM v4
                  LEFT JOIN stage USING (stock_code, signal_date)
                  LEFT JOIN bc USING (stock_code, signal_date)
                  LEFT JOIN st_filter USING (stock_code)
            ),
            ranked AS (
                SELECT *,
                       -- V4 rank percentile within signal_date
                       PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY v4_score) AS v4_pct
                  FROM joined
            )
            SELECT stock_code, signal_date,
                   -- Composite: v4_pct only if (BC signal AND stage in positive AND NOT ST/*ST); else NULL
                   CASE WHEN has_bc = 1 AND stage IN ({stages_csv}) AND not_st = 1
                        THEN v4_pct
                        ELSE NULL END AS score,
                   fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                   '{ENSEMBLE_MODEL_ID}' AS model_id,
                   'composite_v1' AS model_version,
                   'p0a_v3+formula+stage_filter+phase7' AS feature_version,
                   'p0a_v2_governance_v1' AS label_version,
                   'NA' AS walk_forward_mode,
                   '2024-01-02' AS train_start,
                   '2024-06-28' AS train_end,
                   '2024-07-01' AS test_start,
                   '2026-04-13' AS test_end,
                   FALSE AS is_final_holdout,
                   '{now}' AS built_at,
                   trade_date_dt
              FROM ranked
            """
        )
        conn.commit()
        r = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END), "
            "COUNT(DISTINCT signal_date) "
            "FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
            [ENSEMBLE_MODEL_ID],
        ).fetchone()
        print(f"[OK] {ENSEMBLE_MODEL_ID}: total {r[0]:,} rows, non-NULL {r[1]:,}, dates {r[2]}")
        print(f"\nNext: PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py "
              f"--lambdamart-model-id {ENSEMBLE_MODEL_ID}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
