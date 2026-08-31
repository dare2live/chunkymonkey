#!/usr/bin/env python3
"""Machine-classify staged paths into safe_commit tiers L1/L2/L3.

Owner: docs/engineering_governance.md §14 + backend/config/commit_tiers.yaml.

Fail-closed rules (not overridable by env/agent):
  - unknown / unmatched path → L3
  - any deletion / rename / typechange → L3
  - policy missing / invalid / unreadable → L3 + gates=all
  - no self-downgrade knobs

Output (stdout JSON):
  {"tier":"L1"|"L2"|"L3","gates":[...],"reasons":[...],"paths":[...]}

Usage:
  PYTHONPATH=backend python backend/scripts/classify_commit_tier.py
  PYTHONPATH=backend python backend/scripts/classify_commit_tier.py --paths a.md b.py
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO / "backend" / "config" / "commit_tiers.yaml"

KNOWN_GATES = frozenset({
    "project_index_sync",
    "feature_map",
    "moth",
    "rule_compliance",
    "ci_pytest",
    "sandbox_isolation",
    "serve_read_layer",
    "calendar_usage",
    "population_contract",
    "lineage_drift",
    "dead_references",
    "grain_uniqueness",
    "continuity",
    "no_emoji",
    "moth_invariants",
    "staged_worktree_parity",
    "config_refs",
    "doc_drift",
    "doc_governance",
    "doc_runtime_state",
    "commit_msg",
    "rule10",
    "repo_blob_size",
    "tushare_sunset",
})

ALL_GATES_ORDERED = (
    "project_index_sync",
    "feature_map",
    "moth",
    "rule_compliance",
    "ci_pytest",
    "sandbox_isolation",
    "serve_read_layer",
    "calendar_usage",
    "population_contract",
    "lineage_drift",
    "dead_references",
    "grain_uniqueness",
    "continuity",
    "no_emoji",
    "moth_invariants",
    "staged_worktree_parity",
    "config_refs",
    "doc_drift",
    "doc_governance",
    "doc_runtime_state",
    "commit_msg",
    "rule10",
    "repo_blob_size",
    "tushare_sunset",
)

TIER_RANK = {"L1": 1, "L2": 2, "L3": 3}

# Staged blob write-table scan — escalate L2 → L3 when routers/main mutate tables.
WRITE_RE = re.compile(
    r"(?i)\b(?:CREATE(?:\s+OR\s+REPLACE)?\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?"
    r"|INSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO"
    r"|MERGE\s+INTO)\s+"
)

# git name-status letters that force L3 (deletion / rename / typechange / copy).
FORCE_L3_STATUS = frozenset("DRTC")


class PolicyError(RuntimeError):
    """Policy file is missing, malformed, or fails hard invariants."""


def _l3_result(reasons: list[str], paths: list[str] | None = None) -> dict[str, Any]:
    return {
        "tier": "L3",
        "gates": list(ALL_GATES_ORDERED),
        "reasons": reasons,
        "paths": list(paths or []),
    }


def load_policy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PolicyError(f"missing policy: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"unreadable policy: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError("policy root must be a mapping")
    return raw


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("version") != 1:
        raise PolicyError("policy version must be 1")
    for key in (
        "l1_prefixes", "l1_files", "l2_prefixes", "l2_files",
        "content_scan_exempt_prefixes", "tier_gates",
    ):
        if key not in policy:
            raise PolicyError(f"policy missing key: {key}")
    for prefix_key in ("l1_prefixes", "l2_prefixes", "content_scan_exempt_prefixes"):
        vals = policy[prefix_key]
        if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
            raise PolicyError(f"{prefix_key} must be a list of strings")
        for p in vals:
            if not p.endswith("/"):
                raise PolicyError(f"{prefix_key} entries must end with '/': {p!r}")
    for files_key in ("l1_files", "l2_files"):
        vals = policy[files_key]
        if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
            raise PolicyError(f"{files_key} must be a list of strings")
    gates = policy["tier_gates"]
    if not isinstance(gates, dict):
        raise PolicyError("tier_gates must be a mapping")
    for tier in ("L1", "L2", "L3"):
        if tier not in gates:
            raise PolicyError(f"tier_gates missing {tier}")
    if gates["L3"] != "all":
        raise PolicyError("tier_gates.L3 must be the literal 'all'")
    for tier in ("L1", "L2"):
        names = gates[tier]
        if not isinstance(names, list) or not names:
            raise PolicyError(f"tier_gates.{tier} must be a non-empty list")
        unknown = set(names) - KNOWN_GATES
        if unknown:
            raise PolicyError(f"tier_gates.{tier} unknown gates: {sorted(unknown)}")
    if "doc_governance" not in gates["L1"] or "doc_drift" not in gates["L1"]:
        raise PolicyError("L1 must include doc_drift and doc_governance")
    if "rule10" not in gates["L2"]:
        raise PolicyError("L2 must include rule10")
    if "ci_pytest" not in gates["L2"]:
        # 2026-07-20 Fable5 CI-tax fix: L2 code changes must run the same offline
        # pytest surface as public CI locally, not just at push time.
        raise PolicyError("L2 must include ci_pytest")


def staged_name_status() -> list[tuple[str, str]]:
    """Return [(status_letter, path), ...] for the current git index."""
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "--diff-filter=ACMRTD"],
        capture_output=True, text=True, check=False, cwd=REPO,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git name-status exit {r.returncode}")
    rows: list[tuple[str, str]] = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0].upper()
        # Renames/copies: STATUS\told\tnew — classify the destination path.
        path = parts[-1]
        rows.append((status, path))
    return rows


def staged_blob_text(path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f":{path}"],
        capture_output=True, text=True, check=False, cwd=REPO,
    )
    if r.returncode != 0:
        return None
    return r.stdout


def path_base_tier(path: str, policy: dict[str, Any]) -> str:
    for exact in policy["l1_files"]:
        if path == exact:
            return "L1"
    for prefix in policy["l1_prefixes"]:
        if path.startswith(prefix):
            return "L1"
    for exact in policy["l2_files"]:
        if path == exact:
            return "L2"
    for prefix in policy["l2_prefixes"]:
        if path.startswith(prefix):
            return "L2"
    return "L3"


def content_forces_l3(path: str, policy: dict[str, Any]) -> bool:
    for exempt in policy["content_scan_exempt_prefixes"]:
        if path.startswith(exempt):
            return False
    # Only scan L2 candidates (routers / main.py); L1 has no code.
    base = path_base_tier(path, policy)
    if base != "L2":
        return False
    if not path.endswith((".py", ".sql")):
        return False
    text = staged_blob_text(path)
    if text is None:
        # Unreadable staged blob → fail closed.
        return True
    return WRITE_RE.search(text) is not None


def resolve_gates(tier: str, policy: dict[str, Any]) -> list[str]:
    raw = policy["tier_gates"][tier]
    if raw == "all":
        return list(ALL_GATES_ORDERED)
    return list(raw)


def classify_paths(
    entries: list[tuple[str, str]],
    policy: dict[str, Any],
    *,
    scan_content: bool = True,
) -> dict[str, Any]:
    if not entries:
        return _l3_result(["empty_staged_set_fail_closed"], [])

    reasons: list[str] = []
    paths = [p for _, p in entries]
    tier = "L1"

    for status, path in entries:
        if status in FORCE_L3_STATUS:
            tier = "L3"
            reasons.append(f"status_{status}:{path}")
            continue
        base = path_base_tier(path, policy)
        if TIER_RANK[base] > TIER_RANK[tier]:
            tier = base
            reasons.append(f"path_{base}:{path}")
        elif base == tier and not any(r.endswith(f":{path}") for r in reasons):
            reasons.append(f"path_{base}:{path}")
        if scan_content and content_forces_l3(path, policy):
            tier = "L3"
            reasons.append(f"sql_mutation:{path}")

    # Deduplicate reasons while preserving order.
    seen: set[str] = set()
    uniq_reasons: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq_reasons.append(r)

    return {
        "tier": tier,
        "gates": resolve_gates(tier, policy),
        "reasons": uniq_reasons or [f"tier_{tier}"],
        "paths": paths,
    }


def classify(
    paths: list[str] | None = None,
    *,
    policy_path: Path = DEFAULT_POLICY,
    scan_content: bool = True,
) -> dict[str, Any]:
    try:
        policy = load_policy(policy_path)
        validate_policy(policy)
    except PolicyError as exc:
        return _l3_result([f"policy_error:{exc}"], paths or [])

    if paths is not None:
        entries = [("A", p) for p in paths]
        return classify_paths(entries, policy, scan_content=scan_content)

    try:
        entries = staged_name_status()
    except RuntimeError as exc:
        return _l3_result([f"git_error:{exc}"], [])
    return classify_paths(entries, policy, scan_content=scan_content)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--paths", nargs="*", default=None,
                    help="Classify explicit paths (status=A); skip git index")
    ap.add_argument("--no-content-scan", action="store_true",
                    help="Skip staged-blob SQL mutation scan (tests only)")
    args = ap.parse_args(argv)
    result = classify(
        args.paths,
        policy_path=args.policy,
        scan_content=not args.no_content_scan,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
