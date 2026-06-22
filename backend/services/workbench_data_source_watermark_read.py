"""Data-source watermark read-model slices for Workbench."""
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


def latest_trading_day(conn: Any, *, as_of_date: str | None = None) -> str | None:
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
        filters.append(f"CAST({date_col} AS DATE) <= CURRENT_DATE")  # rule-compliance: ok evidence=展示读过滤未来行(UI watermark, 非交易决策)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    return _scalar(conn, f"SELECT CAST(MAX({date_col}) AS VARCHAR) FROM dim_trading_calendar {where}", params)


def build_latest_feature_panel_validation(conn: Any) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_feature_panel_validation"):
        return None
    cols = _columns(conn, "mart_feature_panel_validation")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "validation_id")},
               {_select_expr(cols, "run_mode")},
               {_select_expr(cols, "status")},
               {_select_expr(cols, "validated_at")},
               {_select_expr(cols, "rows")},
               {_select_expr(cols, "duplicate_keys")},
               {_select_expr(cols, "close_coverage")},
               {_select_expr(cols, "source_lineage_coverage")},
               {_select_expr(cols, "source_fallback_ratio")},
               {_select_expr(cols, "source_distribution_json")},
               {_select_expr(cols, "blockers_json")}
          FROM mart_feature_panel_validation
         ORDER BY {"validated_at DESC" if "validated_at" in cols else "validation_id DESC"}
         LIMIT 1
        """
    ).fetchone()
    if not rows:
        return None
    blockers = _safe_json(rows["blockers_json"]) or []
    return {
        "validation_id": rows["validation_id"],
        "run_mode": rows["run_mode"],
        "status": rows["status"],
        "validated_at": rows["validated_at"],
        "rows": rows["rows"],
        "duplicate_keys": rows["duplicate_keys"],
        "close_coverage": rows["close_coverage"],
        "source_lineage_coverage": rows["source_lineage_coverage"],
        "source_fallback_ratio": rows["source_fallback_ratio"],
        "source_distribution": _safe_json(rows["source_distribution_json"]) or [],
        "blockers": blockers,
        "blocker_count": len(blockers) if isinstance(blockers, list) else 0,
    }


def build_data_source_watermarks(conn: Any, *, limit: int = 30) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_data_source_watermark"):
        return []
    wm_cols = _columns(conn, "mart_data_source_watermark")
    query_rows = conn.execute(
        f"""
        SELECT {_select_expr(wm_cols, "data_domain")},
               {_select_expr(wm_cols, "source_name")},
               {_select_expr(wm_cols, "source_tier")},
               {_cast_select_expr(wm_cols, "last_success_at")},
               {_select_expr(wm_cols, "last_data_date")},
               {_select_expr(wm_cols, "row_count")},
               {_select_expr(wm_cols, "consecutive_failures", default="0")},
               {_select_expr(wm_cols, "fallback_active", default="FALSE")},
               {_select_expr(wm_cols, "fallback_reason")},
               {_select_expr(wm_cols, "parser_version")},
               {_cast_select_expr(wm_cols, "updated_at")}
          FROM mart_data_source_watermark
         ORDER BY data_domain, source_tier, source_name
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [
        {
            "data_domain": row["data_domain"],
            "source_name": row["source_name"],
            "source_tier": row["source_tier"],
            "last_success_at": row["last_success_at"],
            "last_data_date": row["last_data_date"],
            "row_count": row["row_count"],
            "consecutive_failures": int(row["consecutive_failures"] or 0),
            "fallback_active": bool(row["fallback_active"]),
            "fallback_reason": row["fallback_reason"],
            "parser_version": row["parser_version"],
            "updated_at": row["updated_at"],
        }
        for row in query_rows
    ]


def build_kline_source_view(rows: list[dict[str, Any]]) -> dict[str, Any]:
    kline_rows = [row for row in rows if row["data_domain"] == "kline_daily"]
    primary = next((row for row in kline_rows if int(row["source_tier"] or 0) == 1), None)
    fallbacks = [row for row in kline_rows if int(row["source_tier"] or 0) > 1]
    return {
        "primary": primary,
        "fallbacks": fallbacks,
        "fallback_active_count": sum(1 for row in fallbacks if row["fallback_active"]),
        "primary_is_tdxhub": bool(primary and "tdxhub" in str(primary.get("source_name") or "").lower()),
    }


def build_watermark_blockers(rows: list[dict[str, Any]], kline: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = []
    primary = kline["primary"]
    if not primary:
        blockers.append({"kind": "missing_tdxhub_primary", "count": 1})
    elif "tdxhub" not in str(primary.get("source_name") or "").lower():
        blockers.append({"kind": "kline_primary_not_tdxhub", "count": 1})
    for row in rows:
        if row["consecutive_failures"] > 0:
            blockers.append(
                {
                    "kind": "source_failures",
                    "data_domain": row["data_domain"],
                    "source_name": row["source_name"],
                    "count": row["consecutive_failures"],
                }
            )
    return blockers
