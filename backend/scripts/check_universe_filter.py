#!/usr/bin/env python3
"""Static population-contract gate; live accepted-data readiness is separate.

The checker proves that a non-empty source snapshot and every formal registry
dataset bind through the production DatasetContract/population-scope code.  It
does not claim that accepted calendar/K-line/ST evidence exists; doctor reports
that independent runtime gate.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Literal

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.data_sources.contracts import dataset_contract_from_spec  # noqa: E402
from services.data_sources.population_scope import (  # noqa: E402
    ExternalAggregateScope,
    ProjectUniversePitScope,
    RawEvidenceScope,
    bind_execution_contract,
)
from services.data_sources.sync_runner import domain_spec  # noqa: E402
from services.universe import (  # noqa: E402
    UniverseDataError,
    UniversePolicy,
    load_universe_policy,
    verify_universe_policy,
)


REGISTRY_RELATIVE_PATH = Path("backend/config/sync_registry.yaml")
POLICY_RELATIVE_PATH = Path("backend/config/universe_rules.yaml")
TEST_PREFIXES = ("backend/tests/", "tests/")
SourceMode = Literal["worktree", "index"]
_SCOPE_TYPES = {
    "raw_evidence": RawEvidenceScope,
    "external_aggregate": ExternalAggregateScope,
    "project_universe_pit": ProjectUniversePitScope,
}


def _issue(code: str, message: str, **context: Any) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "message": message}
    issue.update(context)
    return issue


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def source_inventory(
    repo: Path, *, mode: SourceMode, include_tests: bool = False
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """Enumerate exactly the worktree or Git-index source snapshot."""

    args = (
        ["ls-files", "-z"]
        if mode == "index"
        else ["ls-files", "-co", "--exclude-standard", "-z"]
    )
    result = _git(repo, args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "unknown git error"
        return (), [_issue("source_scan_failed", f"git inventory failed: {detail}")]
    paths = []
    for relative in result.stdout.split("\0"):
        if not relative.endswith(".py"):
            continue
        if not include_tests and relative.startswith(TEST_PREFIXES):
            continue
        if mode == "worktree" and not (repo / relative).is_file():
            continue
        paths.append(relative)
    return tuple(sorted(set(paths))), []


def _index_text(repo: Path, relative: Path) -> str:
    result = _git(repo, ["show", f":{relative.as_posix()}"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "missing index file"
        raise OSError(detail)
    return result.stdout


def _registry(
    repo: Path, registry_path: Path, mode: SourceMode
) -> dict[str, Any]:
    if mode == "index" and registry_path == repo / REGISTRY_RELATIVE_PATH:
        text = _index_text(repo, REGISTRY_RELATIVE_PATH)
    else:
        text = registry_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("domains"), dict):
        raise ValueError("registry root/domains must be mappings")
    return raw


def _policy(repo: Path, mode: SourceMode) -> UniversePolicy:
    if mode == "worktree":
        return load_universe_policy(repo / POLICY_RELATIVE_PATH)
    text = _index_text(repo, POLICY_RELATIVE_PATH)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        return load_universe_policy(handle.name)


def _audit_formal_contracts(
    registry: Mapping[str, Any],
    *,
    policy_snapshot: UniversePolicy,
) -> tuple[int, dict[str, int], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    domains = registry["domains"]
    formal_names = sorted(
        name
        for name, entry in domains.items()
        if isinstance(entry, Mapping) and "dataset_contract" in entry
    )
    if not formal_names:
        return 0, {}, [
            _issue("no_formal_datasets", "registry contains no formal datasets")
        ]

    scope_counts: Counter[str] = Counter()
    for domain in formal_names:
        try:
            spec = domain_spec(dict(registry), domain)
            contract = dataset_contract_from_spec(domain, spec)
            raw_scope = spec.get("population_scope")
            kind = raw_scope.get("kind") if isinstance(raw_scope, Mapping) else None
            injected = policy_snapshot if kind == "project_universe_pit" else None
            bound = bind_execution_contract(contract, spec, injected)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(
                _issue(
                    "population_scope_invalid",
                    f"formal dataset contract/scope is invalid: {exc}",
                    domain=domain,
                )
            )
            continue

        expected_type = _SCOPE_TYPES.get(kind)
        if expected_type is None or type(bound.accepted_scope) is not expected_type:
            issues.append(
                _issue(
                    "population_scope_kind_mismatch",
                    "declared scope kind differs from production-bound type",
                    domain=domain,
                    scope_kind=kind,
                )
            )
            continue
        if kind == "project_universe_pit" and bound.universe_policy is not policy_snapshot:
            issues.append(
                _issue(
                    "universe_policy_not_injected",
                    "project scope lost the exact policy snapshot",
                    domain=domain,
                )
            )
            continue
        if kind != "project_universe_pit" and bound.universe_policy is not None:
            issues.append(
                _issue(
                    "non_project_scope_has_policy",
                    "non-project scope retained project universe policy",
                    domain=domain,
                )
            )
            continue
        scope_counts[str(kind)] += 1
    return len(formal_names), dict(sorted(scope_counts.items())), issues


def audit_repository(
    repo: Path = REPO_ROOT,
    *,
    registry_path: Path | None = None,
    include_tests: bool = False,
    source_mode: SourceMode = "worktree",
    policy_loader: Callable[[], UniversePolicy] | None = None,
) -> dict[str, Any]:
    repo = Path(repo)
    sources, issues = source_inventory(
        repo, mode=source_mode, include_tests=include_tests
    )
    if not sources:
        issues.append(
            _issue("no_source", f"{source_mode} contains zero Python source files")
        )

    formal_count = 0
    scope_counts: dict[str, int] = {}
    try:
        registry = _registry(
            repo,
            registry_path or repo / REGISTRY_RELATIVE_PATH,
            source_mode,
        )
        policy_snapshot = (
            policy_loader() if policy_loader is not None else _policy(repo, source_mode)
        )
        verify_universe_policy(policy_snapshot)
        formal_count, scope_counts, contract_issues = _audit_formal_contracts(
            registry, policy_snapshot=policy_snapshot
        )
        issues.extend(contract_issues)
    except (OSError, TypeError, ValueError, UniverseDataError, yaml.YAMLError) as exc:
        issues.append(_issue("contract_inputs_unreadable", str(exc)))

    return {
        "verdict": "FAIL" if issues else "PASS",
        "source_mode": source_mode,
        "source_count": len(sources),
        "formal_dataset_count": formal_count,
        "scope_counts": scope_counts,
        "issues": issues,
        "live_readiness": "NOT_EVALUATED",
    }


def _render_text(report: Mapping[str, Any]) -> str:
    lines = [
        "[population-contract] "
        f"{report['verdict']} mode={report['source_mode']} "
        f"source_count={report['source_count']} "
        f"formal_dataset_count={report['formal_dataset_count']} "
        f"scope_counts={report['scope_counts']} "
        "live_readiness=NOT_EVALUATED"
    ]
    for issue in report["issues"]:
        domain = f" domain={issue['domain']}" if issue.get("domain") else ""
        lines.append(f"  - {issue['code']}{domain}: {issue['message']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="audit Git-index snapshot")
    mode.add_argument("--all", action="store_true", help="audit live worktree snapshot")
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    report = audit_repository(
        include_tests=args.include_tests,
        source_mode="index" if args.staged else "worktree",
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(_render_text(report))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
