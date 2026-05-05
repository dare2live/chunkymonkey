#!/usr/bin/env python3
"""Refresh source-domain watermarks from current DuckDB tables."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.source_watermarks import refresh_known_source_watermarks  # noqa: E402


def main() -> int:
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    conn = get_conn()
    try:
        items = refresh_known_source_watermarks(conn)
        record_pipeline_run(
            conn,
            run_id=f"refresh_source_watermarks_{started_at.replace(':', '').replace('-', '')[:15]}",
            pipeline_name="refresh_source_watermarks",
            status="success",
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=time.perf_counter() - t0,
            commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
            input_tables=sorted({item.get("table") for item in items if item.get("table")}),
            output_tables=["mart_data_source_watermark"],
            perf_summary={
                "domains": len(items),
                "fallback_active": sum(1 for item in items if item.get("fallback_active")),
                "failures": sum(1 for item in items if int(item.get("consecutive_failures") or 0) > 0),
            },
        )
        print(json.dumps(items, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
