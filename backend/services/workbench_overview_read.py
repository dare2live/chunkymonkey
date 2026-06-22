"""Overview read model for the Workbench operations surface."""
from __future__ import annotations

from typing import Any

from services.schema_versions import detect_drift
from services.workbench_champion_read import _champion_summary
from services.workbench_storage_read import build_storage_cleanup_summary


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


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


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


def _status_counts(conn: Any, table_name: str, *, run_id: str | None = None) -> dict[str, int]:
    if not _table_exists(conn, table_name) or "status" not in _columns(conn, table_name):
        return {}
    params: tuple[Any, ...] = ()
    where = ""
    if run_id and "run_id" in _columns(conn, table_name):
        where = "WHERE run_id = ?"
        params = (run_id,)
    rows = conn.execute(
        f"""
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
          FROM {table_name}
          {where}
         GROUP BY COALESCE(status, 'unknown')
        """,
        params,
    ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}


def _latest_trading_day(conn: Any, *, as_of_date: str | None = None) -> str | None:
    if not _table_exists(conn, "dim_trading_calendar"):
        return None
    cols = _columns(conn, "dim_trading_calendar")
    date_col = "trade_date" if "trade_date" in cols else "date" if "date" in cols else None
    if not date_col:
        return None
    filters = []
    if "is_open" in cols:
        filters.append("is_open = TRUE")
    elif "is_trading_day" in cols:
        filters.append("is_trading_day = TRUE")
    params: tuple[Any, ...] = ()
    if as_of_date:
        filters.append(f"CAST({date_col} AS DATE) <= CAST(? AS DATE)")
        params = (as_of_date,)
    else:
        filters.append(f"CAST({date_col} AS DATE) <= CURRENT_DATE")  # rule-compliance: ok evidence=展示读过滤未来行(UI overview)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    return _scalar(conn, f"SELECT CAST(MAX({date_col}) AS VARCHAR) FROM dim_trading_calendar {where}", params)


def _latest_manifest(conn: Any) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return None
    row = conn.execute(
        """
        SELECT run_id, pipeline_name, status,
               CAST(started_at AS VARCHAR) AS started_at,
               CAST(ended_at AS VARCHAR) AS ended_at,
               duration_s, gate_result
          FROM mart_pipeline_run_manifest
         ORDER BY COALESCE(started_at, created_at) DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row["run_id"],
        "pipeline_name": row["pipeline_name"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_s": row["duration_s"],
        "gate_result": row["gate_result"],
    }


def _drift_offenders(conn: Any, limit: int) -> dict[str, Any]:
    table = "mart_feature_drift_root_cause_summary"
    if not _table_exists(conn, table):
        return {"run_id": None, "top": []}
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return {"run_id": None, "top": []}
    rows = conn.execute(
        """
        SELECT source_run_id, feature_name, offender_count, severe_count,
               max_psi, recommendation
          FROM mart_feature_drift_root_cause_summary
         WHERE run_id = ?
         ORDER BY max_psi DESC, offender_count DESC
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    return {
        "run_id": run_id,
        "top": [
            {
                "source_run_id": row["source_run_id"],
                "feature_name": row["feature_name"],
                "offender_count": int(row["offender_count"]),
                "severe_count": int(row["severe_count"]),
                "max_psi": row["max_psi"],
                "recommendation": row["recommendation"],
            }
            for row in rows
        ],
    }


def build_workbench_overview(conn: Any, *, drift_limit: int = 8, as_of_date: str | None = None) -> dict[str, Any]:
    research_run_id = _latest_run_id(conn, "mart_research_schedule_plan")
    drift = detect_drift(conn)
    blockers = []
    if drift:
        blockers.append({"kind": "schema_drift", "count": len(drift)})

    return {
        "read_model": _read_model_meta(
            conn,
            "overview",
            [
                "dim_trading_calendar",
                "mart_pipeline_run_manifest",
                "mart_research_schedule_plan",
                "mart_model_lifecycle",
                "mart_feature_drift_root_cause_summary",
                "dim_schema_version",
            ],
        ),
        "latest_trading_day": _latest_trading_day(conn, as_of_date=as_of_date),
        "latest_manifest": _latest_manifest(conn),
        "schema_drift_count": len(drift),
        "research_schedule": {
            "run_id": research_run_id,
            "status_counts": _status_counts(conn, "mart_research_schedule_plan", run_id=research_run_id),
        },
        "champion": _champion_summary(conn),
        "feature_drift": _drift_offenders(conn, drift_limit),
        "storage": build_storage_cleanup_summary(conn),
        "blockers": blockers,
    }
