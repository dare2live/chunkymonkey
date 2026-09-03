"""沙盒隔离门 — 实验室产物只留实验室 (2026-06-21 立; 4+ 次隔离纪律失守根治)。

Invariant: backend/ 非测试代码不 import/引用 sandbox/；backend/scripts/ 无
experiment_*.py / analyze_*.py。

根因 (本次实证): sandbox **脚本**隔离了, 但探索弧的**产物**(主库表/builder) 在
  方法确认前就 promote 进主项目 → 跑偏弧的残留污染主代码/库。
契约 (sandbox/README): 探索产物默认随 sandbox wipe；值得保留的结果必须重写为有
snapshot/config/PIT/成本证据的 Tier3 package，并经过 Rule 10。历史 verdict 表不是当前 writer
或 StrategyRelease。
本门拦未经 promotion 的测试残留漏进主项目:
  C1 (FAIL): backend/ 代码引用 sandbox/ (测试码漏进主代码)。
  C3 (FAIL): backend/scripts 有 experiment_*/analyze_* 探索 runner (与 sandbox.sh check 同源)。

2026-09-04: C2 (控制面文档嵌入未 promote 实验 run_id) 删除 —— 它读的控制面文档
登记表所在的脚本连同 docs/ 整个目录一起退役, C2 失去检查对象 (没有控制面文档可扫)。

跑: PYTHONPATH=backend python backend/scripts/check_sandbox_isolation.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# C1 豁免: 这两个文件天然含 "sandbox" (guard 本体 + 本门本体), 非漏码
C1_EXEMPT = {
    "backend/services/sandbox_guard.py",
    "backend/scripts/check_sandbox_isolation.py",
}
C1_PAT = re.compile(r"(^|[^\w])(from sandbox|import sandbox|['\"]sandbox/)")


def _tracked(glob: str, *, repo: Path = REPO) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", glob],
                         capture_output=True, text=True).stdout.split()
    return out


def check_c1(*, repo: Path = REPO) -> list[str]:
    """backend/ (非 test/guard/本门) 引用 sandbox/ = 测试码漏进主代码。"""
    bad = []
    for f in _tracked("backend/**/*.py", repo=repo):
        if f in C1_EXEMPT or "/tests/" in f or f.endswith("_test.py"):
            continue
        try:
            txt = (repo / f).read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in txt.splitlines():
            if C1_PAT.search(ln):
                bad.append(f"{f}: {ln.strip()[:80]}")
                break
    return bad


def check_c3(*, repo: Path = REPO) -> list[str]:
    """backend/scripts 探索 runner (experiment_*/analyze_*) = 探索漏进主脚本。"""
    bad = []
    for f in _tracked("backend/scripts/*.py", repo=repo):
        name = Path(f).name
        if name.startswith("experiment_") or name.startswith("analyze_"):
            bad.append(f)
    return bad


def main() -> int:
    c1, c3 = check_c1(), check_c3()
    fail = False
    if c1:
        fail = True
        print("[C1 FAIL] backend/ 代码引用 sandbox/ (测试码漏进主代码, 该移回 sandbox):", flush=True)
        for x in c1:
            print(f"    {x}", flush=True)
    else:
        print("[C1 OK] backend/ 0 sandbox 引用", flush=True)
    if c3:
        fail = True
        print("[C3 FAIL] backend/scripts 探索 runner (该移 sandbox/<exp>/):", flush=True)
        for x in c3:
            print(f"    {x}", flush=True)
    else:
        print("[C3 OK] backend/scripts 0 探索 runner", flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
