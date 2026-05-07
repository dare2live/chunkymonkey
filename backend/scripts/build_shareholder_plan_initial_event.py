#!/usr/bin/env python3
"""Build the initial shareholder-plan announcement event mart."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import (  # noqa: E402
    current_command,
    git_commit_sha,
    record_pipeline_run,
    utc_now_iso,
)
from services.shareholder_plan_initial_event import (  # noqa: E402
    MART_TABLE,
    SOURCE_TABLE,
    build_shareholder_plan_initial_event,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-id",
        default=None,
        help="Manifest run id. Defaults to shareholder_plan_initial_event_<timestamp>.",
    )
    args = parser.parse_args()

    started_at = utc_now_iso()
    started = time.perf_counter()
    run_id = args.run_id or f"shareholder_plan_initial_event_{started_at[:19].replace(':', '').replace('-', '')}"
    with get_conn() as conn:
        try:
            result = build_shareholder_plan_initial_event(conn)
            ended_at = utc_now_iso()
            duration_s = round(time.perf_counter() - started, 3)
            record_pipeline_run(
                conn,
                run_id=run_id,
                pipeline_name="build_shareholder_plan_initial_event",
                status="completed",
                started_at=started_at,
                ended_at=ended_at,
                duration_s=duration_s,
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                command=current_command(),
                cwd=str(Path.cwd()),
                input_tables=[SOURCE_TABLE],
                output_tables=[MART_TABLE],
                perf_summary=result,
            )
            conn.commit()
            result = {"run_id": run_id, "duration_s": duration_s, **result}
        except Exception as exc:
            ended_at = utc_now_iso()
            duration_s = round(time.perf_counter() - started, 3)
            record_pipeline_run(
                conn,
                run_id=run_id,
                pipeline_name="build_shareholder_plan_initial_event",
                status="failed",
                started_at=started_at,
                ended_at=ended_at,
                duration_s=duration_s,
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                command=current_command(),
                cwd=str(Path.cwd()),
                input_tables=[SOURCE_TABLE],
                output_tables=[MART_TABLE],
                blockers=[str(exc)],
            )
            conn.commit()
            raise

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
