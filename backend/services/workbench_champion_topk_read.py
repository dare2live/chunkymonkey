"""Champion primary top-k read model for Workbench."""
from __future__ import annotations

from typing import Any

from services.workbench_recommendation_read import _latest_recommendation_key


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


def _select_expr(cols: set[str], column: str, *, alias: str | None = None, default: str = "NULL") -> str:
    out = alias or column
    if column in cols:
        return f"{column} AS {out}"
    return f"{default} AS {out}"


def build_latest_primary_topk(conn: Any) -> dict[str, Any]:
    topk = {"snapshot_date": None, "model_id": None, "count": 0, "rows": []}
    key = _latest_recommendation_key(conn, primary_only=True)
    if not key or not _table_exists(conn, "mart_daily_recommendation"):
        return topk

    cols = _columns(conn, "mart_daily_recommendation")
    top_rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "stock_code")},
               {_select_expr(cols, "rank_in_date")},
               {_select_expr(cols, "pred_score")},
               {_select_expr(cols, "percentile")},
               {_select_expr(cols, "regime_flag")},
               {_select_expr(cols, "track_id")},
               {_select_expr(cols, "run_mode")}
          FROM mart_daily_recommendation
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
           {"AND is_primary = TRUE" if "is_primary" in cols else ""}
         ORDER BY {"rank_in_date" if "rank_in_date" in cols else "stock_code"}
         LIMIT 10
        """,
        (key["snapshot_date"], key["model_id"]),
    ).fetchall()
    return {
        "snapshot_date": key["snapshot_date"],
        "model_id": key["model_id"],
        "count": int(key["count"]),
        "rows": [
            {
                "stock_code": item["stock_code"],
                "rank_in_date": item["rank_in_date"],
                "pred_score": item["pred_score"],
                "percentile": item["percentile"],
                "regime_flag": item["regime_flag"],
                "track_id": item["track_id"],
                "run_mode": item["run_mode"],
            }
            for item in top_rows
        ],
    }
