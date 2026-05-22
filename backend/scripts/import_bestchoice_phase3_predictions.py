#!/usr/bin/env python3
"""BestChoice Phase 3 adapter — convert daily candidate feed to predictions table.

Bridges `mart_daily_formula_candidate_bestchoice_v1` (BestChoice top-K daily feed)
into `mart_p0b_lambdamart_v6_predictions` schema so the main project paper_sim
engine can rank/select BestChoice candidates using its battle-tested portfolio
simulator (top-K + concurrent position limit + tx_cost + T+1 + 涨跌停).

Output model_id: bestchoice_formula_challenger_v1

Adapter semantics:
- score = confidence_score (BestChoice 已 normalized 0-100, paper_sim uses raw score for top-K ranking)
- signal_date / stock_code: direct copy from feed
- fwd_cost_after_5d/10d/20d: NULL (paper_sim 用 actual K-line forward when scoring; label only needed for Phase4 IS/OOS)
- model_version=bestchoice_v1, feature_version=formula_engine_v1, label_version=N/A
- walk_forward_mode=NA (BestChoice candidates come from formula trigger, not walk-forward retrain)
- train_start/end/test_start/end: dummy from feed signal_date range (paper_sim doesn't gate on these)
- is_final_holdout: False (challenger is forward-validated by main paper_sim, not standalone train/test)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=plan §5 Phase 3 challenger id naming
CHALLENGER_MODEL_ID = "bestchoice_formula_challenger_v1"
FEED_RUN_ID_DEFAULT = "bestchoice_formula_optuna_20260521_v1"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--feed-run-id", default=FEED_RUN_ID_DEFAULT)
    p.add_argument("--challenger-model-id", default=CHALLENGER_MODEL_ID)
    args = p.parse_args()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with connect(args.db_path, read_only=False) as conn:
        # Verify source feed exists
        n_feed = conn.execute(
            "SELECT COUNT(*) FROM mart_daily_formula_candidate_bestchoice_v1 WHERE run_id = ?",
            [args.feed_run_id],
        ).fetchone()[0]
        if n_feed == 0:
            print(f"ERROR: no feed rows for run_id={args.feed_run_id}", file=sys.stderr)
            return 1
        print(f"[adapter] feed rows: {n_feed} (run_id={args.feed_run_id})")

        # Get feed date range for dummy train/test
        date_range = conn.execute(
            """
            SELECT MIN(signal_date), MAX(signal_date)
              FROM mart_daily_formula_candidate_bestchoice_v1
             WHERE run_id = ?
            """,
            [args.feed_run_id],
        ).fetchone()
        signal_min, signal_max = date_range
        print(f"[adapter] signal date range: {signal_min} -> {signal_max}")

        # Delete existing rows for this challenger (idempotent)
        conn.execute(
            "DELETE FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = ?",
            [args.challenger_model_id],
        )

        # Insert: score = confidence_score, fwd labels NULL (paper_sim doesn't need labels for ranking)
        # Dedup by signal_date+stock_code already done in feed
        conn.execute(
            f"""
            INSERT INTO mart_p0b_lambdamart_v6_predictions (
                stock_code, signal_date, score,
                fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                model_id, model_version, feature_version, label_version,
                walk_forward_mode, train_start, train_end, test_start, test_end,
                is_final_holdout, built_at, trade_date_dt
            )
            SELECT
                stock_code, signal_date, confidence_score,
                NULL, NULL, NULL,
                '{args.challenger_model_id}',
                'bestchoice_v1',
                'formula_engine_v1',
                'NA',
                'NA',
                '{signal_min}', '{signal_max}', '{signal_min}', '{signal_max}',
                FALSE,
                '{now_utc}',
                buy_date
              FROM mart_daily_formula_candidate_bestchoice_v1
             WHERE run_id = '{args.feed_run_id}'
            """
        )
        conn.commit()

        # Audit
        audit = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT signal_date), COUNT(DISTINCT stock_code),
                   MIN(score), MAX(score), AVG(score)
              FROM mart_p0b_lambdamart_v6_predictions
             WHERE model_id = ?
            """,
            [args.challenger_model_id],
        ).fetchone()
        print(f"\n[OK] mart_p0b_lambdamart_v6_predictions imported for {args.challenger_model_id}")
        print(f"  rows={audit[0]} signal_dates={audit[1]} stocks={audit[2]}")
        print(f"  score [{audit[3]:.2f}, {audit[4]:.2f}] mean={audit[5]:.2f}")

        print(f"\n[next] run paper_sim:")
        print(f"  PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py \\")
        print(f"    --lambdamart-model-id {args.challenger_model_id}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
