#!/usr/bin/env python3
"""文档漂移检查器 (2026-06-14 地基-reset 后立, owner=docs/data_management_framework.md)。

固化 mythos §16 "漂移是默认态, 对账要机器做" + 用户"及时更新文档": 活索引文档引用的
**代码文件路径**必须实际存在; 引用已删文件 = 漂移 = FAIL。补 check_doc_governance (幽灵引用/
goal行数) 未覆盖的"PROJECT_INDEX 列已删模块"维度。

只查 backend/(scripts|services)/X.py 这类**路径引用** (历史叙事提模块名不算, 只路径算);
扫 PROJECT_INDEX.md + goal.md (活索引文档)。moth 断言调 --check。

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
DOCS = ["PROJECT_INDEX.md", "goal.md"]
# 代码文件路径模式 (反引号内或裸): backend/scripts/x.py, backend/services/a/b.py
PATH_RE = re.compile(r"`?(backend/(?:scripts|services|routers)/[\w/]+\.py)`?")


# 豁免: 历史叙事 (dated changelog bullet `- **YYYY-MM-DD` 或 含退役/已删关键词, doc-governance 原则历史叙述合法) + 模板占位
_HIST_RE = re.compile(r"(^\s*-\s*\*\*20\d\d-\d\d-\d\d|20\d\d-\d\d-\d\d.*(批|log|退役|已删|removed|reset|reclaim|归档)|已删|退役|移除|reset 移除)")
_TPL_RE = re.compile(r"(your_file|example|示例|<[a-z_]+>|占位)")


def audit() -> dict:
    """只查**活索引条目**引用的代码文件路径是否存在; 豁免历史叙事 + 模板占位 (它们提已删文件合法)。"""
    stale: list[dict] = []
    for doc in DOCS:
        p = REPO / doc
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if _HIST_RE.search(line) or _TPL_RE.search(line):
                continue  # 历史叙事/模板: 提已删文件合法, 不算漂移
            for m in PATH_RE.finditer(line):
                ref = m.group(1)
                if not (REPO / ref).exists():
                    stale.append({"doc": doc, "line": i, "ref": ref})
    return {"overall": "PASS" if not stale else "FAIL", "stale_count": len(stale), "stale": stale}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="moth 闸: JSON")
    args = ap.parse_args()
    r = audit()
    if args.check:
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r["overall"] == "PASS" else 1)
    print(f"=== 文档漂移检查 ({', '.join(DOCS)}) ===")
    print(f"  引用已删文件路径: {r['stale_count']} | overall={r['overall']}")
    for s in r["stale"][:40]:
        print(f"    {s['doc']}:{s['line']}  {s['ref']}")
    sys.exit(0 if r["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
