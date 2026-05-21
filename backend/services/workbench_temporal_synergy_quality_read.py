"""Temporal synergy quality and relevance read model for Workbench."""
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


def _cast_select_expr(cols: set[str], column: str, *, alias: str | None = None) -> str:
    out = alias or column
    if column in cols:
        return f"CAST({column} AS VARCHAR) AS {out}"
    return f"NULL AS {out}"


def build_temporal_synergy_quality_view(
    conn: Any,
    *,
    run_id: str,
    relevance_limit: int = 15,
) -> dict[str, Any]:
    quality_table = "mart_temporal_research_panel_quality"
    relevance_table = "mart_feature_temporal_relevance"
    quality_cols = _columns(conn, quality_table)
    quality_row = conn.execute(
        f"""
        SELECT {_select_expr(quality_cols, "run_id")},
               {_select_expr(quality_cols, "source_panel_table")},
               {_select_expr(quality_cols, "feature_set_id")},
               {_select_expr(quality_cols, "source_available_date_column")},
               {_select_expr(quality_cols, "source_date_filter_applied", default="FALSE")},
               {_select_expr(quality_cols, "input_rows")},
               {_select_expr(quality_cols, "panel_rows")},
               {_select_expr(quality_cols, "dropped_future_source_rows")},
               {_select_expr(quality_cols, "stock_count")},
               {_select_expr(quality_cols, "min_signal_date")},
               {_select_expr(quality_cols, "max_signal_date")},
               {_select_expr(quality_cols, "feature_count")},
               {_select_expr(quality_cols, "label_count")},
               {_select_expr(quality_cols, "labels_json")},
               {_select_expr(quality_cols, "features_json")},
               {_cast_select_expr(quality_cols, "built_at")}
          FROM mart_temporal_research_panel_quality
         WHERE run_id = ?
         LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    quality = None
    if quality_row:
        quality = {
            "run_id": quality_row["run_id"],
            "source_panel_table": quality_row["source_panel_table"],
            "feature_set_id": quality_row["feature_set_id"],
            "source_available_date_column": quality_row["source_available_date_column"],
            "source_date_filter_applied": bool(quality_row["source_date_filter_applied"]),
            "input_rows": quality_row["input_rows"],
            "panel_rows": quality_row["panel_rows"],
            "dropped_future_source_rows": quality_row["dropped_future_source_rows"],
            "stock_count": quality_row["stock_count"],
            "min_signal_date": quality_row["min_signal_date"],
            "max_signal_date": quality_row["max_signal_date"],
            "feature_count": quality_row["feature_count"],
            "label_count": quality_row["label_count"],
            "labels": _safe_json(quality_row["labels_json"]) or [],
            "features": _safe_json(quality_row["features_json"]) or [],
            "built_at": quality_row["built_at"],
        }

    label_summary: list[dict[str, Any]] = []
    top_relevance: list[dict[str, Any]] = []
    if _table_exists(conn, relevance_table):
        rel_cols = _columns(conn, relevance_table)
        if {"run_id", "label_name", "feature_name"}.issubset(rel_cols):
            coverage_expr = "coverage_pct" if "coverage_pct" in rel_cols else "NULL"
            rank_ic_expr = "rank_ic" if "rank_ic" in rel_cols else "NULL"
            directional_spread_expr = "directional_spread" if "directional_spread" in rel_cols else "NULL"
            summary_rows = conn.execute(
                f"""
                SELECT label_name,
                       COUNT(*) AS feature_count,
                       AVG({coverage_expr}) AS avg_coverage_pct,
                       MAX(ABS(COALESCE({rank_ic_expr}, 0))) AS max_abs_rank_ic,
                       MAX(COALESCE({directional_spread_expr}, 0)) AS max_directional_spread
                  FROM mart_feature_temporal_relevance
                 WHERE run_id = ?
                 GROUP BY label_name
                 ORDER BY label_name
                """,
                (run_id,),
            ).fetchall()
            label_summary = [
                {
                    "label_name": row["label_name"],
                    "feature_count": int(row["feature_count"] or 0),
                    "avg_coverage_pct": row["avg_coverage_pct"],
                    "max_abs_rank_ic": row["max_abs_rank_ic"],
                    "max_directional_spread": row["max_directional_spread"],
                }
                for row in summary_rows
            ]
            rel_rows = conn.execute(
                f"""
                SELECT {_select_expr(rel_cols, "label_name")},
                       {_select_expr(rel_cols, "horizon_days")},
                       {_select_expr(rel_cols, "feature_name")},
                       {_select_expr(rel_cols, "coverage_pct")},
                       {_select_expr(rel_cols, "rank_ic")},
                       {_select_expr(rel_cols, "directional_spread")},
                       {_select_expr(rel_cols, "stability_score")},
                       {_select_expr(rel_cols, "long_short_spread")},
                       {_select_expr(rel_cols, "daily_count")}
                  FROM mart_feature_temporal_relevance
                 WHERE run_id = ?
                 ORDER BY ABS(COALESCE(rank_ic, 0)) DESC,
                          ABS(COALESCE(directional_spread, 0)) DESC,
                          feature_name
                 LIMIT ?
                """,
                (run_id, int(relevance_limit)),
            ).fetchall()
            top_relevance = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_name": row["feature_name"],
                    "coverage_pct": row["coverage_pct"],
                    "rank_ic": row["rank_ic"],
                    "directional_spread": row["directional_spread"],
                    "stability_score": row["stability_score"],
                    "long_short_spread": row["long_short_spread"],
                    "daily_count": row["daily_count"],
                }
                for row in rel_rows
            ]
    return {
        "quality": quality,
        "label_summary": label_summary,
        "top_relevance": top_relevance,
    }
