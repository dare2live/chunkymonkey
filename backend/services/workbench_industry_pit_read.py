"""Industry PIT readiness read model for Workbench research."""
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


def build_industry_pit_readiness(conn: Any) -> dict[str, Any]:
    table = "mart_industry_pit_quality"
    pit_table = "mart_stock_industry_pit"
    empty = {
        "run_id": None,
        "pit_eligible": False,
        "blockers": ["missing_industry_pit_quality"],
        "pit_row_count": 0,
        "pit_stock_count": 0,
        "history_snapshot_count": 0,
    }
    if not _table_exists(conn, table):
        return empty
    cols = _columns(conn, table)
    row = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "signal_table")},
               {_select_expr(cols, "signal_stock_column")},
               {_select_expr(cols, "signal_date_column")},
               {_select_expr(cols, "window_start")},
               {_select_expr(cols, "window_end")},
               {_select_expr(cols, "signal_row_count")},
               {_select_expr(cols, "signal_stock_count")},
               {_select_expr(cols, "signal_date_count")},
               {_select_expr(cols, "min_signal_date")},
               {_select_expr(cols, "max_signal_date")},
               {_select_expr(cols, "pit_row_count")},
               {_select_expr(cols, "pit_stock_count")},
               {_select_expr(cols, "history_snapshot_count")},
               {_select_expr(cols, "history_min_snapshot_date")},
               {_select_expr(cols, "history_max_snapshot_date")},
               {_select_expr(cols, "matched_signal_rows")},
               {_select_expr(cols, "observed_pit_signal_rows")},
               {_select_expr(cols, "fallback_signal_rows")},
               {_select_expr(cols, "missing_pit_rows")},
               {_select_expr(cols, "missing_tdx_l1_rows")},
               {_select_expr(cols, "fallback_ratio")},
               {_select_expr(cols, "missing_ratio")},
               {_select_expr(cols, "pit_eligible", default="FALSE")},
               {_select_expr(cols, "blockers_json")},
               {_select_expr(cols, "stage_timings_json")},
               {_cast_select_expr(cols, "built_at")}
          FROM mart_industry_pit_quality
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return empty
    blockers = _safe_json(row["blockers_json"]) or []
    stage_timings = _safe_json(row["stage_timings_json"]) or {}
    fallback_rows = 0
    observed_rows = 0
    if _table_exists(conn, pit_table):
        agg = conn.execute(
            """
            SELECT SUM(CASE WHEN source = 'current_label_fallback' THEN 1 ELSE 0 END) AS fallback_rows,
                   SUM(CASE WHEN is_historical_pit THEN 1 ELSE 0 END) AS observed_rows
              FROM mart_stock_industry_pit
            """
        ).fetchone()
        if agg:
            fallback_rows = int(agg["fallback_rows"] or 0)
            observed_rows = int(agg["observed_rows"] or 0)
    return {
        "run_id": row["run_id"],
        "signal_table": row["signal_table"],
        "signal_stock_column": row["signal_stock_column"],
        "signal_date_column": row["signal_date_column"],
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "signal_row_count": row["signal_row_count"],
        "signal_stock_count": row["signal_stock_count"],
        "signal_date_count": row["signal_date_count"],
        "min_signal_date": row["min_signal_date"],
        "max_signal_date": row["max_signal_date"],
        "pit_row_count": row["pit_row_count"],
        "pit_stock_count": row["pit_stock_count"],
        "history_snapshot_count": row["history_snapshot_count"],
        "history_min_snapshot_date": row["history_min_snapshot_date"],
        "history_max_snapshot_date": row["history_max_snapshot_date"],
        "matched_signal_rows": row["matched_signal_rows"],
        "observed_pit_signal_rows": row["observed_pit_signal_rows"],
        "fallback_signal_rows": row["fallback_signal_rows"],
        "missing_pit_rows": row["missing_pit_rows"],
        "missing_tdx_l1_rows": row["missing_tdx_l1_rows"],
        "fallback_ratio": row["fallback_ratio"],
        "missing_ratio": row["missing_ratio"],
        "pit_eligible": bool(row["pit_eligible"]),
        "blockers": blockers,
        "stage_timings": stage_timings if isinstance(stage_timings, dict) else {},
        "fallback_pit_rows": fallback_rows,
        "observed_pit_rows": observed_rows,
        "built_at": row["built_at"],
    }
