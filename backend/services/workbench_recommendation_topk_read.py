"""Recommendation primary top-k read model for Workbench."""
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


def _champion_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return {"champions": []}
    cols = _columns(conn, "mart_model_lifecycle")
    if "status" not in cols or "model_id" not in cols:
        return {"champions": []}
    rows = conn.execute(
        """
        SELECT model_id, status
          FROM mart_model_lifecycle
         WHERE status = 'champion'
         ORDER BY model_id
         LIMIT 10
        """
    ).fetchall()
    return {
        "champions": [{"model_id": row["model_id"], "status": row["status"]} for row in rows],
    }


def _current_champion_model_id(conn: Any) -> str | None:
    champions = _champion_summary(conn).get("champions") or []
    if not champions:
        return None
    return champions[0].get("model_id")


def _latest_recommendation_key(conn: Any, *, primary_only: bool = True) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_daily_recommendation"):
        return None
    cols = _columns(conn, "mart_daily_recommendation")
    primary_filter = "WHERE is_primary = TRUE" if primary_only and "is_primary" in cols else ""
    champion_model_id = _current_champion_model_id(conn)
    champion_order = "CASE WHEN model_id = ? THEN 0 ELSE 1 END," if champion_model_id else ""
    params: tuple[Any, ...] = (champion_model_id,) if champion_model_id else ()
    latest_built_expr = "MAX(CAST(built_at AS VARCHAR)) AS latest_built_at" if "built_at" in cols else "NULL AS latest_built_at"
    row = conn.execute(
        f"""
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date, model_id,
               COUNT(*) AS n, {latest_built_expr}
          FROM mart_daily_recommendation
         {primary_filter}
         GROUP BY CAST(snapshot_date AS VARCHAR), model_id
         ORDER BY snapshot_date DESC, {champion_order} latest_built_at DESC, model_id
         LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    return {"snapshot_date": row["snapshot_date"], "model_id": row["model_id"], "count": int(row["n"])}


def build_recommendation_rows(conn: Any, key: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
    if not key:
        return []
    table = "mart_daily_topk_view_cache" if _table_exists(conn, "mart_daily_topk_view_cache") else "mart_daily_recommendation"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    primary_filter = "AND is_primary = TRUE" if "is_primary" in cols else ""
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "stock_code")},
               {_select_expr(cols, "stock_name")},
               {_select_expr(cols, "xueqiu_symbol")},
               {_select_expr(cols, "tdx_l1_name")},
               {_select_expr(cols, "tdx_l2_name")},
               {_select_expr(cols, "rank_in_date")},
               {_select_expr(cols, "pred_score")},
               {_select_expr(cols, "percentile")},
               {_select_expr(cols, "regime_flag")},
               {_select_expr(cols, "track_id")},
               {_select_expr(cols, "run_mode")},
               {_select_expr(cols, "baseline_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_confidence")},
               {_select_expr(cols, "horizon_selection_run_id")},
               {_select_expr(cols, "key_features_json")},
               {_select_expr(cols, "built_at")}
          FROM {table}
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
           {primary_filter}
         ORDER BY {"rank_in_date" if "rank_in_date" in cols else "stock_code"}
         LIMIT ?
        """,
        (key["snapshot_date"], key["model_id"], int(limit)),
    ).fetchall()
    if not rows and table != "mart_daily_recommendation" and _table_exists(conn, "mart_daily_recommendation"):
        return _recommendation_rows_from_table(conn, key, "mart_daily_recommendation", limit=limit)
    return [_recommendation_row_dict(row) for row in rows]


def _recommendation_rows_from_table(
    conn: Any,
    key: dict[str, Any],
    table: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cols = _columns(conn, table)
    primary_filter = "AND is_primary = TRUE" if "is_primary" in cols else ""
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "stock_code")},
               {_select_expr(cols, "stock_name")},
               {_select_expr(cols, "xueqiu_symbol")},
               {_select_expr(cols, "tdx_l1_name")},
               {_select_expr(cols, "tdx_l2_name")},
               {_select_expr(cols, "rank_in_date")},
               {_select_expr(cols, "pred_score")},
               {_select_expr(cols, "percentile")},
               {_select_expr(cols, "regime_flag")},
               {_select_expr(cols, "track_id")},
               {_select_expr(cols, "run_mode")},
               {_select_expr(cols, "baseline_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_confidence")},
               {_select_expr(cols, "horizon_selection_run_id")},
               {_select_expr(cols, "key_features_json")},
               {_select_expr(cols, "built_at")}
          FROM {table}
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
           {primary_filter}
         ORDER BY {"rank_in_date" if "rank_in_date" in cols else "stock_code"}
         LIMIT ?
        """,
        (key["snapshot_date"], key["model_id"], int(limit)),
    ).fetchall()
    return [_recommendation_row_dict(row) for row in rows]


def _recommendation_row_dict(row: Any) -> dict[str, Any]:
    key_features = _safe_json(row["key_features_json"]) or {}
    top_features = key_features.get("model_top_features") if isinstance(key_features, dict) else []
    stock_feature_values = key_features.get("stock_feature_values") if isinstance(key_features, dict) else []
    stock_feature_contributions = (
        key_features.get("stock_feature_contributions") if isinstance(key_features, dict) else []
    )
    if isinstance(top_features, list):
        top_features = [
            item.get("name") if isinstance(item, dict) else str(item)
            for item in top_features[:5]
        ]
    else:
        top_features = []
    if isinstance(stock_feature_values, list):
        stock_feature_values = [
            item
            for item in stock_feature_values[:5]
            if isinstance(item, dict)
        ]
    else:
        stock_feature_values = []
    if isinstance(stock_feature_contributions, list):
        stock_feature_contributions = [
            item
            for item in stock_feature_contributions[:5]
            if isinstance(item, dict)
        ]
    else:
        stock_feature_contributions = []
    return {
        "stock_code": row["stock_code"],
        "stock_name": row["stock_name"],
        "xueqiu_symbol": row["xueqiu_symbol"],
        "tdx_l1_name": row["tdx_l1_name"],
        "tdx_l2_name": row["tdx_l2_name"],
        "rank_in_date": row["rank_in_date"],
        "pred_score": row["pred_score"],
        "percentile": row["percentile"],
        "regime_flag": row["regime_flag"],
        "track_id": row["track_id"],
        "run_mode": row["run_mode"],
        "baseline_horizon_days": row["baseline_horizon_days"],
        "selected_horizon_days": row["selected_horizon_days"],
        "selected_horizon_confidence": row["selected_horizon_confidence"],
        "horizon_selection_run_id": row["horizon_selection_run_id"],
        "top_features": top_features,
        "top_feature_values": stock_feature_values,
        "top_feature_contributions": stock_feature_contributions,
        "explanation_status": (
            key_features.get("explanation_status") if isinstance(key_features, dict) else None
        ),
        "base_value": key_features.get("base_value") if isinstance(key_features, dict) else None,
        "additivity_error": key_features.get("additivity_error") if isinstance(key_features, dict) else None,
        "built_at": row["built_at"],
    }
