#!/usr/bin/env python3
"""耦合 / 孤儿引用检查器 (2026-06-16, 用户: 删除过程暴露 模块↔数据↔配置↔DB↔文档↔测试 耦合)。

删一个实体常在别处留悬空引用 (本轮实证: 删 experiment 脚本→孤儿测试 import 崩 CI collection;
drop DB 表→moth 断言/data_layers 声明/代码 DDL 悬空; 删 doc→PROJECT_INDEX 幽灵引用)。两模式:

  --impact <name>   删 <name> (表名 / 文件名 basename) 前看全 fan-in (代码/配置/文档/测试/moth 哪里引用) = 爆炸半径。
  (默认 / --orphans) 扫全仓孤儿引用 (引用了不存在的实体)。FAIL 类 exit 1 (moth 可挂 verdict=PASS)。

孤儿分级:
  [FAIL] T1 测试 import 不存在的脚本 (CI collection 崩根因)
  [FAIL] T4 moth claims.yaml command/database 引用的文件路径不存在
  [WARN] T2 data_layers.yaml 声明的表不在任何 DB (declared-but-missing: 可能待 rebuild, 故 WARN 非 FAIL)
  [WARN] T3 experiment_jobs.yaml artifact 表 / owner_module 不存在

用法:
  python backend/scripts/check_coupling.py                 # orphan 扫描 (FAIL→exit1)
  python backend/scripts/check_coupling.py --impact cyq_perf  # 删前看谁引用 cyq_perf
退出码: 0 无 FAIL 孤儿; 1 有。
"""
from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 扫描面 (耦合可能藏的地方)
_SCAN_GLOBS = [
    "backend/**/*.py", "backend/config/**/*.yaml", "backend/config/**/*.yml",
    "docs/*.md", "goal.md", "PROJECT_INDEX.md", "CLAUDE.md", "AGENTS.md",
    ".moth/assertions/*.yaml", "scripts/*.sh",
]


def _scan_files() -> list[Path]:
    out: list[Path] = []
    for g in _SCAN_GLOBS:
        out += [Path(p) for p in glob.glob(str(REPO / g), recursive=True)]
    return [p for p in out if p.is_file()]


def cmd_impact(name: str) -> int:
    """删 name 前的 fan-in: 哪些文件引用这个表名/文件名。"""
    base = name.split("/")[-1]
    stem = base.rsplit(".", 1)[0]
    pat = re.compile(re.escape(stem))
    cats = {"代码(.py)": [], "配置(.yaml)": [], "文档(.md)": [], "测试": [], "moth": [], "脚本(.sh)": []}
    for f in _scan_files():
        rel = f.relative_to(REPO).as_posix()
        try:
            hits = [i + 1 for i, ln in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines()) if pat.search(ln)]
        except OSError:
            continue
        if not hits:
            continue
        if "tests/" in rel:
            cats["测试"].append((rel, hits))
        elif rel.startswith(".moth/"):
            cats["moth"].append((rel, hits))
        elif rel.endswith(".py"):
            cats["代码(.py)"].append((rel, hits))
        elif rel.endswith((".yaml", ".yml")):
            cats["配置(.yaml)"].append((rel, hits))
        elif rel.endswith(".md"):
            cats["文档(.md)"].append((rel, hits))
        elif rel.endswith(".sh"):
            cats["脚本(.sh)"].append((rel, hits))
    print(f"=== 删 '{name}' 的爆炸半径 (fan-in) ===")
    total = 0
    for cat, items in cats.items():
        if items:
            print(f"\n[{cat}] {len(items)} 文件:")
            for rel, hits in sorted(items):
                print(f"  {rel}: line {hits[:8]}{'...' if len(hits) > 8 else ''}")
            total += len(items)
    print(f"\n总计 {total} 文件引用 '{stem}' — 删前须逐个处理 (改引用/删消费者/迁移)。" if total else f"无文件引用 '{stem}', 删除安全。")
    return 0


def cmd_orphans() -> int:
    fails: list[str] = []
    warns: list[str] = []

    # T1: 测试 collection 真实崩溃 (地面真相, 非正则猜; 删脚本→孤儿测试 module 级 import 崩 = CI 根因)。
    # 负向测试故意引用不存在文件 (测缺失处理) 不在此列 — 它们 collection 不崩。
    try:
        r = subprocess.run(
            ["python", "-m", "pytest", "backend/tests", "--co", "-q", "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "PYTHONPATH": str(REPO / "backend")},
        )
        for ln in (r.stdout + r.stderr).splitlines():
            if ln.startswith("ERROR ") and "collecting" in ln.lower() or (ln.startswith("ERROR ") and "::" not in ln):
                fails.append(f"T1 测试 collection 崩: {ln.strip()[:140]}")
    except Exception as exc:  # noqa: BLE001
        warns.append(f"T1 pytest --co 跑不动 (跳过): {str(exc)[:60]}")

    # T5: CI workflow 硬编码测试清单引用不存在的测试文件 (删测试→ci.yml 悬空, exit 1 = 本轮第二个 CI 崩根因)
    for wf in glob.glob(str(REPO / ".github/workflows/*.yml")):
        wt = Path(wf).read_text(encoding="utf-8", errors="ignore")
        for ref in sorted(set(re.findall(r'(tests/[\w/]+\.py)', wt))):
            if not (REPO / "backend" / ref).exists() and not (REPO / ref).exists():
                fails.append(f"T5 {Path(wf).relative_to(REPO).as_posix()} 引用不存在测试 {ref}")

    # T4: moth claims.yaml command/database 引用的文件路径不存在 (drop 表/删脚本后断言悬空)
    moth = REPO / ".moth/assertions/claims.yaml"
    if moth.exists():
        mt = moth.read_text(encoding="utf-8", errors="ignore")
        for ref in sorted(set(re.findall(r'(backend/[\w/.]+\.py|analysis/[\w/.-]+\.(?:json|md)|scripts/[\w/.]+\.sh)', mt))):
            if not (REPO / ref).exists():
                fails.append(f"T4 .moth/claims.yaml 引用不存在文件 {ref}")

    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")
    print(f"coupling verdict={'FAIL' if fails else 'PASS'} fails={len(fails)} warns={len(warns)}")
    print("提示: 删表/脚本/config 前先跑 --impact <name> 看 fan-in, 改完所有引用再删 (本工具防 CI 孤儿崩复发)。")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--impact", metavar="NAME", help="删 NAME 前看全 fan-in (表名/文件名)")
    p.add_argument("--orphans", action="store_true", help="扫孤儿引用 (默认)")
    args = p.parse_args(argv)
    if args.impact:
        return cmd_impact(args.impact)
    return cmd_orphans()


if __name__ == "__main__":
    sys.exit(main())
