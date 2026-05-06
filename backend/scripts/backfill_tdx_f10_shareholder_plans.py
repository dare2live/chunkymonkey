#!/usr/bin/env python3
"""Backfill TDX F10 shareholder-plan rows from captured raw text."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.tdx_f10_extra_client import (  # noqa: E402
    backfill_tdx_f10_shareholder_plans,
    ensure_tables,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _candidate_stock_codes(conn: Any, *, only_missing: bool, limit: int) -> list[str]:
    where = [
        "raw_text LIKE '%【2.股东增减持计划】%'",
        "raw_text LIKE '%最新公告日期%'",
    ]
    if only_missing:
        where.append(
            """
            NOT EXISTS (
                SELECT 1
                  FROM fact_shareholder_plan_tdx_f10 p
                 WHERE p.stock_code = r.stock_code
                   AND p.raw_hash = r.raw_hash
            )
            """
        )
    sql = f"""
        SELECT DISTINCT stock_code
          FROM raw_tdx_f10_holder_research r
         WHERE {' AND '.join(where)}
         ORDER BY stock_code
    """
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [str(row[0]) for row in conn.execute(sql, params).fetchall()]


def _merge_stats(total: dict[str, Any], batch: dict[str, Any]) -> None:
    for key, value in batch.items():
        if key in {"capability_matrix", "capability_matrix_error", "status"}:
            total[key] = value
        elif isinstance(value, int):
            total[key] = int(total.get(key, 0)) + value
        elif key == "errors":
            total.setdefault("errors", []).extend(value or [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    run_id = args.run_id or f"backfill_tdx_f10_shareholder_plans_{utc_now_iso().replace(':', '').replace('-', '')}"
    started_at = utc_now_iso()
    started = perf_counter()
    conn = get_conn(timeout=300)
    total: dict[str, Any] = {"errors": []}
    try:
        ensure_tables(conn)
        only_missing = not args.include_existing
        codes = _candidate_stock_codes(conn, only_missing=only_missing, limit=int(args.limit or 0))
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "target_stock_count": len(codes),
                    "only_missing": only_missing,
                    "batch_size": int(args.batch_size or 200),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        batch_size = max(int(args.batch_size or 200), 1)
        for start in range(0, len(codes), batch_size):
            batch_started = perf_counter()
            batch_codes = codes[start:start + batch_size]
            result = backfill_tdx_f10_shareholder_plans(
                conn,
                stock_codes=batch_codes,
                only_missing=only_missing,
            )
            _merge_stats(total, result)
            print(
                json.dumps(
                    {
                        "batch": start // batch_size + 1,
                        "stock_count": len(batch_codes),
                        "elapsed_s": round(perf_counter() - batch_started, 3),
                        "stats": result,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                flush=True,
            )
        duration_s = perf_counter() - started
        total["status"] = "partial" if total.get("errors") else "completed"
        record_pipeline_run(
            conn,
            run_id=run_id,
            pipeline_name="backfill_tdx_f10_shareholder_plans",
            status=total["status"],
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=duration_s,
            commit_sha=git_commit_sha(REPO_ROOT),
            input_tables=["raw_tdx_f10_holder_research"],
            output_tables=[
                "fact_shareholder_plan_tdx_f10",
                "raw_tdx_f10_extra_parse_status",
                "mart_tdx_f10_capability_matrix",
            ],
            perf_summary={
                "stage_timings": {"total_s": duration_s},
                "target_stock_count": len(codes),
                "batch_size": batch_size,
                "shareholder_plan_rows": total.get("shareholder_plan_rows", 0),
                "raw_rows": total.get("raw_rows", 0),
                "error_count": len(total.get("errors") or []),
            },
        )
        conn.commit()
        print(
            json.dumps(
                {"total": total, "duration_s": round(duration_s, 3)},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
        return 1 if total.get("errors") else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
