#!/usr/bin/env python3
"""L8 enforcement: lint code for direct dim_active_a_stock usage instead of universe.get_active_universe.

Per user push: 'Universe filter 必用 get_active_universe()' — currently doc-only.
This script enforces it via lint check.

Usage:
  PYTHONPATH=backend python backend/scripts/check_universe_filter.py [--staged]

Exit code:
  0 = clean
  1 = violations found (block commit if used in pre-commit hook)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXEMPT_PREFIXES = (
    "bestchoice/",  # Track A FROZEN, original BC code
    "backend/services/bc_absorbed/",  # Track B initial copy 2026-05-24, grandfathered until Phase 2.2+ full universe wire
)
EXEMPT_FILES = {
    "backend/services/universe.py",  # the implementation itself
    "backend/tests/test_universe.py",  # test fixtures
    "backend/scripts/check_universe_filter.py",  # this file
    "backend/scripts/audit_strategy_universe.py",  # audit tool intentional
    "backend/scripts/audit_survivorship.py",  # audit tool intentional
    "backend/scripts/audit_panel_leakage.py",  # audit tool intentional
    "backend/scripts/build_ensemble_v4_intersect_bc_phase7.py",  # SQL 内联 ST 过滤, rule-compliance 标注
    "backend/services/labels/feature_join_v5.py",  # SQL 内联 ST 过滤, rule-compliance 标注
}

# Pattern: SQL containing 'dim_active_a_stock' JOIN without nearby get_active_universe call
SQL_PATTERN = re.compile(r"dim_active_a_stock", re.IGNORECASE)
UNIVERSE_CALL_PATTERN = re.compile(r"get_active_universe|sql_where_active_a_share|sql_where_no_st")


def check_file(path: Path) -> list[dict]:
    """Returns list of violation dicts."""
    rel = str(path.relative_to(REPO_ROOT))
    if rel in EXEMPT_FILES:
        return []
    if any(rel.startswith(p) for p in EXEMPT_PREFIXES):
        return []
    if not path.suffix == ".py":
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    findings = []
    lines = text.split("\n")
    has_universe_call = bool(UNIVERSE_CALL_PATTERN.search(text))
    for i, line in enumerate(lines, 1):
        if SQL_PATTERN.search(line):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # rule-compliance evidence comment OK
            if "rule-compliance: ok evidence=" in line:
                continue
            # If file has universe call but specific line is dim_active JOIN, still flag unless evidence
            if not has_universe_call:
                findings.append({
                    "file": rel,
                    "line": i,
                    "match": stripped[:80],
                    "reason": "dim_active_a_stock 直接使用, 应改 get_active_universe() (universe.py)",
                })
            elif "JOIN" in line.upper() or "INNER" in line.upper() or "FROM" in line.upper():
                # Has universe call elsewhere in file, but specific JOIN line warrants check
                findings.append({
                    "file": rel,
                    "line": i,
                    "match": stripped[:80],
                    "reason": "dim_active_a_stock 在 JOIN/FROM, file 有 get_active_universe 但 此处 direct — 验证是否 intentional. 加 # rule-compliance: ok evidence=... 跳过.",
                })
    return findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staged", action="store_true",
                   help="check only git-staged files (for pre-commit hook)")
    p.add_argument("--all", action="store_true", default=False,
                   help="check all .py files (default = changed since main)")
    args = p.parse_args()

    if args.staged:
        result = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        files = [REPO_ROOT / f for f in result.stdout.strip().split("\n") if f.endswith(".py")]
    elif args.all:
        files = sorted(REPO_ROOT.rglob("*.py"))
    else:
        # Default: files changed vs main
        result = subprocess.run(["git", "diff", "main", "--name-only", "--diff-filter=ACM"],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        files = [REPO_ROOT / f for f in result.stdout.strip().split("\n") if f.endswith(".py")]

    all_findings = []
    for path in files:
        if path.exists():
            all_findings.extend(check_file(path))

    if not all_findings:
        print(f"[L8 universe-filter] CLEAN ({len(files)} files checked)")
        return 0

    print(f"[L8 universe-filter] {len(all_findings)} violation(s):")
    for f in all_findings:
        print(f"  {f['file']}:{f['line']}: {f['match']}")
        print(f"    {f['reason']}")
    print()
    print("修法 (3 选 1):")
    print("  1. 改用 from services.universe import get_active_universe + filter_active_a_share")
    print("  2. 加 # rule-compliance: ok evidence=<reason> 注释跳过 (need justification)")
    print("  3. 文件加 from services.universe import get_active_universe (file-level intent)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
