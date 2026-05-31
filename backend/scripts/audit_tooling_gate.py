#!/usr/bin/env python3
"""Emit JSON for the CodeGraph + complexity-optimizer local gate."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEFAULT_COMPLEXITY_SCRIPT = (
    Path(os.environ["CHUNKYMONKEY_COMPLEXITY_SCRIPT"])
    if os.environ.get("CHUNKYMONKEY_COMPLEXITY_SCRIPT")
    else Path.home() / ".agents/skills/complexity-optimizer/scripts/analyze_complexity.py"
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
STATUS_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ]+):\s*(?P<value>.+)$")
TABLE_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*)\s+(?P<value>[\d,]+)$")
PENDING_RE = re.compile(r"^(?P<kind>Added|Modified|Deleted|Renamed):\s*(?P<count>[\d,]+)\s+files?$")
COMPLEXITY_HEADER_RE = re.compile(r"^##\s+(?P<severity>HIGH|MEDIUM|LOW)\s+(?P<kind>.+)$")
COMPLEXITY_LOCATION_RE = re.compile(r"^- Location:\s+`(?P<path>.+):(?P<line>\d+)`$")


@dataclass(frozen=True)
class ComplexityFinding:
    severity: str
    kind: str
    path: str
    line: int
    finding: str
    suggestion: str


@dataclass(frozen=True)
class GitStatusEntry:
    path: str
    index_status: str
    worktree_status: str
    raw_status: str


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _parse_int(text: str) -> int:
    return int(text.replace(",", ""))


def _section_name(line: str) -> str | None:
    sections = {
        "Index Statistics:": "index",
        "Nodes by Kind:": "nodes_by_kind",
        "Files by Language:": "files_by_language",
        "Pending Changes:": "pending",
    }
    return sections.get(line)


def _status_key(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def parse_codegraph_status(text: str) -> dict[str, Any]:
    """Parse `codegraph status` text into stable JSON-friendly fields."""
    status: dict[str, Any] = {
        "project": None,
        "index": {},
        "nodes_by_kind": {},
        "files_by_language": {},
        "pending": {"total": 0, "sync_required": False},
    }
    section: str | None = None
    for raw_line in _strip_ansi(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        next_section = _section_name(line)
        if next_section:
            section = next_section
            continue
        if line.startswith("Project:"):
            status["project"] = line.split(":", 1)[1].strip()
            continue
        if section == "index":
            match = STATUS_VALUE_RE.match(line)
            if not match:
                continue
            key = _status_key(match.group("key"))
            value = match.group("value").strip()
            status["index"][key] = _parse_int(value) if value.replace(",", "").isdigit() else value
            continue
        if section in {"nodes_by_kind", "files_by_language"}:
            match = TABLE_VALUE_RE.match(line)
            if match:
                status[section][match.group("key")] = _parse_int(match.group("value"))
            continue
        if section == "pending":
            match = PENDING_RE.match(line)
            if match:
                status["pending"][match.group("kind").lower()] = _parse_int(match.group("count"))
            elif "No pending" in line:
                status["pending"]["none"] = True
    total = sum(
        value
        for key, value in status["pending"].items()
        if key not in {"total", "sync_required", "none"} and isinstance(value, int)
    )
    status["pending"]["total"] = total
    status["pending"]["sync_required"] = total > 0
    return status


def parse_git_status_short(text: str) -> dict[str, Any]:
    """Parse `git status --short` into a compact JSON summary."""
    entries: list[GitStatusEntry] = []
    counts: dict[str, int] = {
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "modified": 0,
        "deleted": 0,
        "added": 0,
        "renamed": 0,
        "copied": 0,
        "unmerged": 0,
    }
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        raw_status = raw_line[:2]
        path = raw_line[3:].strip() if len(raw_line) > 3 else ""
        if raw_status == "??":
            index_status = "?"
            worktree_status = "?"
        else:
            index_status = raw_status[0]
            worktree_status = raw_status[1]
        entry = GitStatusEntry(
            path=path,
            index_status=index_status,
            worktree_status=worktree_status,
            raw_status=raw_status,
        )
        entries.append(entry)
        status_chars = {index_status, worktree_status}
        if index_status not in {" ", "?"}:
            counts["staged"] += 1
        if worktree_status not in {" ", "?"}:
            counts["unstaged"] += 1
        if "?" in status_chars:
            counts["untracked"] += 1
        if "M" in status_chars:
            counts["modified"] += 1
        if "D" in status_chars:
            counts["deleted"] += 1
        if "A" in status_chars:
            counts["added"] += 1
        if "R" in status_chars:
            counts["renamed"] += 1
        if "C" in status_chars:
            counts["copied"] += 1
        if status_chars & {"U"} or raw_status in {"AA", "DD", "AU", "UD", "UA", "DU"}:
            counts["unmerged"] += 1
    return {
        "clean": not entries,
        "total": len(entries),
        "counts": {key: value for key, value in counts.items() if value},
        "entries": [asdict(entry) for entry in sorted(entries, key=lambda item: item.path)],
    }


def _finding_from_dict(raw: dict[str, Any]) -> ComplexityFinding:
    return ComplexityFinding(
        severity=str(raw["severity"]),
        kind=str(raw["kind"]),
        path=str(raw["path"]),
        line=int(raw["line"]),
        finding=str(raw.get("finding") or ""),
        suggestion=str(raw.get("suggestion") or ""),
    )


def parse_complexity_markdown(text: str) -> list[ComplexityFinding]:
    """Parse complexity-optimizer markdown output into structured findings."""
    findings: list[ComplexityFinding] = []
    severity: str | None = None
    kind: str | None = None
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = COMPLEXITY_HEADER_RE.match(line)
        if header:
            if current:
                findings.append(_finding_from_dict(current))
            severity = header.group("severity")
            kind = header.group("kind").strip()
            current = None
            continue
        location = COMPLEXITY_LOCATION_RE.match(line)
        if location and severity and kind:
            if current:
                findings.append(_finding_from_dict(current))
            current = {
                "severity": severity,
                "kind": kind,
                "path": location.group("path"),
                "line": int(location.group("line")),
                "finding": "",
                "suggestion": "",
            }
            continue
        if not current:
            continue
        if line.startswith("- Finding:"):
            current["finding"] = line.split(":", 1)[1].strip()
        elif line.startswith("- Suggestion:"):
            current["suggestion"] = line.split(":", 1)[1].strip()
    if current:
        findings.append(_finding_from_dict(current))
    return findings


def _finding_sort_key(finding: ComplexityFinding) -> tuple[str, int, str, str]:
    return (finding.path, finding.line, finding.severity, finding.kind)


def _finding_identity(finding: ComplexityFinding, mode: str) -> tuple[Any, ...]:
    if mode == "path_kind_message":
        return (finding.severity, finding.kind, finding.path, finding.finding)
    return (finding.severity, finding.kind, finding.path, finding.line, finding.finding)


def _severity_counts(findings: list[ComplexityFinding]) -> dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return {key: value for key, value in counts.items() if value}


def _findings_by_identity(findings: list[ComplexityFinding], mode: str) -> dict[tuple[Any, ...], list[ComplexityFinding]]:
    grouped: dict[tuple[Any, ...], list[ComplexityFinding]] = {}
    for finding in findings:
        grouped.setdefault(_finding_identity(finding, mode), []).append(finding)
    return grouped


def diff_complexity_findings(
    current: list[ComplexityFinding],
    baseline: list[ComplexityFinding],
    *,
    identity_mode: str = "path_kind_message",
) -> dict[str, Any]:
    current_by_key = _findings_by_identity(current, identity_mode)
    baseline_by_key = _findings_by_identity(baseline, identity_mode)
    new_findings: list[ComplexityFinding] = []
    resolved_findings: list[ComplexityFinding] = []
    unchanged_findings: list[ComplexityFinding] = []
    for key in set(current_by_key) | set(baseline_by_key):
        current_items = current_by_key.get(key, [])
        baseline_items = baseline_by_key.get(key, [])
        unchanged_count = min(len(current_items), len(baseline_items))
        unchanged_findings.extend(current_items[:unchanged_count])
        new_findings.extend(current_items[unchanged_count:])
        resolved_findings.extend(baseline_items[unchanged_count:])
    new_findings.sort(key=_finding_sort_key)
    resolved_findings.sort(key=_finding_sort_key)
    unchanged_findings.sort(key=_finding_sort_key)
    return {
        "baseline_count": len(baseline),
        "current_count": len(current),
        "new_count": len(new_findings),
        "resolved_count": len(resolved_findings),
        "unchanged_count": len(unchanged_findings),
        "new_high_count": sum(1 for finding in new_findings if finding.severity == "HIGH"),
        "new_findings": [asdict(finding) for finding in new_findings],
        "resolved_findings": [asdict(finding) for finding in resolved_findings],
        "unchanged_findings": [asdict(finding) for finding in unchanged_findings],
    }


def complexity_diff_report(
    current: list[ComplexityFinding],
    baseline: list[ComplexityFinding],
    *,
    baseline_status: str,
    identity_mode: str = "path_kind_message",
) -> dict[str, Any]:
    if baseline_status != "loaded":
        return {
            "status": "baseline_unavailable",
            "baseline_status": baseline_status,
            "baseline_count": len(baseline),
            "current_count": len(current),
            "new_count": 0,
            "resolved_count": 0,
            "unchanged_count": 0,
            "new_high_count": 0,
            "new_findings": [],
            "resolved_findings": [],
            "unchanged_findings": [],
            "unclassified_count": len(current),
            "unclassified_high_count": sum(1 for finding in current if finding.severity == "HIGH"),
            "note": "Baseline is not loaded; current findings are unclassified, not new regressions.",
        }
    diff = diff_complexity_findings(current, baseline, identity_mode=identity_mode)
    diff.update(
        {
            "status": "compared",
            "baseline_status": baseline_status,
            "unclassified_count": 0,
            "unclassified_high_count": 0,
        }
    )
    return diff


def _load_baseline(path: Path | None) -> tuple[list[ComplexityFinding], str]:
    if not path:
        return [], "not_configured"
    if not path.exists():
        return [], "missing"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    raw_findings = loaded.get("findings") if isinstance(loaded, dict) else loaded
    if isinstance(loaded, dict) and raw_findings is None:
        raw_findings = loaded.get("complexity", {}).get("findings", [])
    if not isinstance(raw_findings, list):
        raise ValueError(f"{path} does not contain a findings list")
    return [_finding_from_dict(item) for item in raw_findings], "loaded"


def _run_command(cmd: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _read_or_run_codegraph(repo: Path, status_file: Path | None) -> str:
    if status_file:
        return status_file.read_text(encoding="utf-8")
    return _run_command(["codegraph", "status", str(repo)], cwd=repo)


def _read_or_run_git_status(repo: Path, status_file: Path | None) -> str:
    if status_file:
        return status_file.read_text(encoding="utf-8")
    return _run_command(["git", "status", "--short"], cwd=repo)


def _read_or_run_complexity(
    repo: Path,
    markdown_file: Path | None,
    *,
    target: Path,
    script_path: Path,
    max_findings: int,
) -> str:
    if markdown_file:
        return markdown_file.read_text(encoding="utf-8")
    if not script_path.exists():
        raise FileNotFoundError(f"complexity script not found: {script_path}")
    return _run_command(
        [
            sys.executable,
            str(script_path),
            str(target),
            "--format",
            "markdown",
            "--max-findings",
            str(max_findings),
        ],
        cwd=repo,
    )


def build_tooling_gate_report(
    *,
    repo: Path,
    codegraph_status_text: str,
    complexity_markdown: str,
    git_status_text: str = "",
    baseline_path: Path | None = None,
    identity_mode: str = "path_kind_message",
    complexity_target: Path | None = None,
    fail_on_dirty_worktree: bool = False,
) -> dict[str, Any]:
    git_status = parse_git_status_short(git_status_text)
    codegraph_status = parse_codegraph_status(codegraph_status_text)
    current_findings = parse_complexity_markdown(complexity_markdown)
    baseline_findings, baseline_status = _load_baseline(baseline_path)
    diff = complexity_diff_report(
        current_findings,
        baseline_findings,
        baseline_status=baseline_status,
        identity_mode=identity_mode,
    )
    verdict = "PASS"
    if codegraph_status["pending"]["sync_required"] or diff["new_high_count"]:
        verdict = "FAIL"
    elif fail_on_dirty_worktree and not git_status["clean"]:
        verdict = "FAIL"
    elif baseline_status != "loaded" and current_findings:
        verdict = "WARN"
    return {
        "schema_version": 1,
        "repo": str(repo),
        "verdict": verdict,
        "git_status": git_status,
        "codegraph": codegraph_status,
        "complexity": {
            "target": str(complexity_target or repo),
            "identity_mode": identity_mode,
            "baseline": {
                "path": str(baseline_path) if baseline_path else None,
                "status": baseline_status,
            },
            "severity_counts": _severity_counts(current_findings),
            "findings": [asdict(finding) for finding in sorted(current_findings, key=_finding_sort_key)],
            "diff": diff,
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--git-status-file", default=None)
    parser.add_argument("--codegraph-status-file", default=None)
    parser.add_argument("--complexity-markdown-file", default=None)
    parser.add_argument("--complexity-target", default="backend")
    parser.add_argument("--complexity-script", default=str(DEFAULT_COMPLEXITY_SCRIPT))
    parser.add_argument("--max-findings", type=int, default=220)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--write-baseline", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--fail-on-dirty-worktree", action="store_true")
    parser.add_argument(
        "--identity-mode",
        choices=("path_line_kind_message", "path_kind_message"),
        default="path_kind_message",
    )
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    complexity_target = Path(args.complexity_target)
    if not complexity_target.is_absolute():
        complexity_target = repo / complexity_target
    git_status_text = _read_or_run_git_status(
        repo,
        Path(args.git_status_file).expanduser().resolve() if args.git_status_file else None,
    )
    codegraph_text = _read_or_run_codegraph(
        repo,
        Path(args.codegraph_status_file).expanduser().resolve() if args.codegraph_status_file else None,
    )
    complexity_text = _read_or_run_complexity(
        repo,
        Path(args.complexity_markdown_file).expanduser().resolve() if args.complexity_markdown_file else None,
        target=complexity_target,
        script_path=Path(args.complexity_script).expanduser().resolve(),
        max_findings=args.max_findings,
    )
    baseline_path = Path(args.baseline).expanduser().resolve() if args.baseline else None
    report = build_tooling_gate_report(
        repo=repo,
        codegraph_status_text=codegraph_text,
        complexity_markdown=complexity_text,
        git_status_text=git_status_text,
        baseline_path=baseline_path,
        identity_mode=args.identity_mode,
        complexity_target=complexity_target,
        fail_on_dirty_worktree=args.fail_on_dirty_worktree,
    )
    if args.write_baseline:
        _write_json(
            Path(args.write_baseline).expanduser().resolve(),
            {"schema_version": 1, "findings": report["complexity"]["findings"]},
        )
    if args.output:
        _write_json(Path(args.output).expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
