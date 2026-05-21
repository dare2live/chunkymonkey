#!/usr/bin/env python3
"""Build a dependency-aware architecture cleanup plan.

The script is intentionally conservative: production cleanup is always dry-run.
Optional view-drop smoke is meant for copied DuckDB files only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.duck_adapter import DuckConn, connect as duck_connect  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent

DDL = """
CREATE TABLE IF NOT EXISTS mart_architecture_cleanup_plan (
    run_id TEXT NOT NULL,
    inventory_run_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    asset_type TEXT,
    path TEXT NOT NULL,
    classification TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    blockers_json TEXT,
    smoke_status TEXT,
    smoke_error TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_arch_cleanup_plan_status
    ON mart_architecture_cleanup_plan(run_id, status, action);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def latest_inventory_run_id(conn: Any) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_architecture_inventory_summary
         ORDER BY built_at DESC
         LIMIT 1
        """
    ).fetchone()
    return row["run_id"] if row else None


def _duckdb_object_name(path: str) -> str:
    parts = [part for part in str(path).split(".") if part]
    return parts[-1] if parts else str(path)


def _view_exists(conn: Any, view_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
           AND table_type = 'VIEW'
         LIMIT 1
        """,
        (view_name,),
    ).fetchone()
    return row is not None


def _managed_recreate_views() -> set[str]:
    try:
        from services.schema_versions import RECREATE_VIEWS  # noqa: WPS433
    except Exception:
        return set()
    return {str(name) for name in RECREATE_VIEWS}


def _classify_cleanup_action(asset: dict[str, Any]) -> dict[str, str]:
    blockers = asset["blockers"]
    if blockers:
        return {
            "action": "keep_blocked",
            "status": "blocked",
            "reason": "active dependency blockers must be removed first",
        }
    asset_type = asset["asset_type"]
    classification = asset["classification"]
    path = asset["path"]
    if asset_type == "duckdb_view" and classification == "compatibility_shim":
        managed_views = _managed_recreate_views()
        if any(path.endswith(f".{view_name}") for view_name in managed_views):
            return {
                "action": "keep_blocked",
                "status": "blocked",
                "reason": "view is still managed by schema_versions.RECREATE_VIEWS",
            }
        return {
            "action": "drop_view_in_copied_db_smoke",
            "status": "ready_for_copied_db_smoke",
            "reason": "compatibility view has no static runtime blockers",
        }
    if asset_type in {"script", "test"} and classification == "deprecated_pending_cleanup":
        return {
            "action": "manual_file_removal_review",
            "status": "manual_review",
            "reason": "file removal requires replacement coverage and git review",
        }
    return {
        "action": "keep_review",
        "status": "manual_review",
        "reason": "not eligible for automated cleanup",
    }


def build_cleanup_rows(conn: Any, *, inventory_run_id: str | None = None) -> list[dict[str, Any]]:
    inventory_run_id = inventory_run_id or latest_inventory_run_id(conn)
    if not inventory_run_id:
        raise RuntimeError("no architecture inventory run found")
    rows = conn.execute(
        """
        SELECT asset_id, asset_type, path, classification, blockers_json
          FROM mart_architecture_inventory_asset
         WHERE run_id = ?
           AND classification IN ('compatibility_shim', 'deprecated_pending_cleanup', 'delete_after_tests')
         ORDER BY classification, path
        """,
        (inventory_run_id,),
    ).fetchall()
    out = []
    for row in rows:
        blockers = _safe_json(row["blockers_json"]) or []
        asset = {
            "inventory_run_id": inventory_run_id,
            "asset_id": row["asset_id"],
            "asset_type": row["asset_type"],
            "path": row["path"],
            "classification": row["classification"],
            "blockers": blockers,
        }
        asset.update(_classify_cleanup_action(asset))
        asset.update({"smoke_status": None, "smoke_error": None})
        out.append(asset)
    return out


