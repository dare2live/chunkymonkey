"""Feature catalog read model for Workbench."""
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


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _latest_run_id(conn: Any, table_name: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    cols = _columns(conn, table_name)
    if "run_id" not in cols:
        return None
    order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else None
    if order_col:
        return _scalar(conn, f"SELECT run_id FROM {table_name} ORDER BY {order_col} DESC LIMIT 1")
    return _scalar(conn, f"SELECT run_id FROM {table_name} LIMIT 1")


def build_feature_catalog_current_view(conn: Any, *, limit: int = 240) -> dict[str, Any]:
    table = "mart_feature_catalog_current"
    if not _table_exists(conn, table):
        return {"run_id": None, "summary": {}, "risk_counts": {}, "table_counts": {}, "rows": []}
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return {"run_id": None, "summary": {}, "risk_counts": {}, "table_counts": {}, "rows": []}
    cols = _columns(conn, table)
    required = {
        "run_id",
        "feature_table",
        "feature_name",
        "feature_family",
        "registry_status",
        "model_input",
        "production_ready",
        "pit_risk_level",
        "total_rows",
        "non_null_rows",
        "coverage_pct",
        "allowed_in_production_research",
    }
    if not required.issubset(cols):
        return {"run_id": run_id, "summary": {}, "risk_counts": {}, "table_counts": {}, "rows": []}

    risk_rows = conn.execute(
        """
        SELECT pit_risk_level, COUNT(*) AS n
          FROM mart_feature_catalog_current
         WHERE run_id = ?
         GROUP BY pit_risk_level
         ORDER BY pit_risk_level
        """,
        (run_id,),
    ).fetchall()
    table_rows = conn.execute(
        """
        SELECT feature_table, COUNT(*) AS n
          FROM mart_feature_catalog_current
         WHERE run_id = ?
         GROUP BY feature_table
         ORDER BY feature_table
        """,
        (run_id,),
    ).fetchall()
    summary_row = conn.execute(
        """
        SELECT COUNT(*) AS total_features,
               SUM(CASE WHEN allowed_in_production_research THEN 1 ELSE 0 END) AS allowed_features,
               SUM(CASE WHEN model_input THEN 1 ELSE 0 END) AS model_input_features,
               SUM(CASE WHEN registry_status = 'unknown' THEN 1 ELSE 0 END) AS unknown_features,
               SUM(CASE WHEN non_null_rows = 0 THEN 1 ELSE 0 END) AS zero_coverage_features,
               SUM(CASE WHEN pit_risk_level = 'critical' THEN 1 ELSE 0 END) AS critical_features,
               SUM(CASE WHEN pit_risk_level = 'high' THEN 1 ELSE 0 END) AS high_features
          FROM mart_feature_catalog_current
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()

    join_exists = _table_exists(conn, "mart_feature_pit_join_plan")
    exclusion_exists = _table_exists(conn, "mart_feature_exclusion_reason")
    join_sql = ""
    if join_exists:
        join_sql = """
        LEFT JOIN mart_feature_pit_join_plan j
          ON j.run_id = c.run_id
         AND j.feature_table = c.feature_table
         AND j.feature_name = c.feature_name
        """
    reason_sql = ""
    if exclusion_exists:
        reason_sql = """
        LEFT JOIN (
            SELECT run_id, feature_table, feature_name,
                   STRING_AGG(reason_code, ', ' ORDER BY reason_code) AS reason_codes,
                   MAX(CASE WHEN production_blocking THEN 1 ELSE 0 END) AS production_blocking
              FROM mart_feature_exclusion_reason
             WHERE run_id = ?
             GROUP BY run_id, feature_table, feature_name
        ) r
          ON r.run_id = c.run_id
         AND r.feature_table = c.feature_table
         AND r.feature_name = c.feature_name
        """
    params: list[Any] = [run_id]
    if exclusion_exists:
        params.append(run_id)
    params.append(int(limit))
    query_rows = conn.execute(
        f"""
        SELECT c.feature_table,
               c.feature_name,
               c.feature_family,
               c.registry_status,
               c.model_input,
               c.production_ready,
               c.candidate_only,
               c.label,
               c.pit_risk_level,
               c.total_rows,
               c.non_null_rows,
               c.coverage_pct,
               c.source_event_date_column,
               c.source_available_date_column,
               c.allowed_in_production_research,
               {"j.join_policy" if join_exists else "NULL"} AS join_policy,
               {"j.production_blocking" if join_exists else "FALSE"} AS join_blocking,
               {"r.production_blocking" if exclusion_exists else "FALSE"} AS exclusion_blocking,
               {"r.reason_codes" if exclusion_exists else "NULL"} AS reason_codes
          FROM mart_feature_catalog_current c
          {join_sql}
          {reason_sql}
         WHERE c.run_id = ?
         ORDER BY
               CASE WHEN COALESCE({"j.production_blocking" if join_exists else "FALSE"}, FALSE)
                      OR COALESCE({"r.production_blocking" if exclusion_exists else "FALSE"}, FALSE)
                    THEN 0 ELSE 1 END,
               CASE c.pit_risk_level
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
               END,
               c.coverage_pct ASC NULLS FIRST,
               c.feature_table,
               c.feature_name
         LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    return {
        "run_id": run_id,
        "summary": dict(summary_row) if summary_row else {},
        "risk_counts": {str(row["pit_risk_level"]): int(row["n"] or 0) for row in risk_rows},
        "table_counts": {str(row["feature_table"]): int(row["n"] or 0) for row in table_rows},
        "rows": [
            {
                "feature_table": row["feature_table"],
                "feature_name": row["feature_name"],
                "feature_family": row["feature_family"],
                "registry_status": row["registry_status"],
                "model_input": bool(row["model_input"]) if row["model_input"] is not None else None,
                "production_ready": (
                    bool(row["production_ready"]) if row["production_ready"] is not None else None
                ),
                "candidate_only": bool(row["candidate_only"]) if row["candidate_only"] is not None else None,
                "label": bool(row["label"]) if row["label"] is not None else None,
                "pit_risk_level": row["pit_risk_level"],
                "total_rows": row["total_rows"],
                "non_null_rows": row["non_null_rows"],
                "coverage_pct": row["coverage_pct"],
                "source_event_date_column": row["source_event_date_column"],
                "source_available_date_column": row["source_available_date_column"],
                "allowed_in_production_research": bool(row["allowed_in_production_research"]),
                "join_policy": row["join_policy"],
                "production_blocking": bool(row["join_blocking"]) or bool(row["exclusion_blocking"]),
                "reason_codes": row["reason_codes"],
            }
            for row in query_rows
        ],
    }
