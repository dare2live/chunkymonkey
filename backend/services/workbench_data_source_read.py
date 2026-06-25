"""Data-source support read models for Workbench."""
from __future__ import annotations

from typing import Any

from services.workbench_asset_health_read import build_asset_health_snapshot
from services.workbench_data_source_watermark_read import (
    build_data_source_watermarks,
    build_kline_source_view,
    build_latest_feature_panel_validation,
    build_watermark_blockers,
    latest_trading_day,
)
from services.workbench_signal_cache_read import (
    build_data_processing_monitor_view,
    build_today_signal_cache_view,
)
from services.workbench_tdx_read import (
    build_f10_source_date_audit_view,
    build_tdx_server_health_view,
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


def _latest_data_health_snapshot_at(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_data_health"):
        return None
    return _scalar(conn, "SELECT CAST(MAX(snapshot_at) AS VARCHAR) FROM mart_data_health")


def _source_health_overview(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "dim_data_asset") or not _table_exists(conn, "mart_data_health"):
        return {"snapshot_at": None, "sources": [], "source_priorities": [], "watermarks": [], "failure_queue": []}
    snap_at = _latest_data_health_snapshot_at(conn)
    dim_cols = _columns(conn, "dim_data_asset")
    deprecation_filter = "AND COALESCE(d.deprecation_status, 'active') = 'active'" if "deprecation_status" in dim_cols else ""
    rows = conn.execute(
        f"""
        SELECT d.upstream_source, d.source_tier,
               COUNT(*) AS asset_count,
               SUM(COALESCE(m.row_count, 0)) AS total_rows,
               SUM(CASE WHEN m.severity = 'red' THEN 1 ELSE 0 END) AS red_count,
               SUM(CASE WHEN m.severity = 'yellow' THEN 1 ELSE 0 END) AS yellow_count,
               SUM(CASE WHEN m.severity = 'green' THEN 1 ELSE 0 END) AS green_count,
               MAX(m.freshness_hours) AS max_freshness_h
          FROM dim_data_asset d
          LEFT JOIN mart_data_health m
            ON m.table_name = d.table_name
           AND CAST(m.snapshot_at AS VARCHAR) = COALESCE(?, CAST(m.snapshot_at AS VARCHAR))
         WHERE d.upstream_source IS NOT NULL
           AND d.source_tier IN (1, 2, 3)
           {deprecation_filter}
         GROUP BY d.upstream_source, d.source_tier
         ORDER BY d.source_tier, d.upstream_source
        """,
        (snap_at,),
    ).fetchall()
    priorities = []
    if _table_exists(conn, "dim_data_source_priority"):
        priorities = [
            dict(row)
            for row in conn.execute(
                """
                SELECT data_domain, preferred_source, fallback_1, fallback_2, reason
                  FROM dim_data_source_priority
                 ORDER BY data_domain
                """
            ).fetchall()
        ]
    return {
        "snapshot_at": snap_at,
        "sources": [dict(row) for row in rows],
        "source_priorities": priorities,
        "watermarks": [],
        "failure_queue": [],
    }


def build_workbench_data_sources(conn: Any, *, limit: int = 30, as_of_date: str | None = None) -> dict[str, Any]:
    rows = build_data_source_watermarks(conn, limit=limit)
    kline = build_kline_source_view(rows)
    latest_validation = build_latest_feature_panel_validation(conn)
    blockers = build_watermark_blockers(rows, kline)
    tdx_f10_capabilities = []
    if _table_exists(conn, "mart_tdx_f10_capability_matrix"):
        tdx_f10_capabilities = [
            dict(row)
            for row in conn.execute(
                """
                SELECT module_id, module_name, endpoint, parser, raw_table,
                       fact_table, raw_text_available, parsed_table_available,
                       coverage_stock_count, row_count, latest_page_update_date,
                       latest_fetched_at, parser_version, pit_risk,
                       source_date_field, availability_date_field, status, notes,
                       built_at
                  FROM mart_tdx_f10_capability_matrix
                 ORDER BY module_id
                """
            ).fetchall()
        ]
    return {
        "read_model": _read_model_meta(
            conn,
            "data-sources",
            [
                "dim_trading_calendar",
                "mart_data_source_watermark",
                "mart_data_health",
                "mart_data_processing_monitor",
                "mart_tdx_server_health",
                "mart_tdx_f10_capability_matrix",
                "mart_tdx_f10_source_date_audit",
            ],
        ),
        "calendar_target": latest_trading_day(conn, as_of_date=as_of_date),
        "watermark_count": len(rows),
        "watermarks": rows,
        "kline": kline,
        "latest_feature_validation": latest_validation,
        "asset_health": build_asset_health_snapshot(conn),
        "source_health": _source_health_overview(conn),
        "processing_monitor": build_data_processing_monitor_view(conn, limit=limit),
        "today_signal_cache": build_today_signal_cache_view(conn),
        "tdx_server_health": build_tdx_server_health_view(conn, limit=limit),
        "tdx_f10_capabilities": tdx_f10_capabilities,
        "f10_source_date_audit": build_f10_source_date_audit_view(conn, limit=limit),
        "blockers": blockers,
    }
