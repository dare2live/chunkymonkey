"""Storage read models for Workbench."""
from __future__ import annotations

from typing import Any, Callable

from services.storage_retention import load_storage_retention_policy, plan_storage_cleanup
from services.workbench_storage_architecture_read import (
    build_architecture_cleanup_plan_summary,
    build_architecture_cleanup_summary,
)


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


def build_storage_cleanup_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return {"latest_run_id": None, "latest_status": None}
    row = conn.execute(
        """
        SELECT run_id, status, CAST(started_at AS VARCHAR) AS started_at
          FROM mart_pipeline_run_manifest
         WHERE pipeline_name IN ('plan_storage_retention', 'execute_storage_cleanup')
         ORDER BY started_at DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return {"latest_run_id": None, "latest_status": None}
    return {
        "latest_run_id": row["run_id"],
        "latest_status": row["status"],
        "started_at": row["started_at"],
    }


def build_workbench_storage(
    conn: Any,
    *,
    include_live_plan: bool = True,
    load_policy: Callable[[], Any] = load_storage_retention_policy,
    plan_cleanup: Callable[[Any, Any], dict[str, Any]] = plan_storage_cleanup,
) -> dict[str, Any]:
    latest_manifest = build_storage_cleanup_summary(conn)
    retention = {
        "mode": "unavailable",
        "candidate_count": 0,
        "protected_model_count": 0,
        "active_optuna_study_count": 0,
        "compaction": {"recommended": False},
        "candidates": [],
        "error": None,
    }
    if include_live_plan:
        try:
            report = plan_cleanup(conn, load_policy())
            retention = {
                "mode": report.get("mode"),
                "candidate_count": report.get("candidate_count", 0),
                "protected_model_count": len(report.get("protected_model_ids") or []),
                "protected_model_ids": report.get("protected_model_ids") or [],
                "active_optuna_study_count": report.get("active_optuna_study_count", 0),
                "compaction": report.get("compaction") or {"recommended": False},
                "candidates": (report.get("candidates") or [])[:20],
                "delete_policy": report.get("delete_policy"),
                "error": None,
            }
        except Exception as exc:  # pragma: no cover - defensive for partially migrated local DBs.
            retention["error"] = str(exc)
    return {
        "read_model": _read_model_meta(
            conn,
            "storage",
            [
                "mart_pipeline_run_manifest",
                "mart_storage_cleanup_plan",
                "mart_architecture_cleanup_summary",
                "mart_architecture_cleanup_plan",
            ],
        ),
        "latest_manifest": latest_manifest,
        "retention": retention,
        "architecture": build_architecture_cleanup_summary(conn),
        "architecture_cleanup": build_architecture_cleanup_plan_summary(conn),
    }
