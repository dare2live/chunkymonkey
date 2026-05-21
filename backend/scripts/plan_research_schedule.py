#!/usr/bin/env python3
"""Build a config-driven research schedule without executing research jobs."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "model_search.yaml"
REPO = Path(__file__).resolve().parent.parent.parent

DEFAULT_RANKER_POLICY: dict[str, Any] = {
    "enabled": True,
    "large_trial_threshold": 0,
    "require_prior_profile": True,
    "max_runtime_ratio_vs_regression": 2.0,
    "passing_status": "pass",
    "gate_failure_tokens": ["drift", "drawdown", "walkforward", "stability", "psi"],
}

DDL = """
CREATE TABLE IF NOT EXISTS mart_research_schedule_plan (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    priority INTEGER,
    status TEXT NOT NULL,
    enabled BOOLEAN NOT NULL,
    evidence_table TEXT,
    evidence_key_column TEXT,
    evidence_run_id TEXT,
    evidence_found BOOLEAN NOT NULL,
    evidence_status TEXT,
    evidence_built_at TEXT,
    depends_on_json TEXT,
    command_json TEXT,
    command_text TEXT,
    resources_json TEXT,
    config_json TEXT,
    reason TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_research_schedule_plan_status
    ON mart_research_schedule_plan(run_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_research_schedule_plan_task
    ON mart_research_schedule_plan(task_type, status);
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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"model search config not found: {path}")
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load model_search.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def load_research_schedule_config(path: str | Path | None = None) -> dict[str, Any]:
    return _load_yaml(Path(path) if path is not None else CONFIG_PATH)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _iter_tasks(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = config.get("research_schedule", [])
    if isinstance(raw, dict):
        raw = raw.get("tasks", [])
    tasks: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if not item.get("task_id"):
            continue
        tasks.append(dict(item))
    return sorted(tasks, key=lambda task: (int(task.get("priority", 1000) or 1000), str(task["task_id"])))


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
    return row is not None


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = ?
        """,
        (table_name,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _command_parts(command: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    python = str(command.get("python") or defaults.get("python") or "python")
    backend_dir = str(command.get("backend_dir") or defaults.get("backend_dir") or "backend")
    script = str(command.get("script") or "").strip()
    if not script:
        return {"argv": [], "command_text": None}
    script_path = script if "/" in script else f"{backend_dir}/scripts/{script}"
    argv: list[str] = [python, script_path]
    args = command.get("args", {})

    def append_arg(flag: str, value: Any = True) -> None:
        if value is None or value is False:
            return
        argv.append(str(flag))
        if value is True:
            return
        if isinstance(value, (list, tuple)):
            argv.append(",".join(str(item) for item in value))
            return
        argv.append(str(value))

    if isinstance(args, dict):
        for flag, value in args.items():
            append_arg(str(flag), value)
    elif isinstance(args, list):
        for item in args:
            if isinstance(item, dict):
                for flag, value in item.items():
                    append_arg(str(flag), value)
            elif item is not None:
                argv.append(str(item))

    return {
        "argv": argv,
        "cwd": str(REPO),
        "command_text": " ".join(shlex.quote(part) for part in argv),
    }


def _evidence_built_at_column(columns: set[str], configured: str | None) -> str | None:
    if configured and configured in columns:
        return configured
    for candidate in ("built_at", "ended_at", "started_at", "created_at"):
        if candidate in columns:
            return candidate
    return None


def check_evidence(conn: Any, evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return {
            "found": False,
            "table": None,
            "key_column": None,
            "run_id": None,
            "status": None,
            "built_at": None,
            "reason": "no evidence configured",
        }
    table = str(evidence.get("table") or "")
    key_column = str(evidence.get("key_column") or "run_id")
    run_id = str(evidence.get("run_id") or "")
    if not table or not run_id:
        return {
            "found": False,
            "table": table or None,
            "key_column": key_column,
            "run_id": run_id or None,
            "status": None,
            "built_at": None,
            "reason": "evidence table or run_id missing",
        }
    if not _table_exists(conn, table):
        return {
            "found": False,
            "table": table,
            "key_column": key_column,
            "run_id": run_id,
            "status": None,
            "built_at": None,
            "reason": f"evidence table missing: {table}",
        }
    columns = _columns(conn, table)
    if key_column not in columns:
        return {
            "found": False,
            "table": table,
            "key_column": key_column,
            "run_id": run_id,
            "status": None,
            "built_at": None,
            "reason": f"evidence key column missing: {table}.{key_column}",
        }
    status_column = str(evidence.get("status_column") or "")
    built_at_column = _evidence_built_at_column(columns, evidence.get("built_at_column"))
    select_parts = [f"{_quote_ident(key_column)} AS evidence_run_id"]
    if status_column and status_column in columns:
        select_parts.append(f"{_quote_ident(status_column)} AS evidence_status")
    else:
        select_parts.append("NULL AS evidence_status")
    if built_at_column:
        select_parts.append(f"CAST({_quote_ident(built_at_column)} AS TEXT) AS evidence_built_at")
    else:
        select_parts.append("NULL AS evidence_built_at")
    row = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
          FROM {_quote_ident(table)}
         WHERE {_quote_ident(key_column)} = ?
         LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if not row:
        return {
            "found": False,
            "table": table,
            "key_column": key_column,
            "run_id": run_id,
            "status": None,
            "built_at": None,
            "reason": f"evidence row missing: {table}.{key_column}={run_id}",
        }
    accepted = {str(item) for item in _as_list(evidence.get("accepted_statuses")) if item is not None}
    evidence_status = row["evidence_status"]
    if accepted and str(evidence_status) not in accepted:
        return {
            "found": False,
            "table": table,
            "key_column": key_column,
            "run_id": run_id,
            "status": str(evidence_status) if evidence_status is not None else None,
            "built_at": row["evidence_built_at"],
            "reason": f"evidence status not accepted: {evidence_status}",
        }
    return {
        "found": True,
        "table": table,
        "key_column": key_column,
        "run_id": run_id,
        "status": str(evidence_status) if evidence_status is not None else None,
        "built_at": row["evidence_built_at"],
        "reason": f"evidence found: {table}.{key_column}={run_id}",
    }


def _command_args(task: dict[str, Any]) -> dict[str, Any]:
    command = task.get("command")
    if not isinstance(command, dict):
        return {}
    args = command.get("args")
    return args if isinstance(args, dict) else {}


def _task_arg(task: dict[str, Any], key: str, default: Any = None) -> Any:
    args = _command_args(task)
    return args.get(key, default)


def _task_model_family(task: dict[str, Any]) -> str:
    resources = task.get("resources") if isinstance(task.get("resources"), dict) else {}
    family = resources.get("model_family") or _task_arg(task, "--model-family") or task.get("model_family")
    return str(family or "").strip()


def _task_trials(task: dict[str, Any]) -> int:
    raw = _task_arg(task, "--trials", task.get("trials", 0))
    try:
        return int(raw)
    except Exception:
        return 0


def ranker_policy_from_config(config: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the configured ranker expansion policy with task override support."""
    policy = dict(DEFAULT_RANKER_POLICY)
    raw = config.get("ranker_policy") if isinstance(config.get("ranker_policy"), dict) else {}
    for key, value in raw.items():
        if key in policy:
            policy[key] = value
    task_raw = task.get("ranker_policy") if task and isinstance(task.get("ranker_policy"), dict) else {}
    for key, value in task_raw.items():
        if key in policy:
            policy[key] = value

    policy["enabled"] = bool(policy.get("enabled", True))
    policy["require_prior_profile"] = bool(policy.get("require_prior_profile", True))
    try:
        policy["large_trial_threshold"] = int(policy.get("large_trial_threshold") or 0)
    except Exception:
        policy["large_trial_threshold"] = 0
    try:
        policy["max_runtime_ratio_vs_regression"] = float(policy.get("max_runtime_ratio_vs_regression") or 2.0)
    except Exception:
        policy["max_runtime_ratio_vs_regression"] = 2.0
    policy["passing_status"] = str(policy.get("passing_status") or "pass")
    policy["gate_failure_tokens"] = [
        str(item).lower()
        for item in _as_list(policy.get("gate_failure_tokens"))
        if str(item).strip()
    ]
    return policy


def _recent_model_stability_profiles(conn: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_model_stability_search_summary"):
        return []
    summary_cols = _columns(conn, "mart_model_stability_search_summary")
    if "run_id" not in summary_cols:
        return []
    has_manifest = _table_exists(conn, "mart_pipeline_run_manifest")
    manifest_cols = _columns(conn, "mart_pipeline_run_manifest") if has_manifest else set()
    built_order = "s.built_at DESC" if "built_at" in summary_cols else "s.run_id DESC"
    select_parts = [
        "s.run_id",
        "s.trials" if "trials" in summary_cols else "NULL AS trials",
        "s.study_total_trials" if "study_total_trials" in summary_cols else "NULL AS study_total_trials",
        "s.config_json" if "config_json" in summary_cols else "NULL AS config_json",
    ]
    if has_manifest and "run_id" in manifest_cols:
        select_parts.extend(
            [
                "m.duration_s" if "duration_s" in manifest_cols else "NULL AS duration_s",
                "m.perf_summary_json" if "perf_summary_json" in manifest_cols else "NULL AS perf_summary_json",
            ]
        )
        join_sql = "LEFT JOIN mart_pipeline_run_manifest m ON m.run_id = s.run_id"
    else:
        select_parts.extend(["NULL AS duration_s", "NULL AS perf_summary_json"])
        join_sql = ""
    rows = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
          FROM mart_model_stability_search_summary s
          {join_sql}
         ORDER BY {built_order}
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    profiles: list[dict[str, Any]] = []
    for row in rows:
        config = {}
        try:
            config = json.loads(row["config_json"] or "{}")
        except Exception:
            config = {}
        model_family = config.get("model_family") if isinstance(config, dict) else None
        best_status = config.get("best_status") if isinstance(config, dict) else None
        best_rejection_reason = config.get("best_rejection_reason") if isinstance(config, dict) else None
        perf = {}
        try:
            perf = json.loads(row["perf_summary_json"] or "{}")
        except Exception:
            perf = {}
        profiles.append(
            {
                "run_id": row["run_id"],
                "model_family": str(model_family or ""),
                "best_status": str(best_status or ""),
                "best_rejection_reason": str(best_rejection_reason or ""),
                "trials": int(row["trials"] or row["study_total_trials"] or 0),
                "duration_s": float(row["duration_s"]) if row["duration_s"] is not None else None,
                "ranker_cache": perf.get("ranker_cache") if isinstance(perf, dict) else None,
            }
        )
    return profiles


def _duration_per_trial(profile: dict[str, Any]) -> float | None:
    duration = profile.get("duration_s")
    trials = int(profile.get("trials") or 0)
    if duration is None or trials <= 0:
        return None
    return float(duration) / float(trials)


def ranker_expansion_guard(conn: Any, task: dict[str, Any], policy: dict[str, Any] | None = None) -> str | None:
    """Return a defer reason when a large ranker study lacks evidence value."""
    policy = dict(DEFAULT_RANKER_POLICY if policy is None else policy)
    if not bool(policy.get("enabled", True)):
        return None
    model_family = _task_model_family(task)
    if model_family != "lightgbm_ranker":
        return None
    trials = _task_trials(task)
    large_trial_threshold = int(policy.get("large_trial_threshold") or 0)
    if trials <= large_trial_threshold:
        return None

    profiles = _recent_model_stability_profiles(conn)
    ranker_profiles = [item for item in profiles if item["model_family"] == "lightgbm_ranker"]
    if not ranker_profiles and bool(policy.get("require_prior_profile", True)):
        return "ranker expansion deferred: missing prior ranker performance profile"
    if not ranker_profiles:
        return None

    latest_ranker = ranker_profiles[0]
    latest_ranker_perf = _duration_per_trial(latest_ranker)
    regression_profiles = [item for item in profiles if item["model_family"] == "lightgbm"]
    regression_perf = next((_duration_per_trial(item) for item in regression_profiles if _duration_per_trial(item)), None)
    passing_status = str(policy.get("passing_status") or "pass")
    if latest_ranker_perf and regression_perf:
        ratio = latest_ranker_perf / regression_perf
        max_ratio = float(policy.get("max_runtime_ratio_vs_regression") or 2.0)
        if ratio > max_ratio and latest_ranker["best_status"] != passing_status:
            return (
                "ranker expansion deferred: runtime per trial is "
                f"{ratio:.2f}x regression (budget {max_ratio:.2f}x) and latest ranker gate status is "
                f"{latest_ranker['best_status'] or 'unknown'}"
            )

    rejection = latest_ranker["best_rejection_reason"].lower()
    gate_tokens = [
        str(item).lower()
        for item in _as_list(policy.get("gate_failure_tokens"))
        if str(item).strip()
    ]
    repeated_gate_failure = any(token in rejection for token in gate_tokens)
    if latest_ranker["best_status"] != passing_status and repeated_gate_failure:
        return "ranker expansion deferred: latest ranker failed deployability gates: " + latest_ranker[
            "best_rejection_reason"
        ]
    return None


def build_research_schedule(config: dict[str, Any], conn: Any) -> list[dict[str, Any]]:
    defaults = config.get("defaults", {}) if isinstance(config.get("defaults"), dict) else {}
    rows: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    for task in _iter_tasks(config):
        task_id = str(task["task_id"])
        enabled = bool(task.get("enabled", True))
        dependencies = [str(item) for item in _as_list(task.get("depends_on"))]
        evidence = check_evidence(conn, task.get("evidence") if isinstance(task.get("evidence"), dict) else None)
        command = _command_parts(task.get("command", {}) if isinstance(task.get("command"), dict) else {}, defaults)

        if evidence["found"]:
            status = "completed"
            reason = evidence["reason"]
        elif not enabled:
            status = "disabled"
            reason = "task disabled in config"
        elif bool(task.get("deferred", False)):
            status = "deferred"
            reason = str(task.get("defer_reason") or "task deferred by config")
        else:
            blocked = [dep for dep in dependencies if statuses.get(dep) != "completed"]
            if blocked:
                status = "blocked"
                reason = "waiting for dependencies: " + ",".join(blocked)
            else:
                ranker_defer_reason = ranker_expansion_guard(conn, task, ranker_policy_from_config(config, task))
                if ranker_defer_reason:
                    status = "deferred"
                    reason = ranker_defer_reason
                else:
                    status = "planned"
                    reason = evidence["reason"]

        row = {
            "task_id": task_id,
            "task_type": str(task.get("task_type") or "research"),
            "priority": int(task.get("priority", 1000) or 1000),
            "status": status,
            "enabled": enabled,
            "evidence": evidence,
            "depends_on": dependencies,
            "command": command,
            "resources": task.get("resources", {}) if isinstance(task.get("resources"), dict) else {},
            "config": task,
            "reason": reason,
        }
        rows.append(row)
        statuses[task_id] = status
    return rows


def persist_research_schedule(conn: Any, *, run_id: str, rows: list[dict[str, Any]], built_at: str) -> None:
    ensure_tables(conn)
    conn.execute("DELETE FROM mart_research_schedule_plan WHERE run_id = ?", (run_id,))
    schedule_rows = [
        (
            run_id,
            row["task_id"],
            row["task_type"],
            row["priority"],
            row["status"],
            row["enabled"],
            row["evidence"]["table"],
            row["evidence"]["key_column"],
            row["evidence"]["run_id"],
            bool(row["evidence"]["found"]),
            row["evidence"]["status"],
            row["evidence"]["built_at"],
            _json(row["depends_on"]),
            _json(row["command"]),
            row["command"].get("command_text"),
            _json(row["resources"]),
            _json(row["config"]),
            row["reason"],
            built_at,
        )
        for row in rows
    ]
    if schedule_rows:
        conn.executemany(
            """
            INSERT INTO mart_research_schedule_plan
            (run_id, task_id, task_type, priority, status, enabled,
             evidence_table, evidence_key_column, evidence_run_id, evidence_found,
             evidence_status, evidence_built_at, depends_on_json, command_json,
             command_text, resources_json, config_json, reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            schedule_rows,
        )
    record_actual_version(conn, "mart_research_schedule_plan")
    conn.commit()


def plan_research_schedule(
    conn: Any,
    *,
    config_path: str | Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    run_id = run_id or f"research_schedule_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    config = load_research_schedule_config(config_path)
    rows = build_research_schedule(config, conn)
    built_at = utc_now_iso()
    persist_research_schedule(conn, run_id=run_id, rows=rows, built_at=built_at)
    ended_at = utc_now_iso()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    ranker_policy_deferred = sum(
        1
        for row in rows
        if row["status"] == "deferred" and str(row.get("reason") or "").startswith("ranker expansion deferred")
    )
    evidence_tables = sorted(
        {
            row["evidence"]["table"]
            for row in rows
            if row["evidence"].get("table")
        }
    )
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="plan_research_schedule",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        commit_sha=git_commit_sha(REPO),
        input_tables=evidence_tables,
        output_tables=["mart_research_schedule_plan"],
        perf_summary={
            "status_counts": counts,
            "task_count": len(rows),
            "ranker_policy_deferred": ranker_policy_deferred,
            "ranker_policy": ranker_policy_from_config(config),
            "config_path": str(Path(config_path) if config_path else CONFIG_PATH),
        },
    )
    return {
        "run_id": run_id,
        "status": "success",
        "task_count": len(rows),
        "status_counts": counts,
        "ranker_policy_deferred": ranker_policy_deferred,
        "tasks": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--db-path", default=None, help="Open an explicit DuckDB path instead of production smartmoney.duckdb")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    if args.db_path:
        conn = duck_connect(str(Path(args.db_path)))
    else:
        conn = get_conn()
    try:
        result = plan_research_schedule(conn, config_path=args.config, run_id=args.run_id)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
