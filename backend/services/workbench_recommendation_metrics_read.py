"""Recommendation risk and outcome read models for Workbench."""
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


def build_recommendation_risk(conn: Any, key: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not key or not _table_exists(conn, "mart_daily_recommendation_risk"):
        return []
    rows = conn.execute(
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
               model_id, track_id, is_primary, top_size, top1_industry,
               top1_industry_share, top3_industry_share,
               top20_amount_ma20_p25, top20_amount_ma20_median,
               overlap_with_primary, built_at
          FROM mart_daily_recommendation_risk
         WHERE CAST(snapshot_date AS VARCHAR) = ?
         ORDER BY is_primary DESC, top_size, track_id
         LIMIT 20
        """,
        (key["snapshot_date"],),
    ).fetchall()
    return [
        {
            "snapshot_date": row["snapshot_date"],
            "model_id": row["model_id"],
            "track_id": row["track_id"],
            "is_primary": bool(row["is_primary"]),
            "top_size": row["top_size"],
            "top1_industry": row["top1_industry"],
            "top1_industry_share": row["top1_industry_share"],
            "top3_industry_share": row["top3_industry_share"],
            "top20_amount_ma20_p25": row["top20_amount_ma20_p25"],
            "top20_amount_ma20_median": row["top20_amount_ma20_median"],
            "overlap_with_primary": row["overlap_with_primary"],
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def build_recommendation_outcomes(conn: Any, key: dict[str, Any] | None) -> dict[str, Any]:
    if not key or not _table_exists(conn, "mart_prediction_outcome"):
        return {"count": 0}
    row = conn.execute(
        """
        SELECT COUNT(*) AS n,
               AVG(ret_5d) AS avg_ret_5d,
               AVG(ret_10d) AS avg_ret_10d,
               AVG(ret_30d) AS avg_ret_30d,
               AVG(CASE WHEN hit_5d THEN 1.0 WHEN hit_5d IS NULL THEN NULL ELSE 0.0 END) AS hit_rate_5d,
               AVG(CASE WHEN hit_30d THEN 1.0 WHEN hit_30d IS NULL THEN NULL ELSE 0.0 END) AS hit_rate_30d,
               MAX(CAST(outcome_known_at AS VARCHAR)) AS latest_outcome_known_at
          FROM mart_prediction_outcome
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
        """,
        (key["snapshot_date"], key["model_id"]),
    ).fetchone()
    if not row:
        return {"count": 0}
    return {
        "count": int(row["n"] or 0),
        "avg_ret_5d": row["avg_ret_5d"],
        "avg_ret_10d": row["avg_ret_10d"],
        "avg_ret_30d": row["avg_ret_30d"],
        "hit_rate_5d": row["hit_rate_5d"],
        "hit_rate_30d": row["hit_rate_30d"],
        "latest_outcome_known_at": row["latest_outcome_known_at"],
    }
