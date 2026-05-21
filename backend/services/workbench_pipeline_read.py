"""Pipeline read models for Workbench."""
from __future__ import annotations

from typing import Any
import json


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


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _latest_run_id(conn: Any, table_name: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    cols = _columns(conn, table_name)
    if "run_id" not in cols:
        return None
    order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else None
    if order_col:
        return _scalar(conn, f"SELECT run_id FROM {table_name} ORDER BY {order_col} DESC LIMIT 1")
    return _scalar(conn, f"SELECT run_id FROM {table_name} LIMIT 1")


def _read_model_meta(conn: Any, endpoint: str, tables: list[str]) -> dict[str, Any]:
    materialized_tables = []
    latest_values: list[str] = []
    freshness_candidates = [
        "built_at",
        "updated_at",
        "created_at",
        "ended_at",
        "started_at",
        "snapshot_at",
        "evaluated_at",
        "snapshot_date",
        "trade_date",
        "date",
    ]
    for table in tables:
        item: dict[str, Any] = {
            "table": table,
            "available": _table_exists(conn, table),
            "latest_run_id": None,
            "latest_row_at": None,
            "freshness_column": None,
        }
        if item["available"]:
            cols = _columns(conn, table)
            item["latest_run_id"] = _latest_run_id(conn, table) if "run_id" in cols else None
            freshness_col = next((col for col in freshness_candidates if col in cols), None)
            if freshness_col:
                item["freshness_column"] = freshness_col
                latest = _scalar(
                    conn,
                    f"SELECT CAST(MAX(TRY_CAST({freshness_col} AS TIMESTAMP)) AS VARCHAR) FROM {table}",
                )
                if not latest:
                    latest = _scalar(conn, f"SELECT CAST(MAX({freshness_col}) AS VARCHAR) FROM {table}")
                item["latest_row_at"] = latest
                if latest:
                    latest_values.append(str(latest))
        materialized_tables.append(item)

    return {
        "endpoint": endpoint,
        "source_mode": "materialized_snapshot",
        "recompute_on_read": False,
        "refresh_semantics": "reload_materialized_json_only",
        "trigger": "pipeline_or_manual_job",
        "latest_materialized_at": max(latest_values) if latest_values else None,
        "materialized_tables": materialized_tables,
    }


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _json_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 0


def _pipeline_manifest_rows(conn: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return []
    rows = conn.execute(
        """
        SELECT run_id, pipeline_name, status,
               CAST(started_at AS VARCHAR) AS started_at,
               CAST(ended_at AS VARCHAR) AS ended_at,
               duration_s, gate_result, blockers_json, perf_summary_json,
               model_id, feature_group, label_name, holding_period
          FROM mart_pipeline_run_manifest
         ORDER BY COALESCE(started_at, created_at) DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out = []
    for row in rows:
        blockers = _safe_json(row["blockers_json"]) or []
        perf = _safe_json(row["perf_summary_json"]) or {}
        out.append(
            {
                "run_id": row["run_id"],
                "pipeline_name": row["pipeline_name"],
                "status": row["status"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "duration_s": row["duration_s"],
                "gate_result": row["gate_result"],
                "blockers": blockers,
                "blocker_count": _json_count(blockers),
                "perf_summary": perf,
                "model_id": row["model_id"],
                "feature_group": row["feature_group"],
                "label_name": row["label_name"],
                "holding_period": row["holding_period"],
            }
        )
    return out


def _manifest_status_counts(conn: Any, *, limit: int = 50) -> dict[str, int]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return {}
    rows = conn.execute(
        """
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
          FROM (
            SELECT status
              FROM mart_pipeline_run_manifest
             ORDER BY COALESCE(started_at, created_at) DESC
             LIMIT ?
          )
         GROUP BY COALESCE(status, 'unknown')
        """,
        (int(limit),),
    ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}


def build_workbench_pipelines(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    rows = _pipeline_manifest_rows(conn, limit=limit)
    latest_by_pipeline: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest_by_pipeline.setdefault(str(row["pipeline_name"]), row)
    slowest = sorted(
        [row for row in rows if row["duration_s"] is not None],
        key=lambda row: float(row["duration_s"] or 0),
        reverse=True,
    )[:8]
    blocker_rows = [row for row in rows if row["blocker_count"] or str(row["status"]).lower() not in {"success", "completed"}]
    return {
        "read_model": _read_model_meta(
            conn,
            "pipelines",
            ["mart_pipeline_run_manifest"],
        ),
        "status_counts": _manifest_status_counts(conn, limit=limit),
        "recent": rows,
        "latest_by_pipeline": list(latest_by_pipeline.values()),
        "slowest": slowest,
        "blockers": blocker_rows,
    }
