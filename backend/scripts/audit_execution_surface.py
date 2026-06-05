#!/usr/bin/env python3
"""Audit automation and execution surfaces for stale local entrypoints.

The goal is to catch the class of failure where a script is deleted or retired
but launchd, cron, installers, dashboards, registries, or evidence profiles
still point at it.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "backend" / "config" / "test_tool_registry.yaml"
MOTH_PROFILE_PATH = REPO / ".moth" / "profile.yaml"

PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"("
    r"(?:/[^\s'\"`<>|]+/chunkymonkey/)?"
    r"(?:scripts|backend/scripts|backend/services|backend/config|configs|gcp|\.moth)"
    r"/[^\s'\"`),;]+"
    r")"
)
STRIP_PATH_CHARS = "`'\"[](){}:.,，。；;、\\"
SKIP_PATH_CHARS = {"$", "{", "}", "<", ">", "*"}
RETIRED_LABELS = {
    "com.chunkymonkey.gcp-cost-tracker",
    "com.chunkymonkey.phase5-monitor",
}
RETIRED_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deleted_gcp_policy", re.compile(r"\bgcp_policy\.yaml\b")),
    ("deleted_gcp_guard", re.compile(r"\bgcp_guard\b|scripts/lib/gcp_guard\.sh")),
    ("deleted_gcp_cost_tracker", re.compile(r"gcp-cost-tracker|gcp/cost_tracker\.sh|gcp_cost_summary")),
    ("deleted_phase5_monitor", re.compile(r"phase5-monitor|monitor_phase5_gcp|watch_phase5")),
    ("deleted_gcs_sync", re.compile(r"\bgcs_sync\b|sync_kline_from_gcs")),
    ("retired_latch", re.compile(r"CHUNKYMONKEY_GCP|retired-GCP|retired GCP|double-latch|双 latch")),
    ("provider_bound_vm_status", re.compile(r"\bvm_status\b|VM 状态|VM 上次")),
    ("phase5_chain_state", re.compile(r"phase5_chain|/tmp/phase5_retrain_mac\.log|lgbm_phase5_")),
    ("legacy_claude_authority", re.compile(r"CLAUDE\.md")),
)
TOKEN_SCAN_ALLOWLIST: dict[str, set[str]] = {
    "backend/config/test_tool_registry.yaml": {"legacy_claude_authority"},
    ".moth/profile.yaml": {"legacy_claude_authority"},
}


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    path: str
    message: str
    evidence: str | None = None


def _rel(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _normalize_candidate(raw: str, *, repo: Path) -> Path | None:
    token = raw.strip().strip(STRIP_PATH_CHARS)
    if not token or any(char in token for char in SKIP_PATH_CHARS):
        return None
    if "/chunkymonkey/" in token:
        marker = "/chunkymonkey/"
        token = token.split(marker, 1)[1]
    path = Path(token)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] in {"scripts", "backend", "configs", "gcp", ".moth"}:
        return repo / path
    return None


def _is_token_allowed(path: Path, token_id: str, *, repo: Path) -> bool:
    return token_id in TOKEN_SCAN_ALLOWLIST.get(_rel(path, repo), set())


def _surface_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for rel_path in (
        "scripts/chunkyctl",
        "scripts/cm.sh",
        "scripts/daily_update.sh",
        "scripts/post_retrain_pipeline.sh",
    ):
        candidate = repo / rel_path
        if candidate.exists():
            files.append(candidate)
    for pattern in (
        "configs/cron/*.txt",
        "configs/cron/*.sh",
        "configs/launchd/*.sh",
        "scripts/install*.sh",
        "scripts/cm_resume.sh",
        "scripts/session*.sh",
        "scripts/workflow_checkpoint.sh",
        "backend/scripts/*dashboard*.py",
        "backend/scripts/audit_delivery_readiness.py",
    ):
        files.extend(sorted(repo.glob(pattern)))
    if REGISTRY_PATH.exists():
        files.append(repo / "backend/config/test_tool_registry.yaml")
    if MOTH_PROFILE_PATH.exists():
        files.append(repo / ".moth/profile.yaml")
    return sorted({path for path in files if path.exists() and path.is_file()})


def _check_path_exists(candidate: Path, *, source: Path, line_no: int | None, repo: Path) -> Finding | None:
    if candidate.exists():
        return None
    evidence = f"line {line_no}" if line_no is not None else None
    return Finding(
        severity="FAIL",
        check="missing_local_entrypoint",
        path=_rel(source, repo),
        message=f"references missing local path `{_rel(candidate, repo)}`",
        evidence=evidence,
    )


def _scan_text_surface(path: Path, *, repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        for match in PATH_RE.finditer(line):
            candidate = _normalize_candidate(match.group(1), repo=repo)
            if candidate is None:
                continue
            finding = _check_path_exists(candidate, source=path, line_no=line_no, repo=repo)
            if finding is not None:
                findings.append(finding)
        for token_id, pattern in RETIRED_TOKEN_PATTERNS:
            if _is_token_allowed(path, token_id, repo=repo):
                continue
            if not pattern.search(line):
                continue
            findings.append(
                Finding(
                    severity="FAIL",
                    check="retired_execution_token",
                    path=_rel(path, repo),
                    message=f"active execution surface contains retired token `{token_id}`",
                    evidence=f"line {line_no}",
                )
            )
    return findings


def _scan_launchd_plists(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    for plist_path in sorted((repo / "configs" / "launchd").glob("*.plist")):
        try:
            payload = plistlib.loads(plist_path.read_bytes())
        except Exception as exc:
            findings.append(
                Finding(
                    severity="FAIL",
                    check="invalid_launchd_plist",
                    path=_rel(plist_path, repo),
                    message=f"plist could not be parsed: {exc}",
                )
            )
            continue
        label = str(payload.get("Label") or "")
        if label in RETIRED_LABELS:
            findings.append(
                Finding(
                    severity="FAIL",
                    check="retired_launchd_label",
                    path=_rel(plist_path, repo),
                    message=f"retired launchd label `{label}` remains in repo automation",
                )
            )
        candidates: list[str] = []
        program = payload.get("Program")
        if isinstance(program, str):
            candidates.append(program)
        args = payload.get("ProgramArguments")
        if isinstance(args, list):
            candidates.extend(str(item) for item in args if isinstance(item, str))
        for item in candidates:
            candidate = _normalize_candidate(item, repo=repo)
            if candidate is None:
                continue
            finding = _check_path_exists(candidate, source=plist_path, line_no=None, repo=repo)
            if finding is not None:
                findings.append(finding)
    return findings


def _scan_registry_paths(repo: Path) -> list[Finding]:
    path = repo / "backend" / "config" / "test_tool_registry.yaml"
    if not path.exists():
        return []
    raw = _load_yaml(path)
    findings: list[Finding] = []
    for tool in raw.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("status") != "active":
            continue
        tool_id = str(tool.get("id") or "<unknown>")
        for raw_path in tool.get("paths") or []:
            candidate = Path(str(raw_path))
            if not candidate.is_absolute():
                candidate = repo / candidate
            if candidate.exists():
                continue
            findings.append(
                Finding(
                    severity="FAIL",
                    check="active_registry_missing_path",
                    path=_rel(path, repo),
                    message=f"active tool `{tool_id}` references missing path `{raw_path}`",
                )
            )
    return findings


def _scan_moth_evidence_paths(repo: Path) -> list[Finding]:
    path = repo / ".moth" / "profile.yaml"
    if not path.exists():
        return []
    raw = _load_yaml(path)
    findings: list[Finding] = []
    evidence_paths = raw.get("evidence_paths") or {}
    if not isinstance(evidence_paths, dict):
        return findings
    for key, value in evidence_paths.items():
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            candidate = repo / candidate
        if candidate.exists():
            continue
        findings.append(
            Finding(
                severity="FAIL",
                check="moth_missing_evidence_path",
                path=_rel(path, repo),
                message=f"Moth evidence path `{key}` references missing path `{value}`",
            )
        )
    return findings


def _scan_live_launch_agents(repo: Path, launch_agents_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    for label in sorted(RETIRED_LABELS):
        plist_path = launch_agents_dir / f"{label}.plist"
        if not plist_path.exists():
            continue
        findings.append(
            Finding(
                severity="FAIL",
                check="live_retired_launchd_agent",
                path=str(plist_path),
                message=f"retired launchd agent `{label}` is still installed in user LaunchAgents",
            )
        )
    for plist_path in sorted(launch_agents_dir.glob("com.chunkymonkey.*.plist")):
        try:
            payload = plistlib.loads(plist_path.read_bytes())
        except Exception:
            continue
        args = payload.get("ProgramArguments")
        if not isinstance(args, list):
            continue
        for item in args:
            if not isinstance(item, str):
                continue
            candidate = _normalize_candidate(item, repo=repo)
            if candidate is None:
                continue
            finding = _check_path_exists(candidate, source=plist_path, line_no=None, repo=repo)
            if finding is not None:
                findings.append(finding)
    return findings


def build_report(*, repo: Path, include_live_launchd: bool = False, launch_agents_dir: Path | None = None) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    findings: list[Finding] = []
    findings.extend(_scan_launchd_plists(repo))
    for surface in _surface_files(repo):
        findings.extend(_scan_text_surface(surface, repo=repo))
    findings.extend(_scan_registry_paths(repo))
    findings.extend(_scan_moth_evidence_paths(repo))
    if include_live_launchd:
        findings.extend(_scan_live_launch_agents(repo, launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")))
    verdict = "FAIL" if any(item.severity == "FAIL" for item in findings) else ("WARN" if findings else "PASS")
    return {
        "schema_version": 1,
        "command": "audit_execution_surface",
        "repo": str(repo),
        "verdict": verdict,
        "finding_count": len(findings),
        "findings": [asdict(item) for item in findings],
        "checked": {
            "launchd_plists": str(repo / "configs" / "launchd"),
            "surface_files": [_rel(path, repo) for path in _surface_files(repo)],
            "test_registry": _rel(repo / "backend/config/test_tool_registry.yaml", repo),
            "moth_profile": _rel(repo / ".moth/profile.yaml", repo),
            "live_launchd": bool(include_live_launchd),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Execution Surface Audit",
        "",
        f"| Verdict | {report['verdict']} |",
        f"| Findings | {report['finding_count']} |",
        "",
    ]
    if report["findings"]:
        lines.append("## Findings")
        for finding in report["findings"]:
            evidence = f" ({finding['evidence']})" if finding.get("evidence") else ""
            lines.append(f"- `{finding['severity']}` `{finding['check']}` `{finding['path']}`{evidence}: {finding['message']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--format", choices=("json", "markdown", "text"), default="text")
    parser.add_argument("--include-live-launchd", action="store_true")
    parser.add_argument("--launch-agents-dir", default=None)
    args = parser.parse_args()

    launch_agents_dir = Path(args.launch_agents_dir).expanduser() if args.launch_agents_dir else None
    report = build_report(
        repo=Path(args.repo),
        include_live_launchd=args.include_live_launchd,
        launch_agents_dir=launch_agents_dir,
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(render_markdown(report))
    else:
        print(f"{report['verdict']}: {report['finding_count']} execution-surface finding(s)")
        for finding in report["findings"]:
            evidence = f" {finding['evidence']}" if finding.get("evidence") else ""
            print(f"- {finding['severity']} {finding['check']} {finding['path']}{evidence}: {finding['message']}")
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
