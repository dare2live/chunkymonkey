"""沙盒隔离门 — 实验室产物只留实验室 (2026-06-21 立; 4+ 次隔离纪律失守根治)。

根因 (本次实证): sandbox **脚本**隔离了, 但探索弧的**产物**(主库表/builder/控制面文档 KPI/裁决) 在
  方法确认前就 promote 进主项目 → 跑偏弧的残留污染主代码/文档/库。
契约 (sandbox/README): 探索产物默认随 sandbox wipe；值得保留的结果必须重写为有
snapshot/config/PIT/成本证据的 Tier3 package，并经过 Rule 10。历史 verdict 表不是当前 writer
或 StrategyRelease。
本门拦未经 promotion 的测试残留漏进主项目:
  C1 (FAIL): backend/ 代码引用 sandbox/ (测试码漏进主代码)。
  C2 (WARN): 控制面文档嵌入历史未确认实验 run_id (confirmed_by_owner=0)。
  C3 (FAIL): backend/scripts 有 experiment_*/analyze_* 探索 runner (与 sandbox.sh check 同源)。
C2 是需要人工闭合的 WARN，但进程返回非零，提交门不得把 WARN 再打印成 PASS；C1/C3 是明确 FAIL。

跑: PYTHONPATH=backend python backend/scripts/check_sandbox_isolation.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.check_doc_governance import active_doc_paths
except ModuleNotFoundError:  # direct ``python backend/scripts/check_sandbox_isolation.py``
    from check_doc_governance import active_doc_paths

REPO = Path(__file__).resolve().parents[2]
# C1 豁免: 这两个文件天然含 "sandbox" (guard 本体 + 本门本体), 非漏码
C1_EXEMPT = {
    "backend/services/sandbox_guard.py",
    "backend/scripts/check_sandbox_isolation.py",
    "backend/scripts/check_doc_drift.py",  # detection patterns mention sandbox paths; no runtime import/access
}
C1_PAT = re.compile(r"(^|[^\w])(from sandbox|import sandbox|['\"]sandbox/)")


def _tracked(glob: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", glob],
                         capture_output=True, text=True).stdout.split()
    return out


def check_c1() -> list[str]:
    """backend/ (非 test/guard/本门) 引用 sandbox/ = 测试码漏进主代码。"""
    bad = []
    for f in _tracked("backend/**/*.py"):
        if f in C1_EXEMPT or "/tests/" in f or f.endswith("_test.py"):
            continue
        try:
            txt = (REPO / f).read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in txt.splitlines():
            if C1_PAT.search(ln):
                bad.append(f"{f}: {ln.strip()[:80]}")
                break
    return bad


def check_c3() -> list[str]:
    """backend/scripts 探索 runner (experiment_*/analyze_*) = 探索漏进主脚本。"""
    bad = []
    for f in _tracked("backend/scripts/*.py"):
        name = Path(f).name
        if name.startswith("experiment_") or name.startswith("analyze_"):
            bad.append(f)
    return bad


def control_doc_paths(root: Path = REPO) -> list[Path]:
    """Use the same live-document registry as the document gates."""
    return active_doc_paths(root)


def check_c2() -> list[str]:
    """控制面文档嵌入未 promote (confirmed_by_owner=0) 的 experiment_store run_id。"""
    try:
        sys.path.insert(0, str(REPO / "backend"))
        # 2026-06-28: services.experiment_store helper 模块退役 → 经平台 sanctioned 读路 resolver.connect_ro (manifest 路由 + read_only)
        from services.data_access.resolver import connect_ro
        c = connect_ro("experiment_store")
        try:
            rows = c.execute(
                "SELECT run_id FROM fact_experiment_verdict WHERE COALESCE(confirmed_by_owner,0)=0").fetchall()
        finally:
            c.close()
        unpromoted = [r[0] for r in rows if r[0]]
    except Exception as e:
        return [f"UNVERIFIED: experiment_store 不可读 ({str(e)[:60]})"]
    hits = []
    for p in control_doc_paths(REPO):
        d = p.relative_to(REPO).as_posix()
        txt = p.read_text(encoding="utf-8")
        for rid in unpromoted:
            if rid in txt:
                hits.append(f"{d} 嵌入未promote run_id: {rid}")
    return hits


def main() -> int:
    c1, c3 = check_c1(), check_c3()
    c2 = check_c2()
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
    if c2:
        print("[C2 WARN] 控制面文档嵌入未 promote (confirmed_by_owner=0) 的实验结果:", flush=True)
        for x in c2:
            print(f"    {x}", flush=True)
        print("    探索结论不得进入控制面；须按现行 Tier3 contract 重做并独立复核。", flush=True)
    else:
        print("[C2 OK] 控制面文档 0 未-promote 实验结果", flush=True)
    return 1 if (fail or c2) else 0


if __name__ == "__main__":
    sys.exit(main())
