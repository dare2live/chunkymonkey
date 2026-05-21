"""Research metadata and drift read models for the Workbench surface."""
from __future__ import annotations

from typing import Any


RESEARCH_READ_MODEL_TABLES = [
    "mart_research_schedule_plan",
    "mart_model_stability_search_summary",
    "mart_pipeline_run_manifest",
    "mart_stock_horizon_profile",
    "mart_stock_horizon_selection",
    "mart_shareholder_plan_initial_feature_panel_quality",
    "mart_shareholder_plan_feature_family_eval",
    "mart_shareholder_plan_family_walkforward_summary",
    "mart_temporal_synergy_relevance",
    "mart_synergy_policy_validation",
    "mart_synergy_policy_mtm_validation",
    "mart_synergy_policy_mtm_strategy_sweep",
    "mart_industry_pit_quality",
]


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


def build_research_read_model_meta(conn: Any) -> dict[str, Any]:
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
    for table in RESEARCH_READ_MODEL_TABLES:
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
        "endpoint": "research",
        "source_mode": "materialized_snapshot",
        "recompute_on_read": False,
        "refresh_semantics": "reload_materialized_json_only",
        "trigger": "pipeline_or_manual_job",
        "latest_materialized_at": max(latest_values) if latest_values else None,
        "materialized_tables": materialized_tables,
    }


def build_research_feature_drift(conn: Any, *, limit: int = 12) -> dict[str, Any]:
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
