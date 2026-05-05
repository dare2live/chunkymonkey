"""Helpers for using coverage-gated feature retention decisions."""
from __future__ import annotations

from typing import Any


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def latest_retention_decision_run(conn, feature_set_id: str) -> str | None:
    if not _table_exists(conn, "mart_feature_retention_decision"):
        return None
    row = conn.execute(
        """
        SELECT decision_run_id
          FROM mart_feature_retention_decision
         WHERE feature_set_id = ?
         GROUP BY decision_run_id
         ORDER BY MAX(built_at) DESC, decision_run_id DESC
         LIMIT 1
        """,
        (feature_set_id,),
    ).fetchone()
    return str(row["decision_run_id"]) if row else None


def load_production_keep_features(
    conn,
    *,
    feature_set_id: str,
    decision_run_id: str | None = None,
) -> tuple[list[str], str | None]:
    if not feature_set_id or not _table_exists(conn, "mart_feature_retention_decision"):
        return [], decision_run_id
    resolved_run = decision_run_id or latest_retention_decision_run(conn, feature_set_id)
    if not resolved_run:
        return [], None
    rows = conn.execute(
        """
        SELECT feature_name
          FROM mart_feature_retention_decision
         WHERE feature_set_id = ?
           AND decision_run_id = ?
           AND decision = 'keep'
         ORDER BY feature_group, feature_name
        """,
        (feature_set_id, resolved_run),
    ).fetchall()
    return [str(row["feature_name"]) for row in rows], resolved_run


def retention_keep_detail(
    conn,
    *,
    feature_set_id: str,
    decision_run_id: str | None = None,
) -> dict[str, Any]:
    features, resolved_run = load_production_keep_features(
        conn,
        feature_set_id=feature_set_id,
        decision_run_id=decision_run_id,
    )
    return {
        "feature_set_id": feature_set_id,
        "decision_run_id": resolved_run,
        "features": features,
        "n_features": len(features),
    }
