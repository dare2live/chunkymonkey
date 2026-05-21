"""Champion read model for the Workbench operations surface."""
from __future__ import annotations

from typing import Any

from services.workbench_champion_detail_read import (
    build_champion_candidate_evaluations,
    build_champion_challengers,
    build_champion_evidence_bundles,
    build_champion_promotion_gates,
)
from services.workbench_champion_deployment_read import build_champion_deployment_summary
from services.workbench_champion_topk_read import build_latest_primary_topk
from services.workbench_model_stability_read import build_model_stability_context as _model_stability_context


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


def _status_counts(conn: Any, table_name: str, *, run_id: str | None = None) -> dict[str, int]:
    if not _table_exists(conn, table_name) or "status" not in _columns(conn, table_name):
        return {}
    params: tuple[Any, ...] = ()
    where = ""
    if run_id and "run_id" in _columns(conn, table_name):
        where = "WHERE run_id = ?"
        params = (run_id,)
    rows = conn.execute(
        f"""
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
          FROM {table_name}
          {where}
         GROUP BY COALESCE(status, 'unknown')
        """,
        params,
    ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}


def _read_model_meta(conn: Any, endpoint: str, tables: list[str]) -> dict[str, Any]:
    materialized_tables = []
    latest_values: list[str] = []
    freshness_candidates = [
        "built_at",
        "updated_at",
        "created_at",
        "ended_at",
        "started_at",
        "snapshot_at",
        "evaluated_at",
        "snapshot_date",
        "trade_date",
        "date",
    ]
    for table in tables:
        item: dict[str, Any] = {
            "table": table,
            "available": _table_exists(conn, table),
            "latest_run_id": None,
            "latest_row_at": None,
            "freshness_column": None,
        }
        if item["available"]:
            cols = _columns(conn, table)
            item["latest_run_id"] = _latest_run_id(conn, table) if "run_id" in cols else None
            freshness_col = next((col for col in freshness_candidates if col in cols), None)
            if freshness_col:
                item["freshness_column"] = freshness_col
                latest = _scalar(
                    conn,
                    f"SELECT CAST(MAX(TRY_CAST({freshness_col} AS TIMESTAMP)) AS VARCHAR) FROM {table}",
                )
                if not latest:
                    latest = _scalar(conn, f"SELECT CAST(MAX({freshness_col}) AS VARCHAR) FROM {table}")
                item["latest_row_at"] = latest
                if latest:
                    latest_values.append(str(latest))
        materialized_tables.append(item)

    return {
        "endpoint": endpoint,
        "source_mode": "materialized_snapshot",
        "recompute_on_read": False,
        "refresh_semantics": "reload_materialized_json_only",
        "trigger": "pipeline_or_manual_job",
        "latest_materialized_at": max(latest_values) if latest_values else None,
        "materialized_tables": materialized_tables,
    }


def _champion_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return {"counts": {}, "champions": []}
    cols = _columns(conn, "mart_model_lifecycle")
    if "status" not in cols or "model_id" not in cols:
        return {"counts": {}, "champions": []}
    counts = _status_counts(conn, "mart_model_lifecycle")
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
        "counts": counts,
        "champions": [{"model_id": row["model_id"], "status": row["status"]} for row in rows],
    }


def _current_champion_model_id(conn: Any) -> str | None:
    champions = _champion_summary(conn).get("champions") or []
    if not champions:
        return None
    return champions[0].get("model_id")


def build_workbench_champion(conn: Any, *, limit: int = 12) -> dict[str, Any]:
    lifecycle = _champion_summary(conn)
    challengers = build_champion_challengers(conn, limit=limit)
    evaluations = build_champion_candidate_evaluations(conn, limit=limit)
    evidence_bundles = build_champion_evidence_bundles(conn, limit=limit)
    gates = build_champion_promotion_gates(conn, limit=limit)
    topk = build_latest_primary_topk(conn)

    deployment = build_champion_deployment_summary(lifecycle=lifecycle, gates=gates, topk=topk)
    return {
        "read_model": _read_model_meta(
            conn,
            "champion",
            [
                "mart_model_lifecycle",
                "mart_champion_candidate_evaluation",
                "mart_challenger_evidence_bundle",
                "mart_tdx_keep_promotion_gate",
                "mart_model_stability_context_summary",
                "mart_daily_recommendation",
            ],
        ),
        "lifecycle": lifecycle,
        "deployment": deployment,
        "challengers": challengers,
        "candidate_evaluations": evaluations,
        "evidence_bundles": evidence_bundles,
        "promotion_gates": gates,
        "stability_context": _model_stability_context(conn, summary_limit=4, diagnostic_limit=8),
        "latest_primary_topk": topk,
    }
