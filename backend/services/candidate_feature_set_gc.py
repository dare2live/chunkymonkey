"""Delete obsolete candidate feature-set assets after dependency verification."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.data_deletion import ensure_data_deletion_tables, record_data_deletion
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_ROOT = REPO_ROOT / "data" / "multidim_models"
FEATURE_SET_COLUMNS = (
    "feature_set_id",
    "source_feature_set_id",
    "retention_feature_set_id",
    "panel_feature_set_id",
    "output_feature_set_id",
    "base_feature_set_id",
    "extra_feature_set_id",
)
PRODUCTION_MODEL_STATUSES = {"champion", "production", "deployed"}
FEATURE_SET_DELETE_TABLE_ALLOWLIST = {
    "fact_feature_panel_candidate",
    "fact_feature_panel_tdx_keep_challenger",
    "mart_candidate_feature_set_contract",
    "mart_candidate_walkforward_eval",
    "mart_feature_candidate_coverage",
    "mart_feature_candidate_score",
    "mart_feature_drift",
    "mart_feature_drift_mitigation_panel_build",
    "mart_feature_group_ablation",
    "mart_feature_pit_audit",
    "mart_feature_pit_coverage_summary",
    "mart_feature_retention_decision",
    "mart_hybrid_feature_panel_build",
    "mart_model_holding_topk_eval",
    "mart_model_selection_run",
    "mart_model_stability_search_summary",
    "mart_stock_horizon_profile",
    "mart_stock_horizon_selection",
    "mart_tdx_challenger_report",
    "mart_tdx_gpcw_auto_challenger_report",
    "mart_tdx_gpcw_auto_feature_cluster",
    "mart_tdx_gpcw_auto_feature_score",
    "mart_tdx_gpcw_auto_optuna_run",
    "mart_tdx_gpcw_auto_pit_audit",
    "mart_tdx_gpcw_auto_retention_decision",
    "mart_temporal_research_panel",
    "mart_temporal_research_panel_quality",
}


@dataclass(frozen=True)
class DeleteAction:
    kind: str
    target: str
    predicate: str | None
    params: tuple[Any, ...]
    reason: str
    key_column: str | None = None
    key_value: str | None = None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _placeholders(values: list[str] | set[str] | tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


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


def _base_tables(conn: Any) -> list[str]:
    rows = conn.execute(
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'main'
           AND table_type = 'BASE TABLE'
         ORDER BY table_name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _json_load(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None


def _config_references_feature_set(config: Any, feature_set_ids: set[str]) -> bool:
    payload = _json_load(config)
    if isinstance(payload, dict):
        feature_table = str(payload.get("feature_table") or "").strip()
        if feature_table == "fact_feature_panel_candidate":
            return True
        for key in FEATURE_SET_COLUMNS:
            value = payload.get(key)
            if isinstance(value, str) and value in feature_set_ids:
                return True
            if isinstance(value, list) and any(str(item) in feature_set_ids for item in value):
                return True
    text = str(config or "")
    return any(feature_set_id in text for feature_set_id in feature_set_ids)


def candidate_feature_set_summary(conn: Any) -> list[dict[str, Any]]:
    if not _table_exists(conn, "fact_feature_panel_candidate"):
        return []
    rows = conn.execute(
        """
        SELECT feature_set_id,
               COUNT(*) AS row_count,
               COUNT(DISTINCT stock_code) AS stock_count,
               MIN(date) AS min_date,
               MAX(date) AS max_date
          FROM fact_feature_panel_candidate
         GROUP BY feature_set_id
         ORDER BY row_count DESC, feature_set_id
        """
    ).fetchall()
    return [
        {
            "feature_set_id": row["feature_set_id"],
            "row_count": int(row["row_count"] or 0),
            "stock_count": int(row["stock_count"] or 0),
            "min_date": row["min_date"],
            "max_date": row["max_date"],
        }
        for row in rows
    ]


def _stale_model_ids(conn: Any, feature_set_ids: set[str]) -> set[str]:
    if not feature_set_ids or not _table_exists(conn, "mart_model_lifecycle"):
        return set()
    cols = _columns(conn, "mart_model_lifecycle")
    if "model_id" not in cols or "training_config" not in cols:
        return set()
    rows = conn.execute("SELECT model_id, training_config FROM mart_model_lifecycle").fetchall()
    return {
        str(row["model_id"])
        for row in rows
        if row["model_id"] and _config_references_feature_set(row["training_config"], feature_set_ids)
    }


def _walkforward_prediction_run_ids(conn: Any, stale_model_ids: set[str]) -> set[str]:
    if not stale_model_ids or not _table_exists(conn, "mart_model_walkforward_fold"):
        return set()
    cols = _columns(conn, "mart_model_walkforward_fold")
    if "model_id" not in cols or "run_id" not in cols:
        return set()
    model_ids = sorted(stale_model_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT run_id
          FROM mart_model_walkforward_fold
         WHERE model_id IN ({_placeholders(model_ids)})
           AND run_id IS NOT NULL
           AND run_id <> ''
        """,
        model_ids,
    ).fetchall()
    return {str(row["run_id"]) for row in rows}


