"""Temporal synergy redundancy and conditional discovery read model for Workbench."""
from __future__ import annotations

from typing import Any


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


def _build_redundancy_clusters(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    table = "mart_feature_cluster_redundancy"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if not {"run_id", "source_run_id", "cluster_id", "feature_name"}.issubset(cols):
        return []
    redundancy_run = conn.execute(
        """
        SELECT run_id
          FROM mart_feature_cluster_redundancy
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if not redundancy_run:
        return []
    rows = conn.execute(
        """
        SELECT run_id,
               cluster_id,
               representative_feature,
               cluster_size,
               MAX(max_abs_corr_in_cluster) AS max_abs_corr_in_cluster,
               STRING_AGG(feature_name, ', ' ORDER BY redundancy_status, feature_name) AS members,
               CAST(MAX(built_at) AS VARCHAR) AS built_at
          FROM mart_feature_cluster_redundancy
         WHERE run_id = ?
         GROUP BY run_id, cluster_id, representative_feature, cluster_size
         ORDER BY cluster_size DESC, max_abs_corr_in_cluster DESC, cluster_id
         LIMIT 12
        """,
        (redundancy_run["run_id"],),
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "cluster_id": row["cluster_id"],
            "representative_feature": row["representative_feature"],
            "cluster_size": row["cluster_size"],
            "max_abs_corr_in_cluster": row["max_abs_corr_in_cluster"],
            "members": row["members"],
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def _build_conditional_synergies(
    conn: Any,
    *,
    run_id: str,
    synergy_limit: int,
) -> list[dict[str, Any]]:
    table = "mart_feature_conditional_synergy"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if not {"run_id", "label_name", "condition_feature", "response_feature"}.issubset(cols):
        return []
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "label_name")},
               {_select_expr(cols, "horizon_days")},
               {_select_expr(cols, "condition_feature")},
               {_select_expr(cols, "response_feature")},
               {_select_expr(cols, "incremental_uplift")},
               {_select_expr(cols, "conditional_response_uplift")},
               {_select_expr(cols, "response_uplift")},
               {_select_expr(cols, "interaction_score")},
               {_select_expr(cols, "conditional_response_obs_count")},
               {_select_expr(cols, "feature_corr")},
               {_select_expr(cols, "selected")},
               {_select_expr(cols, "selection_reason")}
          FROM mart_feature_conditional_synergy
         WHERE run_id = ?
         ORDER BY interaction_score DESC NULLS LAST,
                  incremental_uplift DESC NULLS LAST,
                  condition_feature,
                  response_feature
         LIMIT ?
        """,
        (run_id, int(synergy_limit)),
    ).fetchall()
    return [
        {
            "label_name": row["label_name"],
            "horizon_days": row["horizon_days"],
            "condition_feature": row["condition_feature"],
            "response_feature": row["response_feature"],
            "incremental_uplift": row["incremental_uplift"],
            "conditional_response_uplift": row["conditional_response_uplift"],
            "response_uplift": row["response_uplift"],
            "interaction_score": row["interaction_score"],
            "conditional_response_obs_count": row["conditional_response_obs_count"],
            "feature_corr": row["feature_corr"],
            "selected": bool(row["selected"]),
            "selection_reason": row["selection_reason"],
        }
        for row in rows
    ]


def build_temporal_synergy_discovery_view(
    conn: Any,
    *,
    run_id: str,
    synergy_limit: int = 15,
) -> dict[str, Any]:
    return {
        "redundancy_clusters": _build_redundancy_clusters(conn, run_id=run_id),
        "conditional_synergies": _build_conditional_synergies(
            conn,
            run_id=run_id,
            synergy_limit=synergy_limit,
        ),
    }
