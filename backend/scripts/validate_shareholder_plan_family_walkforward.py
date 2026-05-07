#!/usr/bin/env python3
"""Run controlled walk-forward validation for shareholder-plan feature families."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import (  # noqa: E402
    current_command,
    git_commit_sha,
    record_pipeline_run,
    utc_now_iso,
)
from services.shareholder_plan_family_walkforward import (  # noqa: E402
    DEFAULT_FOLD_COUNT,
    DEFAULT_HOLDOUT_DAYS,
    DEFAULT_TRAIN_DAYS,
    FOLD_TABLE,
    SUMMARY_TABLE,
    build_shareholder_plan_family_walkforward,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--source-eval-run-id", default=None)
    parser.add_argument("--panel-table", default="fact_feature_panel")
    parser.add_argument("--label", action="append", dest="labels", help="Repeatable follow-return label.")
    parser.add_argument(
        "--source-family",
        action="append",
        dest="source_families",
        help="Repeatable source family. Defaults to latest_state and initial_event.",
    )
    parser.add_argument("--fold-count", type=int, default=DEFAULT_FOLD_COUNT)
    parser.add_argument("--train-days", type=int, default=DEFAULT_TRAIN_DAYS)
    parser.add_argument("--holdout-days", type=int, default=DEFAULT_HOLDOUT_DAYS)
    parser.add_argument("--min-daily-count", type=int, default=30)
    parser.add_argument("--top-quantile", type=float, default=0.10)
    parser.add_argument("--min-folds", type=int, default=3)
    parser.add_argument("--min-avg-signal-rank-ic", type=float, default=0.001)
    parser.add_argument("--min-avg-long-short-spread", type=float, default=0.0)
    parser.add_argument("--min-positive-fold-share", type=float, default=0.50)
    parser.add_argument("--max-long-short-drawdown", type=float, default=0.50)
    parser.add_argument("--min-active-pct", type=float, default=0.05)
    args = parser.parse_args()

    started_at = utc_now_iso()
    with get_conn() as conn:
        try:
            result = build_shareholder_plan_family_walkforward(
                conn,
                run_id=args.run_id,
                source_eval_run_id=args.source_eval_run_id,
                panel_table=args.panel_table,
                labels=args.labels,
                source_families=args.source_families,
                fold_count=args.fold_count,
                train_days=args.train_days,
                holdout_days=args.holdout_days,
                min_daily_count=args.min_daily_count,
                top_quantile=args.top_quantile,
                min_folds=args.min_folds,
                min_avg_signal_rank_ic=args.min_avg_signal_rank_ic,
                min_avg_long_short_spread=args.min_avg_long_short_spread,
                min_positive_fold_share=args.min_positive_fold_share,
                max_long_short_drawdown=args.max_long_short_drawdown,
                min_active_pct=args.min_active_pct,
            )
            record_pipeline_run(
                conn,
                run_id=result["run_id"],
                pipeline_name="validate_shareholder_plan_family_walkforward",
                status="completed",
                started_at=started_at,
                ended_at=utc_now_iso(),
                duration_s=result["duration_s"],
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                command=current_command(),
                cwd=str(Path.cwd()),
                input_tables=[
                    args.panel_table,
                    "mart_shareholder_plan_feature_family_eval",
                    "fact_shareholder_plan_tdx_f10",
                    "mart_shareholder_plan_initial_event",
                ],
                output_tables=[FOLD_TABLE, SUMMARY_TABLE],
                perf_summary=result,
            )
            conn.commit()
        except Exception as exc:
            record_pipeline_run(
                conn,
                run_id=args.run_id or "shareholder_plan_family_walkforward_failed",
                pipeline_name="validate_shareholder_plan_family_walkforward",
                status="failed",
                started_at=started_at,
                ended_at=utc_now_iso(),
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                command=current_command(),
                cwd=str(Path.cwd()),
                input_tables=[
                    args.panel_table,
                    "mart_shareholder_plan_feature_family_eval",
                    "fact_shareholder_plan_tdx_f10",
                    "mart_shareholder_plan_initial_event",
                ],
                output_tables=[FOLD_TABLE, SUMMARY_TABLE],
                blockers=[str(exc)],
            )
            conn.commit()
            raise

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
