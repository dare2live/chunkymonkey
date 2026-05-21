"""Storage architecture cleanup read-model slices for Workbench."""
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


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


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


def build_architecture_cleanup_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_architecture_inventory_asset"):
        return {"run_id": None, "classification_counts": {}, "cleanup_candidates": []}
    run_id = _latest_run_id(conn, "mart_architecture_inventory_asset")
    if not run_id:
        return {"run_id": None, "classification_counts": {}, "cleanup_candidates": []}
    counts = conn.execute(
        """
        SELECT classification, COUNT(*) AS n
          FROM mart_architecture_inventory_asset
         WHERE run_id = ?
         GROUP BY classification
        """,
        (run_id,),
    ).fetchall()
    candidates = conn.execute(
        """
        SELECT path, asset_type, module_area, classification, notes
          FROM mart_architecture_inventory_asset
         WHERE run_id = ?
           AND classification IN ('deprecated_pending_cleanup', 'delete_after_tests', 'compatibility_shim')
         ORDER BY classification, path
         LIMIT 20
        """,
        (run_id,),
    ).fetchall()
    return {
        "run_id": run_id,
        "classification_counts": {str(row["classification"]): int(row["n"]) for row in counts},
        "cleanup_candidates": [
            {
                "path": row["path"],
                "asset_type": row["asset_type"],
                "module_area": row["module_area"],
                "classification": row["classification"],
                "notes": row["notes"],
            }
            for row in candidates
        ],
    }


def _empty_cleanup_plan(*, run_id: str | None = None, manifest_perf: dict[str, Any] | None = None) -> dict[str, Any]:
    perf = manifest_perf or {}
    return {
        "run_id": run_id,
        "inventory_run_id": perf.get("inventory_run_id"),
        "candidate_count": int(perf.get("candidate_count") or 0),
        "status_counts": perf.get("status_counts") if isinstance(perf.get("status_counts"), dict) else {},
        "action_counts": perf.get("action_counts") if isinstance(perf.get("action_counts"), dict) else {},
        "smoke_counts": perf.get("smoke_counts") if isinstance(perf.get("smoke_counts"), dict) else {},
        "candidates": [],
    }


def build_architecture_cleanup_plan_summary(conn: Any) -> dict[str, Any]:
    table = "mart_architecture_cleanup_plan"
    manifest_run_id = None
    manifest_perf: dict[str, Any] = {}
    if _table_exists(conn, "mart_pipeline_run_manifest"):
        manifest_cols = _columns(conn, "mart_pipeline_run_manifest")
        perf_expr = "perf_summary_json" if "perf_summary_json" in manifest_cols else "NULL AS perf_summary_json"
        started_expr = "CAST(started_at AS VARCHAR) AS started_at" if "started_at" in manifest_cols else "NULL AS started_at"
        manifest_row = conn.execute(
            f"""
            SELECT run_id, {perf_expr},
                   {started_expr}
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name IN (
                   'plan_architecture_cleanup',
                   'import_architecture_cleanup_smoke',
                   'execute_architecture_cleanup'
             )
             ORDER BY started_at DESC
             LIMIT 1
            """
        ).fetchone()
        if manifest_row:
            manifest_run_id = manifest_row["run_id"]
            parsed = _safe_json(manifest_row["perf_summary_json"]) or {}
            manifest_perf = parsed if isinstance(parsed, dict) else {}
    if not _table_exists(conn, table) and not manifest_run_id:
        return _empty_cleanup_plan()
    run_id = manifest_run_id or _latest_run_id(conn, table)
    if not run_id:
        return _empty_cleanup_plan()
    if not _table_exists(conn, table):
        return _empty_cleanup_plan(run_id=run_id, manifest_perf=manifest_perf)
    status_counts = conn.execute(
        """
        SELECT status, COUNT(*) AS n
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         GROUP BY status
        """,
        (run_id,),
    ).fetchall()
    action_counts = conn.execute(
        """
        SELECT action, COUNT(*) AS n
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         GROUP BY action
        """,
        (run_id,),
    ).fetchall()
    smoke_counts = conn.execute(
        """
        SELECT COALESCE(smoke_status, 'none') AS smoke_status, COUNT(*) AS n
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         GROUP BY COALESCE(smoke_status, 'none')
        """,
        (run_id,),
    ).fetchall()
    rows = conn.execute(
        """
        SELECT inventory_run_id, asset_type, path, classification, action,
               status, reason, blockers_json, smoke_status, smoke_error
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         ORDER BY status, action, path
         LIMIT 30
        """,
        (run_id,),
    ).fetchall()
    inventory_run_id = rows[0]["inventory_run_id"] if rows else None
    if not rows and manifest_perf:
        return _empty_cleanup_plan(run_id=run_id, manifest_perf=manifest_perf)
    return {
        "run_id": run_id,
        "inventory_run_id": inventory_run_id,
        "candidate_count": len(rows),
        "status_counts": {str(row["status"]): int(row["n"]) for row in status_counts},
        "action_counts": {str(row["action"]): int(row["n"]) for row in action_counts},
        "smoke_counts": {str(row["smoke_status"]): int(row["n"]) for row in smoke_counts},
        "candidates": [
            {
                "asset_type": row["asset_type"],
                "path": row["path"],
                "classification": row["classification"],
                "action": row["action"],
                "status": row["status"],
                "reason": row["reason"],
                "blockers": _safe_json(row["blockers_json"]) or [],
                "smoke_status": row["smoke_status"],
                "smoke_error": row["smoke_error"],
            }
            for row in rows
        ],
    }
