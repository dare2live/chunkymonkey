#!/usr/bin/env python3
"""Compare latest-state and initial-event shareholder-plan feature families."""
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
from services.shareholder_plan_feature_family_eval import (  # noqa: E402
    DEFAULT_LABELS,
    EVAL_TABLE,
    build_shareholder_plan_feature_family_eval,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--panel-table", default="fact_feature_panel")
    parser.add_argument("--label", action="append", dest="labels", help="Repeatable follow-return label.")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-daily-count", type=int, default=30)
    args = parser.parse_args()

    started_at = utc_now_iso()
    with get_conn() as conn:
        try:
            result = build_shareholder_plan_feature_family_eval(
                conn,
                run_id=args.run_id,
                panel_table=args.panel_table,
                labels=args.labels or list(DEFAULT_LABELS),
                start_date=args.start_date,
                end_date=args.end_date,
                min_daily_count=args.min_daily_count,
            )
            record_pipeline_run(
                conn,
                run_id=result["run_id"],
                pipeline_name="evaluate_shareholder_plan_feature_families",
                status="completed",
                started_at=started_at,
                ended_at=utc_now_iso(),
                duration_s=result["duration_s"],
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                command=current_command(),
                cwd=str(Path.cwd()),
                input_tables=[
                    args.panel_table,
                    "fact_shareholder_plan_tdx_f10",
                    "mart_shareholder_plan_initial_event",
                ],
                output_tables=[EVAL_TABLE],
                perf_summary=result,
            )
            conn.commit()
        except Exception as exc:
            record_pipeline_run(
                conn,
                run_id=args.run_id or "shareholder_plan_family_eval_failed",
                pipeline_name="evaluate_shareholder_plan_feature_families",
                status="failed",
                started_at=started_at,
                ended_at=utc_now_iso(),
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                command=current_command(),
                cwd=str(Path.cwd()),
                input_tables=[
                    args.panel_table,
                    "fact_shareholder_plan_tdx_f10",
                    "mart_shareholder_plan_initial_event",
                ],
                output_tables=[EVAL_TABLE],
                blockers=[str(exc)],
            )
            conn.commit()
            raise

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