def verify_candidate_feature_set_delete(
    conn: Any,
    feature_set_ids: set[str],
    stale_model_ids: set[str],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if stale_model_ids and _table_exists(conn, "mart_model_lifecycle"):
        cols = _columns(conn, "mart_model_lifecycle")
        if {"model_id", "status"}.issubset(cols):
            model_ids = sorted(stale_model_ids)
            rows = conn.execute(
                f"""
                SELECT model_id, status
                  FROM mart_model_lifecycle
                 WHERE model_id IN ({_placeholders(model_ids)})
                   AND lower(status) IN ({_placeholders(sorted(PRODUCTION_MODEL_STATUSES))})
                """,
                [*model_ids, *sorted(PRODUCTION_MODEL_STATUSES)],
            ).fetchall()
            blockers.extend(
                {
                    "type": "production_model_status",
                    "model_id": row["model_id"],
                    "status": row["status"],
                }
                for row in rows
            )
    for table in ("mart_daily_recommendation", "mart_daily_topk_view_cache"):
        if not stale_model_ids or not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if "model_id" not in cols or "is_primary" not in cols:
            continue
        model_ids = sorted(stale_model_ids)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
              FROM {_quote_ident(table)}
             WHERE model_id IN ({_placeholders(model_ids)})
               AND is_primary = TRUE
            """,
            model_ids,
        ).fetchone()
        count = int(row["n"] or 0)
        if count:
            blockers.append(
                {
                    "type": "primary_recommendation_reference",
                    "table": table,
                    "row_count": count,
                }
            )
    return blockers


def build_candidate_delete_actions(
    conn: Any,
    *,
    feature_set_ids: set[str],
    stale_model_ids: set[str],
    stale_walkforward_run_ids: set[str],
) -> list[DeleteAction]:
    actions: list[DeleteAction] = []
    feature_sets = sorted(feature_set_ids)
    models = sorted(stale_model_ids)
    walkforward_runs = sorted(stale_walkforward_run_ids)
    for table in _base_tables(conn):
        cols = _columns(conn, table)
        if table == "fact_feature_panel_candidate" and feature_sets and "feature_set_id" in cols:
            actions.append(
                DeleteAction(
                    kind="truncate",
                    target=table,
                    predicate=None,
                    params=(),
                    key_column="feature_set_id",
                    key_value=",".join(feature_sets),
                    reason="all current candidate feature_set rows are obsolete and must be rebuilt",
                )
            )
            continue
        feature_cols = [
            col
            for col in FEATURE_SET_COLUMNS
            if table in FEATURE_SET_DELETE_TABLE_ALLOWLIST and col in cols
        ]
        if feature_sets and feature_cols:
            predicate = " OR ".join(
                f"{_quote_ident(col)} IN ({_placeholders(feature_sets)})"
                for col in feature_cols
            )
            params = tuple(value for _ in feature_cols for value in feature_sets)
            actions.append(
                DeleteAction(
                    kind="rows",
                    target=table,
                    predicate=predicate,
                    params=params,
                    key_column=",".join(feature_cols),
                    key_value=",".join(feature_sets),
                    reason="obsolete candidate feature_set rows are not current trainable assets",
                )
            )
        if models and "model_id" in cols:
            actions.append(
                DeleteAction(
                    kind="rows",
                    target=table,
                    predicate=f"model_id IN ({_placeholders(models)})",
                    params=tuple(models),
                    key_column="model_id",
                    key_value=",".join(models),
                    reason="model output belongs to obsolete candidate feature_set",
                )
            )
    if (
        walkforward_runs
        and _table_exists(conn, "mart_model_walkforward_prediction")
        and "run_id" in _columns(conn, "mart_model_walkforward_prediction")
    ):
        actions.append(
            DeleteAction(
                kind="rows",
                target="mart_model_walkforward_prediction",
                predicate=f"run_id IN ({_placeholders(walkforward_runs)})",
                params=tuple(walkforward_runs),
                key_column="run_id",
                key_value=",".join(walkforward_runs),
                reason="walk-forward predictions belong to obsolete candidate models",
            )
        )
    for model_id in models:
        path = MODEL_ROOT / f"{model_id}.pkl"
        if path.exists():
            actions.append(
                DeleteAction(
                    kind="file",
                    target=str(path),
                    predicate=None,
                    params=(),
                    key_column="model_id",
                    key_value=model_id,
                    reason="model artifact belongs to obsolete candidate feature_set",
                )
            )
    return actions


def plan_obsolete_candidate_feature_set_delete(conn: Any) -> dict[str, Any]:
    feature_sets = candidate_feature_set_summary(conn)
    feature_set_ids = {str(item["feature_set_id"]) for item in feature_sets if item["feature_set_id"]}
    stale_model_ids = _stale_model_ids(conn, feature_set_ids)
    stale_walkforward_run_ids = _walkforward_prediction_run_ids(conn, stale_model_ids)
    blockers = verify_candidate_feature_set_delete(conn, feature_set_ids, stale_model_ids)
    actions = build_candidate_delete_actions(
        conn,
        feature_set_ids=feature_set_ids,
        stale_model_ids=stale_model_ids,
        stale_walkforward_run_ids=stale_walkforward_run_ids,
    )
    return {
        "mode": "dry_run",
        "feature_sets": feature_sets,
        "feature_set_count": len(feature_sets),
        "stale_model_ids": sorted(stale_model_ids),
        "stale_model_count": len(stale_model_ids),
        "stale_walkforward_run_ids": sorted(stale_walkforward_run_ids),
        "production_blockers": blockers,
        "action_count": len(actions),
        "actions": [
            {
                "kind": action.kind,
                "target": action.target,
                "predicate": action.predicate,
                "reason": action.reason,
                "key_column": action.key_column,
                "key_value": action.key_value,
            }
            for action in actions
        ],
    }


def _delete_rows(conn: Any, action: DeleteAction) -> int:
    assert action.predicate
    before = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_quote_ident(action.target)} WHERE {action.predicate}",
        action.params,
    ).fetchone()
    before_count = int(before["n"] or 0)
    if before_count <= 0:
        return 0
    conn.execute(
        f"DELETE FROM {_quote_ident(action.target)} WHERE {action.predicate}",
        action.params,
    )
    after = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_quote_ident(action.target)} WHERE {action.predicate}",
        action.params,
    ).fetchone()
    return before_count - int(after["n"] or 0)


def _replace_with_empty_table(conn: Any, action: DeleteAction) -> int:
    before = conn.execute(f"SELECT COUNT(*) AS n FROM {_quote_ident(action.target)}").fetchone()
    before_count = int(before["n"] or 0)
    if before_count <= 0:
        return 0
    temp_table = f"__empty_{action.target}"
    conn.execute(
        f"CREATE OR REPLACE TABLE {_quote_ident(temp_table)} AS "
        f"SELECT * FROM {_quote_ident(action.target)} WHERE 1 = 0"
    )
    conn.execute(f"DROP TABLE {_quote_ident(action.target)}")
    conn.execute(f"ALTER TABLE {_quote_ident(temp_table)} RENAME TO {_quote_ident(action.target)}")
    columns = _columns(conn, action.target)
    if {"feature_set_id", "stock_code", "date"}.issubset(columns):
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_candidate_pk "
            "ON fact_feature_panel_candidate(feature_set_id, stock_code, date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feature_candidate_date "
            "ON fact_feature_panel_candidate(feature_set_id, date)"
        )
    return before_count


def execute_obsolete_candidate_feature_set_delete(
    conn: Any,
    *,
    run_id: str | None = None,
    approve: bool = False,
) -> dict[str, Any]:
    if not approve:
        raise RuntimeError("candidate feature-set deletion requires approve=True")
    run_id = run_id or f"delete_obsolete_candidate_feature_sets_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = datetime.now(UTC)
    ensure_data_deletion_tables(conn)
    plan = plan_obsolete_candidate_feature_set_delete(conn)
    blockers = plan["production_blockers"]
    if blockers:
        raise RuntimeError(f"production references block candidate feature-set deletion: {blockers}")
    actions = build_candidate_delete_actions(
        conn,
        feature_set_ids={item["feature_set_id"] for item in plan["feature_sets"]},
        stale_model_ids=set(plan["stale_model_ids"]),
        stale_walkforward_run_ids=set(plan["stale_walkforward_run_ids"]),
    )
    executed: list[dict[str, Any]] = []
    verification = {
        "feature_sets": plan["feature_sets"],
        "stale_model_ids": plan["stale_model_ids"],
        "stale_walkforward_run_ids": plan["stale_walkforward_run_ids"],
        "production_blockers": blockers,
        "delete_policy": "verified_direct_delete_no_archive",
    }
    for action in actions:
        action_index = len(executed) + 1
        print(
            f"[candidate_gc] {utc_now_iso()} action {action_index}/{len(actions)} "
            f"{action.kind} {action.target}",
            flush=True,
        )
        if action.kind in {"rows", "truncate"}:
            deleted_rows = _replace_with_empty_table(conn, action) if action.kind == "truncate" else _delete_rows(conn, action)
            if deleted_rows:
                record_data_deletion(
                    conn,
                    deletion_run_id=run_id,
                    table_name=action.target,
                    delete_scope="candidate_feature_set_gc",
                    key_column=action.key_column,
                    key_value=action.key_value,
                    deleted_rows=deleted_rows,
                    reason=action.reason,
                    verification=verification,
                )
                conn.commit()
            executed.append({"target": action.target, "kind": action.kind, "deleted_rows": deleted_rows})
            print(
                f"[candidate_gc] {utc_now_iso()} action {action_index}/{len(actions)} "
                f"done deleted_rows={deleted_rows}",
                flush=True,
            )
            continue
        path = Path(action.target)
        bytes_deleted = path.stat().st_size if path.exists() else 0
        deleted_files = 0
        if path.exists():
            path.unlink()
            deleted_files = 1
            record_data_deletion(
                conn,
                deletion_run_id=run_id,
                table_name="filesystem:data/multidim_models",
                delete_scope="candidate_feature_set_gc",
                key_column=action.key_column,
                key_value=action.key_value,
                deleted_files=deleted_files,
                deleted_bytes=bytes_deleted,
                reason=action.reason,
                verification=verification,
            )
            conn.commit()
        executed.append(
            {
                "target": action.target,
                "kind": action.kind,
                "deleted_files": deleted_files,
                "deleted_bytes": bytes_deleted,
            }
        )
        print(
            f"[candidate_gc] {utc_now_iso()} action {action_index}/{len(actions)} "
            f"done deleted_files={deleted_files} deleted_bytes={bytes_deleted}",
            flush=True,
        )
    ended_at = utc_now_iso()
    duration_s = (datetime.now(UTC) - started).total_seconds()
    total_deleted_rows = sum(int(item.get("deleted_rows") or 0) for item in executed)
    total_deleted_files = sum(int(item.get("deleted_files") or 0) for item in executed)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="delete_obsolete_candidate_feature_sets",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO_ROOT),
        input_tables=[
            "fact_feature_panel_candidate",
            "mart_model_lifecycle",
            "mart_daily_recommendation",
            "mart_daily_topk_view_cache",
        ],
        output_tables=["mart_data_deletion_record"],
        perf_summary={
            "feature_set_count": plan["feature_set_count"],
            "stale_model_count": plan["stale_model_count"],
            "action_count": len(executed),
            "deleted_rows": total_deleted_rows,
            "deleted_files": total_deleted_files,
        },
    )
    conn.commit()
    return {
        "mode": "execute_approved",
        "run_id": run_id,
        "feature_set_count": plan["feature_set_count"],
        "stale_model_count": plan["stale_model_count"],
        "deleted_rows": total_deleted_rows,
        "deleted_files": total_deleted_files,
        "executed": executed,
    }
