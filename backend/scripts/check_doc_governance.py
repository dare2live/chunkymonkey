"""文档治理执法器 — 新旧文档混用的机械防线 (2026-06-12, 用户点名建机制).

默认语义 (docs/README.md 状态标头契约节为 owner):
  - 控制面 (goal.md / CLAUDE.md / AGENTS.md / PROJECT_INDEX.md / docs/*.md) = live
  - analysis/ = 按日期冻结的证据 (evidence-frozen), 例外必须前 10 行声明状态标头:
      "> 状态: live" | "> 状态: superseded-by: <path>" | "> 状态: retired"

检查项 (FAIL 退出码 1):
  C1 goal.md 行数 <= 上限 (薄入口契约)
  C2 docs/*.md 文件数 <= 上限 (10 活跃 + README)
  C3 控制面文档引用的 analysis/*.md 必须存在 (防幽灵引用误导)
  C4 superseded-by 指向的文件必须存在 (防断链)
  C5 控制面文档引用 retired/superseded 文件 → WARN (叙述历史合法, 当 owner 引用须人审)

用法: PYTHONPATH=backend python backend/scripts/check_doc_governance.py [--root <repo>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

GOAL_MAX_LINES = 165  # from yaml-like contract: goal.md 自述 ~150 行上限, +10% 容忍
DOCS_MAX_FILES = 10   # 2026-06-16 重启: 方法论并入 MASTER §5 删独立 doc, 11→10 (前: +conditional_alpha_program)
_HEADER_SCAN_LINES = 10
_STATUS_RE = re.compile(r">\s*状态\s*[:：]\s*(live|retired|superseded-by\s*[:：]?\s*(\S+))", re.I)
_ANALYSIS_REF_RE = re.compile(r"(?<![\w/])analysis/[\w\-./]+\.(?:md|py|json|yaml)")  # 负向回顾: 不匹配绝对路径/跨仓路径中段


def _status_of(path: Path) -> tuple[str, str | None]:
    """返回 (状态, superseded 目标). 无标头 = 目录默认语义."""
    try:
        head = "\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[:_HEADER_SCAN_LINES])
    except OSError:
        return ("unreadable", None)
    m = _STATUS_RE.search(head)
    if m:
        if m.group(1).lower().startswith("superseded"):
            return ("superseded", m.group(2))
        return (m.group(1).lower(), None)
    return ("default", None)


def run(root: Path) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []

    goal = root / "goal.md"
    if goal.exists():
        n = len(goal.read_text(encoding="utf-8", errors="ignore").splitlines())
        if n > GOAL_MAX_LINES:
            fails.append(f"C1 goal.md {n} 行 > {GOAL_MAX_LINES} (薄入口契约破裂 — 完成项移 ledger)")

    docs_dir = root / "docs"
    if docs_dir.exists():
        md_count = len(list(docs_dir.glob("*.md")))
        if md_count > DOCS_MAX_FILES:
            fails.append(f"C2 docs/ 有 {md_count} 个 md > {DOCS_MAX_FILES} (超额者按 lifecycle 移 analysis/)")

    control_plane = [p for p in [goal, root / "CLAUDE.md", root / "AGENTS.md", root / "PROJECT_INDEX.md"] if p.exists()]
    control_plane += sorted(docs_dir.glob("*.md")) if docs_dir.exists() else []

    retired_like: set[str] = set()
    for f in (root / "analysis").glob("*.md") if (root / "analysis").exists() else []:
        status, target = _status_of(f)
        rel = f.relative_to(root).as_posix()
        if status == "superseded":
            retired_like.add(rel)
            if target and not (root / target.strip("`")).exists():
                fails.append(f"C4 {rel} 的 superseded-by 目标不存在: {target}")
        elif status == "retired":
            retired_like.add(rel)

    for doc in control_plane:
        text = doc.read_text(encoding="utf-8", errors="ignore")
        rel_doc = doc.relative_to(root).as_posix()
        for ref in set(_ANALYSIS_REF_RE.findall(text)):
            if not (root / ref).exists():
                fails.append(f"C3 {rel_doc} 引用不存在的 {ref} (幽灵引用)")
            elif ref in retired_like:
                warns.append(f"C5 {rel_doc} 引用已退役/被取代的 {ref} — 历史叙述合法, 当 owner 引用须改指现行文件")

    return fails, warns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()
    fails, warns = run(Path(args.root))
    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")
    print(f"doc-governance verdict={'FAIL' if fails else 'PASS'} fails={len(fails)} warns={len(warns)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