def smoke_drop_ready_views(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        if row["action"] != "drop_view_in_copied_db_smoke":
            continue
        view_name = _duckdb_object_name(row["path"])
        try:
            existed_before = _view_exists(conn, view_name)
            conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            exists_after = _view_exists(conn, view_name)
            row["smoke_status"] = "passed" if existed_before and not exists_after else "skipped_missing"
            row["smoke_error"] = None
            if row["smoke_status"] == "passed":
                row["status"] = "smoke_passed"
                row["reason"] = "view dropped successfully in copied DB smoke"
        except Exception as exc:  # pragma: no cover - defensive for DB-specific DDL errors.
            row["smoke_status"] = "failed"
            row["smoke_error"] = str(exc)
            row["status"] = "blocked"
            row["reason"] = "copied DB smoke failed"
    return rows


def persist_cleanup_plan(
    conn: Any,
    *,
    run_id: str,
    rows: list[dict[str, Any]],
    built_at: str,
) -> None:
    ensure_tables(conn)
    conn.execute("DELETE FROM mart_architecture_cleanup_plan WHERE run_id = ?", (run_id,))
    cleanup_rows = [
        (
            run_id,
            row["inventory_run_id"],
            row["asset_id"],
            row["asset_type"],
            row["path"],
            row["classification"],
            row["action"],
            row["status"],
            row["reason"],
            _json(row["blockers"]),
            row["smoke_status"],
            row["smoke_error"],
            built_at,
        )
        for row in rows
    ]
    if cleanup_rows:
        conn.executemany(
            """
            INSERT INTO mart_architecture_cleanup_plan
            (run_id, inventory_run_id, asset_id, asset_type, path, classification,
             action, status, reason, blockers_json, smoke_status, smoke_error, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cleanup_rows,
        )
    record_actual_version(conn, "mart_architecture_cleanup_plan")
    conn.commit()


def plan_architecture_cleanup(
    conn: Any,
    *,
    run_id: str | None = None,
    inventory_run_id: str | None = None,
    smoke_drop_views: bool = False,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    run_id = run_id or f"architecture_cleanup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    selected_inventory_run_id = inventory_run_id or latest_inventory_run_id(conn)
    rows = build_cleanup_rows(conn, inventory_run_id=selected_inventory_run_id)
    if smoke_drop_views:
        rows = smoke_drop_ready_views(conn, rows)
    built_at = utc_now_iso()
    persist_cleanup_plan(conn, run_id=run_id, rows=rows, built_at=built_at)
    counts: dict[str, int] = {}
    actions: dict[str, int] = {}
    smoke_counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        actions[row["action"]] = actions.get(row["action"], 0) + 1
        if row["smoke_status"]:
            smoke_counts[row["smoke_status"]] = smoke_counts.get(row["smoke_status"], 0) + 1
    ended_at = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="plan_architecture_cleanup",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        commit_sha=git_commit_sha(REPO),
        input_tables=["mart_architecture_inventory_asset", "mart_architecture_inventory_summary"],
        output_tables=["mart_architecture_cleanup_plan"],
        perf_summary={
            "status_counts": counts,
            "action_counts": actions,
            "smoke_counts": smoke_counts,
            "candidate_count": len(rows),
            "smoke_drop_views": smoke_drop_views,
            "inventory_run_id": rows[0]["inventory_run_id"] if rows else selected_inventory_run_id,
        },
    )
    return {
        "run_id": run_id,
        "status": "success",
        "inventory_run_id": rows[0]["inventory_run_id"] if rows else selected_inventory_run_id,
        "candidate_count": len(rows),
        "status_counts": counts,
        "action_counts": actions,
        "smoke_counts": smoke_counts,
        "smoke_drop_views": smoke_drop_views,
        "candidates": rows,
    }


def import_smoke_results(
    conn: Any,
    *,
    smoke_db_path: str | Path,
    smoke_run_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    run_id = run_id or f"architecture_cleanup_smoke_record_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    smoke_conn = duck_connect(str(Path(smoke_db_path)))
    try:
        source_rows = smoke_conn.execute(
            """
            SELECT inventory_run_id, asset_id, asset_type, path, classification,
                   action, status, reason, blockers_json, smoke_status,
                   smoke_error, built_at
              FROM mart_architecture_cleanup_plan
             WHERE run_id = ?
             ORDER BY status, action, path
            """,
            (smoke_run_id,),
        ).fetchall()
    finally:
        smoke_conn.close()
    if not source_rows:
        raise RuntimeError(f"no smoke cleanup rows found for run_id={smoke_run_id}")

    rows = [
        {
            "inventory_run_id": row["inventory_run_id"],
            "asset_id": row["asset_id"],
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
        for row in source_rows
    ]
    built_at = utc_now_iso()
    persist_cleanup_plan(conn, run_id=run_id, rows=rows, built_at=built_at)
    counts: dict[str, int] = {}
    actions: dict[str, int] = {}
    smoke_counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        actions[row["action"]] = actions.get(row["action"], 0) + 1
        if row["smoke_status"]:
            smoke_counts[row["smoke_status"]] = smoke_counts.get(row["smoke_status"], 0) + 1
    ended_at = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="import_architecture_cleanup_smoke",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        commit_sha=git_commit_sha(REPO),
        input_tables=["mart_architecture_cleanup_plan"],
        output_tables=["mart_architecture_cleanup_plan"],
        perf_summary={
            "source_smoke_db_path": str(smoke_db_path),
            "source_smoke_run_id": smoke_run_id,
            "status_counts": counts,
            "action_counts": actions,
            "smoke_counts": smoke_counts,
        },
    )
    return {
        "run_id": run_id,
        "status": "success",
        "source_smoke_run_id": smoke_run_id,
        "candidate_count": len(rows),
        "status_counts": counts,
        "action_counts": actions,
        "smoke_counts": smoke_counts,
        "candidates": rows,
    }


def execute_approved_cleanup(
    conn: Any,
    *,
    source_run_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    run_id = run_id or f"architecture_cleanup_execute_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    source_rows = conn.execute(
        """
        SELECT inventory_run_id, asset_id, asset_type, path, classification,
               action, status, reason, blockers_json, smoke_status, smoke_error
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         ORDER BY status, action, path
        """,
        (source_run_id,),
    ).fetchall()
    if not source_rows:
        raise RuntimeError(f"no cleanup plan rows found for source_run_id={source_run_id}")

    rows = []
    executed = []
    for src in source_rows:
        row = {
            "inventory_run_id": src["inventory_run_id"],
            "asset_id": src["asset_id"],
            "asset_type": src["asset_type"],
            "path": src["path"],
            "classification": src["classification"],
            "action": src["action"],
            "status": src["status"],
            "reason": src["reason"],
            "blockers": _safe_json(src["blockers_json"]) or [],
            "smoke_status": src["smoke_status"],
            "smoke_error": src["smoke_error"],
        }
        if (
            row["action"] == "drop_view_in_copied_db_smoke"
            and row["status"] == "smoke_passed"
            and row["smoke_status"] == "passed"
        ):
            view_name = _duckdb_object_name(row["path"])
            existed_before = _view_exists(conn, view_name)
            conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            exists_after = _view_exists(conn, view_name)
            row["status"] = "executed"
            row["reason"] = (
                "view dropped in production after copied DB smoke"
                if existed_before and not exists_after
                else "view already absent in production during approved cleanup"
            )
            executed.append(
                {
                    "path": row["path"],
                    "view_name": view_name,
                    "existed_before": existed_before,
                    "exists_after": exists_after,
                }
            )
        rows.append(row)

    built_at = utc_now_iso()
    persist_cleanup_plan(conn, run_id=run_id, rows=rows, built_at=built_at)
    counts: dict[str, int] = {}
    actions: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        actions[row["action"]] = actions.get(row["action"], 0) + 1
    ended_at = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="execute_architecture_cleanup",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        commit_sha=git_commit_sha(REPO),
        input_tables=["mart_architecture_cleanup_plan"],
        output_tables=["mart_architecture_cleanup_plan"],
        perf_summary={
            "source_run_id": source_run_id,
            "status_counts": counts,
            "action_counts": actions,
            "executed": executed,
        },
    )
    return {
        "run_id": run_id,
        "status": "success",
        "source_run_id": source_run_id,
        "candidate_count": len(rows),
        "executed_count": len(executed),
        "status_counts": counts,
        "action_counts": actions,
        "executed": executed,
        "candidates": rows,
    }


def prepare_db_copy(*, source: str | None, target: str | None, overwrite: bool) -> None:
    if not source:
        return
    if not target:
        raise RuntimeError("--copy-from requires --db-path")
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.exists():
        raise RuntimeError(f"copy source does not exist: {source_path}")
    if target_path.exists() and not overwrite:
        raise RuntimeError(f"copy target already exists; pass --overwrite-copy: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


@contextmanager
def open_connection(db_path: str | None = None) -> Iterator[DuckConn]:
    conn = duck_connect(str(Path(db_path))) if db_path else get_conn()
    try:
        yield conn
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--inventory-run-id", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--copy-from", default=None)
    parser.add_argument("--overwrite-copy", action="store_true")
    parser.add_argument("--smoke-drop-views", action="store_true")
    parser.add_argument("--import-smoke-from-db", default=None)
    parser.add_argument("--import-smoke-run-id", default=None)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--source-run-id", default=None)
    args = parser.parse_args()

    if args.execute_approved:
        if not args.source_run_id:
            raise RuntimeError("--execute-approved requires --source-run-id")
        with open_connection(args.db_path) as conn:
            result = execute_approved_cleanup(
                conn,
                source_run_id=args.source_run_id,
                run_id=args.run_id,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.import_smoke_from_db:
        if not args.import_smoke_run_id:
            raise RuntimeError("--import-smoke-from-db requires --import-smoke-run-id")
        with open_connection(args.db_path) as conn:
            result = import_smoke_results(
                conn,
                smoke_db_path=args.import_smoke_from_db,
                smoke_run_id=args.import_smoke_run_id,
                run_id=args.run_id,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    prepare_db_copy(source=args.copy_from, target=args.db_path, overwrite=args.overwrite_copy)
    if args.smoke_drop_views and not args.db_path:
        raise RuntimeError("--smoke-drop-views must run against --db-path, preferably a copied DB")
    with open_connection(args.db_path) as conn:
        result = plan_architecture_cleanup(
            conn,
            run_id=args.run_id,
            inventory_run_id=args.inventory_run_id,
            smoke_drop_views=args.smoke_drop_views,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
