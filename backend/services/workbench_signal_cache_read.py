"""Signal cache and data processing read models for Workbench."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json


REPO = Path(__file__).resolve().parent.parent.parent
MARKET_DB = REPO / "data" / "market.duckdb"


def _relation_exists(conn: Any, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None and _relation_exists(conn, table_name)


def _attach_market_readonly(conn: Any) -> bool:
    if _relation_exists(conn, "market.sqlite_master"):
        return True
    if not MARKET_DB.exists():
        return False
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
        return True
    except Exception:
        return False


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(_row_value(row, "column_name", 0))
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def build_data_processing_monitor_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    sources: list[tuple[str, str]] = []
    if _table_exists(conn, "mart_data_processing_tool_run"):
        sources.append(("main", "mart_data_processing_tool_run"))
    if _attach_market_readonly(conn) and _relation_exists(conn, "market.mart_data_processing_tool_run"):
        sources.append(("market", "market.mart_data_processing_tool_run"))

    runs: list[dict[str, Any]] = []
    if sources:
        query_sql = "\nUNION ALL\n".join(
            f"""
            SELECT * FROM (
                SELECT ? AS scope, run_id, tool_name, policy_id, source_name, status,
                       input_rows, accepted_rows, rejected_rows,
                       reason_counts_json, output_table, batch_id,
                       CAST(ended_at AS VARCHAR) AS ended_at, duration_s
                  FROM {table}
                 ORDER BY ended_at DESC
                 LIMIT ?
            )
            """.strip()
            for _scope, table in sources
        )
        query_params = [value for scope, _table in sources for value in (scope, int(limit))]
        try:
            query_rows = conn.execute(query_sql, query_params).fetchall()
        except Exception:
            query_rows = []
        for row in query_rows:
            runs.append(
                {
                    "scope": row["scope"],
                    "run_id": row["run_id"],
                    "tool_name": row["tool_name"],
                    "policy_id": row["policy_id"],
                    "source_name": row["source_name"],
                    "status": row["status"],
                    "input_rows": int(row["input_rows"] or 0),
                    "accepted_rows": int(row["accepted_rows"] or 0),
                    "rejected_rows": int(row["rejected_rows"] or 0),
                    "reason_counts": _safe_json(row["reason_counts_json"]) or {},
                    "output_table": row["output_table"],
                    "batch_id": row["batch_id"],
                    "ended_at": row["ended_at"],
                    "duration_s": row["duration_s"],
                }
            )
    runs.sort(key=lambda item: str(item.get("ended_at") or ""), reverse=True)
    runs = runs[: int(limit)]
    reason_counts: dict[str, int] = {}
    for row in runs:
        for reason, count in (row.get("reason_counts") or {}).items():
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + int(count or 0)
    return {
        "sources": [scope for scope, _ in sources],
        "run_count": len(runs),
        "total_input_rows": sum(row["input_rows"] for row in runs),
        "total_accepted_rows": sum(row["accepted_rows"] for row in runs),
        "total_rejected_rows": sum(row["rejected_rows"] for row in runs),
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "recent_runs": runs,
    }


def build_today_signal_cache_view(conn: Any) -> dict[str, Any]:
    try:
        from services.signals_v2 import describe_today_signal_cache  # noqa: WPS433

        status = describe_today_signal_cache(conn)
    except Exception as exc:
        status = {
            "status": "unavailable",
            "signal_count": 0,
            "freshness_days": None,
            "source_max_notice_date": None,
            "current_source_max_notice_date": None,
            "built_at": None,
            "stale": True,
            "requires_refresh": True,
            "error": str(exc)[:160],
        }
    step = None
    if _table_exists(conn, "step_status"):
        cols = _columns(conn, "step_status")
        if {"step_id", "status"}.issubset(cols):
            records_expr = "records" if "records" in cols else "NULL AS records"
            started_expr = "CAST(started_at AS VARCHAR) AS started_at" if "started_at" in cols else "NULL AS started_at"
            finished_expr = "CAST(finished_at AS VARCHAR) AS finished_at" if "finished_at" in cols else "NULL AS finished_at"
            error_expr = "error" if "error" in cols else "NULL AS error"
            row = conn.execute(
                f"""
                SELECT status, {records_expr}, {started_expr},
                       {finished_expr}, {error_expr}
                  FROM step_status
                 WHERE step_id = 'refresh_today_signals'
                 LIMIT 1
                """
            ).fetchone()
            if row:
                step = {
                    "status": row["status"],
                    "records": row["records"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "error": row["error"],
                }
    status["step"] = step
    return status
