"""Delete obsolete pipeline run manifests that violate current observability policy."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.data_deletion import ensure_data_deletion_tables, record_data_deletion
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.pipeline_performance_policy import load_pipeline_performance_policy
from services.pricing_policy import load_pricing_label_policy


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _has_stage_timing(perf: Any) -> bool:
    if not isinstance(perf, dict):
        return False
    return any(key in perf for key in {"steps", "stage_timings", "stage_timing", "timing", "timings", "profile", "step_durations"})


def _json_load(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None


def _champion_model_ids(conn: Any) -> set[str]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return set()
    rows = conn.execute(
        """
        SELECT model_id
          FROM mart_model_lifecycle
         WHERE lower(status) IN ('champion', 'production', 'deployed')
        """
    ).fetchall()
    return {str(row["model_id"]) for row in rows if row["model_id"]}


def plan_obsolete_pipeline_performance_run_delete(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return {"mode": "dry_run", "offenders": [], "blocked": [], "offender_count": 0}
    pricing_policy = load_pricing_label_policy()
    perf_policy = load_pipeline_performance_policy().to_dict()
    if not perf_policy.get("pipeline_duration_budgets_s"):
        perf_policy = pricing_policy.definition_sections.get("performance_policy") or {}
    progress_s = float(perf_policy.get("progress_heartbeat_required_after_s") or 30)
    default_budget = float(perf_policy.get("default_pipeline_duration_budget_s") or 600)
    budgets = perf_policy.get("pipeline_duration_budgets_s") or {}
    tracked = sorted(str(name) for name in budgets)
    if not tracked:
        return {"mode": "dry_run", "offenders": [], "blocked": [], "offender_count": 0}
    placeholders = ", ".join("?" for _ in tracked)
    rows = conn.execute(
        f"""
        SELECT run_id, pipeline_name, status, started_at, duration_s,
               model_id, gate_result, perf_summary_json
          FROM mart_pipeline_run_manifest
         WHERE duration_s IS NOT NULL
           AND pipeline_name IN ({placeholders})
         ORDER BY COALESCE(started_at, created_at) DESC
        """,
        tracked,
    ).fetchall()
    champions = _champion_model_ids(conn)
    offenders: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in rows:
        pipeline_name = str(row["pipeline_name"])
        duration = float(row["duration_s"] or 0.0)
        budget = float(budgets.get(pipeline_name) or default_budget)
        perf = _json_load(row["perf_summary_json"])
        reasons = []
        if duration >= progress_s and not _has_stage_timing(perf):
            reasons.append("slow_without_stage_timing")
        if duration > budget:
            reasons.append("over_duration_budget")
        if not reasons:
            continue
        item = {
            "run_id": row["run_id"],
            "pipeline_name": pipeline_name,
            "status": row["status"],
            "started_at": row["started_at"],
            "duration_s": duration,
            "budget_s": budget,
            "model_id": row["model_id"],
            "gate_result": row["gate_result"],
            "reasons": reasons,
        }
        if (
            row["model_id"] in champions
            and pipeline_name == "train_multidim_model"
            and str(row["status"]).lower() == "success"
        ):
            blocked.append({**item, "block_reason": "current champion training evidence must be rerun, not deleted"})
        else:
            offenders.append(item)
    return {
        "mode": "dry_run",
        "policy_id": pricing_policy.policy_id,
        "policy_hash": pricing_policy.policy_hash(),
        "performance_policy_id": perf_policy.get("policy_id"),
        "tracked_pipelines": tracked,
        "offender_count": len(offenders),
        "blocked_count": len(blocked),
        "offenders": offenders,
        "blocked": blocked,
    }


def execute_obsolete_pipeline_performance_run_delete(
    conn: Any,
    *,
    run_id: str | None = None,
    approve: bool = False,
) -> dict[str, Any]:
    if not approve:
        raise RuntimeError("pipeline performance manifest deletion requires approve=True")
    run_id = run_id or f"delete_obsolete_pipeline_runs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = datetime.now(UTC)
    ensure_data_deletion_tables(conn)
    plan = plan_obsolete_pipeline_performance_run_delete(conn)
    if plan["blocked"]:
        raise RuntimeError(f"protected pipeline manifests require rerun before delete: {plan['blocked']}")
    deleted = []
    for item in plan["offenders"]:
        print(
            f"[pipeline_gc] {utc_now_iso()} delete {item['pipeline_name']} {item['run_id']} "
            f"reasons={','.join(item['reasons'])}",
            flush=True,
        )
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM mart_pipeline_run_manifest WHERE run_id = ?",
            (item["run_id"],),
        ).fetchone()["n"]
        conn.execute("DELETE FROM mart_pipeline_run_manifest WHERE run_id = ?", (item["run_id"],))
        deleted_rows = int(before or 0)
        if deleted_rows:
            record_data_deletion(
                conn,
                deletion_run_id=run_id,
                table_name="mart_pipeline_run_manifest",
                delete_scope="pipeline_performance_gc",
                key_column="run_id",
                key_value=str(item["run_id"]),
                deleted_rows=deleted_rows,
                reason="obsolete pipeline manifest violates current performance/observability policy",
                verification=item,
            )
            conn.commit()
        deleted.append({**item, "deleted_rows": deleted_rows})
    ended_at = utc_now_iso()
    duration_s = (datetime.now(UTC) - started).total_seconds()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="delete_obsolete_pipeline_runs",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO_ROOT),
        input_tables=["mart_pipeline_run_manifest"],
        output_tables=["mart_pipeline_run_manifest", "mart_data_deletion_record"],
        perf_summary={
            "stage_timings": {"delete_pipeline_manifest_s": duration_s},
            "deleted_rows": sum(int(item["deleted_rows"] or 0) for item in deleted),
            "offender_count": plan["offender_count"],
        },
    )
    conn.commit()
    return {
        "mode": "execute_approved",
        "run_id": run_id,
        "deleted_rows": sum(int(item["deleted_rows"] or 0) for item in deleted),
        "deleted": deleted,
    }
