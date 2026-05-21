"""Rank-matrix cache read models for Workbench research."""
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


def build_rank_matrix_cache_view(conn: Any, *, limit: int = 8) -> dict[str, Any]:
    summary = {
        "entry_count": 0,
        "total_rows": 0,
        "total_hits": 0,
        "latest_used_at": None,
    }
    cache_entries: list[dict[str, Any]] = []
    latest_benchmarks: list[dict[str, Any]] = []
    if _table_exists(conn, "mart_feature_rank_matrix_cache_manifest"):
        rows = conn.execute(
            """
            SELECT cache_key, table_name, panel_table, feature_set_id,
                   row_count, rank_column_count, build_duration_s,
                   created_at, last_used_at, hit_count
              FROM mart_feature_rank_matrix_cache_manifest
             ORDER BY last_used_at DESC NULLS LAST, created_at DESC NULLS LAST
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        cache_entries = [
            {
                "cache_key": row["cache_key"],
                "table_name": row["table_name"],
                "panel_table": row["panel_table"],
                "feature_set_id": row["feature_set_id"],
                "row_count": int(row["row_count"] or 0),
                "rank_column_count": int(row["rank_column_count"] or 0),
                "build_duration_s": row["build_duration_s"],
                "created_at": row["created_at"],
                "last_used_at": row["last_used_at"],
                "hit_count": int(row["hit_count"] or 0),
            }
            for row in rows
        ]
        agg = conn.execute(
            """
            SELECT COUNT(*) AS entry_count,
                   SUM(COALESCE(row_count, 0)) AS total_rows,
                   SUM(COALESCE(hit_count, 0)) AS total_hits,
                   MAX(last_used_at) AS latest_used_at
              FROM mart_feature_rank_matrix_cache_manifest
            """
        ).fetchone()
        if agg:
            summary = {
                "entry_count": int(agg["entry_count"] or 0),
                "total_rows": int(agg["total_rows"] or 0),
                "total_hits": int(agg["total_hits"] or 0),
                "latest_used_at": agg["latest_used_at"],
            }
    if _table_exists(conn, "mart_feature_rank_matrix_benchmark"):
        cols = _columns(conn, "mart_feature_rank_matrix_benchmark")
        rows = conn.execute(
            f"""
            SELECT run_id, panel_table, label_name, feature_count, label_count,
                   total_rows, rank_matrix_rows, proxy_rows,
                   matrix_duration_s, rank_matrix_build_s, proxy_association_s,
                   compared_pairs, max_abs_rank_ic_delta, avg_abs_rank_ic_delta,
                   {_select_expr(cols, "gate_status")},
                   {_select_expr(cols, "config_json")},
                   {_select_expr(cols, "stage_timings_json")},
                   {_cast_select_expr(cols, "built_at")}
              FROM mart_feature_rank_matrix_benchmark
             ORDER BY built_at DESC NULLS LAST, run_id DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        for row in rows:
            config = _safe_json(row["config_json"]) or {}
            timings = _safe_json(row["stage_timings_json"]) or {}
            latest_benchmarks.append(
                {
                    "run_id": row["run_id"],
                    "panel_table": row["panel_table"],
                    "label_name": row["label_name"],
                    "feature_count": int(row["feature_count"] or 0),
                    "label_count": int(row["label_count"] or 0),
                    "total_rows": int(row["total_rows"] or 0),
                    "rank_matrix_rows": int(row["rank_matrix_rows"] or 0),
                    "proxy_rows": int(row["proxy_rows"] or 0),
                    "matrix_duration_s": row["matrix_duration_s"],
                    "rank_matrix_build_s": row["rank_matrix_build_s"],
                    "proxy_association_s": row["proxy_association_s"],
                    "compared_pairs": int(row["compared_pairs"] or 0),
                    "max_abs_rank_ic_delta": row["max_abs_rank_ic_delta"],
                    "avg_abs_rank_ic_delta": row["avg_abs_rank_ic_delta"],
                    "gate_status": row["gate_status"],
                    "rank_matrix_cache": config.get("rank_matrix_cache") if isinstance(config, dict) else None,
                    "stage_timings": timings if isinstance(timings, dict) else {},
                    "built_at": row["built_at"],
                }
            )
    return {
        "summary": summary,
        "cache_entries": cache_entries,
        "latest_benchmarks": latest_benchmarks,
    }
