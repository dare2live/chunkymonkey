"""Delete obsolete recommendation outputs for retired/non-current models."""
from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from services.data_deletion import ensure_data_deletion_tables, record_data_deletion
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_ROOT = REPO_ROOT / "data" / "multidim_models"
OUTPUT_TABLES = (
    "mart_daily_recommendation",
    "mart_daily_topk_view_cache",
    "mart_daily_recommendation_risk",
    "mart_daily_recommendation_explanation",
    "mart_model_explanation",
    "mart_prediction_outcome",
)
DELETE_STATUSES = {"retired", "deprecated", "deleted"}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _obsolete_output_model_rows(conn: Any) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return []
    cols = _columns(conn, "mart_model_lifecycle")
    if not {"model_id", "status"}.issubset(cols):
        return []
    rows = conn.execute(
        """
        SELECT model_id, status
          FROM mart_model_lifecycle
         WHERE lower(status) IN ('retired', 'deprecated', 'deleted')
           AND model_id IS NOT NULL
           AND model_id <> ''
         ORDER BY model_id
        """
    ).fetchall()
    obsolete_models = [
        {"model_id": str(row["model_id"]), "status": str(row["status"])}
        for row in rows
    ]
    model_ids = [item["model_id"] for item in obsolete_models]
    table_counts_by_model: dict[str, dict[str, int]] = {model_id: {} for model_id in model_ids}
    output_tables = [
        table
        for table in OUTPUT_TABLES
        if _table_exists(conn, table) and "model_id" in _columns(conn, table)
    ]
    if model_ids and output_tables:
        placeholders = ", ".join("?" for _ in model_ids)
        count_sql = "\nUNION ALL\n".join(
            f"""
            SELECT ? AS table_name, model_id, COUNT(*) AS n
              FROM {_quote_ident(table)}
             WHERE model_id IN ({placeholders})
             GROUP BY model_id
            """.strip()
            for table in output_tables
        )
        params = [
            value
            for table in output_tables
            for value in (table, *model_ids)
        ]
        for row in conn.execute(count_sql, params).fetchall():
            model_id = str(row["model_id"])
            table_name = str(row["table_name"])
            count = int(row["n"] or 0)
            if count:
                table_counts_by_model.setdefault(model_id, {})[table_name] = count
    candidates = []
    for item in obsolete_models:
        model_id = item["model_id"]
        table_counts = table_counts_by_model.get(model_id, {})
        path = MODEL_ROOT / f"{model_id}.pkl"
        file_bytes = path.stat().st_size if path.exists() else 0
        if table_counts or file_bytes:
            candidates.append(
                {
                    "model_id": model_id,
                    "status": item["status"],
                    "table_counts": table_counts,
                    "model_file": str(path) if file_bytes else None,
                    "model_file_bytes": file_bytes,
                }
            )
    return candidates


def plan_obsolete_recommendation_output_delete(conn: Any) -> dict[str, Any]:
    candidates = _obsolete_output_model_rows(conn)
    return {
        "mode": "dry_run",
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def execute_obsolete_recommendation_output_delete(
    conn: Any,
    *,
    run_id: str | None = None,
    approve: bool = False,
) -> dict[str, Any]:
    if not approve:
        raise RuntimeError("recommendation output deletion requires approve=True")
    run_id = run_id or f"delete_obsolete_recommendation_outputs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = datetime.now(UTC)
    ensure_data_deletion_tables(conn)
    plan = plan_obsolete_recommendation_output_delete(conn)
    executed = []
    delete_model_ids_by_table: dict[str, list[str]] = {}
    for candidate in plan["candidates"]:
        for table in candidate["table_counts"]:
            delete_model_ids_by_table.setdefault(table, []).append(candidate["model_id"])
    if delete_model_ids_by_table:
        delete_sql = "\n".join(
            f"DELETE FROM {_quote_ident(table)} WHERE model_id IN ({', '.join(_sql_literal(model_id) for model_id in sorted(set(model_ids)))});"
            for table, model_ids in delete_model_ids_by_table.items()
        )
        conn.executescript(delete_sql)
    for candidate in plan["candidates"]:
        model_id = candidate["model_id"]
        verification = {
            "model_id": model_id,
            "status": candidate["status"],
            "delete_policy": "retired_model_outputs_direct_delete_no_archive",
            "table_counts": candidate["table_counts"],
        }
        for table, expected_rows in candidate["table_counts"].items():
            print(f"[recommendation_gc] {utc_now_iso()} delete {table} model_id={model_id}", flush=True)
            deleted_rows = int(expected_rows or 0)
            if deleted_rows:
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name=table,
                    delete_scope="recommendation_output_gc",
                    key_column="model_id",
                    key_value=model_id,
                    deleted_rows=deleted_rows,
                    reason="retired model output must not remain in recommendation surfaces",
                    verification=verification,
                )
                conn.commit()
            executed.append(
                {
                    "kind": "rows",
                    "table_name": table,
                    "model_id": model_id,
                    "expected_rows": expected_rows,
                    "deleted_rows": deleted_rows,
                }
            )
        path_text = candidate.get("model_file")
        if path_text:
            path = Path(path_text)
            bytes_deleted = path.stat().st_size if path.exists() else 0
            deleted_files = 0
            if path.exists():
                print(f"[recommendation_gc] {utc_now_iso()} delete file {path}", flush=True)
                path.unlink()
                deleted_files = 1
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name="filesystem:data/multidim_models",
                    delete_scope="recommendation_output_gc",
                    key_column="model_id",
                    key_value=model_id,
                    deleted_files=deleted_files,
                    deleted_bytes=bytes_deleted,
                    reason="retired model artifact must not remain as an active selectable model",
                    verification=verification,
                )
                conn.commit()
            executed.append(
                {
                    "kind": "file",
                    "path": path_text,
                    "model_id": model_id,
                    "deleted_files": deleted_files,
                    "deleted_bytes": bytes_deleted,
                }
            )
    ended_at = utc_now_iso()
    duration_s = (datetime.now(UTC) - started).total_seconds()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="delete_obsolete_recommendation_outputs",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO_ROOT),
        input_tables=["mart_model_lifecycle", *OUTPUT_TABLES],
        output_tables=["mart_data_deletion_record", *OUTPUT_TABLES],
        perf_summary={
            "stage_timings": {"delete_recommendation_outputs_s": duration_s},
            "candidate_count": plan["candidate_count"],
            "deleted_rows": sum(int(item.get("deleted_rows") or 0) for item in executed),
            "deleted_files": sum(int(item.get("deleted_files") or 0) for item in executed),
        },
    )
    conn.commit()
    return {
        "mode": "execute_approved",
        "run_id": run_id,
        "candidate_count": plan["candidate_count"],
        "deleted_rows": sum(int(item.get("deleted_rows") or 0) for item in executed),
        "deleted_files": sum(int(item.get("deleted_files") or 0) for item in executed),
        "executed": executed,
    }
