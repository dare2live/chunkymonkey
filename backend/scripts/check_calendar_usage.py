"""交易日历强制使用门 (第零条规定执法 — 2026-06-22 用户根治: 日历总被静默绕过)。

镜像 check_universe_filter.py: universe 有"强制调用"门 (拦内联前缀绕过), 但交易日历**没有任何使用门** —
只有 moth calendar-floor 查数据起点, 不查代码是否真用日历。结果: ~20 文件自己算交易日/新鲜度
(INTERVAL DAY / wall-clock now 当"最新"), 静默绕开 dim_trading_calendar 真相源 (calendar.py docstring
明文"拒绝 fallback to wall-clock now")。这是日历"总也不生效"的结构根因。

本门扫两个最清晰的绕过信号 (非全部 date 运算 — 避免误伤合法日历天窗口):
  B1 wall-clock 当"最新交易日": datetime.now()/date.today() 决定 latest/cutoff → 必须 latest_completed_trade_date
  B2 SQL 日历天 cutoff: current_date - INTERVAL N DAY / CURRENT_DATE - N (对 trade_date 列做新鲜度/窗口) → 应走日历

豁免: 同行 `# rule-compliance: ok evidence=`（合法日历天窗口，写明理由）/
calendar.py 本体 / 测试。仅 import 日历服务不能豁免同文件中的 wall-clock 绕过。

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
# B1: wall-clock 当"最新/今天"做交易日决策 (calendar.py 点名禁)
# 2026-08-10 修盲点：原正则要求 now() 括号内为空，而带时区参数
# （`datetime.now(tz).date()`）才是**正确**写法 —— 于是本门只抓得住写得不规范的
# 代码，写规范了反而绕过。实测盲点覆盖 stock_dossier / org_holding_aif10 /
# check_continuity_integrity 等真实调用点。
#
# 能力边界（诚实声明，不假装完备）：本门按**行**做正则匹配，把 `.now()` 与
# `.date()` 拆成两行赋值仍可绕过（实例：build_agent_board.py 的
# `_now = ...now(tz)` + 次行取值）。完备检测需要在 AST 上追踪返回值流向到
# trade_date 形参，未实现。**本门是提示与显式声明的强制点，不是绕过的保证。**
WALLCLOCK = re.compile(
    r"datetime\.now\([^)]*\)\s*\.date\(\)"
    r"|date\.today\(\)"
    r"|datetime\.today\(\)"
)
# B2: SQL 日历天 cutoff (current_date - INTERVAL N DAY / now() - INTERVAL)
SQL_CALDAY = re.compile(r"current_date\s*-\s*(?:INTERVAL|interval)|CURRENT_DATE\s*-\s*(?:INTERVAL|\d)|now\(\)\s*-\s*(?:INTERVAL|interval)", re.IGNORECASE)
# B3: SQL 上界锚 (col <= CURRENT_DATE / <= CAST(CURRENT_DATE — wall-clock 当 PIT 决策上界, 周末/盘中 admit 未收盘日).
# 负 lookahead 排除 `CURRENT_DATE -`(那是 B2 lookback 窗口非上界锚) — 2026-06-22 P0-11 修验证器盲区:
# 旧 SQL_CALDAY 只抓减法窗口, 漏抓裸 <= CURRENT_DATE 真上界锚 (signals_v2:1495/242), architect rule7.
SQL_UPPER = re.compile(r"(?:<=|<|=)\s*(?:CAST\s*\(\s*)?CURRENT_DATE(?!\s*-)", re.IGNORECASE)

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
    findings: list[dict] = []
    for i, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if s.startswith("#") or EVIDENCE in line:
            continue
        if WALLCLOCK.search(line):
            findings.append({"file": rel, "line": i, "kind": "B1 wall-clock-as-latest", "match": s[:90]})
        if SQL_CALDAY.search(line):
            findings.append({"file": rel, "line": i, "kind": "B2 SQL 日历天 cutoff", "match": s[:90]})
        if SQL_UPPER.search(line):   # B3 上界锚 (真 PIT 上界 bug, 区别于 B2 lookback 窗口)
            findings.append({"file": rel, "line": i, "kind": "B3 SQL CURRENT_DATE 上界锚", "match": s[:90]})
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有违规 exit 1 (硬门)")
    args = ap.parse_args(argv)
    files = _staged_py() if args.staged else _all_py()
    if not files:
        print("[calendar-usage] FAIL — tracked Python scan is empty")
        return 1
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
