#!/usr/bin/env python3
"""Repo-local audit and development assistant for ChunkyMonkey."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[1]
COMPLEXITY_SCRIPT = Path.home() / ".agents/skills/complexity-optimizer/scripts/analyze_complexity.py"
DEFAULT_COMPLEXITY_BASELINE = Path("data/reports/tooling/complexity_baseline.json")

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_tooling_gate  # noqa: E402
import audit_docs_graph  # noqa: E402


def _run_command(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _json_from_stdout(result: dict[str, Any]) -> dict[str, Any] | None:
    try:
        parsed = json.loads(result.get("stdout") or "")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _command_summary(result: dict[str, Any], *, include_stdout: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "cmd": result["cmd"],
        "returncode": result["returncode"],
    }
    if result.get("stderr"):
        summary["stderr_tail"] = result["stderr"][-1200:]
    if include_stdout and result.get("stdout"):
        summary["stdout"] = result["stdout"]
    return summary


def _aggregate_verdict(sections: list[dict[str, Any]]) -> str:
    saw_warn = False
    for section in sections:
        verdict = section.get("verdict")
        returncode = section.get("returncode")
        if verdict == "FAIL" or (returncode is not None and returncode != 0 and verdict != "WARN"):
            return "FAIL"
        if verdict == "WARN":
            saw_warn = True
    return "WARN" if saw_warn else "PASS"


def _storage_payload_summary(report: dict[str, Any] | None, *, max_findings: int = 20) -> dict[str, Any] | None:
    if not report:
        return None
    findings = [
        item
        for item in report.get("findings", [])
        if item.get("severity") in {"FAIL", "WARN"}
    ]
    findings.sort(
        key=lambda item: (
            0 if item.get("severity") == "FAIL" else 1,
            -int(item.get("max_value_bytes") or 0),
            str(item.get("table") or ""),
            str(item.get("column") or ""),
        )
    )
    return {
        "verdict": report.get("verdict"),
        "summary": report.get("summary", {}),
        "top_findings": findings[:max_findings],
    }


def _stage_opt_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    recommendation = report.get("next_action_recommendation")
    if not isinstance(recommendation, dict):
        recommendation = None
    return {
        "summary": {
            "raw_signal_rows": report.get("raw_signal_rows"),
            "filtered_signal_rows": report.get("filtered_signal_rows"),
            "unique_keys": report.get("unique_keys"),
            "ready_keys": report.get("ready_keys"),
            "ready_coverage_pct": report.get("ready_coverage_pct"),
            "below_min_signals": (report.get("blocked_reason_counts") or {}).get("below_min_signals", 0),
            "codes_without_bars": report.get("codes_without_bars", 0),
        },
        "next_action_recommendation": recommendation,
    }


def _need_coverage_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    need_gap_summary = report.get("need_gap_summary") if isinstance(report.get("need_gap_summary"), dict) else None
    source_payload = need_gap_summary or report
    blocked_needs = source_payload.get("blocked_needs")
    if not isinstance(blocked_needs, list):
        blocked_needs = []
    registered_source_names = source_payload.get("registered_source_names")
    if not isinstance(registered_source_names, list):
        registered_source_names = []
    return {
        "summary": {
            "need_count": source_payload.get("need_count"),
            "blocked_need_count": source_payload.get("blocked_need_count", 0),
            "registered_source_name_count": len(registered_source_names),
        },
        "blocked_needs": blocked_needs,
    }


def _next_actions(
    tooling_gate: dict[str, Any] | None,
    worktree_summary: dict[str, Any] | None = None,
    storage_payload: dict[str, Any] | None = None,
    data_health: dict[str, Any] | None = None,
    stage_opt: dict[str, Any] | None = None,
    need_coverage: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    if not tooling_gate:
        return [{"priority": "P0", "action": "Fix chunkyctl/audit_tooling_gate JSON parsing before relying on doctor output"}]
    actions: list[dict[str, str]] = []
    git_status = tooling_gate.get("git_status", {})
    codegraph = tooling_gate.get("codegraph", {})
    complexity = tooling_gate.get("complexity", {})
    if not git_status.get("clean", True):
        unknown_count = (worktree_summary or {}).get("unknown_count")
        if unknown_count == 0:
            actions.append(
                {
                    "priority": "P0",
                    "action": "Review dirty worktree one bucket at a time with scripts/chunkyctl worktree --bucket <name>; do not bulk stage",
                }
            )
        else:
            actions.append(
                {
                    "priority": "P0",
                    "action": "Classify dirty worktree into review/stage/delete/generated buckets; do not bulk stage",
                }
            )
    if codegraph.get("pending", {}).get("sync_required"):
        candidate_count = (worktree_summary or {}).get("codegraph_candidate_untracked_count")
        if candidate_count is None:
            candidate_count = (worktree_summary or {}).get("codegraph_reconciliation", {}).get("untracked_indexable_files")
        if candidate_count == codegraph.get("pending", {}).get("added"):
            actions.append(
                {
                    "priority": "P0",
                    "action": "CodeGraph Added pending matches untracked indexable files; review/stage by worktree bucket, not by forcing sync",
                }
            )
        else:
            actions.append(
                {
                    "priority": "P0",
                    "action": "Run codegraph sync, then reconcile remaining Added pending against untracked files",
                }
            )
    if not codegraph.get("pending", {}).get("sync_required"):
        actions.append(
            {
                "priority": "P1",
                "action": "CodeGraph has no pending changes; keep syncing after Python edits",
            }
        )
    if complexity.get("baseline", {}).get("status") != "loaded":
        actions.append(
            {
                "priority": "P1",
                "action": "Choose a scoped complexity baseline before treating historical HIGH as current regressions",
            }
        )
    if complexity.get("diff", {}).get("new_high_count"):
        actions.append(
            {
                "priority": "P0",
                "action": "Inspect and fix new complexity HIGH before claiming the change is clean",
            }
        )
    if storage_payload and storage_payload.get("verdict") in {"FAIL", "WARN"}:
        priority = "P0" if storage_payload.get("verdict") == "FAIL" else "P1"
        actions.append(
            {
                "priority": priority,
                "action": "Review storage payload audit findings for recursive JSON or oversized opaque DB payloads before claiming cleanup complete",
            }
        )
    if data_health and data_health.get("summary"):
        summary = data_health["summary"]
        red_count = int(summary.get("red", 0) or 0)
        yellow_count = int(summary.get("yellow", 0) or 0)
        blocking_yellow_tables = data_health.get("blocking_yellow_tables") or []
        if red_count:
            actions.append(
                {
                    "priority": "P0",
                    "action": "Review data health red tables one bucket at a time; prioritize active writers, stale tables, and missing assets before trusting freshness claims",
                }
            )
        elif blocking_yellow_tables:
            table_names = ", ".join(
                str(item.get("table_name") or "").strip()
                for item in blocking_yellow_tables[:3]
                if str(item.get("table_name") or "").strip()
            )
            if len(blocking_yellow_tables) > 3 and table_names:
                table_names = f"{table_names}, +{len(blocking_yellow_tables) - 3} more"
            detail = (
                "Review data health yellow tables with quality_gate_level=blocking first"
                + (f" ({table_names})" if table_names else "")
                + "; they are capped to yellow for verdict aggregation but still carry blocking gate status"
            )
            actions.append({"priority": "P1", "action": detail})
        elif yellow_count:
            actions.append(
                {
                    "priority": "P1",
                    "action": "Review data health yellow tables and decide whether they are expected on-demand assets or writer/SLA debt",
                }
            )
    if stage_opt and stage_opt.get("next_action_recommendation"):
        recommendation = stage_opt["next_action_recommendation"]
        focus = str(recommendation.get("focus") or "upstream_candidate_supply")
        weakest_formula_ids = ", ".join(str(item) for item in recommendation.get("weakest_formula_ids") or [])
        weakest_stage_bins = ", ".join(str(item) for item in recommendation.get("weakest_stage_bins") or [])
        action_text = (
            f"Stage-opt candidate supply [{focus}]: {recommendation.get('reason') or 'review current recommendation'} "
            f"→ {recommendation.get('recommended_lever') or 'review upstream candidate supply'}"
        )
        if weakest_formula_ids or weakest_stage_bins:
            details: list[str] = []
            if weakest_formula_ids:
                details.append(f"weakest formulas: {weakest_formula_ids}")
            if weakest_stage_bins:
                details.append(f"weakest stages: {weakest_stage_bins}")
            structural_notes = [str(item) for item in recommendation.get("structural_notes") or [] if str(item).strip()]
            if structural_notes:
                details.append(f"structural notes: {'; '.join(structural_notes)}")
            action_text += " (" + "; ".join(details) + ")"
        actions.append(
            {
                "priority": str(recommendation.get("priority") or "P1"),
                "action": action_text,
            }
        )
    blocked_needs = (need_coverage or {}).get("blocked_needs") or []
    if blocked_needs:
        blocked_need_ids = [str(item.get("need_id") or "") for item in blocked_needs if str(item.get("need_id") or "").strip()]
        blocked_need_names = [str(item.get("need_name") or "") for item in blocked_needs if str(item.get("need_name") or "").strip()]
        action_text = (
            "Need coverage blocked-gap triage: review blocked needs and source evidence before treating them as production-ready"
        )
        details: list[str] = []
        if blocked_need_ids:
            details.append(f"blocked needs: {', '.join(blocked_need_ids[:3])}")
        if blocked_need_names:
            details.append(f"names: {', '.join(blocked_need_names[:2])}")
        if details:
            action_text += " (" + "; ".join(details) + ")"
        special_need = next((item for item in blocked_needs if str(item.get("need_id") or "") == "need_027"), None)
        if special_need:
            failure_queue_snapshot = special_need.get("failure_queue_snapshot") or {}
            status_counts = failure_queue_snapshot.get("status_counts") or {}
            open_count = int(status_counts.get("open") or 0)
            resolved_count = int(status_counts.get("resolved") or 0)
            source_registration = special_need.get("source_registration") or {}
            fallback_supports_individual_fund_flow = source_registration.get("fallback_source_supports_individual_fund_flow")
            fallback_source_family = str(source_registration.get("fallback_source_family") or "aif10")
            if open_count or resolved_count:
                action_text += f" [need_027 blocked/unknown; failure_queue open={open_count} resolved={resolved_count}]"
            else:
                action_text += " [need_027 blocked/unknown; failure_queue evidence unavailable]"
            if fallback_supports_individual_fund_flow is False:
                action_text += (
                    f"; {fallback_source_family} exact individual_fund_flow unavailable"
                )
        actions.append({"priority": "P1", "action": action_text})
    return actions


def _doctor_baseline_arg(repo: Path, explicit_baseline: str | None) -> str | None:
    if explicit_baseline:
        return explicit_baseline
    candidate = repo / DEFAULT_COMPLEXITY_BASELINE
    return str(candidate) if candidate.exists() else None


def run_doctor(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    baseline_arg = _doctor_baseline_arg(repo, args.baseline)
    tooling_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "audit_tooling_gate.py"),
        "--repo",
        str(repo),
        "--complexity-target",
        args.complexity_target,
        "--max-findings",
        str(args.max_findings),
    ]
    if baseline_arg:
        tooling_cmd.extend(["--baseline", baseline_arg])
    if args.fail_on_dirty_worktree:
        tooling_cmd.append("--fail-on-dirty-worktree")

    tooling_result = _run_command(tooling_cmd, cwd=repo)
    tooling_payload = _json_from_stdout(tooling_result)
    sections: list[dict[str, Any]] = []
    if tooling_payload:
        sections.append({"name": "tooling_gate", "verdict": tooling_payload.get("verdict")})
    else:
        sections.append({"name": "tooling_gate", "verdict": "FAIL"})

    test_tool: dict[str, Any] | None = None
    if not args.skip_test_tool:
        result = _run_command(
            [sys.executable, "backend/scripts/audit_test_tool_health.py"],
            cwd=repo,
        )
        parsed = _json_from_stdout(result)
        test_tool = {
            "command": _command_summary(result),
            "report": parsed,
            "verdict": parsed.get("verdict") if parsed else "FAIL",
        }
        sections.append({"name": "test_tool", "verdict": test_tool["verdict"], "returncode": result["returncode"]})

    universe: dict[str, Any] | None = None
    if not args.skip_universe:
        result = _run_command(
            [sys.executable, "backend/scripts/check_universe_filter.py", "--all"],
            cwd=repo,
        )
        universe = {
            "command": _command_summary(result, include_stdout=True),
            "verdict": "PASS" if result["returncode"] == 0 else "FAIL",
        }
        sections.append({"name": "universe", "verdict": universe["verdict"], "returncode": result["returncode"]})

    storage_payload: dict[str, Any] | None = None
    if not args.skip_storage_payload:
        result = _run_command(
            [
                sys.executable,
                "backend/scripts/audit_storage_payloads.py",
                "--format",
                "json",
            ],
            cwd=repo,
        )
        parsed = _json_from_stdout(result)
        storage_payload = {
            "command": _command_summary(result),
            "report": _storage_payload_summary(parsed, max_findings=args.storage_max_findings),
            "verdict": parsed.get("verdict") if parsed else "FAIL",
        }
        sections.append(
            {
                "name": "storage_payload",
                "verdict": storage_payload["verdict"],
                "returncode": result["returncode"],
            }
        )

    data_health: dict[str, Any] | None = None
    result = _run_command(
        [
            sys.executable,
            "backend/scripts/data_health_snapshot.py",
            "--dry-run",
            "--format",
            "json",
        ],
        cwd=repo,
    )
    parsed = _json_from_stdout(result)
    data_health = {
        "command": _command_summary(result),
        "report": parsed,
        "verdict": parsed.get("verdict") if parsed else "FAIL",
    }
    sections.append(
        {
            "name": "data_health",
            "verdict": data_health["verdict"],
            "returncode": result["returncode"],
        }
    )

    stage_opt: dict[str, Any] | None = None
    if not args.skip_stage_opt:
        result = _run_command(
            [
                sys.executable,
                "backend/scripts/audit_stage_opt_candidate_supply.py",
                "--format",
                "json",
            ],
            cwd=repo,
        )
        parsed = _json_from_stdout(result)
        stage_opt = {
            "command": _command_summary(result),
            "report": _stage_opt_summary(parsed),
            "verdict": parsed.get("verdict") if parsed else "FAIL",
        }
        sections.append(
            {
                "name": "stage_opt",
                "verdict": stage_opt["verdict"],
                "returncode": result["returncode"],
            }
        )

    need_coverage: dict[str, Any] | None = None
    result = _run_command(
        [
            sys.executable,
            "backend/scripts/audit_tdx_data_need_coverage.py",
            "--format",
            "json",
        ],
        cwd=repo,
    )
    parsed = _json_from_stdout(result)
    need_coverage = {
        "command": _command_summary(result),
        "report": _need_coverage_summary(parsed),
        "verdict": "PASS" if result["returncode"] == 0 else "FAIL",
    }
    sections.append(
        {
            "name": "need_coverage",
            "verdict": need_coverage["verdict"],
            "returncode": result["returncode"],
        }
    )

    worktree_report = None
    worktree_summary = None
    if tooling_payload:
        git_result = _run_command(["git", "status", "--short"], cwd=repo)
        worktree_report = build_worktree_report(repo=repo, git_status_text=git_result["stdout"])
        worktree_summary = build_doctor_worktree_summary(worktree_report, codegraph=tooling_payload.get("codegraph", {}))

    report = {
        "schema_version": 1,
        "command": "doctor",
        "repo": str(repo),
        "verdict": _aggregate_verdict(sections),
        "tooling_gate": tooling_payload,
        "tooling_gate_command": _command_summary(tooling_result),
        "worktree": worktree_summary,
        "test_tool": test_tool,
        "universe": universe,
        "storage_payload": storage_payload,
        "data_health": data_health,
        "stage_opt": stage_opt,
        "need_coverage": need_coverage,
        "next_actions": _next_actions(
            tooling_payload,
            worktree_summary,
            storage_payload.get("report") if storage_payload else None,
            data_health.get("report") if data_health else None,
            stage_opt.get("report") if stage_opt else None,
            need_coverage.get("report") if need_coverage else None,
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["verdict"] == "FAIL" else 0


def _scope_list(raw_scopes: list[str]) -> list[str]:
    return [scope for scope in raw_scopes if scope]


def _is_python_scope(scope: str) -> bool:
    return scope.endswith(".py")


def _is_test_scope(scope: str) -> bool:
    return "/tests/" in scope or scope.startswith("backend/tests") or scope.startswith("tests/")


WORKTREE_BUCKET_ORDER = [
    "controller_state",
    "startup_tooling",
    "docs_archive_moves",
    "updater_split",
    "universe_governance",
    "data_source_lineage_profiles",
    "audit_gate_scripts",
    "pipeline_build_scripts",
    "backend_services_api",
    "tests",
    "generated_evidence",
    "project_docs",
    "config_project",
    "unknown",
]

WORKTREE_BUCKET_META = {
    "controller_state": {
        "priority": "P0",
        "recommended_action": "Review with current goal/handoff facts; stage only with the matching delivery slice",
    },
    "startup_tooling": {
        "priority": "P0",
        "recommended_action": "Review as governance/tooling slice; keep quickstart, registry, and tests synchronized",
    },
    "docs_archive_moves": {
        "priority": "P0/P1",
        "recommended_action": "Prove moved content and reference updates with rg before accepting deletion",
    },
    "updater_split": {
        "priority": "P1",
        "recommended_action": "Review as updater modularization slice with updater targeted tests and CodeGraph",
    },
    "universe_governance": {
        "priority": "P0",
        "recommended_action": "Run universe lint and verify legacy active-stock cache is not a truth source",
    },
    "data_source_lineage_profiles": {
        "priority": "P0/P1",
        "recommended_action": "Review data-need/source/PIT/freshness contracts before business or UI claims",
    },
    "audit_gate_scripts": {
        "priority": "P1",
        "recommended_action": "Run script-specific tests; treat generated reports as evidence only if ledger-linked",
    },
    "pipeline_build_scripts": {
        "priority": "P1",
        "recommended_action": "Review with script-specific tests, data-write scope, and CodeGraph/complexity gates",
    },
    "backend_services_api": {
        "priority": "P1",
        "recommended_action": "Review by module boundary with targeted tests, CodeGraph, and complexity scan",
    },
    "tests": {
        "priority": "P1",
        "recommended_action": "Check test-tool registry coverage before citing pytest results",
    },
    "generated_evidence": {
        "priority": "P1/P2",
        "recommended_action": "Keep only stable evidence artifacts referenced from current ledgers",
    },
    "project_docs": {
        "priority": "P1",
        "recommended_action": "Keep durable design in docs and current state in goal/handoff; remove stale duplicates",
    },
    "config_project": {
        "priority": "P1",
        "recommended_action": "Review as config/policy slice and confirm no hidden business-rule duplication",
    },
    "unknown": {
        "priority": "P0",
        "recommended_action": "Do not stage; inspect with CodeGraph, rg, owner docs, and targeted tests first",
    },
}


def _status_path(entry: dict[str, Any]) -> str:
    path = str(entry.get("path") or "")
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    return path.strip('"')


def _worktree_status_kind(entry: dict[str, Any]) -> str:
    raw_status = str(entry.get("raw_status") or "")
    status_chars = {str(entry.get("index_status") or ""), str(entry.get("worktree_status") or "")}
    if raw_status == "??":
        return "untracked"
    if "U" in status_chars:
        return "unmerged"
    if "D" in status_chars:
        return "deleted"
    if "R" in status_chars:
        return "renamed"
    if "A" in status_chars:
        return "added"
    if "M" in status_chars:
        return "modified"
    return "changed"


def _is_top_level_markdown(path: str) -> bool:
    return "/" not in path and path.endswith(".md")


def _worktree_bucket(path: str, status_kind: str) -> str:
    if path in {"goal.md", "SESSION_HANDOFF.md", "PROJECT_INDEX.md", "AGENTS.md", "CLAUDE.md"}:
        return "controller_state"
    if path.startswith("analysis/workflow_checkpoint.") or path.startswith("analysis/handoff_"):
        return "controller_state"
    if path.startswith("analysis/codex_bootstrap_") or path.startswith("analysis/next_session_prompt_"):
        return "controller_state"
    if path == "docs/implementation_plan.md":
        return "controller_state"

    if path in {
        "backend/scripts/chunkyctl.py",
        "backend/scripts/audit_tooling_gate.py",
        "backend/scripts/audit_test_tool_health.py",
        "backend/config/test_tool_registry.yaml",
        "docs/chunkyctl_session_quickstart.md",
        "docs/engineering_governance.md",
        "scripts/chunkyctl",
        "scripts/safe_commit.sh",
        "scripts/session_handoff_audit.py",
        "gcp/README_GCP_BATCH.md",
        "gcp/cost_tracker.sh",
        "gcp/vm_start.sh",
    }:
        return "startup_tooling"

    if status_kind == "deleted" and _is_top_level_markdown(path):
        return "docs_archive_moves"
    if path.startswith("analysis/") and any(
        marker in path
        for marker in (
            "archived",
            "data_integrity_audit_20260517",
            "market_perception_development_plan",
        )
    ):
        return "docs_archive_moves"

    if path == "backend/routers/updater.py" or path.startswith("backend/routers/updater_"):
        return "updater_split"
    if path.startswith("backend/tests/test_updater"):
        return "updater_split"

    if path in {
        "backend/scripts/check_universe_filter.py",
        "backend/services/universe.py",
        "backend/services/recommendation_universe.py",
        "backend/services/labels/universe.py",
        "backend/config/universe_rules.yaml",
    }:
        return "universe_governance"

    if path == "backend/config/tdx_data_need_coverage.yaml":
        return "data_source_lineage_profiles"
    if path.startswith("backend/services/data_sources/") or path.startswith("backend/services/data_lineage/"):
        return "data_source_lineage_profiles"
    if path in {
        "docs/chip_distribution_cyq_spec.md",
        "docs/data_product_contract.md",
    }:
        return "data_source_lineage_profiles"
    if "tdx_data_need_coverage" in path:
        return "data_source_lineage_profiles"

    if path.startswith("backend/scripts/") and path.endswith((".json", ".md", ".csv")):
        return "generated_evidence"
    if path.startswith("data/reports/") or (path.startswith("analysis/") and path.endswith(".json")):
        return "generated_evidence"

    if path.startswith("backend/scripts/audit_") or path.startswith("backend/tests/scripts/test_audit_"):
        return "audit_gate_scripts"
    if path.startswith("backend/scripts/") and path.endswith((".py", ".sh")):
        return "pipeline_build_scripts"

    if path.startswith("backend/services/") or path.startswith("backend/routers/"):
        return "backend_services_api"
    if path.startswith("backend/tests/"):
        return "tests"
    if path.startswith("docs/") or path.startswith("analysis/"):
        return "project_docs"
    if path in {".gitignore", "pytest.ini"} or path.startswith(("backend/config/", "configs/")):
        return "config_project"
    return "unknown"


def _is_codegraph_indexable_path(path: str) -> bool:
    return path.endswith((".py", ".js", ".jsx"))


def build_worktree_report(*, repo: Path, git_status_text: str, bucket: str | None = None) -> dict[str, Any]:
    git_status = audit_tooling_gate.parse_git_status_short(git_status_text)
    buckets = {
        name: {
            "bucket": name,
            "priority": WORKTREE_BUCKET_META[name]["priority"],
            "recommended_action": WORKTREE_BUCKET_META[name]["recommended_action"],
            "count": 0,
            "status_counts": {},
            "entries": [],
        }
        for name in WORKTREE_BUCKET_ORDER
    }
    for entry in git_status["entries"]:
        path = _status_path(entry)
        status_kind = _worktree_status_kind(entry)
        bucket_name = _worktree_bucket(path, status_kind)
        grouped = buckets[bucket_name]
        grouped["count"] += 1
        grouped["status_counts"][status_kind] = grouped["status_counts"].get(status_kind, 0) + 1
        grouped["entries"].append(
            {
                "path": str(entry.get("path") or ""),
                "normalized_path": path,
                "status": status_kind,
                "raw_status": entry.get("raw_status"),
            }
        )
    all_bucket_counts = {name: buckets[name]["count"] for name in WORKTREE_BUCKET_ORDER if buckets[name]["count"]}
    indexable_untracked_bucket_counts: dict[str, int] = {}
    for name in WORKTREE_BUCKET_ORDER:
        count = sum(
            1
            for entry in buckets[name]["entries"]
            if entry["status"] == "untracked" and _is_codegraph_indexable_path(entry["normalized_path"])
        )
        if count:
            indexable_untracked_bucket_counts[name] = count
    selected_buckets = [buckets[name] for name in WORKTREE_BUCKET_ORDER if buckets[name]["count"]]
    if bucket:
        selected_buckets = [item for item in selected_buckets if item["bucket"] == bucket]
    bucket_counts = {item["bucket"]: item["count"] for item in selected_buckets}
    unknown_count = buckets["unknown"]["count"]
    return {
        "schema_version": 1,
        "command": "worktree",
        "repo": str(repo),
        "bucket_filter": bucket,
        "verdict": "PASS" if git_status["clean"] else "FAIL",
        "summary": {
            "total": git_status["total"],
            "git_counts": git_status["counts"],
            "bucket_counts": all_bucket_counts,
            "selected_bucket_counts": bucket_counts,
            "unknown_count": unknown_count,
            "codegraph_candidate_untracked_count": sum(indexable_untracked_bucket_counts.values()),
            "codegraph_candidate_untracked_bucket_counts": indexable_untracked_bucket_counts,
        },
        "buckets": selected_buckets,
        "next_actions": [
            {
                "priority": "P0",
                "action": "Review one bucket at a time; never git add . or mix unrelated slices",
            },
            {
                "priority": "P0" if unknown_count else "P1",
                "action": "Inspect unknown bucket with CodeGraph + rg before staging or deleting",
            },
            {
                "priority": "P1",
                "action": "For deletion candidates, prove moved content/references/tests before deleting for real",
            },
        ],
    }


def _format_status_counts(status_counts: dict[str, int]) -> str:
    if not status_counts:
        return "-"
    return ", ".join(f"{status}:{count}" for status, count in sorted(status_counts.items()))


def render_worktree_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    selected_counts = summary.get("selected_bucket_counts") or {}
    indexable_counts = summary.get("codegraph_candidate_untracked_bucket_counts") or {}
    lines = [
        "# ChunkyCtl Worktree Report",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Verdict | {report.get('verdict', 'unknown')} |",
        f"| Total dirty entries | {summary.get('total', 0)} |",
        f"| Unknown entries | {summary.get('unknown_count', 0)} |",
        f"| CodeGraph candidate untracked files | {summary.get('codegraph_candidate_untracked_count', 0)} |",
        "",
        "## Bucket Summary",
        "",
        "| Priority | Bucket | Count | Status Counts | CodeGraph Candidates | Review Command |",
        "|---|---|---:|---|---:|---|",
    ]
    bucket_by_name = {item["bucket"]: item for item in report.get("buckets", [])}
    for bucket_name, count in selected_counts.items():
        bucket = bucket_by_name.get(bucket_name, {})
        status_counts = _format_status_counts(bucket.get("status_counts") or {})
        codegraph_candidates = indexable_counts.get(bucket_name, 0)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(bucket.get("priority") or "-"),
                    bucket_name,
                    str(count),
                    status_counts,
                    str(codegraph_candidates),
                    f"`scripts/chunkyctl worktree --bucket {bucket_name} --format markdown`",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Next Actions", ""])
    for action in report.get("next_actions", []):
        lines.append(f"- {action.get('priority', 'P?')}: {action.get('action', '')}")

    bucket_filter = report.get("bucket_filter")
    if bucket_filter:
        lines.extend(["", f"## Entries: {bucket_filter}", ""])
        selected_bucket = bucket_by_name.get(bucket_filter)
        if not selected_bucket:
            lines.append("- No entries matched this bucket.")
        else:
            for entry in selected_bucket.get("entries", []):
                lines.append(
                    f"- `{entry.get('status', 'changed')}` `{entry.get('normalized_path') or entry.get('path')}`"
                )
            recommended = selected_bucket.get("recommended_action")
            if recommended:
                lines.extend(["", f"Recommended action: {recommended}"])
    return "\n".join(lines) + "\n"


def build_doctor_worktree_summary(worktree_report: dict[str, Any], *, codegraph: dict[str, Any]) -> dict[str, Any]:
    summary = dict(worktree_report.get("summary") or {})
    codegraph_pending = (codegraph or {}).get("pending", {})
    pending_added = codegraph_pending.get("added", 0)
    candidate_count = summary.get("codegraph_candidate_untracked_count", 0)
    return {
        "verdict": worktree_report.get("verdict"),
        "total": summary.get("total", 0),
        "unknown_count": summary.get("unknown_count", 0),
        "bucket_counts": summary.get("bucket_counts", {}),
        "codegraph_candidate_untracked_count": candidate_count,
        "codegraph_reconciliation": {
            "pending_added": pending_added,
            "untracked_indexable_files": candidate_count,
            "matches": pending_added == candidate_count,
            "interpretation": "CodeGraph Added pending matches untracked .py/.js/.jsx files"
            if pending_added == candidate_count
            else "CodeGraph Added pending does not match untracked indexable file count; inspect status/sync",
        },
    }


DOCS_CLEANUP_BUCKETS = ("project_docs", "docs_archive_moves")
DOCS_SUPPORT_PATHS = {
    "goal.md",
    "docs/implementation_plan.md",
    "docs/chunkyctl_session_quickstart.md",
    "docs/engineering_governance.md",
    "backend/scripts/audit_docs_graph.py",
    "backend/tests/scripts/test_audit_docs_graph.py",
    "backend/scripts/chunkyctl.py",
    "backend/tests/scripts/test_chunkyctl.py",
    "scripts/chunkyctl",
}


def _worktree_entries_by_bucket(worktree_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        bucket.get("bucket", ""): list(bucket.get("entries", []))
        for bucket in worktree_report.get("buckets", [])
    }


def build_docs_cleanup_report(*, repo: Path, git_status_text: str) -> dict[str, Any]:
    docs_graph = audit_docs_graph.build_docs_graph_report(repo)
    worktree_report = build_worktree_report(repo=repo, git_status_text=git_status_text)
    bucket_counts = worktree_report.get("summary", {}).get("bucket_counts", {})
    docs_bucket_counts = {
        bucket: bucket_counts.get(bucket, 0)
        for bucket in DOCS_CLEANUP_BUCKETS
        if bucket_counts.get(bucket, 0)
    }
    entries_by_bucket = _worktree_entries_by_bucket(worktree_report)
    support_entries: list[dict[str, str]] = []
    for entries in entries_by_bucket.values():
        for entry in entries:
            path = str(entry.get("normalized_path") or "")
            if path in DOCS_SUPPORT_PATHS:
                support_entries.append(
                    {
                        "path": path,
                        "bucket": _worktree_bucket(path, str(entry.get("status") or "")),
                        "status": str(entry.get("status") or "changed"),
                    }
                )
    support_bucket_counts: dict[str, int] = {}
    for entry in support_entries:
        bucket = entry["bucket"]
        support_bucket_counts[bucket] = support_bucket_counts.get(bucket, 0) + 1
    dirty_docs_entries = sum(docs_bucket_counts.values())
    dirty_support_entries = sum(support_bucket_counts.values())
    if docs_graph.get("verdict") == "FAIL":
        verdict = "FAIL"
    elif dirty_docs_entries or dirty_support_entries:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return {
        "schema_version": 1,
        "command": "docs",
        "repo": str(repo),
        "verdict": verdict,
        "docs_graph": {
            "verdict": docs_graph.get("verdict"),
            "docs_count": docs_graph.get("docs_count"),
            "docs_hard_max": docs_graph.get("docs_hard_max"),
            "edge_count": docs_graph.get("edge_count"),
            "authority_edge_count": docs_graph.get("authority_edge_count"),
            "context_only_edge_count": docs_graph.get("context_only_edge_count"),
            "unmentioned_docs": len(docs_graph.get("unmentioned_docs", [])),
            "unresolved_live_refs": len(docs_graph.get("unresolved_live_refs", [])),
            "missing_cleanup_archive_targets": len(docs_graph.get("missing_cleanup_archive_targets", [])),
            "forbidden_scc": len(docs_graph.get("forbidden_scc", [])),
            "largest_scc": docs_graph.get("largest_scc"),
            "archive_content": docs_graph.get("archive_content", {}),
        },
        "worktree_slice": {
            "docs_bucket_counts": docs_bucket_counts,
            "support_bucket_counts": support_bucket_counts,
            "support_entries": support_entries,
            "dirty_docs_entries": dirty_docs_entries,
            "dirty_support_entries": dirty_support_entries,
            "unknown_count": worktree_report.get("summary", {}).get("unknown_count", 0),
        },
        "next_actions": [
            {
                "priority": "P0",
                "action": "Keep docs deletions, analysis archives, docs/README.md, goal.md, implementation_plan, audit_docs_graph.py, and tests in one reviewed docs-cleanup slice",
            },
            {
                "priority": "P0" if docs_graph.get("verdict") == "FAIL" else "P1",
                "action": "Run PYTHONPATH=backend python backend/scripts/audit_docs_graph.py --format markdown before accepting docs cleanup",
            },
            {
                "priority": "P1",
                "action": "Use scripts/chunkyctl worktree --bucket project_docs --format markdown and docs_archive_moves before staging; never git add .",
            },
        ],
    }


def render_docs_cleanup_markdown(report: dict[str, Any]) -> str:
    docs_graph = report.get("docs_graph") or {}
    archive = docs_graph.get("archive_content") or {}
    worktree_slice = report.get("worktree_slice") or {}
    lines = [
        "# ChunkyCtl Docs Cleanup Report",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Verdict | {report.get('verdict', 'unknown')} |",
        f"| docs_graph_verdict | {docs_graph.get('verdict', 'unknown')} |",
        f"| docs_count | {docs_graph.get('docs_count', 'unknown')} |",
        f"| docs_hard_max | {docs_graph.get('docs_hard_max', 'unknown')} |",
        f"| edge_count | {docs_graph.get('edge_count', 'unknown')} |",
        f"| authority_edge_count | {docs_graph.get('authority_edge_count', 'unknown')} |",
        f"| context_only_edge_count | {docs_graph.get('context_only_edge_count', 'unknown')} |",
        f"| unresolved_live_refs | {docs_graph.get('unresolved_live_refs', 'unknown')} |",
        f"| missing_cleanup_archive_targets | {docs_graph.get('missing_cleanup_archive_targets', 'unknown')} |",
        f"| forbidden_scc | {docs_graph.get('forbidden_scc', 'unknown')} |",
        f"| archive_content_checked | {archive.get('checked', 0)} |",
        f"| archive_content_exact_match | {archive.get('exact_match', 0)} |",
        f"| archive_content_changed | {archive.get('changed', 0)} |",
        f"| archive_content_no_head_baseline | {archive.get('no_head_baseline', 0)} |",
        f"| dirty_docs_entries | {worktree_slice.get('dirty_docs_entries', 0)} |",
        f"| dirty_support_entries | {worktree_slice.get('dirty_support_entries', 0)} |",
        f"| unknown_worktree_entries | {worktree_slice.get('unknown_count', 0)} |",
        "",
        "## Dirty Buckets",
        "",
        "| Bucket | Count |",
        "|---|---:|",
    ]
    dirty_buckets = {
        **(worktree_slice.get("docs_bucket_counts") or {}),
        **(worktree_slice.get("support_bucket_counts") or {}),
    }
    if dirty_buckets:
        for bucket, count in dirty_buckets.items():
            lines.append(f"| {bucket} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(["", "## Next Actions", ""])
    for action in report.get("next_actions", []):
        lines.append(f"- {action.get('priority', 'P?')}: {action.get('action', '')}")
    return "\n".join(lines) + "\n"


def run_docs(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    git_result = _run_command(["git", "status", "--short"], cwd=repo)
    report = build_docs_cleanup_report(repo=repo, git_status_text=git_result["stdout"])
    report["git_status_command"] = _command_summary(git_result)
    if args.format == "markdown":
        print(render_docs_cleanup_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["verdict"] == "FAIL" else 0


def run_worktree(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    git_result = _run_command(["git", "status", "--short"], cwd=repo)
    report = build_worktree_report(repo=repo, git_status_text=git_result["stdout"], bucket=args.bucket)
    report["git_status_command"] = _command_summary(git_result)
    if args.format == "markdown":
        print(render_worktree_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _gate_commands_for_task(task: str, scopes: list[str]) -> list[dict[str, str]]:
    commands = [
        {
            "gate": "codegraph_context",
            "command": f'codegraph context "{task}"',
            "when": "before editing or accepting architecture boundaries",
        }
    ]
    if scopes:
        scope_args = " ".join(f"--scope {scope}" for scope in scopes)
        commands.append(
            {
                "gate": "test_tool_validity",
                "command": f"PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py {scope_args}",
                "when": "before running or citing scoped tests",
            }
        )
    if any(_is_python_scope(scope) for scope in scopes):
        commands.extend(
            [
                {
                    "gate": "py_compile",
                    "command": "python -m py_compile " + " ".join(scope for scope in scopes if _is_python_scope(scope)),
                    "when": "after Python edits",
                },
                {
                    "gate": "complexity",
                    "command": "/Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py "
                    + " ".join(scope for scope in scopes if _is_python_scope(scope))
                    + " --format markdown",
                    "when": "after Python edits, paired with CodeGraph sync",
                },
            ]
        )
    commands.append(
        {
            "gate": "post_edit_codegraph",
            "command": "codegraph sync .",
            "when": "after code edits",
        }
    )
    return commands


def _task_mentions(task: str, terms: tuple[str, ...]) -> bool:
    tokens = set(part for part in re.split(r"[^a-z0-9]+", task.lower()) if part)
    return any(term.lower() in tokens for term in terms)


def build_preflight_report(
    *,
    repo: Path,
    task: str,
    scopes: list[str],
    git_status_text: str,
    codegraph_status_text: str,
) -> dict[str, Any]:
    git_status = audit_tooling_gate.parse_git_status_short(git_status_text)
    codegraph_status = audit_tooling_gate.parse_codegraph_status(codegraph_status_text)
    risks: list[dict[str, str]] = []
    if not git_status["clean"]:
        risks.append({"severity": "FAIL", "risk": "dirty_worktree", "detail": "classify/stage by scope; never git add ."})
    if codegraph_status["pending"]["sync_required"]:
        risks.append({"severity": "FAIL", "risk": "codegraph_pending", "detail": "sync and disclose remaining untracked Added pending"})
    if _task_mentions(task, ("gcp", "optuna", "backtest", "paper", "strategy")):
        risks.append({"severity": "FAIL", "risk": "strategy_or_cloud_gate", "detail": "require explicit preflight gates before expensive or strategy work"})
    if _task_mentions(task, ("frontend", "ui", "browser")):
        risks.append({"severity": "WARN", "risk": "frontend_contract", "detail": "backend contract and Browser verification required"})
    if _task_mentions(task, ("delete", "cleanup", "remove")):
        risks.append({"severity": "WARN", "risk": "deletion_governance", "detail": "prove with CodeGraph + rg + tests before deleting"})
    verdict = "FAIL" if any(risk["severity"] == "FAIL" for risk in risks) else ("WARN" if risks else "PASS")
    return {
        "schema_version": 1,
        "command": "preflight",
        "repo": str(repo),
        "task": task,
        "scopes": scopes,
        "verdict": verdict,
        "risks": risks,
        "required_gates": _gate_commands_for_task(task, scopes),
        "truth_sources": [
            "K-line is trading truth",
            "calendar is date truth",
            "universe_rules.yaml is limit/board truth",
            "dim_active_a_stock is code-to-name/cache only",  # rule-compliance: ok evidence=governance-message-not-universe-query
        ],
    }


def run_preflight(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    task, scopes = _resolve_preflight_task_and_scopes(args)
    if not task:
        print("ERROR: preflight requires a task, either positional or --task", file=sys.stderr)
        return 2
    git_result = _run_command(["git", "status", "--short"], cwd=repo)
    codegraph_result = _run_command(["codegraph", "status", str(repo)], cwd=repo)
    report = build_preflight_report(
        repo=repo,
        task=task,
        scopes=scopes,
        git_status_text=git_result["stdout"],
        codegraph_status_text=codegraph_result["stdout"],
    )
    report["git_status_command"] = _command_summary(git_result)
    report["codegraph_status_command"] = _command_summary(codegraph_result)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["verdict"] == "FAIL" else 0


def _resolve_preflight_task_and_scopes(args: argparse.Namespace) -> tuple[str, list[str]]:
    task = str(getattr(args, "task", None) or getattr(args, "task_arg", "") or "").strip()
    scopes = [
        *list(getattr(args, "scope", []) or []),
        *list(getattr(args, "scope_arg", []) or []),
    ]
    return task, _scope_list(scopes)


def build_audit_plan(*, scopes: list[str]) -> dict[str, Any]:
    commands: list[list[str]] = []
    if scopes:
        commands.append([sys.executable, "backend/scripts/audit_test_tool_health.py", *sum([["--scope", scope] for scope in scopes], [])])
    py_scopes = [scope for scope in scopes if _is_python_scope(scope)]
    test_scopes = [scope for scope in scopes if _is_test_scope(scope)]
    if py_scopes:
        commands.append([sys.executable, "-m", "py_compile", *py_scopes])
        for scope in py_scopes:
            commands.append([sys.executable, str(COMPLEXITY_SCRIPT), scope, "--format", "markdown", "--max-findings", "80"])
        commands.append([sys.executable, "backend/scripts/check_universe_filter.py", "--all"])
        commands.append(["codegraph", "sync", "."])
    if test_scopes:
        commands.append([sys.executable, "-m", "pytest", "-q", *test_scopes])
    return {
        "schema_version": 1,
        "command": "audit",
        "scopes": scopes,
        "commands": [{"cmd": command} for command in commands],
        "verdict": "WARN" if not scopes else "PASS",
    }


def run_audit(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    report = build_audit_plan(scopes=_scope_list(args.scope))
    if args.run:
        results = [_command_summary(_run_command(item["cmd"], cwd=repo), include_stdout=args.include_stdout) for item in report["commands"]]
        report["results"] = results
        if any(result["returncode"] != 0 for result in results):
            report["verdict"] = "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["verdict"] == "FAIL" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO))
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Run the standard project health snapshot")
    doctor.add_argument("--complexity-target", default="backend")
    doctor.add_argument("--max-findings", type=int, default=80)
    doctor.add_argument("--baseline", default=None)
    doctor.add_argument("--fail-on-dirty-worktree", action="store_true")
    doctor.add_argument("--skip-test-tool", action="store_true")
    doctor.add_argument("--skip-universe", action="store_true")
    doctor.add_argument("--skip-storage-payload", action="store_true")
    doctor.add_argument("--skip-stage-opt", action="store_true")
    doctor.add_argument("--storage-max-findings", type=int, default=20)
    doctor.set_defaults(func=run_doctor)

    preflight = subparsers.add_parser("preflight", help="Emit task-specific gates before editing")
    preflight.add_argument("task_arg", nargs="?")
    preflight.add_argument("scope_arg", nargs="*")
    preflight.add_argument("--task", default=None)
    preflight.add_argument("--scope", action="append", default=[])
    preflight.set_defaults(func=run_preflight)

    worktree = subparsers.add_parser("worktree", help="Classify dirty worktree entries into review buckets")
    worktree.add_argument("--bucket", default=None)
    worktree.add_argument("--format", choices=["json", "markdown"], default="json")
    worktree.set_defaults(func=run_worktree)

    docs = subparsers.add_parser("docs", help="Audit docs graph and docs-cleanup worktree slice")
    docs.add_argument("--format", choices=["json", "markdown"], default="json")
    docs.set_defaults(func=run_docs)

    audit = subparsers.add_parser("audit", help="Build or run a scoped audit command plan")
    audit.add_argument("--scope", action="append", default=[])
    audit.add_argument("--run", action="store_true")
    audit.add_argument("--include-stdout", action="store_true")
    audit.set_defaults(func=run_audit)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
