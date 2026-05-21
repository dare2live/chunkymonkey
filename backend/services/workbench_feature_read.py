"""Feature read models for Workbench."""
from __future__ import annotations

from typing import Any

from services.feature_registry import load_feature_registry
from services.workbench_data_source_watermark_read import build_latest_feature_panel_validation
from services.workbench_feature_availability_read import build_feature_availability_contract_view
from services.workbench_feature_catalog_read import build_feature_catalog_current_view
from services.workbench_feature_detail_read import (
    build_feature_drift_mitigation_builds,
    build_feature_drift_offenders,
    build_feature_pit_coverage,
    build_feature_search_spaces,
    build_feature_top_associations,
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


def build_workbench_features(conn: Any, *, association_limit: int = 12) -> dict[str, Any]:
    registry = load_feature_registry()
    group_counts: dict[str, int] = {}
    production_ready = 0
    candidate_only = 0
    for spec in registry.features.values():
        group_counts[spec.group] = group_counts.get(spec.group, 0) + 1
        if spec.production_ready:
            production_ready += 1
        if spec.candidate_only:
            candidate_only += 1

    return {
        "read_model": _read_model_meta(
            conn,
            "features",
            [
                "mart_feature_panel_validation",
                "mart_feature_availability_contract",
                "mart_feature_search_space_summary",
                "mart_feature_association_stat",
                "mart_feature_drift_root_cause_summary",
                "mart_feature_drift_mitigation_panel_build",
                "mart_feature_pit_coverage_summary",
            ],
        ),
        "registry": {
            "feature_count": len(registry.features),
            "model_input_count": len(registry.model_input_columns()),
            "label_count": len(registry.label_columns()),
            "production_ready_count": production_ready,
            "candidate_only_count": candidate_only,
            "group_counts": group_counts,
        },
        "latest_validation": build_latest_feature_panel_validation(conn),
        "search_spaces": build_feature_search_spaces(conn),
        "availability_contract": build_feature_availability_contract_view(conn),
        "feature_catalog": build_feature_catalog_current_view(conn),
        "pit_coverage": build_feature_pit_coverage(conn),
        "drift_mitigation_builds": build_feature_drift_mitigation_builds(conn),
        "top_associations": build_feature_top_associations(conn, association_limit=association_limit),
        "feature_drift": build_feature_drift_offenders(conn, 12),
    }
