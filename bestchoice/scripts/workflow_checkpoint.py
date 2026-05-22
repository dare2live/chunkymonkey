from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ANALYSIS_DIR = ROOT / "analysis"
DEFAULT_JSON = ANALYSIS_DIR / "workflow_checkpoint.json"
DEFAULT_MD = ANALYSIS_DIR / "workflow_checkpoint.md"
SESSION_HANDOFF = ROOT / "SESSION_HANDOFF.md"
SNAPSHOT_ROOT = ANALYSIS_DIR / "recovery_snapshot"
LATEST_SNAPSHOT = SNAPSHOT_ROOT / "latest"
SNAPSHOT_LOCK = SNAPSHOT_ROOT / ".latest.lock"
BATCH_ADOPTION = ANALYSIS_DIR / "formula_local_optuna_batch_adoption.csv"
BATCH_MERGE_PLAN = ANALYSIS_DIR / "formula_local_optuna_batch_merge_plan.csv"
BATCH_REPLACEMENTS = ANALYSIS_DIR / "formula_local_optuna_batch_stock_best_replacements.csv"
AGGREGATE_AUDIT = ANALYSIS_DIR / "formula_local_optuna_aggregate_audit.json"
OPERATIONAL_AUDIT = ANALYSIS_DIR / "operational_delivery_readiness.json"
RESEARCH_CACHE = ANALYSIS_DIR / "research_cache.duckdb"
INCREMENTAL_EVAL = ANALYSIS_DIR / "incremental_eval.duckdb"
DRIFT_TRIGGER = ANALYSIS_DIR / "drift_trigger.duckdb"
COMPLEXITY_SKILL = Path.home() / ".codex" / "skills" / "complexity-optimizer" / "SKILL.md"
CODEGRAPH_DB = ROOT / ".codegraph" / "codegraph.db"
ACTIVE_WORKER_PATTERNS = {
    "formula_batch": "scripts/formula_local_optuna_batch.py",
    "research_cache_build": "scripts/research_cache_build.py",
    "incremental_eval_build": "scripts/incremental_eval_build.py",
    "drift_trigger_build": "scripts/drift_trigger_build.py",
}
SNAPSHOT_CONTEXT_FILES = [
    ROOT / "goal.md",
    ROOT / "agent.md",
    SESSION_HANDOFF,
    ANALYSIS_DIR / "top_level_architecture_plan.md",
    ANALYSIS_DIR / "complexity_codegraph_audit.md",
]
SNAPSHOT_MANIFEST_FILES = [
    BATCH_ADOPTION,
    BATCH_MERGE_PLAN,
    BATCH_REPLACEMENTS,
    RESEARCH_CACHE,
    INCREMENTAL_EVAL,
    DRIFT_TRIGGER,
    ANALYSIS_DIR / "stock_formula_best.csv",
    ANALYSIS_DIR / "strategy_research_audit.md",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _safe_git_status() -> list[str]:
    try:
        out = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    except Exception:
        return []
    return [line for line in out.splitlines() if line.strip()]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _file_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": _rel(path), "exists": False}
    stat = path.stat()
    return {
        "path": _rel(path),
        "exists": True,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _duckdb_summary(path: Path, table: str) -> dict[str, Any]:
    if not path.exists():
        return {"ready": False, "path": str(path.relative_to(ROOT)), "row_count": 0}
    try:
        import duckdb

        with duckdb.connect(str(path), read_only=True) as con:
            row_count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            manifest = {}
            try:
                manifest = {str(k): str(v) for k, v in con.execute("SELECT key, value FROM cache_manifest").fetchall()}
            except Exception:
                pass
    except Exception as exc:
        return {
            "ready": False,
            "path": str(path.relative_to(ROOT)),
            "row_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ready": True,
        "path": str(path.relative_to(ROOT)),
        "row_count": int(row_count or 0),
        "manifest": manifest,
    }


def _formula_cache_status() -> dict[str, Any]:
    try:
        from compute import _cache_fresh, _safe_cache_path, get_strategy_profiles

        profiles = get_strategy_profiles()
        rows = []
        for pid, profile in profiles.items():
            if profile.get("signal_source") != "formula":
                continue
            path = _safe_cache_path(pid)
            fresh = bool(_cache_fresh(pid, profile))
            rows.append(
                {
                    "profile_id": pid,
                    "name": profile.get("name"),
                    "fresh": fresh,
                    "path": _rel(path),
                    "exists": path.exists(),
                    "size": path.stat().st_size if path.exists() else 0,
                }
            )
        missing = [r["profile_id"] for r in rows if not r["fresh"]]
        return {
            "ready": bool(rows) and not missing,
            "ready_count": sum(1 for r in rows if r["fresh"]),
            "total_count": len(rows),
            "missing": missing,
            "profiles": rows,
        }
    except Exception as exc:
        return {
            "ready": False,
            "ready_count": 0,
            "total_count": 0,
            "missing": [],
            "profiles": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _formula_cache_status_blocked_by_lock(market_db_locks: dict[str, Any]) -> dict[str, Any]:
    return {
        "ready": False,
        "ready_count": None,
        "total_count": None,
        "missing": [],
        "profiles": [],
        "status": "unknown_due_to_market_db_lock",
        "error": "market.duckdb is locked by another process; formula cache freshness was not checked",
        "lock_holders": market_db_locks.get("holders", []),
    }


def _market_total_count() -> int | None:
    try:
        from scripts.formula_parameter_search import _load_market_rows

        return len(_load_market_rows(0))
    except Exception:
        return None


def _aggregate_audit_ready() -> bool:
    try:
        data = json.loads(AGGREGATE_AUDIT.read_text(encoding="utf-8"))
        return bool(data.get("passed"))
    except Exception:
        return False


def _operational_audit_ready() -> bool:
    try:
        data = json.loads(OPERATIONAL_AUDIT.read_text(encoding="utf-8"))
        return bool(data.get("operational_ready"))
    except Exception:
        return False


def _market_db_locks() -> dict[str, Any]:
    try:
        from settings import MARKET_DB
    except Exception as exc:
        return {"ready": False, "path": "", "holders": [], "error": f"{type(exc).__name__}: {exc}"}

    path = Path(MARKET_DB)
    if not path.exists():
        return {"ready": True, "path": str(path), "holders": []}

    try:
        out = subprocess.run(
            ["lsof", str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return {"ready": True, "path": str(path), "holders": [], "error": "lsof not found"}
    except Exception as exc:
        return {"ready": False, "path": str(path), "holders": [], "error": f"{type(exc).__name__}: {exc}"}

    holders: list[dict[str, Any]] = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        pid = parts[1]
        holder: dict[str, Any] = {
            "command": parts[0],
            "pid": pid,
            "user": parts[2] if len(parts) > 2 else "",
        }
        try:
            ps = subprocess.check_output(
                ["ps", "-p", pid, "-o", "pid=,ppid=,etime=,%cpu=,%mem=,command="],
                text=True,
            ).strip()
            holder["process"] = ps
        except Exception:
            pass
        holders.append(holder)
    return {
        "ready": not holders,
        "path": str(path),
        "holders": holders,
        "error": out.stderr.strip() if out.returncode not in (0, 1) else "",
    }


def _active_worker_processes() -> list[dict[str, Any]]:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,etime=,%cpu=,%mem=,command="],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception:
        return []
    workers: list[dict[str, Any]] = []
    current_pid = str(os.getpid())
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        pid, elapsed, cpu, mem, command = parts
        if pid == current_pid:
            continue
        for kind, needle in ACTIVE_WORKER_PATTERNS.items():
            if needle in command:
                workers.append(
                    {
                        "kind": kind,
                        "pid": pid,
                        "elapsed": elapsed,
                        "cpu": cpu,
                        "mem": mem,
                        "command": command,
                    }
                )
                break
    return workers


def _json_manifest_value(summary: dict[str, Any], key: str) -> dict[str, int]:
    raw = (summary.get("manifest") or {}).get(key)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    out: dict[str, int] = {}
    if isinstance(parsed, dict):
        for k, v in parsed.items():
            try:
                out[str(k)] = int(v)
            except Exception:
                pass
    return out


def _state_consistency(
    batch: dict[str, int],
    stores: dict[str, dict[str, Any]],
    formula_caches: dict[str, Any],
) -> dict[str, Any]:
    row_count = int(batch.get("row_count") or 0)
    merge_plan_rows = int(batch.get("merge_plan_rows") or 0)
    replacement_count = int(batch.get("replacement_count") or 0)
    candidate_count = int(batch.get("candidate_count") or 0)
    formula_cache_check_name = (
        "formula_caches_unknown_due_to_market_db_lock"
        if formula_caches.get("status") == "unknown_due_to_market_db_lock"
        else "formula_caches_ready"
    )
    checks = {
        "adoption_rows_positive": row_count > 0,
        "merge_plan_matches_adoption": merge_plan_rows == row_count,
        "replacement_count_matches_candidates": replacement_count == candidate_count,
        "missing_reasons_complete": int(batch.get("missing_without_reason") or 0) == 0,
        "research_cache_ready": bool(stores["research_cache"].get("ready")),
        "incremental_eval_ready": bool(stores["incremental_eval"].get("ready")),
        "drift_trigger_ready": bool(stores["drift_trigger"].get("ready")),
        formula_cache_check_name: bool(formula_caches.get("ready")),
    }
    source_rows = _json_manifest_value(stores["research_cache"], "source_rows")
    if source_rows:
        checks["research_cache_adoption_matches"] = source_rows.get("adoption") == row_count
        checks["research_cache_merge_plan_matches"] = source_rows.get("merge_plan") == merge_plan_rows
    else:
        checks["research_cache_adoption_matches"] = False
        checks["research_cache_merge_plan_matches"] = False
    ready = all(checks.values())
    warnings = [name for name, ok in checks.items() if not ok]
    return {"ready": ready, "checks": checks, "warnings": warnings}


def _resume_commands(next_offset: int) -> list[str]:
    return [
        f"python scripts/formula_local_optuna_batch.py --offset {next_offset} --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume",
        "python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md",
        "python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md",
        "python scripts/research_cache_build.py",
        "python scripts/incremental_eval_build.py",
        "python scripts/drift_trigger_build.py",
        "python scripts/workflow_checkpoint.py",
    ]


def _next_action(
    batch: dict[str, int],
    stores: dict[str, dict[str, Any]],
    formula_caches: dict[str, Any],
    market_db_locks: dict[str, Any],
    active_workers: list[dict[str, Any]],
    resume_commands: list[str],
) -> dict[str, Any]:
    row_count = int(batch.get("row_count") or 0)
    merge_plan_rows = int(batch.get("merge_plan_rows") or 0)
    candidate_count = int(batch.get("candidate_count") or 0)
    replacement_count = int(batch.get("replacement_count") or 0)
    source_rows = _json_manifest_value(stores["research_cache"], "source_rows")
    if active_workers:
        return {
            "kind": "wait_active_worker",
            "commands": [
                "ps -axo pid,etime,pcpu,pmem,command | grep -E 'formula_local_optuna_batch|research_cache_build|incremental_eval_build|drift_trigger_build' | grep -v grep",
                "python scripts/workflow_checkpoint.py --brief",
            ],
            "reason": "a BestChoice worker is already running; wait for it before launching resume commands",
            "workers": active_workers,
        }
    if row_count <= 0:
        return {"kind": "run_batch", "command": resume_commands[0], "reason": "no batch adoption rows found"}
    if market_db_locks.get("holders"):
        return {
            "kind": "wait_external_duckdb_lock",
            "commands": [
                "lsof /Users/dp/Documents/M/stock/chunkymonkey/data/market.duckdb",
                "python scripts/workflow_checkpoint.py --brief",
            ],
            "reason": "market.duckdb is locked by another process; wait for it to finish before rebuilding caches or running batches",
            "holders": market_db_locks.get("holders", []),
        }
    if not formula_caches.get("ready"):
        return {
            "kind": "rebuild_formula_caches",
            "commands": [
                "python scripts/compute_formula_caches.py",
                "python scripts/strategy_rebuild_audit.py",
                "python scripts/workflow_checkpoint.py",
            ],
            "reason": "formula strategy caches are missing or stale",
        }
    if merge_plan_rows != row_count or replacement_count != candidate_count:
        return {
            "kind": "rebuild_adoption_and_merge_plan",
            "commands": resume_commands[1:3],
            "reason": "adoption/merge artifacts are incomplete or inconsistent",
        }
    if source_rows.get("adoption") != row_count or source_rows.get("merge_plan") != merge_plan_rows:
        return {
            "kind": "rebuild_state_stores",
            "commands": resume_commands[3:],
            "reason": "state stores are behind current batch artifacts",
        }
    market_total = batch.get("market_total")
    if market_total and int(batch.get("covered_stocks") or 0) >= int(market_total):
        if not _aggregate_audit_ready():
            return {
                "kind": "run_aggregate_audit",
                "commands": [
                    "python scripts/formula_local_optuna_aggregate_audit.py",
                    "python scripts/workflow_checkpoint.py",
                ],
                "reason": "full coverage is complete; run aggregate audit before any production merge decision",
            }
        if _operational_audit_ready():
            return {
                "kind": "operational_ready",
                "commands": [
                    "cat analysis/operational_delivery_readiness.md",
                ],
                "reason": "full coverage, aggregate audit, and final operational gates passed",
            }
        return {
            "kind": "full_coverage_audited",
            "commands": [
                "python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py",
                "python scripts/execution_model_smoke.py",
                "python scripts/unified_data_smoke.py",
                "python scripts/strategy_rebuild_audit.py",
                "python scripts/operational_delivery_audit.py",
                "git diff --check",
            ],
            "reason": "full coverage and aggregate audit passed; run final operational verification gates",
        }
    return {
        "kind": "run_next_batch",
        "command": resume_commands[0],
        "reason": "checkpoint is consistent; continue from next offset",
    }


def build_checkpoint() -> dict[str, Any]:
    rows = _read_csv(BATCH_ADOPTION)
    merge_rows = _read_csv(BATCH_MERGE_PLAN)
    replacement_rows = _read_csv(BATCH_REPLACEMENTS)
    stock_codes = sorted({r.get("stock_code", "") for r in rows if r.get("stock_code")})
    formula_ids = sorted({r.get("formula_id", "") for r in rows if r.get("formula_id")})
    covered_stocks = len(stock_codes)
    row_count = len(rows)
    candidate_count = sum(1 for r in rows if r.get("adoption_decision") == "candidate")
    rejected_count = row_count - candidate_count
    next_offset = covered_stocks
    completed_batches = covered_stocks // 20
    market_total = _market_total_count()
    missing_without_reason = sum(
        1
        for r in rows
        if (
            (r.get("baseline_status") and r.get("baseline_status") != "ok" and not r.get("baseline_investigation"))
            or (r.get("optuna_status") and r.get("optuna_status") != "ok" and not r.get("optuna_investigation"))
        )
    )
    stores = {
        "research_cache": _duckdb_summary(RESEARCH_CACHE, "research_cache"),
        "incremental_eval": _duckdb_summary(INCREMENTAL_EVAL, "incremental_eval_state"),
        "drift_trigger": _duckdb_summary(DRIFT_TRIGGER, "drift_trigger"),
    }
    market_db_locks = _market_db_locks()
    active_workers = _active_worker_processes()
    formula_caches = (
        _formula_cache_status_blocked_by_lock(market_db_locks)
        if market_db_locks.get("holders")
        else _formula_cache_status()
    )
    batch = {
        "covered_stocks": covered_stocks,
        "market_total": market_total,
        "completed_batches": completed_batches,
        "next_offset": next_offset,
        "row_count": row_count,
        "formula_count": len(formula_ids),
        "candidate_count": candidate_count,
        "rejected_count": rejected_count,
        "merge_plan_rows": len(merge_rows),
        "replacement_count": len(replacement_rows),
        "missing_without_reason": missing_without_reason,
    }
    resume_commands = _resume_commands(next_offset)
    consistency = _state_consistency(batch, stores, formula_caches)
    next_action = _next_action(batch, stores, formula_caches, market_db_locks, active_workers, resume_commands)
    assistant_resume_prompt = (
        "请从中断处继续。先运行 `python scripts/workflow_checkpoint.py --brief`，"
        "检查 consistency.ready 和 next_action，然后按照 next_action/命令继续；"
        "遵守 goal.md 和 agent.md，不写入生产 stock_formula_best.csv。"
    )
    checkpoint = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "objective": "请你按照goal.md持续推进，按照 agent.md的规则",
        "current_phase": "full_market_local_optuna_dry_run",
        "cwd": str(ROOT),
        "batch": batch,
        "state_stores": stores,
        "formula_caches": formula_caches,
        "market_db_locks": market_db_locks,
        "active_workers": active_workers,
        "consistency": consistency,
        "next_action": next_action,
        "assistant_resume_prompt": assistant_resume_prompt,
        "tooling": {
            "codegraph_db": {
                "ready": CODEGRAPH_DB.exists(),
                "path": str(CODEGRAPH_DB.relative_to(ROOT)),
                "size": CODEGRAPH_DB.stat().st_size if CODEGRAPH_DB.exists() else 0,
            },
            "complexity_optimizer_skill": {
                "ready": COMPLEXITY_SKILL.exists(),
                "path": str(COMPLEXITY_SKILL),
            },
        },
        "resume_commands": resume_commands,
        "verification_commands": [
            "python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py",
            "python scripts/execution_model_smoke.py",
            "python scripts/unified_data_smoke.py",
            "python scripts/strategy_rebuild_audit.py",
            "git diff --check",
        ],
        "git_status_short": _safe_git_status(),
        "terminal_recovery": {
            "human_command": "bash scripts/bc_resume.sh",
            "brief_command": "python scripts/workflow_checkpoint.py --brief",
            "machine_command": "python scripts/workflow_checkpoint.py --print",
            "next_command": "python scripts/workflow_checkpoint.py --next",
            "session_handoff": str(SESSION_HANDOFF.relative_to(ROOT)),
            "snapshot_dir": str(LATEST_SNAPSHOT.relative_to(ROOT)),
            "codex_message": assistant_resume_prompt,
        },
        "recovery_rule": "After terminal crash or reboot, run `bash scripts/bc_resume.sh`; if next_action is wait_active_worker, do not start another batch. If consistency.ready is true and no worker is active, continue from next_action.command; otherwise run next_action.commands in order.",
    }
    return checkpoint


def _next_action_commands(next_action: dict[str, Any]) -> list[str]:
    if next_action.get("command"):
        return [str(next_action["command"])]
    commands = next_action.get("commands")
    if isinstance(commands, list):
        return [str(c) for c in commands]
    return []


def _format_brief(checkpoint: dict[str, Any]) -> str:
    batch = checkpoint["batch"]
    consistency = checkpoint["consistency"]
    next_action = checkpoint["next_action"]
    commands = _next_action_commands(next_action)
    lines = [
        "Workflow checkpoint",
        f"cwd: {checkpoint['cwd']}",
        f"phase: {checkpoint['current_phase']}",
        f"progress: batch {batch['completed_batches']} complete, covered {batch['covered_stocks']} stocks, next_offset {batch['next_offset']}",
        f"artifacts: rows {batch['row_count']}, candidates {batch['candidate_count']}, replacements {batch['replacement_count']}, missing_without_reason {batch['missing_without_reason']}",
        f"consistency.ready: {consistency['ready']}",
        _format_formula_cache_brief(checkpoint.get("formula_caches", {})),
        f"market_db_lock_holders: {len((checkpoint.get('market_db_locks') or {}).get('holders') or [])}",
        f"active_workers: {len(checkpoint.get('active_workers') or [])}",
        f"snapshot: {checkpoint['terminal_recovery']['snapshot_dir']}",
    ]
    for holder in (checkpoint.get("market_db_locks") or {}).get("holders", [])[:3]:
        lines.append(f"market_db_lock: pid={holder.get('pid')} process={holder.get('process') or holder.get('command')}")
    if consistency.get("warnings"):
        lines.append("consistency.warnings: " + ", ".join(consistency["warnings"]))
    for worker in (checkpoint.get("active_workers") or [])[:5]:
        lines.append(
            "active_worker: "
            f"kind={worker.get('kind')} pid={worker.get('pid')} "
            f"elapsed={worker.get('elapsed')} cpu={worker.get('cpu')} command={worker.get('command')}"
        )
    lines.extend(
        [
            f"next_action: {next_action.get('kind')} ({next_action.get('reason')})",
            "next command(s):",
        ]
    )
    lines.extend(f"  {cmd}" for cmd in commands)
    lines.extend(
        [
            "",
            "Tell Codex after a crash:",
            checkpoint["assistant_resume_prompt"],
        ]
    )
    return "\n".join(lines)


def _format_formula_cache_brief(formula_caches: dict[str, Any]) -> str:
    if formula_caches.get("status") == "unknown_due_to_market_db_lock":
        return "formula_caches: unknown (market.duckdb locked; freshness check skipped)"
    return f"formula_caches: {formula_caches.get('ready_count')}/{formula_caches.get('total_count')} ready"


def _format_formula_cache_count(formula_caches: dict[str, Any]) -> str:
    if formula_caches.get("status") == "unknown_due_to_market_db_lock":
        return "unknown_due_to_market_db_lock"
    return f"{formula_caches.get('ready_count')}/{formula_caches.get('total_count')}"


def _write_snapshot(snapshot_dir: Path, checkpoint: dict[str, Any], checkpoint_json: Path, checkpoint_md: Path) -> None:
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire_snapshot_lock(SNAPSHOT_LOCK)
    tmp_dir = SNAPSHOT_ROOT / f".latest.tmp.{os.getpid()}"
    try:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        (tmp_dir / "checkpoint.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if checkpoint_md.exists():
            shutil.copy2(checkpoint_md, tmp_dir / "checkpoint.md")
        if checkpoint_json.exists():
            shutil.copy2(checkpoint_json, tmp_dir / "workflow_checkpoint.json")

        for src in SNAPSHOT_CONTEXT_FILES:
            if src.exists():
                shutil.copy2(src, tmp_dir / src.name)

        manifest = {
            "generated_at": checkpoint["generated_at"],
            "note": "Latest-only recovery snapshot. Large artifacts are referenced by manifest, not copied.",
            "files": [_file_manifest(path) for path in SNAPSHOT_CONTEXT_FILES + SNAPSHOT_MANIFEST_FILES],
        }
        (tmp_dir / "artifact_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        commands = _next_action_commands(checkpoint["next_action"])
        (tmp_dir / "resume.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"cd {shlex_quote(str(ROOT))}\n"
            + "\n".join(commands)
            + "\n",
            encoding="utf-8",
        )
        (tmp_dir / "verify.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"cd {shlex_quote(str(ROOT))}\n"
            + "\n".join(checkpoint["verification_commands"])
            + "\n",
            encoding="utf-8",
        )
        (tmp_dir / "RECOVER.md").write_text(
            "\n".join(
                [
                    "# Latest Recovery Snapshot",
                    "",
                    "This directory is deleted and recreated on every checkpoint run. Only the latest snapshot is kept.",
                    "",
                    "## What To Tell Codex",
                    "",
                    checkpoint["assistant_resume_prompt"],
                    "",
                    "## Human Recovery",
                    "",
                    "```bash",
                    "python scripts/workflow_checkpoint.py --brief",
                    "bash analysis/recovery_snapshot/latest/resume.sh",
                    "```",
                    "",
                    "## Next Command",
                    "",
                    "```bash",
                    *commands,
                    "```",
                    "",
                    "## Verification",
                    "",
                    "```bash",
                    "bash analysis/recovery_snapshot/latest/verify.sh",
                    "```",
                    "",
                    "## Space Policy",
                    "",
                    "- Only `analysis/recovery_snapshot/latest/` is kept.",
                    "- Old snapshots are removed before writing the new one.",
                    "- Large CSV and DuckDB artifacts are not copied; `artifact_manifest.json` records their size and timestamp.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        tmp_dir.rename(snapshot_dir)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        os.close(lock_fd)
        try:
            SNAPSHOT_LOCK.unlink()
        except FileNotFoundError:
            pass


def _acquire_snapshot_lock(lock_path: Path, timeout_sec: float = 10.0, stale_sec: float = 120.0) -> int:
    deadline = time.time() + timeout_sec
    while True:
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale_sec:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                raise TimeoutError(f"Timed out waiting for snapshot lock: {lock_path}")
            time.sleep(0.05)


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _write_markdown(path: Path, checkpoint: dict[str, Any]) -> None:
    batch = checkpoint["batch"]
    stores = checkpoint["state_stores"]
    tooling = checkpoint["tooling"]
    market_db_locks = checkpoint.get("market_db_locks") or {}
    active_workers = checkpoint.get("active_workers") or []
    lines = [
        "# Workflow Checkpoint",
        "",
        f"- generated_at: `{checkpoint['generated_at']}`",
        f"- phase: `{checkpoint['current_phase']}`",
        f"- cwd: `{checkpoint['cwd']}`",
        "",
        "## Progress",
        "",
        f"- covered_stocks: `{batch['covered_stocks']}`",
        f"- completed_batches: `{batch['completed_batches']}`",
        f"- next_offset: `{batch['next_offset']}`",
        f"- batch_rows: `{batch['row_count']}`",
        f"- candidates: `{batch['candidate_count']}`",
        f"- rejected: `{batch['rejected_count']}`",
        f"- replacements: `{batch['replacement_count']}`",
        f"- missing_without_reason: `{batch['missing_without_reason']}`",
        f"- consistency_ready: `{checkpoint['consistency']['ready']}`",
        f"- formula_caches_status: `{checkpoint['formula_caches'].get('status') or 'checked'}`",
        f"- formula_caches_ready: `{checkpoint['formula_caches'].get('ready')}`",
        f"- formula_caches_ready_count: `{_format_formula_cache_count(checkpoint['formula_caches'])}`",
        f"- market_db_lock_holders: `{len(market_db_locks.get('holders') or [])}`",
        f"- active_workers: `{len(active_workers)}`",
        "",
        "## Next Action",
        "",
        f"- kind: `{checkpoint['next_action'].get('kind')}`",
        f"- reason: `{checkpoint['next_action'].get('reason')}`",
        "",
        "```bash",
        *_next_action_commands(checkpoint["next_action"]),
        "```",
        "",
        "## State Stores",
        "",
        f"- research_cache: ready=`{stores['research_cache'].get('ready')}` rows=`{stores['research_cache'].get('row_count')}`",
        f"- incremental_eval: ready=`{stores['incremental_eval'].get('ready')}` rows=`{stores['incremental_eval'].get('row_count')}`",
        f"- drift_trigger: ready=`{stores['drift_trigger'].get('ready')}` rows=`{stores['drift_trigger'].get('row_count')}`",
        "",
        "## Tooling",
        "",
        f"- codegraph_db: ready=`{tooling['codegraph_db'].get('ready')}` path=`{tooling['codegraph_db'].get('path')}`",
        f"- complexity_optimizer_skill: ready=`{tooling['complexity_optimizer_skill'].get('ready')}` path=`{tooling['complexity_optimizer_skill'].get('path')}`",
        f"- latest_snapshot: `{checkpoint['terminal_recovery']['snapshot_dir']}`",
        "",
        "## Resume Commands",
        "",
        "```bash",
        *checkpoint["resume_commands"],
        "```",
        "",
        "## Verification Commands",
        "",
        "```bash",
        *checkpoint["verification_commands"],
        "```",
        "",
        "## Market DB Locks",
        "",
        f"- path: `{market_db_locks.get('path', '')}`",
        f"- holder_count: `{len(market_db_locks.get('holders') or [])}`",
        "",
        "```text",
        *[
            str(holder.get("process") or f"{holder.get('command')} {holder.get('pid')}")
            for holder in (market_db_locks.get("holders") or [])
        ],
        "```",
        "",
        "## Active BestChoice Workers",
        "",
        "```text",
        *[
            f"{worker.get('kind')} pid={worker.get('pid')} elapsed={worker.get('elapsed')} cpu={worker.get('cpu')} command={worker.get('command')}"
            for worker in active_workers
        ],
        "```",
        "",
        "## Recovery Rule",
        "",
        checkpoint["recovery_rule"],
        "",
        "## Tell Codex After A Crash",
        "",
        checkpoint["assistant_resume_prompt"],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_session_handoff(path: Path, checkpoint: dict[str, Any]) -> None:
    batch = checkpoint["batch"]
    next_action = checkpoint["next_action"]
    active_workers = checkpoint.get("active_workers") or []
    lines = [
        "# BestChoice Session Handoff",
        "",
        "> Auto-generated by `scripts/workflow_checkpoint.py`. Use this file after terminal interruption or Mac reboot.",
        "",
        f"- generated_at: `{checkpoint['generated_at']}`",
        f"- cwd: `{checkpoint['cwd']}`",
        f"- phase: `{checkpoint['current_phase']}`",
        f"- covered_stocks: `{batch['covered_stocks']}`",
        f"- completed_batches: `{batch['completed_batches']}`",
        f"- next_offset: `{batch['next_offset']}`",
        f"- consistency_ready: `{checkpoint['consistency']['ready']}`",
        f"- active_workers: `{len(active_workers)}`",
        "",
        "## Recovery",
        "",
        "```bash",
        "bash scripts/bc_resume.sh",
        "```",
        "",
        "## Next Action",
        "",
        f"- kind: `{next_action.get('kind')}`",
        f"- reason: `{next_action.get('reason')}`",
        "",
        "```bash",
        *_next_action_commands(next_action),
        "```",
        "",
        "## Active Workers",
        "",
        "```text",
        *[
            f"{worker.get('kind')} pid={worker.get('pid')} elapsed={worker.get('elapsed')} cpu={worker.get('cpu')} command={worker.get('command')}"
            for worker in active_workers
        ],
        "```",
        "",
        "## Rules",
        "",
        "- BestChoice root is `/Users/dp/Documents/M/stock/bestchoice`.",
        "- Do not write production `analysis/stock_formula_best.csv` until full coverage and aggregate audit pass.",
        "- If `next_action=wait_active_worker`, wait and rerun `bash scripts/bc_resume.sh`; do not launch a duplicate batch.",
        "- `chunkymonkey` is read-only upstream for BestChoice.",
        "- Do not start, stop, monitor, or occupy GCP resources from BestChoice unless the user explicitly authorizes GCP in the current conversation.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a recovery checkpoint for terminal crashes and Mac reboots.")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--print", action="store_true", help="Print the checkpoint JSON to stdout.")
    parser.add_argument("--brief", action="store_true", help="Print a human-readable recovery summary.")
    parser.add_argument("--next", action="store_true", help="Print only the next command(s) to run.")
    parser.add_argument("--no-snapshot", action="store_true", help="Do not refresh the latest recovery snapshot.")
    args = parser.parse_args()
    checkpoint = build_checkpoint()
    args.json.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_markdown(args.md, checkpoint)
    _write_session_handoff(SESSION_HANDOFF, checkpoint)
    if not args.no_snapshot:
        _write_snapshot(LATEST_SNAPSHOT, checkpoint, args.json, args.md)
    if args.print:
        print(json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.brief:
        print(_format_brief(checkpoint))
    elif args.next:
        print("\n".join(_next_action_commands(checkpoint["next_action"])))
    else:
        batch = checkpoint["batch"]
        print(
            "workflow_checkpoint: "
            f"covered={batch['covered_stocks']} next_offset={batch['next_offset']} "
            f"rows={batch['row_count']} candidates={batch['candidate_count']} "
            f"replacements={batch['replacement_count']} missing_without_reason={batch['missing_without_reason']}"
        )


if __name__ == "__main__":
    main()
