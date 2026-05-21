"""Temporal synergy pair and selected interaction read model for Workbench."""
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


def build_temporal_synergy_pair_view(
    conn: Any,
    *,
    run_id: str,
    synergy_limit: int = 15,
) -> dict[str, Any]:
    pair_table = "mart_feature_pair_synergy"
    candidate_table = "mart_feature_interaction_candidate"

    top_synergies: list[dict[str, Any]] = []
    if _table_exists(conn, pair_table):
        pair_cols = _columns(conn, pair_table)
        if {"run_id", "label_name", "feature_a", "feature_b"}.issubset(pair_cols):
            pair_rows = conn.execute(
                f"""
                SELECT {_select_expr(pair_cols, "label_name")},
                       {_select_expr(pair_cols, "horizon_days")},
                       {_select_expr(pair_cols, "feature_a")},
                       {_select_expr(pair_cols, "feature_b")},
                       {_select_expr(pair_cols, "joint_uplift")},
                       {_select_expr(pair_cols, "interaction_score")},
                       {_select_expr(pair_cols, "joint_obs_count")},
                       {_select_expr(pair_cols, "feature_corr")},
                       {_select_expr(pair_cols, "joint_active_label_mean")},
                       {_select_expr(pair_cols, "best_standalone_label_mean")}
                  FROM mart_feature_pair_synergy
                 WHERE run_id = ?
                 ORDER BY interaction_score DESC NULLS LAST,
                          joint_uplift DESC NULLS LAST,
                          feature_a,
                          feature_b
                 LIMIT ?
                """,
                (run_id, int(synergy_limit)),
            ).fetchall()
            top_synergies = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_a": row["feature_a"],
                    "feature_b": row["feature_b"],
                    "joint_uplift": row["joint_uplift"],
                    "interaction_score": row["interaction_score"],
                    "joint_obs_count": row["joint_obs_count"],
                    "feature_corr": row["feature_corr"],
                    "joint_active_label_mean": row["joint_active_label_mean"],
                    "best_standalone_label_mean": row["best_standalone_label_mean"],
                }
                for row in pair_rows
            ]

    selected_interactions: list[dict[str, Any]] = []
    if _table_exists(conn, candidate_table):
        candidate_cols = _columns(conn, candidate_table)
        if {"run_id", "label_name", "feature_a", "feature_b", "selected"}.issubset(candidate_cols):
            candidate_rows = conn.execute(
                f"""
                SELECT {_select_expr(candidate_cols, "label_name")},
                       {_select_expr(candidate_cols, "horizon_days")},
                       {_select_expr(candidate_cols, "feature_a")},
                       {_select_expr(candidate_cols, "feature_b")},
                       {_select_expr(candidate_cols, "selected")},
                       {_select_expr(candidate_cols, "selection_reason")},
                       {_select_expr(candidate_cols, "joint_uplift")},
                       {_select_expr(candidate_cols, "interaction_score")},
                       {_select_expr(candidate_cols, "joint_obs_count")}
                  FROM mart_feature_interaction_candidate
                 WHERE run_id = ?
                   AND selected = TRUE
                 ORDER BY interaction_score DESC NULLS LAST,
                          joint_uplift DESC NULLS LAST,
                          feature_a,
                          feature_b
                 LIMIT ?
                """,
                (run_id, int(synergy_limit)),
            ).fetchall()
            selected_interactions = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_a": row["feature_a"],
                    "feature_b": row["feature_b"],
                    "selected": bool(row["selected"]),
                    "selection_reason": row["selection_reason"],
                    "joint_uplift": row["joint_uplift"],
                    "interaction_score": row["interaction_score"],
                    "joint_obs_count": row["joint_obs_count"],
                }
                for row in candidate_rows
            ]

    return {
        "top_synergies": top_synergies,
        "selected_interactions": selected_interactions,
    }
