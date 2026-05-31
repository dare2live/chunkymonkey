#!/usr/bin/env python3
"""Audit whether test tools still match the current architecture."""
from __future__ import annotations

import argparse
import ast
import configparser
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "test_tool_registry.yaml"
PYTEST_INI = REPO / "pytest.ini"

REQUIRED_TOOL_FIELDS = (
    "id",
    "paths",
    "owner_module",
    "scope",
    "runner",
    "status",
    "evidence_level",
    "truth_source",
    "risk_reason",
    "replacement",
    "last_verified",
)
VALID_STATUS = {"active", "needs_refactor", "quarantined", "deprecated", "delete_candidate"}
VALID_EVIDENCE_LEVEL = {
    "trusted_current",
    "trusted_with_scope",
    "legacy_guard",
    "quarantined",
    "invalid",
}
OPT_IN_SCOPES = {"realdb", "perf", "network", "gcp", "slow"}
DEFAULT_TESTPATHS = ("backend/tests",)
DEFAULT_REQUIRED_EXCLUDES = {"realdb", "perf", "network", "gcp", "slow"}
DEFAULT_REQUIRED_EXCLUDES_ORDERED = tuple(sorted(DEFAULT_REQUIRED_EXCLUDES))
VALID_SELECTED_REGISTRY_OWNER = {"off", "warn", "fail"}
DEFAULT_UNREGISTERED_SELECTED_SAMPLE_LIMIT = 20
UNREGISTERED_SLICE_SAMPLE_LIMIT = 5
ALLOWED_DIM_ACTIVE_HINTS = (
    "audit-fixture",
    "code-to-name",
    "cache",
    "schema",
    "metadata",
    "data-sync",
    "limited",
)
SQLITE_EXCEPTION_HINTS = ("optuna", "storage", "sqlite storage")
TEST_FILE_SUFFIXES = {".py", ".sh"}


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    path: str | None = None
    controller_action: str = "review"
    durable_owner: str = "docs/engineering_governance.md"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _read_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def load_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or CONFIG_PATH
    raw = _read_yaml(registry_path)
    tools = raw.get("tools")
    if not isinstance(tools, list):
        raise ValueError("test_tool_registry.yaml must contain tools: list")
    policy = raw.get("policy") or {}
    if not isinstance(policy, dict):
        raise ValueError("test_tool_registry.yaml policy must be a mapping")
    return {
        "path": registry_path,
        "version": raw.get("version"),
        "updated_at": raw.get("updated_at"),
        "policy": policy,
        "tools": tools,
    }


