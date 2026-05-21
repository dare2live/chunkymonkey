"""Feature detail read-model slices for Workbench."""
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


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


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


def build_feature_search_spaces(conn: Any) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_feature_search_space_summary"):
        return []
    rows = conn.execute(
        """
        SELECT run_id, source_association_run_id, panel_table, label_name,
               selected_count, excluded_count, selected_features_json,
               group_counts_json, built_at
          FROM mart_feature_search_space_summary
         ORDER BY built_at DESC
         LIMIT 5
        """
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "source_association_run_id": row["source_association_run_id"],
            "panel_table": row["panel_table"],
            "label_name": row["label_name"],
            "selected_count": row["selected_count"],
            "excluded_count": row["excluded_count"],
            "selected_features": _safe_json(row["selected_features_json"]) or [],
            "group_counts": _safe_json(row["group_counts_json"]) or {},
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def build_feature_top_associations(conn: Any, *, association_limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_feature_association_stat"):
        return []
    latest_run = _scalar(
        conn,
        """
        SELECT run_id
          FROM mart_feature_association_stat
         ORDER BY built_at DESC
         LIMIT 1
        """,
    )
    if not latest_run:
        return []
    rows = conn.execute(
        """
        SELECT run_id, panel_table, label_name, feature_name,
               feature_group, coverage_pct, rank_ic,
               long_short_spread, source_fallback_pct, built_at
          FROM mart_feature_association_stat
         WHERE run_id = ?
         ORDER BY ABS(COALESCE(rank_ic, 0)) DESC, coverage_pct DESC
         LIMIT ?
        """,
        (latest_run, int(association_limit)),
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "panel_table": row["panel_table"],
            "label_name": row["label_name"],
            "feature_name": row["feature_name"],
            "feature_group": row["feature_group"],
            "coverage_pct": row["coverage_pct"],
            "rank_ic": row["rank_ic"],
            "long_short_spread": row["long_short_spread"],
            "source_fallback_pct": row["source_fallback_pct"],
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def build_feature_drift_mitigation_builds(conn: Any) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_feature_drift_mitigation_panel_build"):
        return []
    rows = conn.execute(
        """
        SELECT run_id, output_feature_set_id, model_selection_run_id,
               base_model_selection_run_id, base_table, root_cause_run_id,
               transformed_features_json, copied_features_json,
               selected_features_json, row_count, stock_count, date_count,
               min_date, max_date, built_at
          FROM mart_feature_drift_mitigation_panel_build
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 5
        """
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "output_feature_set_id": row["output_feature_set_id"],
            "model_selection_run_id": row["model_selection_run_id"],
            "base_model_selection_run_id": row["base_model_selection_run_id"],
            "base_table": row["base_table"],
            "root_cause_run_id": row["root_cause_run_id"],
            "transformed_features": _safe_json(row["transformed_features_json"]) or {},
            "copied_features": _safe_json(row["copied_features_json"]) or [],
            "selected_features": _safe_json(row["selected_features_json"]) or [],
            "row_count": row["row_count"],
            "stock_count": row["stock_count"],
            "date_count": row["date_count"],
            "min_date": row["min_date"],
            "max_date": row["max_date"],
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def build_feature_pit_coverage(conn: Any) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_feature_pit_coverage_summary"):
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT audit_run_id, feature_set_id, feature_table, audit_scope,
                   total_columns, audited_columns, passed_columns,
                   failed_columns, unknown_blocking_columns,
                   missing_source_columns, not_applicable_columns,
                   high_risk_columns, critical_risk_columns, audited_at
              FROM mart_feature_pit_coverage_summary
             ORDER BY audited_at DESC NULLS LAST, audit_run_id DESC
             LIMIT 5
            """
        ).fetchall()
    ]


def build_feature_drift_offenders(conn: Any, limit: int) -> dict[str, Any]:
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
