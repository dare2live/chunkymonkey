"""Delete obsolete model artifacts and model-scoped rows after verification."""
from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from services.data_deletion import ensure_data_deletion_tables, record_data_deletion
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.pricing_policy import load_pricing_label_policy


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_ROOT = REPO_ROOT / "data" / "multidim_models"
PROTECTED_STATUSES = {"champion", "production", "deployed"}
DELETE_STATUSES = {"retired", "deprecated", "deleted"}
MODEL_ID_TABLE_DELETE_LAST = ("mart_multidim_model", "mart_model_lifecycle")
RUN_ID_DEPENDENT_TABLES = ("mart_model_walkforward_prediction", "mart_pipeline_run_manifest")
MODEL_REF_TABLE_DELETE_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mart_tdx_keep_promotion_gate", ("challenger_model_id", "champion_model_id")),
)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _placeholders(values: list[str] | tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except Exception:
        return row[index]


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        str(_row_value(row, "column_name"))
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _model_id_tables(conn: Any) -> list[str]:
    rows = conn.execute(
        """
        SELECT c.table_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON c.table_schema = t.table_schema
           AND c.table_name = t.table_name
         WHERE c.table_schema = 'main'
           AND c.column_name = 'model_id'
           AND t.table_type = 'BASE TABLE'
         ORDER BY c.table_name
        """
    ).fetchall()
    tables = [str(_row_value(row, "table_name")) for row in rows]
    last = [table for table in MODEL_ID_TABLE_DELETE_LAST if table in tables]
    first = [table for table in tables if table not in set(last)]
    return [*first, *last]


def _model_ids(conn: Any, model_root: Path) -> list[str]:
    ids: set[str] = set()
    model_tables = _model_id_tables(conn)
    if model_tables:
        sql = "\nUNION\n".join(
            f"SELECT DISTINCT model_id FROM {_quote_ident(table)} WHERE model_id IS NOT NULL AND model_id <> ''"
            for table in model_tables
        )
        rows = conn.execute(sql).fetchall()
        ids.update(str(_row_value(row, "model_id")) for row in rows)
    ref_selects: list[str] = []
    for table, ref_columns in MODEL_REF_TABLE_DELETE_SPECS:
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        for column in ref_columns:
            if column not in cols:
                continue
            ref_selects.append(
                f"""
                SELECT DISTINCT {_quote_ident(column)} AS model_id
                  FROM {_quote_ident(table)}
                 WHERE {_quote_ident(column)} IS NOT NULL
                   AND {_quote_ident(column)} <> ''
                """.strip()
            )
    if ref_selects:
        rows = conn.execute("\nUNION\n".join(ref_selects)).fetchall()
        ids.update(str(_row_value(row, "model_id")) for row in rows)
    if model_root.exists():
        ids.update(path.stem for path in model_root.glob("*.pkl"))
    return sorted(ids)


def _current_policy_hash() -> str | None:
    try:
        return load_pricing_label_policy().policy_hash()
    except Exception:
        return None


def _model_metadata(conn: Any, model_id: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"model_id": model_id}
    if _table_exists(conn, "mart_model_lifecycle"):
        cols = _columns(conn, "mart_model_lifecycle")
        if {"model_id", "status"}.issubset(cols):
            selected = [col for col in ("status", "created_at", "updated_at") if col in cols]
            row = conn.execute(
                f"""
                SELECT {", ".join(_quote_ident(col) for col in selected)}
                  FROM mart_model_lifecycle
                 WHERE model_id = ?
                 LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if row:
                for idx, col in enumerate(selected):
                    value = _row_value(row, col, idx)
                    if col == "status":
                        metadata["status"] = str(value or "")
                    else:
                        metadata[f"lifecycle_{col}"] = value
    if _table_exists(conn, "mart_multidim_model"):
        cols = _columns(conn, "mart_multidim_model")
        selected = [
            col
            for col in ("label_name", "pricing_policy_hash", "created_at", "feature_schema_version")
            if col in cols
        ]
        if selected:
            row = conn.execute(
                f"""
                SELECT {", ".join(_quote_ident(col) for col in selected)}
                  FROM mart_multidim_model
                 WHERE model_id = ?
                 LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if row:
                for idx, col in enumerate(selected):
                    metadata[col] = _row_value(row, col, idx)
    return metadata


def _primary_dependency(conn: Any, model_id: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_daily_recommendation"):
        return None
    cols = _columns(conn, "mart_daily_recommendation")
    if "model_id" not in cols:
        return None
    predicates = ["model_id = ?"]
    if "is_primary" in cols:
        predicates.append("COALESCE(is_primary, false) = true")
    if "run_mode" in cols:
        predicates.append("lower(COALESCE(run_mode, '')) IN ('champion', 'primary', 'production')")
    if len(predicates) == 1:
        return None
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM mart_daily_recommendation
         WHERE {predicates[0]}
           AND ({' OR '.join(predicates[1:])})
        """,
        (model_id,),
    ).fetchone()
    count = int(_row_value(row, "n") or 0)
    if not count:
        return None
    return {"type": "primary_recommendation_dependency", "model_id": model_id, "row_count": count}


def _deletion_reason(metadata: dict[str, Any], current_policy_hash: str | None) -> str | None:
    status = str(metadata.get("status") or "").lower()
    pricing_hash = str(metadata.get("pricing_policy_hash") or "")
    if status in DELETE_STATUSES:
        return f"lifecycle status is {status}"
    if current_policy_hash and not pricing_hash:
        return f"pricing policy hash is missing; current is {current_policy_hash}"
    if pricing_hash and current_policy_hash and pricing_hash != current_policy_hash:
        return f"pricing policy hash {pricing_hash} is stale; current is {current_policy_hash}"
    if status == "challenger":
        return "unprotected challenger superseded by current champion or current 60d baseline evidence"
    if not status and metadata.get("pricing_policy_hash") is None:
        return "orphan model artifact or model row without lifecycle protection"
    return None


def _has_pricing_policy_staleness(metadata: dict[str, Any], current_policy_hash: str | None) -> bool:
    if not current_policy_hash:
        return False
    pricing_hash = str(metadata.get("pricing_policy_hash") or "")
    return pricing_hash != current_policy_hash


def _table_count_for_model(conn: Any, table: str, model_id: str) -> int:
    if not _table_exists(conn, table) or "model_id" not in _columns(conn, table):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_quote_ident(table)} WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    return int(_row_value(row, "n") or 0)


def _ref_table_counts_for_model(conn: Any, model_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, ref_columns in MODEL_REF_TABLE_DELETE_SPECS:
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        existing_columns = [column for column in ref_columns if column in cols]
        if not existing_columns:
            continue
        predicates = [f"{_quote_ident(column)} = ?" for column in existing_columns]
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
              FROM {_quote_ident(table)}
             WHERE {' OR '.join(predicates)}
            """,
            [model_id] * len(existing_columns),
        ).fetchone()
        count = int(_row_value(row, "n") or 0)
        if count:
            counts[f"{table}:{','.join(existing_columns)}"] = count
    return counts


def _walkforward_run_ids(conn: Any, model_id: str) -> list[str]:
    if not _table_exists(conn, "mart_model_walkforward_fold"):
        return []
    cols = _columns(conn, "mart_model_walkforward_fold")
    if not {"model_id", "run_id"}.issubset(cols):
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT run_id
          FROM mart_model_walkforward_fold
         WHERE model_id = ?
           AND run_id IS NOT NULL
           AND run_id <> ''
         ORDER BY run_id
        """,
        (model_id,),
    ).fetchall()
    return [str(_row_value(row, "run_id")) for row in rows]


def _run_id_table_counts(conn: Any, run_ids: list[str]) -> dict[str, int]:
    if not run_ids:
        return {}
    selects: list[str] = []
    params: list[Any] = []
    for table in RUN_ID_DEPENDENT_TABLES:
        if not _table_exists(conn, table) or "run_id" not in _columns(conn, table):
            continue
        selects.append(
            f"""
            SELECT ? AS table_name, COUNT(*) AS n
              FROM {_quote_ident(table)}
             WHERE run_id IN ({_placeholders(run_ids)})
            """.strip()
        )
        params.extend([table, *run_ids])
    if not selects:
        return {}
    rows = conn.execute("\nUNION ALL\n".join(selects), params).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        count = int(_row_value(row, "n", 1) or 0)
        if count:
            counts[str(_row_value(row, "table_name"))] = count
    return counts


def plan_obsolete_model_artifact_delete(
    conn: Any,
    *,
    keep_model_ids: set[str] | None = None,
    current_policy_hash: str | None = None,
    model_root: str | Path | None = None,
) -> dict[str, Any]:
    keep_model_ids = set(keep_model_ids or set())
    current_policy_hash = current_policy_hash or _current_policy_hash()
    root = Path(model_root) if model_root is not None else MODEL_ROOT
    candidates: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    model_tables = _model_id_tables(conn)
    for model_id in _model_ids(conn, root):
        metadata = _model_metadata(conn, model_id)
        status = str(metadata.get("status") or "").lower()
        pricing_policy_stale = _has_pricing_policy_staleness(metadata, current_policy_hash)
        reason = _deletion_reason(metadata, current_policy_hash)
        if model_id in keep_model_ids:
            protected.append({"model_id": model_id, "status": status or None, "reason": "explicit_keep"})
            continue
        if status in PROTECTED_STATUSES and not pricing_policy_stale:
            protected.append({"model_id": model_id, "status": status or None, "reason": "protected_status_current_policy"})
            continue
        dependency = _primary_dependency(conn, model_id)
        if dependency and not pricing_policy_stale:
            blockers.append(dependency)
            protected.append({"model_id": model_id, "status": status or None, "reason": dependency["type"]})
            continue
        if not reason:
            protected.append({"model_id": model_id, "status": status or None, "reason": "no_obsolete_rule_matched"})
            continue
        table_counts = {
            table: count
            for table in model_tables
            if (count := _table_count_for_model(conn, table, model_id))
        }
        ref_table_counts = _ref_table_counts_for_model(conn, model_id)
        run_ids = _walkforward_run_ids(conn, model_id)
        run_id_table_counts = _run_id_table_counts(conn, run_ids)
        path = root / f"{model_id}.pkl"
        file_bytes = path.stat().st_size if path.exists() else 0
        if table_counts or ref_table_counts or run_id_table_counts or file_bytes:
            candidates.append(
                {
                    "model_id": model_id,
                    "status": status or None,
                    "reason": reason,
                    "metadata": metadata,
                    "table_counts": table_counts,
                    "ref_table_counts": ref_table_counts,
                    "walkforward_run_ids": run_ids,
                    "run_id_table_counts": run_id_table_counts,
                    "model_file": str(path) if file_bytes else None,
                    "model_file_bytes": file_bytes,
                }
            )
    return {
        "mode": "dry_run",
        "delete_policy": "verified_direct_delete_no_archive",
        "current_policy_hash": current_policy_hash,
        "keep_model_ids": sorted(keep_model_ids),
        "candidate_count": len(candidates),
        "blocker_count": len(blockers),
        "candidates": candidates,
        "blockers": blockers,
        "protected": protected,
    }


def _delete_rows_by_model(
    conn: Any,
    *,
    table: str,
    model_id: str,
    run_id: str,
    reason: str,
    verification: dict[str, Any],
) -> int:
    before = _table_count_for_model(conn, table, model_id)
    if not before:
        return 0
    print(f"[model_gc] {utc_now_iso()} delete {table} model_id={model_id}", flush=True)
    conn.execute(f"DELETE FROM {_quote_ident(table)} WHERE model_id = ?", (model_id,))
    record_data_deletion(
        conn,
        deletion_run_id=run_id,
        table_name=table,
        delete_scope="model_artifact_gc",
        key_column="model_id",
        key_value=model_id,
        deleted_rows=before,
        reason=reason,
        verification=verification,
    )
    conn.commit()
    return before


def _delete_rows_by_run_ids(
    conn: Any,
    *,
    table: str,
    run_ids: list[str],
    model_id: str,
    run_id: str,
    reason: str,
    verification: dict[str, Any],
) -> int:
    if not run_ids or not _table_exists(conn, table) or "run_id" not in _columns(conn, table):
        return 0
    before = int(
        _row_value(
            conn.execute(
                f"""
                SELECT COUNT(*) AS n
                  FROM {_quote_ident(table)}
                 WHERE run_id IN ({_placeholders(run_ids)})
                """,
                run_ids,
            ).fetchone(),
            "n",
        )
        or 0
    )
    if not before:
        return 0
    print(f"[model_gc] {utc_now_iso()} delete {table} walkforward runs for model_id={model_id}", flush=True)
    conn.execute(
        f"""
        DELETE FROM {_quote_ident(table)}
         WHERE run_id IN ({_placeholders(run_ids)})
        """,
        run_ids,
    )
    record_data_deletion(
        conn,
        deletion_run_id=run_id,
        table_name=table,
        delete_scope="model_artifact_gc_run_dependency",
        key_column="model_id",
        key_value=model_id,
        deleted_rows=before,
        reason=reason,
        verification={**verification, "run_ids": run_ids},
    )
    conn.commit()
    return before


def _delete_rows_by_model_refs(
    conn: Any,
    *,
    table: str,
    columns: tuple[str, ...],
    model_id: str,
    run_id: str,
    reason: str,
    verification: dict[str, Any],
) -> int:
    if not _table_exists(conn, table):
        return 0
    cols = _columns(conn, table)
    existing_columns = [column for column in columns if column in cols]
    if not existing_columns:
        return 0
    predicates = [f"{_quote_ident(column)} = ?" for column in existing_columns]
    params = [model_id] * len(existing_columns)
    before = int(
        _row_value(
            conn.execute(
                f"""
                SELECT COUNT(*) AS n
                  FROM {_quote_ident(table)}
                 WHERE {' OR '.join(predicates)}
                """,
                params,
            ).fetchone(),
            "n",
        )
        or 0
    )
    if not before:
        return 0
    print(f"[model_gc] {utc_now_iso()} delete {table} refs for model_id={model_id}", flush=True)
    conn.execute(
        f"""
        DELETE FROM {_quote_ident(table)}
         WHERE {' OR '.join(predicates)}
        """,
        params,
    )
    record_data_deletion(
        conn,
        deletion_run_id=run_id,
        table_name=table,
        delete_scope="model_artifact_gc_reference_rows",
        key_column="model_id_reference",
        key_value=model_id,
        deleted_rows=before,
        reason=reason,
        verification={**verification, "reference_columns": existing_columns},
    )
    conn.commit()
    return before


def _clear_dangling_promoted_from(conn: Any, *, run_id: str) -> int:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return 0
    cols = _columns(conn, "mart_model_lifecycle")
    if not {"model_id", "promoted_from"}.issubset(cols):
        return 0
    rows = conn.execute(
        """
        SELECT l.model_id, l.promoted_from
          FROM mart_model_lifecycle l
          LEFT JOIN mart_model_lifecycle p
            ON l.promoted_from = p.model_id
         WHERE l.promoted_from IS NOT NULL
           AND l.promoted_from <> ''
           AND p.model_id IS NULL
         ORDER BY l.model_id
        """
    ).fetchall()
    if not rows:
        return 0
    print(f"[model_gc] {utc_now_iso()} clear dangling promoted_from references", flush=True)
    model_ids = [str(_row_value(row, "model_id", 0)) for row in rows]
    conn.execute(
        f"""
        UPDATE mart_model_lifecycle
           SET promoted_from = NULL
         WHERE model_id IN ({_placeholders(model_ids)})
        """,
        model_ids,
    )
    record_data_deletion(
        conn,
        deletion_run_id=run_id,
        table_name="mart_model_lifecycle",
        delete_scope="model_artifact_gc_reference_cleanup",
        key_column="promoted_from",
        key_value="dangling_deleted_model",
        deleted_rows=0,
        reason="clear dangling promoted_from references after direct model deletion",
        verification={
            "updated_rows": len(rows),
            "references": [
                {
                    "model_id": _row_value(row, "model_id", 0),
                    "promoted_from": _row_value(row, "promoted_from", 1),
                }
                for row in rows
            ],
        },
    )
    conn.commit()
    return len(rows)


def execute_obsolete_model_artifact_delete(
    conn: Any,
    *,
    run_id: str | None = None,
    approve: bool = False,
    keep_model_ids: set[str] | None = None,
    current_policy_hash: str | None = None,
    model_root: str | Path | None = None,
) -> dict[str, Any]:
    if not approve:
        raise RuntimeError("obsolete model artifact deletion requires approve=True")
    run_id = run_id or f"delete_obsolete_model_artifacts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = datetime.now(UTC)
    root = Path(model_root) if model_root is not None else MODEL_ROOT
    ensure_data_deletion_tables(conn)
    plan = plan_obsolete_model_artifact_delete(
        conn,
        keep_model_ids=keep_model_ids,
        current_policy_hash=current_policy_hash,
        model_root=root,
    )
    if plan["blockers"]:
        raise RuntimeError(f"obsolete model deletion blocked: {plan['blockers'][:3]}")
    executed: list[dict[str, Any]] = []
    for candidate in plan["candidates"]:
        model_id = candidate["model_id"]
        reason = str(candidate["reason"])
        verification = {
            "model_id": model_id,
            "status": candidate.get("status"),
            "delete_policy": "verified_direct_delete_no_archive",
            "reason": reason,
            "metadata": candidate.get("metadata") or {},
            "table_counts": candidate.get("table_counts") or {},
            "ref_table_counts": candidate.get("ref_table_counts") or {},
            "run_id_table_counts": candidate.get("run_id_table_counts") or {},
        }
        for table in RUN_ID_DEPENDENT_TABLES:
            deleted_rows = _delete_rows_by_run_ids(
                conn,
                table=table,
                run_ids=list(candidate.get("walkforward_run_ids") or []),
                model_id=model_id,
                run_id=run_id,
                reason=reason,
                verification=verification,
            )
            if deleted_rows:
                executed.append({"kind": "rows", "table_name": table, "model_id": model_id, "deleted_rows": deleted_rows})
        for table, columns in MODEL_REF_TABLE_DELETE_SPECS:
            deleted_rows = _delete_rows_by_model_refs(
                conn,
                table=table,
                columns=columns,
                model_id=model_id,
                run_id=run_id,
                reason=reason,
                verification=verification,
            )
            if deleted_rows:
                executed.append({"kind": "reference_rows", "table_name": table, "model_id": model_id, "deleted_rows": deleted_rows})
        for table in _model_id_tables(conn):
            deleted_rows = _delete_rows_by_model(
                conn,
                table=table,
                model_id=model_id,
                run_id=run_id,
                reason=reason,
                verification=verification,
            )
            if deleted_rows:
                executed.append({"kind": "rows", "table_name": table, "model_id": model_id, "deleted_rows": deleted_rows})
        path_text = candidate.get("model_file")
        if path_text:
            path = Path(path_text)
            bytes_deleted = path.stat().st_size if path.exists() else 0
            if path.exists():
                print(f"[model_gc] {utc_now_iso()} delete file {path}", flush=True)
                path.unlink()
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name="filesystem:data/multidim_models",
                    delete_scope="model_artifact_gc",
                    key_column="model_id",
                    key_value=model_id,
                    deleted_files=1,
                    deleted_bytes=bytes_deleted,
                    reason=reason,
                    verification=verification,
                )
                conn.commit()
                executed.append(
                    {
                        "kind": "file",
                        "path": path_text,
                        "model_id": model_id,
                        "deleted_files": 1,
                        "deleted_bytes": bytes_deleted,
                    }
                )
    duration_s = (datetime.now(UTC) - started).total_seconds()
    cleared_promoted_from = _clear_dangling_promoted_from(conn, run_id=run_id)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="delete_obsolete_model_artifacts",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO_ROOT),
        input_tables=["mart_model_lifecycle", "mart_multidim_model", "mart_daily_recommendation", "mart_tdx_keep_promotion_gate"],
        output_tables=["mart_data_deletion_record"],
        perf_summary={
            "stage_timings": {"delete_model_artifacts_s": duration_s},
            "candidate_count": plan["candidate_count"],
            "deleted_rows": sum(int(item.get("deleted_rows") or 0) for item in executed),
            "deleted_files": sum(int(item.get("deleted_files") or 0) for item in executed),
            "cleared_dangling_promoted_from": cleared_promoted_from,
        },
    )
    conn.commit()
    return {
        "mode": "execute_approved",
        "delete_policy": "verified_direct_delete_no_archive",
        "run_id": run_id,
        "candidate_count": plan["candidate_count"],
        "deleted_rows": sum(int(item.get("deleted_rows") or 0) for item in executed),
        "deleted_files": sum(int(item.get("deleted_files") or 0) for item in executed),
        "cleared_dangling_promoted_from": cleared_promoted_from,
        "executed": executed,
    }
