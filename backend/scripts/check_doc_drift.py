#!/usr/bin/env python3
"""文档漂移检查器 (2026-06-14 地基-reset 后立, owner=docs/data_management_framework.md)。

固化 mythos §16 "漂移是默认态, 对账要机器做" + 用户"及时更新文档": **活文档**引用的
**代码文件路径**必须实际存在; 引用已删文件 = 漂移 = FAIL。补 check_doc_governance (幽灵引用/
goal行数) 未覆盖的"活索引/规则文档列已删模块"维度。

2026-06-20 扩展 (用户"全面检查文档保持最新避免污染" 后机器化根治): 扫描集从只活索引 (PROJECT_INDEX/
goal) → 扩到**全活文档** (+ AGENTS.md + docs/*.md), 否则规则/契约文档里指向已删脚本的"可运行命令"
漏检 (本次实测 AGENTS/quickstart/engineering_governance 指 reset 删的 audit_*/build_* 脚本)。

只查 `backend/(scripts|services|routers)/X.py` 与 `scripts/X.(py|sh)` 这类**全路径引用** (历史叙事提
裸模块名不算, 只路径算 — 防假阳性)。路径用 lookbehind 锚边界, 防 `bestchoice/scripts/x.py` 被误剥
前缀匹配成 `scripts/x.py` (本次 scan 踩过的坑)。豁免: 历史叙事 (dated/退役/已删/retired/deprecated)
+ 模板占位 (它们提已删文件合法)。moth 断言 doc-drift 调 --check。

用法:
  python backend/scripts/check_doc_drift.py            # 人看
  python backend/scripts/check_doc_drift.py --check    # moth 闸 (JSON, exit 0/1)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def active_docs() -> list[str]:
    """活文档集: 活索引 + 规则 + docs/ 契约 (相对 REPO 的路径)。"""
    docs = ["PROJECT_INDEX.md", "goal.md", "AGENTS.md"]
    docs += sorted(str(p.relative_to(REPO)) for p in (REPO / "docs").glob("*.md"))
    return docs


# 全路径代码引用: backend/(scripts|services|routers)/x.py 或 scripts/x.(py|sh)。
# (?<![\w/.-]) 锚左边界 -> 不匹配 bestchoice/scripts/x.py 里的 scripts/ 片段 (防剥前缀假阳性)。
PATH_RE = re.compile(
    r"(?<![\w/.-])(backend/(?:scripts|services|routers)/[\w/]+\.py|scripts/[\w/]+\.(?:py|sh))"
)

# 豁免: 历史叙事 (dated changelog / 退役/已删/reset/retired/deprecated 上下文) + 模板占位。
_HIST_RE = re.compile(
    r"(^\s*-\s*\*\*20\d\d-\d\d-\d\d"
    r"|20\d\d-\d\d-\d\d.*(批|log|退役|已删|removed|reset|reclaim|归档|retired|deprecated|deleted)"
    r"|已删|退役|移除|reset 移除|retired|deprecated|deleted|陈旧|stale)"
)
_TPL_RE = re.compile(r"(your_file|example|示例|<[a-z_]+>|占位|placeholder)")
# 文档级豁免: 头部 "状态:" 头注声明自身 deprecated/偏离 的整档 = 历史归档 (lifecycle 保留+冻结)。
# 必须是 "状态:" 声明 (项目约定头注), 不是提及别处 deprecated (防 PROJECT_INDEX 描述别档时被误豁免)。
_DEPRECATED_DOC_RE = re.compile(r"状态:.{0,16}(偏离|研究档|deprecated|archived|归档)")


def _is_deprecated_doc(text: str) -> bool:
    head = "\n".join(text.splitlines()[:15])
    return bool(_DEPRECATED_DOC_RE.search(head))


def audit() -> dict:
    """查活文档引用的代码文件路径是否存在; 豁免: 整档 deprecated + 行级历史叙事/模板占位 (提已删文件合法)。"""
    stale: list[dict] = []
    skipped_deprecated: list[str] = []
    docs = active_docs()
    for doc in docs:
        p = REPO / doc
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if _is_deprecated_doc(text):
            skipped_deprecated.append(doc)
            continue  # 整档 deprecated: 历史归档, 不扫
        for i, line in enumerate(text.splitlines(), 1):
            if _HIST_RE.search(line) or _TPL_RE.search(line):
                continue  # 历史叙事/模板: 提已删文件合法, 不算漂移
            for m in PATH_RE.finditer(line):
                ref = m.group(1)
                if not (REPO / ref).exists():
                    stale.append({"doc": doc, "line": i, "ref": ref})
    return {"overall": "PASS" if not stale else "FAIL",
            "stale_count": len(stale), "n_docs": len(docs),
            "skipped_deprecated": skipped_deprecated, "stale": stale}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="moth 闸: JSON")
    args = ap.parse_args()
    r = audit()
    if args.check:
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r["overall"] == "PASS" else 1)
    print(f"=== 文档漂移检查 ({r['n_docs']} 活文档: 活索引+AGENTS+docs/) ===")
    print(f"  引用已删文件路径: {r['stale_count']} | overall={r['overall']}")
    for s in r["stale"][:60]:
        print(f"    {s['doc']}:{s['line']}  {s['ref']}")
    sys.exit(0 if r["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
