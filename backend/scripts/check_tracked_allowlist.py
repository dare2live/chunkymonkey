#!/usr/bin/env python3
"""跟踪文档白名单执法器 (2026-09-04 A2.1 新立; 替代 check_doc_governance.py 的 C2/C3/C9
与旧版规则文档「禁止平行文档」全段——那两样连同旧规则文档本身随 A2.3 一起退役)。

守两件事 (FAIL 退出码 1):
  1. git 跟踪的 ``*.md`` 文件集合 **必须是** 7 份白名单的子集
     (清单见 backend/config/tracked_doc_allowlist.yaml 的 tracked_md;
     frontend/DESIGN.md / frontend/README.md)。业主裁决"文档越少越好"；这次 diff 新增一份
     跟踪 md 就是**这次 diff** 的错，不是历史欠账 → diff_correctness。
  2. ``bestchoice/`` 下 git 跟踪的文件集合 **必须精确等于**冻结清单 (BESTCHOICE_ALLOWLIST，
     一字不差)——它是 challenger 的冻结证据包，多一个文件是复活，少一个文件是残缺。

白名单只在 governance_gates.yaml 显式改本门时变更，不接受隐式放宽。

用法:
  python backend/scripts/check_tracked_allowlist.py [--root <repo>]   # 人看
  python backend/scripts/check_tracked_allowlist.py --check           # moth/CI 闸: JSON, exit 0/1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_ALLOWLIST_YAML = REPO / "backend" / "config" / "tracked_doc_allowlist.yaml"


def _load_allowlists() -> tuple[frozenset[str], frozenset[str]]:
    """白名单是**数据**不是代码 —— 读 YAML, 门脚本里不留任何路径字面量。

    2026-09-04: 原实现把两份清单 hardcode 在本文件里, 其中一条是探索目录下的 README,
    被 check_sandbox_isolation 的 C1 抓成「backend 代码引用探索目录」而挡住提交。
    C1 想守的是**依赖**(from/import), 实际问的是**字符串里出现过那个目录前缀** ——
    它的正则第三支对路径访问是对的, 对"一份文件名清单"是误报。
    修法不是给 C1 开文件级豁免(那会连带放过本文件里真正的 import), 而是让本门无字面量可抓。
    """
    import yaml

    raw = yaml.safe_load(_ALLOWLIST_YAML.read_text(encoding="utf-8")) or {}
    md = raw.get("tracked_md")
    bc = raw.get("bestchoice_frozen")
    if not isinstance(md, list) or not md or not isinstance(bc, list) or not bc:
        raise SystemExit(
            f"{_ALLOWLIST_YAML} 缺 tracked_md / bestchoice_frozen 或为空 —— "
            "白名单为空会让本门恒绿, fail closed"
        )
    # bestchoice_frozen 在 YAML 里是仓库根相对 (否则 check_dead_references 的
    # config-dead-path 会把 "scripts/xxx.py" 解析成 <repo>/scripts/xxx.py 判成死引用);
    # 本门内部按 bestchoice/ 相对比对, 所以在这里剥前缀。
    bc_rel = frozenset(str(x).removeprefix("bestchoice/") for x in bc)
    return frozenset(str(x) for x in md), bc_rel


MD_ALLOWLIST, BESTCHOICE_ALLOWLIST = _load_allowlists()


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def audit(root: Path = REPO) -> dict:
    tracked = _tracked_files(root)

    md_files = {p for p in tracked if p.endswith(".md")}
    extra_md = sorted(md_files - MD_ALLOWLIST)

    bestchoice_prefix = "bestchoice/"
    bestchoice_files = {
        p[len(bestchoice_prefix):] for p in tracked if p.startswith(bestchoice_prefix)
    }
    extra_bestchoice = sorted(bestchoice_files - BESTCHOICE_ALLOWLIST)
    missing_bestchoice = sorted(BESTCHOICE_ALLOWLIST - bestchoice_files)

    fails: list[str] = []
    if extra_md:
        fails.append(f"tracked markdown 越出白名单 (⊆ 7 文件): {extra_md}")
    if extra_bestchoice:
        fails.append(f"bestchoice/ 跟踪集合多出冻结清单外文件: {extra_bestchoice}")
    if missing_bestchoice:
        fails.append(f"bestchoice/ 跟踪集合缺失冻结清单文件: {missing_bestchoice}")

    return {
        "overall": "PASS" if not fails else "FAIL",
        "extra_md": extra_md,
        "extra_bestchoice": extra_bestchoice,
        "missing_bestchoice": missing_bestchoice,
        "n_tracked_md": len(md_files),
        "n_allowlist_md": len(MD_ALLOWLIST),
        "fails": fails,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=REPO)
    ap.add_argument("--check", action="store_true", help="moth/CI 闸: JSON")
    args = ap.parse_args(argv)

    r = audit(args.root)

    if args.check:
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["overall"] == "PASS" else 1

    print(f"=== 跟踪文档白名单 ({r['n_tracked_md']} 跟踪 md / {r['n_allowlist_md']} 白名单) === overall={r['overall']}")
    for f in r["fails"]:
        print(f"  FAIL: {f}")
    return 0 if r["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
