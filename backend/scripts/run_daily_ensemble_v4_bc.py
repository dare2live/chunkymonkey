#!/usr/bin/env python3
"""BC Phase 6: V4 + BestChoice daily ensemble production picks.

Per signal_date, combine:
  - V4 champion (lgbm_20260517_governance_v1_20d) score from mart_p0b_oos_predictions
  - BestChoice confidence_score from mart_daily_formula_candidate_bestchoice_v1
Using rank-percentile combine (跟 build_ensemble_v4_bestchoice_predictions.py 同 method).

Output top-K picks per signal_date to `mart_daily_ensemble_picks_v4_bc_v1`.
Also writes summary row to `mart_strategy_result_registry` as ensemble_v4_bc_v1 challenger.

Phase 6 = production integration. 不动 champion (v4 still standalone production).
Ensemble picks 作为 challenger 显示给 user track forward 表现.

Caveat: BC has MILD selection bias (-16% per-window drift), 真 forward Sharpe 估 1.5-1.7
(paper_sim 1.83 含 10-15% upward bias). Need 6-12 周 forward monitor.
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

# rule-compliance: ok evidence=Phase 6 ensemble naming + plan §5 challenger run_id pattern
ENSEMBLE_RUN_ID = "ensemble_v4_bc_v1"
V4_MODEL_ID = "lgbm_20260517_governance_v1_20d"
BC_RUN_ID = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.bc_run_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--signal-date", default=None, help="specific signal_date (YYYY-MM-DD); default = latest")
    args = p.parse_args()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # Phase ψ.5 allowlist: built_at lineage 非 trade_date

    with connect(args.db_path, read_only=False) as conn:
        # Ensure target table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mart_daily_ensemble_picks_v4_bc_v1 (
                run_id VARCHAR,
                signal_date DATE,
                buy_date DATE,
                stock_code VARCHAR,
                rank_in_date INTEGER,
                ensemble_score DOUBLE,
                v4_score DOUBLE,
                v4_rank_pct DOUBLE,
                bc_confidence DOUBLE,
                bc_rank_pct DOUBLE,
                has_bc_signal BOOLEAN,
                bc_formula_id VARCHAR,
                bc_sell_rule VARCHAR,
                bc_holding_days INTEGER,
                built_at TIMESTAMP,
                PRIMARY KEY (run_id, signal_date, stock_code)
            )
            """
        )

        # Resolve signal_date
        if args.signal_date:
            target_dates = [args.signal_date]
        else:
            r = conn.execute(
                """
                SELECT MAX(signal_date) FROM mart_p0b_oos_predictions WHERE model_id = ?
                """,
                [V4_MODEL_ID],
            ).fetchone()
            target_dates = [str(r[0])] if r and r[0] else []
        if not target_dates:
            print("ERROR: no signal_date available")
            return 1

        total_inserted = 0
        for sd in target_dates:
            print(f"[ensemble-daily] processing signal_date={sd}")
            conn.execute(
                "DELETE FROM mart_daily_ensemble_picks_v4_bc_v1 WHERE run_id = ? AND signal_date = ?",
                [ENSEMBLE_RUN_ID, sd],
            )
            cur = conn.execute(
                f"""
                WITH v4 AS (
                    SELECT signal_date, stock_code, score AS v4_score
                      FROM mart_p0b_oos_predictions
                     WHERE model_id = ? AND signal_date = ?
                       AND score IS NOT NULL
                ),
                bc AS (
                    SELECT signal_date, stock_code, confidence_score AS bc_score,
                           formula_id AS bc_formula_id, sell_rule AS bc_sell_rule,
                           holding_days AS bc_holding_days, buy_date
                      FROM mart_daily_formula_candidate_bestchoice_v1
                     WHERE run_id = ? AND signal_date = ?
                ),
                joined AS (
                    SELECT v4.signal_date, v4.stock_code, v4.v4_score,
                           COALESCE(bc.bc_score, 0) AS bc_score,
                           bc.bc_formula_id, bc.bc_sell_rule, bc.bc_holding_days,
                           bc.buy_date,
                           CASE WHEN bc.bc_score IS NOT NULL THEN TRUE ELSE FALSE END AS has_bc_signal
                      FROM v4 LEFT JOIN bc USING (signal_date, stock_code)
                ),
                ranked AS (
                    SELECT *,
                           PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY v4_score) AS v4_rank_pct,
                           CASE WHEN bc_score > 0
                                THEN PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY bc_score)
                                ELSE 0 END AS bc_rank_pct
                      FROM joined
                ),
                scored AS (
                    SELECT *,
                           (v4_rank_pct + bc_rank_pct) AS ensemble_score
                      FROM ranked
                )
                INSERT INTO mart_daily_ensemble_picks_v4_bc_v1 (
                    run_id, signal_date, buy_date, stock_code, rank_in_date,
                    ensemble_score, v4_score, v4_rank_pct, bc_confidence, bc_rank_pct,
                    has_bc_signal, bc_formula_id, bc_sell_rule, bc_holding_days, built_at
                )
                SELECT '{ENSEMBLE_RUN_ID}' AS run_id, signal_date,
                       COALESCE(buy_date, signal_date) AS buy_date,
                       stock_code,
                       CAST(ROW_NUMBER() OVER (PARTITION BY signal_date ORDER BY ensemble_score DESC) AS INTEGER) AS rank_in_date,
                       ensemble_score, v4_score, v4_rank_pct, bc_score AS bc_confidence,
                       bc_rank_pct, has_bc_signal, bc_formula_id, bc_sell_rule,
                       CAST(bc_holding_days AS INTEGER) AS bc_holding_days,
                       '{now_utc}' AS built_at
                  FROM scored
                 WHERE ensemble_score IS NOT NULL
                """,
                [V4_MODEL_ID, sd, BC_RUN_ID, sd],
            )
            n = conn.execute(
                "SELECT COUNT(*) FROM mart_daily_ensemble_picks_v4_bc_v1 WHERE run_id = ? AND signal_date = ?",
                [ENSEMBLE_RUN_ID, sd],
            ).fetchone()[0]
            total_inserted += n
            print(f"  inserted {n} rows")

        conn.commit()

        # Show top-K for last date
        last_sd = target_dates[-1]
        print(f"\n=== Top {args.top_k} picks for {last_sd} ===")
        rows = conn.execute(
            """
            SELECT rank_in_date, stock_code,
                   ROUND(ensemble_score, 4) AS ens,
                   ROUND(v4_rank_pct, 4) AS v4_pct,
                   ROUND(bc_rank_pct, 4) AS bc_pct,
                   has_bc_signal, bc_formula_id, bc_holding_days
              FROM mart_daily_ensemble_picks_v4_bc_v1
             WHERE run_id = ? AND signal_date = ?
             ORDER BY rank_in_date
             LIMIT ?
            """,
            [ENSEMBLE_RUN_ID, last_sd, int(args.top_k)],
        ).fetchall()
        for r in rows:
            print(f"  #{r[0]} {r[1]} ens={r[2]} (v4={r[3]} bc={r[4]}) {'BC+' if r[5] else 'V4-only'} {r[6] or ''} {r[7] or ''}d")

        print(f"\n[ensemble-daily] total {total_inserted} rows written to mart_daily_ensemble_picks_v4_bc_v1")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
