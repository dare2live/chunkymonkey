"""Recommendation read models for Workbench."""
from __future__ import annotations

from typing import Any, Callable

from services.workbench_recommendation_metrics_read import (
    build_recommendation_outcomes,
    build_recommendation_risk,
)
from services.workbench_recommendation_topk_read import (
    _latest_recommendation_key,
    build_recommendation_rows,
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


def _select_expr(cols: set[str], column: str, *, alias: str | None = None, default: str = "NULL") -> str:
    out = alias or column
    if column in cols:
        return f"{column} AS {out}"
    return f"{default} AS {out}"


def _latest_feature_panel_validation(conn: Any) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_feature_panel_validation"):
        return None
    cols = _columns(conn, "mart_feature_panel_validation")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "validation_id")},
               {_select_expr(cols, "status")},
               {_select_expr(cols, "source_lineage_coverage")},
               {_select_expr(cols, "source_fallback_ratio")}
          FROM mart_feature_panel_validation
         ORDER BY {"validated_at DESC" if "validated_at" in cols else "validation_id DESC"}
         LIMIT 1
        """
    ).fetchone()
    if not rows:
        return None
    return {
        "validation_id": rows["validation_id"],
        "status": rows["status"],
        "source_lineage_coverage": rows["source_lineage_coverage"],
        "source_fallback_ratio": rows["source_fallback_ratio"],
    }


def build_workbench_recommendations(
    conn: Any,
    *,
    limit: int = 50,
    data_sources_builder: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    key = _latest_recommendation_key(conn, primary_only=True)
    rows = build_recommendation_rows(conn, key, limit=limit) if key else []
    validation = _latest_feature_panel_validation(conn)
    data_sources = data_sources_builder(conn) if data_sources_builder else {}
    kline = data_sources.get("kline") or {}
    return {
        "read_model": _read_model_meta(
            conn,
            "recommendations",
            [
                "mart_daily_recommendation",
                "mart_prediction_outcome",
                "mart_feature_panel_validation",
                "mart_data_source_watermark",
            ],
        ),
        "latest_primary": key or {"snapshot_date": None, "model_id": None, "count": 0},
        "rows": rows,
        "risk": build_recommendation_risk(conn, key),
        "outcomes": build_recommendation_outcomes(conn, key),
        "source_quality": {
            "kline_primary": (kline.get("primary") or {}).get("source_name"),
            "kline_primary_is_tdxhub": kline.get("primary_is_tdxhub"),
            "source_fallback_ratio": (validation or {}).get("source_fallback_ratio"),
            "source_lineage_coverage": (validation or {}).get("source_lineage_coverage"),
            "feature_validation_id": (validation or {}).get("validation_id"),
            "feature_validation_status": (validation or {}).get("status"),
        },
    }
