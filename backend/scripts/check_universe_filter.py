#!/usr/bin/env python3
"""L8 enforcement: lint code for direct dim_active_a_stock usage instead of universe.get_active_universe.

Per user push: 'Universe filter 必用 get_active_universe()' — currently doc-only.
This script enforces it via lint check.

Usage:
  PYTHONPATH=backend python backend/scripts/check_universe_filter.py [--staged] [--include-tests]

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
TEST_PREFIXES = (
    "backend/tests/",
)
EXEMPT_FILES = {
    "backend/services/universe.py",  # the implementation itself
    "backend/tests/test_universe.py",  # test fixtures
    "backend/scripts/check_universe_filter.py",  # this file
    "backend/scripts/audit_strategy_universe.py",  # audit tool intentional
    "backend/scripts/audit_survivorship.py",  # audit tool intentional
    "backend/scripts/audit_panel_leakage.py",  # audit tool intentional
    "backend/scripts/migrate_reference_db.py",  # §9迁移工具: 操作 dim_active_a_stock 表本身(搬库), 非取universe选股list
}

# Pattern: SQL containing 'dim_active_a_stock' JOIN without nearby get_active_universe call
SQL_PATTERN = re.compile(r"dim_active_a_stock", re.IGNORECASE)
# 2026-06-22 P0-12: 认 assert_universe_clean(运行时硬门)/in_active_universe(helper) 也是 universe 执法
UNIVERSE_CALL_PATTERN = re.compile(r"get_active_universe|sql_where_active_a_share|sql_where_no_st|assert_universe_clean|in_active_universe")
# 2026-06-22 P0-12 盲区2: 从 K线直接派生股票宇宙 (SELECT DISTINCT code FROM price_kline) 无 universe
# 过滤 = §4.5 universe 污染根因 (实验直扫全部股含北交所/ST)。区别于"读某股K线数据"(WHERE code=?)。
KLINE_UNIVERSE_SCAN = re.compile(r"DISTINCT\s+(?:code|ts_code)\s+FROM\s+\S*price_kline", re.IGNORECASE)

# 2026-06-17: 硬编码白名单前缀绕过 (universe 升交易日历级真相源). 任何文件内联
# ('60','00','30','68') 白名单 = 第二真相源 → 必须 import universe.ACTIVE_A_SHARE_PREFIXES.
# 这是污染复发的根 (实验/旧GT 内联前缀或干脆不过滤直扫 K线).
_WHITELIST_PREFIXES = ("60", "00", "30", "68")


def _has_board_whitelist_literal(line: str) -> bool:
    """line 是否硬编码了完整白名单前缀 (4 个全在, 任意序)."""
    return all((f"'{p}'" in line) or (f'"{p}"' in line) for p in _WHITELIST_PREFIXES)


def _is_test_path(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in TEST_PREFIXES)


def _filter_files(files: list[Path], *, include_tests: bool) -> list[Path]:
    if include_tests:
        return files
    return [path for path in files if not _is_test_path(str(path.relative_to(REPO_ROOT)))]


def check_file(path: Path, *, include_tests: bool = False) -> list[dict]:
    """Returns list of violation dicts."""
    rel = str(path.relative_to(REPO_ROOT))
    if rel in EXEMPT_FILES:
        return []
    if not include_tests and _is_test_path(rel):
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
        # 2026-06-17: 硬编码白名单前缀绕过 (universe 升真相源后必拦)
        if _has_board_whitelist_literal(line):
            stripped = line.strip()
            if stripped.startswith("#") or "rule-compliance: ok evidence=" in line:
                continue
            findings.append({
                "file": rel,
                "line": i,
                "match": stripped[:80],
                "reason": "硬编码白名单前缀(60/00/30/68)=第二真相源, 应 import services.universe.ACTIVE_A_SHARE_PREFIXES 或调 assert_universe_clean()",
            })
        # 2026-06-22 P0-12 盲区2: 从 K线派生股票宇宙 (DISTINCT code) 无 universe 过滤 = §4.5 污染根因
        if KLINE_UNIVERSE_SCAN.search(line) and not has_universe_call:
            stripped = line.strip()
            if stripped.startswith("#") or "rule-compliance: ok evidence=" in line:
                continue
            findings.append({
                "file": rel,
                "line": i,
                "match": stripped[:80],
                "reason": "SELECT DISTINCT code FROM price_kline 派生股票宇宙但 file 无 universe 过滤 = §4.5 污染根因(直扫全部股含北交所/ST), 应过 assert_universe_clean()/get_active_universe(); 合法(如writer/已过滤)加 # rule-compliance: ok evidence=",
            })
    return findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staged", action="store_true",
                   help="check only git-staged files (for pre-commit hook)")
    p.add_argument("--all", action="store_true", default=False,
                   help="check all .py files (default = changed since main)")
    p.add_argument("--include-tests", action="store_true",
                   help="include backend/tests fixtures in the lint scan")
    args = p.parse_args()

    if args.staged:
        result = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        files = [REPO_ROOT / f for f in result.stdout.strip().split("\n") if f.endswith(".py")]
    elif args.all:
        files = sorted(REPO_ROOT.rglob("*.py"))
    else:
        # 2026-06-22 P0-12 盲区1: 默认改全量扫 (旧 default=git diff main → 无 diff 分支假绿).
        # pre-commit hook 用 --staged (增量); bare 调用 = 全量审计.
        files = sorted(REPO_ROOT.rglob("*.py"))
    files = _filter_files(files, include_tests=args.include_tests)

    all_findings = []
    for path in files:
        if path.exists():
            all_findings.extend(check_file(path, include_tests=args.include_tests))

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