def _path_from_registry(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else REPO / path


def _iter_files_under(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return [
        p
        for p in path.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    ]


def _test_files_under(path: Path) -> list[Path]:
    return [
        p
        for p in _iter_files_under(path)
        if p.suffix in TEST_FILE_SUFFIXES and (p.name.startswith("test_") or "/tests/" in f"/{_rel(p)}")
    ]


def _is_test_like_path(path: Path) -> bool:
    rel = _rel(path)
    return rel.startswith("backend/tests/") or rel.startswith("tests/")


def _all_test_files() -> list[Path]:
    files: list[Path] = []
    for root in (REPO / "backend" / "tests", REPO / "tests"):
        files.extend(_test_files_under(root))
    return sorted(set(files))


def _marker_names(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set(re.findall(r"pytest\.mark\.([A-Za-z_]\w*)", text))
    markers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parent = node.value
        if (
            isinstance(parent, ast.Attribute)
            and parent.attr == "mark"
            and isinstance(parent.value, ast.Name)
            and parent.value.id == "pytest"
        ):
            markers.add(node.attr)
    return markers


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _tool_paths(tool: dict[str, Any]) -> list[Path]:
    raw_paths = tool.get("paths", [])
    if not isinstance(raw_paths, list):
        return []
    return [_path_from_registry(str(path)) for path in raw_paths]


def _covers(tool: dict[str, Any], path: Path) -> bool:
    for base in _tool_paths(tool):
        if base == path:
            return True
        if base.exists() and base.is_dir():
            try:
                path.relative_to(base)
                return True
            except ValueError:
                continue
    return False


def _covering_tools(tools: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    return [tool for tool in tools if _covers(tool, path)]


def _normalize_scopes(scope: Any) -> set[str]:
    if isinstance(scope, str):
        return {scope}
    if isinstance(scope, list):
        return {str(item) for item in scope}
    return set()


def _policy_string_list(policy: dict[str, Any], key: str, default: tuple[str, ...]) -> list[str]:
    raw = policy.get(key, list(default))
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        return []
    return list(raw)


def _default_testpaths(registry: dict[str, Any]) -> list[str]:
    return _policy_string_list(registry["policy"], "default_testpaths", DEFAULT_TESTPATHS)


def _default_excluded_markers(registry: dict[str, Any]) -> list[str]:
    return _policy_string_list(
        registry["policy"],
        "default_excluded_markers",
        tuple(DEFAULT_REQUIRED_EXCLUDES_ORDERED),
    )


def _is_default_testpath(path: Path, registry: dict[str, Any]) -> bool:
    rel = _rel(path)
    for raw in _default_testpaths(registry):
        normalized = raw.rstrip("/")
        if rel == normalized or rel.startswith(f"{normalized}/"):
            return True
    return False


def _missing_path_findings(tool_id: str, paths: list[Any]) -> list[Finding]:
    findings: list[Finding] = []
    for raw_path in paths:
        path = _path_from_registry(str(raw_path))
        if path.exists():
            continue
        findings.append(
            Finding(
                "FAIL",
                "registry_path",
                f"{tool_id} references missing path",
                path=_rel(path),
                controller_action="fix registry path or create the owned artifact",
            )
        )
    return findings


def _registry_findings(registry: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    policy = registry["policy"]
    if not _default_testpaths(registry):
        findings.append(
            Finding("FAIL", "registry_schema", "policy.default_testpaths must be a non-empty string list")
        )
    if not _default_excluded_markers(registry):
        findings.append(
            Finding(
                "FAIL",
                "registry_schema",
                "policy.default_excluded_markers must be a non-empty string list",
            )
        )
    selected_owner = str(policy.get("selected_registry_owner", "off"))
    if selected_owner not in VALID_SELECTED_REGISTRY_OWNER:
        findings.append(
            Finding(
                "FAIL",
                "registry_schema",
                "policy.selected_registry_owner must be one of: off, warn, fail",
            )
        )
    sample_limit = policy.get("unregistered_selected_sample_limit", DEFAULT_UNREGISTERED_SELECTED_SAMPLE_LIMIT)
    if not isinstance(sample_limit, int) or sample_limit < 1:
        findings.append(
            Finding(
                "FAIL",
                "registry_schema",
                "policy.unregistered_selected_sample_limit must be a positive integer",
            )
        )
    seen_ids: set[str] = set()
    for index, tool in enumerate(registry["tools"], start=1):
        if not isinstance(tool, dict):
            findings.append(Finding("FAIL", "registry_schema", f"tools[{index}] must be a mapping"))
            continue
        missing = [field for field in REQUIRED_TOOL_FIELDS if field not in tool]
        tool_id = str(tool.get("id", f"tools[{index}]"))
        if tool_id in seen_ids:
            findings.append(Finding("FAIL", "registry_schema", f"duplicate tool id: {tool_id}"))
        seen_ids.add(tool_id)
        if missing:
            findings.append(
                Finding("FAIL", "registry_schema", f"{tool_id} missing fields: {', '.join(missing)}")
            )
        if tool.get("status") not in VALID_STATUS:
            findings.append(Finding("FAIL", "registry_schema", f"{tool_id} has invalid status"))
        if tool.get("evidence_level") not in VALID_EVIDENCE_LEVEL:
            findings.append(Finding("FAIL", "registry_schema", f"{tool_id} has invalid evidence_level"))
        paths = tool.get("paths")
        if not isinstance(paths, list) or not paths:
            findings.append(Finding("FAIL", "registry_schema", f"{tool_id} paths must be a non-empty list"))
            continue
        findings.extend(_missing_path_findings(tool_id, paths))
    return findings


def _pytest_config_findings(registry: dict[str, Any], pytest_ini: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    pytest_ini = pytest_ini or PYTEST_INI
    if not pytest_ini.exists():
        return [Finding("FAIL", "pytest_config", "pytest.ini is missing", path="pytest.ini")]
    parser = configparser.ConfigParser()
    parser.read(pytest_ini)
    if "pytest" not in parser:
        return [Finding("FAIL", "pytest_config", "pytest.ini lacks [pytest]", path="pytest.ini")]
    addopts = parser["pytest"].get("addopts", "")
    testpaths = parser["pytest"].get("testpaths", "")
    for testpath in _default_testpaths(registry):
        if testpath not in testpaths:
            findings.append(
                Finding(
                    "FAIL",
                    "pytest_config",
                    f"default pytest testpaths must include {testpath}",
                    path="pytest.ini",
                )
            )
    for marker in _default_excluded_markers(registry):
        if f"not {marker}" not in addopts:
            findings.append(
                Finding(
                    "FAIL",
                    "pytest_config",
                    f"default pytest addopts must exclude opt-in marker: {marker}",
                    path="pytest.ini",
                    controller_action="update pytest.ini or registry policy",
                )
            )
    return findings


def _registered_files_for_prefixes(registry: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    policy = registry["policy"]
    prefixes = policy.get("coverage_required_prefixes", [])
    if not isinstance(prefixes, list):
        return [Finding("FAIL", "registry_schema", "policy.coverage_required_prefixes must be a list")]
    tools = registry["tools"]
    for raw_prefix in prefixes:
        findings.extend(_unregistered_files_for_prefix(_path_from_registry(str(raw_prefix)), tools))
    return findings


def _unregistered_files_for_prefix(prefix: Path, tools: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    for path in _test_files_under(prefix):
        if _covering_tools(tools, path):
            continue
        findings.append(
            Finding(
                "WARN",
                "registry_coverage",
                "test file is under required prefix but has no registry owner",
                path=_rel(path),
                controller_action="add registry entry or quarantine/delete the test",
            )
        )
    return findings


def _scope_paths_for_path(path: Path) -> list[Path]:
    return _test_files_under(path) if path.is_dir() else [path]


def _scope_paths_for_tool(tool: dict[str, Any]) -> list[Path]:
    selected: list[Path] = []
    for path in _tool_paths(tool):
        selected.extend(_scope_paths_for_path(path))
    return selected


def _scope_paths(raw_scopes: list[str] | None, registry: dict[str, Any]) -> list[Path]:
    if not raw_scopes:
        return _all_test_files()
    selected: list[Path] = []
    tools = registry["tools"]
    by_id = {str(tool.get("id")): tool for tool in tools if isinstance(tool, dict)}
    for raw in raw_scopes:
        if raw in by_id:
            selected.extend(_scope_paths_for_tool(by_id[raw]))
            continue
        path = _path_from_registry(raw)
        selected.extend(_scope_paths_for_path(path))
    return sorted(set(path for path in selected if path.exists()))


def _scope_selection_findings(raw_scopes: list[str] | None, selected_paths: list[Path]) -> list[Finding]:
    if raw_scopes and not selected_paths:
        return [
            Finding(
                "FAIL",
                "empty_scope_selection",
                "explicit test-tool audit scope selected zero existing files",
                controller_action="fix the scope id/path or create the expected test artifact",
            )
        ]
    return []


def _entry_context(tool: dict[str, Any]) -> str:
    return " ".join(
        str(tool.get(key, ""))
        for key in ("truth_source", "risk_reason", "replacement", "status", "evidence_level")
    ).lower()


def _format_marker_names(markers: set[str]) -> str:
    return str(sorted(markers))


def _path_health_findings(paths: list[Path], registry: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    tools = registry["tools"]
    for path in paths:
        rel = _rel(path)
        covering = _covering_tools(tools, path)
        if not _is_test_like_path(path):
            continue
        text = _read_text(path)
        non_current = [
            tool
            for tool in covering
            if tool.get("status") != "active"
            or tool.get("evidence_level") in {"legacy_guard", "quarantined", "invalid"}
        ]
        if non_current:
            tool_ids = ", ".join(str(tool.get("id")) for tool in non_current)
            severity = "FAIL" if _is_default_testpath(path, registry) else "WARN"
            findings.append(
                Finding(
                    severity,
                    "registered_tool_state",
                    f"test is covered by non-current registry state: {tool_ids}",
                    path=rel,
                    controller_action="refactor, quarantine, or promote registry state after evidence review",
                )
            )
        if rel.startswith("tests/") and not covering:
            findings.append(
                Finding(
                    "WARN",
                    "root_test_drift",
                    "root-level test is outside pytest.ini default testpaths and has no registry owner",
                    path=rel,
                    controller_action="register owner/runner or move/delete it",
                )
            )
        if "dim_active_a_stock" in text:
            context = " ".join(_entry_context(tool) for tool in covering)
            if not any(hint in context for hint in ALLOWED_DIM_ACTIVE_HINTS):
                findings.append(
                    Finding(
                        "FAIL" if covering else "WARN",
                        "universe_fixture_drift",
                        "dim_active_a_stock appears without a registry statement limiting it to cache/name/schema fixtures",
                        path=rel,
                        controller_action="refactor fixture to K-line/get_active_universe or update registry with evidence",
                    )
                )
        if re.search(r"^\s*import\s+sqlite3\b", text, re.MULTILINE):
            context = rel.lower() + " " + " ".join(_entry_context(tool) for tool in covering)
            if not any(hint in context for hint in SQLITE_EXCEPTION_HINTS):
                findings.append(
                    Finding(
                        "FAIL",
                        "legacy_db_drift",
                        "test imports sqlite3 without a documented exception",
                        path=rel,
                        controller_action="use DuckDB/duck_mem or document the SQLite exception",
                    )
                )
        markers = _marker_names(text)
        if markers & OPT_IN_SCOPES:
            opt_in_markers = markers & OPT_IN_SCOPES
            covered_scopes = set().union(*(_normalize_scopes(tool.get("scope")) for tool in covering)) if covering else set()
            if not covered_scopes.intersection(opt_in_markers):
                findings.append(
                    Finding(
                        "FAIL",
                        "marker_registry_drift",
                        f"file marker {_format_marker_names(opt_in_markers)} is not reflected in registry scope",
                        path=rel,
                        controller_action="align registry scope/runner with pytest marker",
                    )
                )
    return findings


def _unregistered_sample_limit(registry: dict[str, Any]) -> int:
    raw_limit = registry["policy"].get(
        "unregistered_selected_sample_limit",
        DEFAULT_UNREGISTERED_SELECTED_SAMPLE_LIMIT,
    )
    return raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else DEFAULT_UNREGISTERED_SELECTED_SAMPLE_LIMIT


def _unregistered_slice_path(path: Path) -> str:
    parts = Path(_rel(path)).parts
    if len(parts) >= 3 and parts[:2] == ("backend", "tests"):
        if len(parts) == 3:
            return "backend/tests/<root-files>"
        if len(parts) >= 5 and parts[2] == "services":
            return f"backend/tests/services/{parts[3]}"
        return f"backend/tests/{parts[2]}"
    if len(parts) >= 2 and parts[0] == "tests":
        if len(parts) == 2:
            return "tests/<root-files>"
        return f"tests/{parts[1]}"
    return str(Path(*parts[:-1])) if len(parts) > 1 else _rel(path)


def _slice_action(slice_path: str) -> str:
    if slice_path.endswith("/<root-files>"):
        return "triage direct test files into smaller registry owners; do not bulk-register blindly"
    return "add registry owner/status/evidence for this slice or quarantine/delete stale tests"


def _unregistered_task_slices(unregistered: list[Path]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Path]] = {}
    for path in unregistered:
        grouped.setdefault(_unregistered_slice_path(path), []).append(path)

    def sort_key(item: tuple[str, list[Path]]) -> tuple[bool, int, str]:
        slice_path, paths = item
        return (slice_path.endswith("/<root-files>"), -len(paths), slice_path)

    slices: list[dict[str, Any]] = []
    ordered_groups = list(grouped.items())
    ordered_groups.sort(key=sort_key)
    for slice_path, paths in ordered_groups:
        slices.append(
            {
                "priority": "P1",
                "path": slice_path,
                "count": len(paths),
                "sample": [_rel(path) for path in paths[:UNREGISTERED_SLICE_SAMPLE_LIMIT]],
                "action": _slice_action(slice_path),
            }
        )
    return slices


def _selected_registry_coverage(paths: list[Path], registry: dict[str, Any]) -> dict[str, Any]:
    selected = sorted(path for path in set(paths) if _is_test_like_path(path))
    registered = [path for path in selected if _covering_tools(registry["tools"], path)]
    unregistered = [path for path in selected if not _covering_tools(registry["tools"], path)]
    unregistered = sorted(unregistered, key=lambda path: (not path.name.startswith("test_"), _rel(path)))
    total = len(selected)
    return {
        "selected_test_files": total,
        "registered_selected_files": len(registered),
        "unregistered_selected_files": len(unregistered),
        "registry_coverage_pct": round((len(registered) / total) * 100, 2) if total else 100.0,
        "unregistered_selected_sample": [_rel(path) for path in unregistered[:_unregistered_sample_limit(registry)]],
        "unregistered_selected_slices": _unregistered_task_slices(unregistered),
    }


def _selected_registry_owner_findings(coverage: dict[str, Any], registry: dict[str, Any]) -> list[Finding]:
    mode = str(registry["policy"].get("selected_registry_owner", "off"))
    if mode not in VALID_SELECTED_REGISTRY_OWNER or mode == "off":
        return []
    unregistered_count = int(coverage["unregistered_selected_files"])
    if unregistered_count == 0:
        return []
    sample_items = coverage["unregistered_selected_sample"]
    sample = ", ".join(sample_items)
    if unregistered_count > len(sample_items):
        sample = f"{sample}, ..." if sample else "..."
    severity = "FAIL" if mode == "fail" else "WARN"
    return [
        Finding(
            severity,
            "selected_registry_coverage",
            f"{unregistered_count} selected test files have no registry owner; sample: {sample}",
            path="backend/config/test_tool_registry.yaml",
            controller_action="add registry owner/status/evidence for selected tests or narrow the audited scope",
        )
    ]


def _controller_feedback(
    findings: list[Finding],
    registry_coverage: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, str]]]:
    feedback = {
        "registry_updates": [],
        "gate_updates": [],
        "mechanism_updates": [],
        "next_task_slices": [],
    }
    registry_coverage = registry_coverage or {}
    for finding in findings:
        if finding.check in {
            "registry_path",
            "registry_schema",
            "registry_coverage",
            "selected_registry_coverage",
        }:
            feedback["registry_updates"].append(
                {
                    "priority": "P0" if finding.severity == "FAIL" else "P1",
                    "path": finding.path or "backend/config/test_tool_registry.yaml",
                    "action": finding.controller_action,
                }
            )
        elif finding.check == "registered_tool_state":
            feedback["gate_updates"].append(
                {
                    "priority": "P0" if finding.severity == "FAIL" else "P1",
                    "path": finding.path or "",
                    "action": finding.controller_action,
                }
            )
        elif finding.check in {"pytest_config", "marker_registry_drift"}:
            feedback["mechanism_updates"].append(
                {
                    "priority": "P0" if finding.severity == "FAIL" else "P1",
                    "path": finding.path or "pytest.ini",
                    "action": finding.controller_action,
                }
            )
        else:
            feedback["next_task_slices"].append(
                {
                    "priority": "P0" if finding.severity == "FAIL" else "P1",
                    "path": finding.path or "",
                    "action": finding.controller_action,
                }
            )
    for task_slice in registry_coverage.get("unregistered_selected_slices", []):
        feedback["next_task_slices"].append(
            {
                "priority": str(task_slice["priority"]),
                "path": str(task_slice["path"]),
                "action": f"{task_slice['action']} (count={task_slice['count']})",
            }
        )
    return feedback


def audit_test_tool_health(
    *,
    config_path: Path | None = None,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    registry = load_registry(config_path)
    selected_paths = _scope_paths(scopes, registry)
    registry_coverage = _selected_registry_coverage(selected_paths, registry)
    findings: list[Finding] = []
    findings.extend(_scope_selection_findings(scopes, selected_paths))
    findings.extend(_registry_findings(registry))
    findings.extend(_pytest_config_findings(registry))
    findings.extend(_registered_files_for_prefixes(registry))
    findings.extend(_selected_registry_owner_findings(registry_coverage, registry))
    findings.extend(_path_health_findings(selected_paths, registry))
    fail_count = sum(1 for finding in findings if finding.severity == "FAIL")
    warn_count = sum(1 for finding in findings if finding.severity == "WARN")
    verdict = "FAIL" if fail_count else "WARN" if warn_count else "PASS"
    return {
        "verdict": verdict,
        "summary": {
            "fail": fail_count,
            "warn": warn_count,
            "info": sum(1 for finding in findings if finding.severity == "INFO"),
            "selected_files": len(selected_paths),
            "registry_tools": len(registry["tools"]),
            **registry_coverage,
        },
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "registry_path": _rel(registry["path"]),
        "scopes": scopes or ["all"],
        "findings": [asdict(finding) for finding in findings],
        "controller_feedback": _controller_feedback(findings, registry_coverage),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit test tool validity before running tests.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--scope", action="append", default=None, help="Path or registry id to audit")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 after writing the report")
    args = parser.parse_args()

    report = audit_test_tool_health(config_path=args.config, scopes=args.scope)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if report["verdict"] == "FAIL" and not args.no_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
