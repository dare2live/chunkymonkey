#!/usr/bin/env python3
"""Audit code/doc consumers for storage-retention inventory tables.

This is a static, read-only guard for the DB cleanup path. It does not authorize
deletes; it makes unknown consumer placeholders and live references visible
before a retention policy can claim an obsolete table is safe to clean.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.storage_retention import TableInventoryRule, load_storage_retention_policy


REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "backend" / "config" / "storage_retention.yaml"
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "htmlcov",
    "node_modules",
}
SKIP_DIR_GLOBS = tuple(f"!{skip_dir}/**" for skip_dir in sorted(SKIP_DIRS))
SELF_PATHS = {
    "backend/config/storage_retention.yaml",
    "backend/scripts/audit_storage_retention_consumers.py",
    "backend/tests/scripts/test_audit_storage_retention_consumers.py",
}


@dataclass(frozen=True)
class Reference:
    path: str
    line: int
    category: str
    snippet: str


@dataclass(frozen=True)
class ConsumerAuditItem:
    table: str
    classification: str
    owner: str
    consumers: list[str]
    unknown_consumers: list[str]
    runtime_reference_count: int
    references: list[Reference]
    verdict: str


def _as_list(values: Iterable[str]) -> list[str]:
    return [str(value) for value in values]


def _unknown_consumers(rule: TableInventoryRule) -> list[str]:
    return [value for value in rule.consumers if str(value).startswith("unknown_pending")]


def _rel(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _reference_category(rel_path: str) -> str:
    if rel_path in SELF_PATHS:
        return "policy_self"
    if rel_path.startswith("backend/tests/"):
        return "test"
    if rel_path.startswith("backend/config/"):
        return "active_config"
    if rel_path.startswith(("backend/scripts/", "backend/services/", "scripts/", "assets/")):
        return "runtime_code"
    if rel_path.startswith("analysis/"):
        return "analysis_history"
    if rel_path.startswith("docs/") or rel_path in {"goal.md", "PROJECT_INDEX.md", "SESSION_HANDOFF.md"}:
        return "project_docs"
    return "other"


def _scan_reference_index(repo: Path, tables: Iterable[str]) -> dict[str, list[Reference]]:
    table_names = sorted({str(table) for table in tables if table}, key=len, reverse=True)
    refs: dict[str, list[Reference]] = {table: [] for table in table_names}
    if not table_names:
        return refs
    pattern = (
        r"(?<![A-Za-z0-9_])("
        + "|".join(re.escape(table) for table in table_names)
        + r")(?![A-Za-z0-9_])"
    )
    command = ["rg", "--json", "--pcre2", "--glob", "!CLAUDE.md"]
    for glob in SKIP_DIR_GLOBS:
        command.extend(["--glob", glob])
    command.extend([pattern, "."])
    result = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.strip() or "rg consumer scan failed")
    for raw_line in result.stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        _append_rg_event_refs(refs, event)
    return refs


def _scan_like_references(repo: Path, rule: TableInventoryRule) -> list[Reference]:
    if not rule.table_like:
        return []
    like_pattern = re.escape(rule.table_like).replace("%", "[A-Za-z0-9_]*")
    pattern = rf"(?<![A-Za-z0-9_])({like_pattern})(?![A-Za-z0-9_])"
    command = ["rg", "--json", "--pcre2", "--glob", "!CLAUDE.md"]
    for glob in SKIP_DIR_GLOBS:
        command.extend(["--glob", glob])
    command.extend([pattern, "."])
    result = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.strip() or "rg consumer scan failed")
    refs: list[Reference] = []
    excluded = set(rule.exclude_tables)
    for raw_line in result.stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        _append_like_event_refs(refs, event, excluded=excluded)
    return refs


def _append_rg_event_refs(refs: dict[str, list[Reference]], event: dict[str, Any]) -> None:
    if event.get("type") != "match":
        return
    data = event.get("data") or {}
    path_text = str((data.get("path") or {}).get("text") or "").removeprefix("./")
    line_no = int(data.get("line_number") or 0)
    snippet = str((data.get("lines") or {}).get("text") or "").strip()[:240]
    for submatch in data.get("submatches") or []:
        table = str(((submatch.get("match") or {}).get("text")) or "")
        table_refs = refs.get(table)
        if table_refs is None:
            continue
        table_refs.append(
            Reference(
                path=path_text,
                line=line_no,
                category=_reference_category(path_text),
                snippet=snippet,
            )
        )


def _append_like_event_refs(refs: list[Reference], event: dict[str, Any], *, excluded: set[str]) -> None:
    if event.get("type") != "match":
        return
    data = event.get("data") or {}
    path_text = str((data.get("path") or {}).get("text") or "").removeprefix("./")
    line_no = int(data.get("line_number") or 0)
    snippet = str((data.get("lines") or {}).get("text") or "").strip()[:240]
    for submatch in data.get("submatches") or []:
        table = str(((submatch.get("match") or {}).get("text")) or "")
        if not excluded.isdisjoint((table,)):
            continue
        refs.append(
            Reference(
                path=path_text,
                line=line_no,
                category=_reference_category(path_text),
                snippet=snippet,
            )
        )


def _should_audit_rule(rule: TableInventoryRule) -> bool:
    return bool(rule.table or rule.table_like)


def _audit_item(rule: TableInventoryRule, refs: list[Reference]) -> ConsumerAuditItem:
    table = str(rule.table or rule.table_like)
    runtime_count = sum(1 for ref in refs if ref.category == "runtime_code")
    unknown = _unknown_consumers(rule)
    verdict = "FAIL" if unknown else "PASS"
    return ConsumerAuditItem(
        table=table,
        classification=rule.classification,
        owner=rule.owner,
        consumers=_as_list(rule.consumers),
        unknown_consumers=unknown,
        runtime_reference_count=runtime_count,
        references=refs,
        verdict=verdict,
    )


def build_report(*, repo: Path = REPO, config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    policy = load_storage_retention_policy(config_path)
    rules = [rule for rule in policy.table_inventory if _should_audit_rule(rule)]
    exact_rules = [rule for rule in rules if rule.table]
    refs_by_table = _scan_reference_index(repo, [str(rule.table) for rule in exact_rules])
    items = [
        _audit_item(
            rule,
            refs_by_table.get(str(rule.table), []) if rule.table else _scan_like_references(repo, rule),
        )
        for rule in rules
    ]
    fail_count = sum(1 for item in items if item.verdict == "FAIL")
    runtime_ref_tables = [item.table for item in items if item.runtime_reference_count > 0]
    return {
        "schema_version": 1,
        "command": "audit_storage_retention_consumers",
        "repo": str(repo),
        "config_path": str(config_path),
        "verdict": "FAIL" if fail_count else "PASS",
        "summary": {
            "audited_tables": len(items),
            "fail_count": fail_count,
            "runtime_ref_table_count": len(runtime_ref_tables),
            "runtime_ref_tables": runtime_ref_tables,
        },
        "items": [
            {
                **{key: value for key, value in asdict(item).items() if key != "references"},
                "references": [asdict(ref) for ref in item.references],
            }
            for item in items
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Storage Retention Consumer Audit",
        "",
        f"- Verdict: `{report['verdict']}`",
        f"- Audited tables: `{report['summary']['audited_tables']}`",
        f"- Runtime-ref tables: `{report['summary']['runtime_ref_table_count']}`",
        "",
        "| Table | Verdict | Runtime refs | Unknown consumers |",
        "|---|---|---:|---|",
    ]
    for item in report["items"]:
        unknown = ", ".join(item["unknown_consumers"]) or "-"
        lines.append(
            f"| `{item['table']}` | `{item['verdict']}` | {item['runtime_reference_count']} | {unknown} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    report = build_report(repo=args.repo.resolve(), config_path=args.config.resolve())
    if args.format == "markdown":
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
