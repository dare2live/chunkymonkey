"""交易日历强制使用门 (第零条规定执法 — 2026-06-22 用户根治: 日历总被静默绕过)。

镜像 check_universe_filter.py: universe 有"强制调用"门 (拦内联前缀绕过), 但交易日历**没有任何使用门** —
只有 moth calendar-floor 查数据起点, 不查代码是否真用日历。结果: ~20 文件自己算交易日/新鲜度
(INTERVAL DAY / wall-clock now 当"最新"), 静默绕开 dim_trading_calendar 真相源 (calendar.py docstring
明文"拒绝 fallback to wall-clock now")。这是日历"总也不生效"的结构根因。

本门扫两个最清晰的绕过信号 (非全部 date 运算 — 避免误伤合法日历天窗口):
  B1 wall-clock 当"最新交易日": datetime.now()/date.today() 决定 latest/cutoff → 必须 latest_completed_trade_date
  B2 SQL 日历天 cutoff: current_date - INTERVAL N DAY / CURRENT_DATE - N (对 trade_date 列做新鲜度/窗口) → 应走日历

豁免: 文件 import services.calendar (已用日历真相源) / 同行 `# rule-compliance: ok evidence=` (合法日历天窗口, 写明理由) / calendar.py 本体 / 测试。

跑: PYTHONPATH=backend python backend/scripts/check_calendar_usage.py [--staged] [--strict]
--strict: 有违规 exit 1 (硬门模式, wired pre-commit); 默认 scanner 模式 exit 0 只报清单。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 豁免: 日历本体 + 已知合法 (audit 工具自身查日历 / 一次性脚本)
EXEMPT_FILES = {
    "backend/services/calendar.py",                  # 日历真相源本体
    "backend/scripts/check_calendar_usage.py",       # 本门
}
EXEMPT_PREFIXES = (
    "backend/tests/",
)
# import 了日历服务 = 已接真相源 (低层 fn 误用由 fail-loud wrapper 单独治, 不在本门范围)
CALENDAR_IMPORT = re.compile(r"from services\.calendar import|import services\.calendar|services\.calendar\.")

# B1: wall-clock 当"最新/今天"做交易日决策 (calendar.py 点名禁)
WALLCLOCK = re.compile(r"datetime\.now\(\)\.date\(\)|date\.today\(\)|datetime\.today\(\)")
# B2: SQL 日历天 cutoff (current_date - INTERVAL N DAY / now() - INTERVAL)
SQL_CALDAY = re.compile(r"current_date\s*-\s*(?:INTERVAL|interval)|CURRENT_DATE\s*-\s*(?:INTERVAL|\d)|now\(\)\s*-\s*(?:INTERVAL|interval)", re.IGNORECASE)

EVIDENCE = "rule-compliance: ok evidence="


def _staged_py() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    return [REPO_ROOT / f for f in out if f.endswith(".py")]


def _all_py() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "backend/**/*.py"], cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    return [REPO_ROOT / f for f in out if f]


def check_file(path: Path) -> list[dict]:
    rel = str(path.relative_to(REPO_ROOT))
    if rel in EXEMPT_FILES or any(rel.startswith(p) for p in EXEMPT_PREFIXES):
        return []
    if path.suffix != ".py":
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    if CALENDAR_IMPORT.search(text):
        return []  # 文件已接日历真相源
    findings: list[dict] = []
    for i, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if s.startswith("#") or EVIDENCE in line:
            continue
        if WALLCLOCK.search(line):
            findings.append({"file": rel, "line": i, "kind": "B1 wall-clock-as-latest", "match": s[:90]})
        elif SQL_CALDAY.search(line):
            findings.append({"file": rel, "line": i, "kind": "B2 SQL 日历天 cutoff", "match": s[:90]})
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有违规 exit 1 (硬门)")
    args = ap.parse_args(argv)
    files = _staged_py() if args.staged else _all_py()
    all_findings: list[dict] = []
    for f in files:
        all_findings.extend(check_file(f))
    if all_findings:
        print(f"[calendar-usage] {len(all_findings)} 处内联绕过交易日历真相源 (应走 services.calendar):")
        for v in all_findings:
            print(f"  {v['file']}:{v['line']} [{v['kind']}] {v['match']}")
        print("正解: latest_completed_trade_date / latest_closed_or_raise (services.calendar); "
              "合法日历天窗口加 `# rule-compliance: ok evidence=...`。")
    else:
        print("[calendar-usage] PASS — 无内联绕过交易日历")
    return 1 if (all_findings and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
