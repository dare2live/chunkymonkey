"""Shareholder-plan initial feature-panel read model for Workbench."""
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


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _select_expr(cols: set[str], column: str, *, alias: str | None = None, default: str = "NULL") -> str:
    out = alias or column
    if column in cols:
        return f"{column} AS {out}"
    return f"{default} AS {out}"


def _cast_select_expr(cols: set[str], column: str, *, alias: str | None = None) -> str:
    out = alias or column
    if column in cols:
        return f"CAST({column} AS VARCHAR) AS {out}"
    return f"NULL AS {out}"


def build_shareholder_plan_initial_feature_panel_view(conn: Any) -> dict[str, Any]:
    table = "mart_shareholder_plan_initial_feature_panel_quality"
    empty = {
        "run_id": None,
        "quality": None,
    }
    if not _table_exists(conn, table):
        return empty
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return empty
    cols = _columns(conn, table)
    row = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "feature_set_id")},
               {_select_expr(cols, "base_panel_table")},
               {_select_expr(cols, "initial_event_table")},
               {_select_expr(cols, "window_days")},
               {_select_expr(cols, "input_rows")},
               {_select_expr(cols, "panel_rows")},
               {_select_expr(cols, "stock_count")},
               {_select_expr(cols, "date_count")},
               {_select_expr(cols, "min_date")},
               {_select_expr(cols, "max_date")},
               {_select_expr(cols, "initial_event_rows")},
               {_select_expr(cols, "matched_event_rows")},
               {_select_expr(cols, "active_rows")},
               {_select_expr(cols, "active_pct")},
               {_select_expr(cols, "dropped_invalid_date_rows")},
               {_select_expr(cols, "dropped_incomplete_label_rows")},
               {_select_expr(cols, "dropped_incomplete_context_rows")},
               {_select_expr(cols, "calendar_mismatch_rows")},
               {_select_expr(cols, "labels_json")},
               {_select_expr(cols, "context_features_json")},
               {_select_expr(cols, "initial_features_json")},
               {_select_expr(cols, "require_complete_labels", default="FALSE")},
               {_select_expr(cols, "require_complete_context", default="FALSE")},
               {_select_expr(cols, "stage_timings_json")},
               {_cast_select_expr(cols, "built_at")}
          FROM {table}
         WHERE run_id = ?
         LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if not row:
        return empty
    quality = {
        "run_id": row["run_id"],
        "feature_set_id": row["feature_set_id"],
        "base_panel_table": row["base_panel_table"],
        "initial_event_table": row["initial_event_table"],
        "window_days": row["window_days"],
        "input_rows": row["input_rows"],
        "panel_rows": row["panel_rows"],
        "stock_count": row["stock_count"],
        "date_count": row["date_count"],
        "min_date": row["min_date"],
        "max_date": row["max_date"],
        "initial_event_rows": row["initial_event_rows"],
        "matched_event_rows": row["matched_event_rows"],
        "active_rows": row["active_rows"],
        "active_pct": row["active_pct"],
        "dropped_invalid_date_rows": row["dropped_invalid_date_rows"],
        "dropped_incomplete_label_rows": row["dropped_incomplete_label_rows"],
        "dropped_incomplete_context_rows": row["dropped_incomplete_context_rows"],
        "calendar_mismatch_rows": row["calendar_mismatch_rows"],
        "labels": _safe_json(row["labels_json"]) or [],
        "context_features": _safe_json(row["context_features_json"]) or [],
        "initial_features": _safe_json(row["initial_features_json"]) or [],
        "require_complete_labels": bool(row["require_complete_labels"]),
        "require_complete_context": bool(row["require_complete_context"]),
        "stage_timings": _safe_json(row["stage_timings_json"]) or {},
        "built_at": row["built_at"],
    }
    return {
        "run_id": row["run_id"],
        "quality": quality,
    }
