#!/usr/bin/env python3
"""文档漂移检查器 (2026-06-14 地基-reset 后立, owner=docs/engineering_governance.md)。

固化 mythos §16 "漂移是默认态, 对账要机器做" + 用户"及时更新文档": **活文档**引用的
**代码文件路径、本地 Markdown 链接、显式 owner 路径**必须实际存在; 代码/配置也不得
引用已删文档或把 legacy ``CLAUDE.md`` 的旧章节继续当规则 owner。引用已删目标 = 漂移 = FAIL。
补 check_doc_governance (幽灵引用/goal行数) 未覆盖的"活索引/规则文档列已删模块"维度。

2026-06-20 扩展 (用户"全面检查文档保持最新避免污染" 后机器化根治): 扫描集从只活索引 (PROJECT_INDEX/
goal) → 扩到**全活文档** (+ AGENTS.md + docs/*.md), 否则规则/契约文档里指向已删脚本的"可运行命令"
漏检 (本次实测 AGENTS/quickstart/engineering_governance 指 reset 删的 audit_*/build_* 脚本)。
历史 ``analysis/`` 内容不全扫；它由 active docs 的显式引用与 lifecycle gate 管，避免冻结证据制造噪音。
``FEATURE_MAP.md`` 作为生成投影也扫描，但不计入人类 active owner/docs 数量。

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
from urllib.parse import unquote, urlsplit

try:
    from scripts.check_doc_governance import active_doc_paths, governed_doc_paths
except ModuleNotFoundError:  # direct ``python backend/scripts/check_doc_drift.py``
    from check_doc_governance import active_doc_paths, governed_doc_paths

REPO = Path(__file__).resolve().parents[2]


def active_docs(root: Path = REPO) -> list[str]:
    """活文档集: 活索引 + 规则 + docs/ 契约 (相对 REPO 的路径)。"""
    return [str(path.relative_to(root)) for path in active_doc_paths(root)]


def governed_docs(root: Path = REPO) -> list[str]:
    """All checked docs, including generated projections such as FEATURE_MAP."""
    return [str(path.relative_to(root)) for path in governed_doc_paths(root)]


# 全路径代码引用: backend/(scripts|services|routers)/x.py 或 scripts/x.(py|sh)。
# (?<![\w/.-]) 锚左边界 -> 不匹配 bestchoice/scripts/x.py 里的 scripts/ 片段 (防剥前缀假阳性)。
PATH_RE = re.compile(
    r"(?<![\w/.-])(backend/(?:scripts|services|routers)/[\w/]+\.py|scripts/[\w/]+\.(?:py|sh))"
)
MARKDOWN_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*)?\)"
)
MARKDOWN_REFERENCE_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)")
OWNER_VALUE_RE = re.compile(
    r"\bowner\s*(?:=|:|：)\s*(?:`([^`]+)`|([^\s,，;；)]+))",
    re.IGNORECASE,
)
_OWNER_EXT_RE = re.compile(r"^(.+?\.(?:md|ya?ml|json|py|sh))", re.IGNORECASE)
_OWNER_ROOTS = ("analysis/", "docs/", "backend/", "scripts/", "sandbox/", "./", "../")
_OWNER_ROOT_FILES = {"AGENTS.md", "goal.md", "PROJECT_INDEX.md"}
_SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".sql", ".sh", ".toml", ".ts", ".tsx", ".js", ".jsx"}
_SOURCE_ROOTS = ("backend", "scripts", "frontend", ".moth")
_SOURCE_EXCLUDES = {
    "backend/tests/test_check_doc_drift.py",
    "backend/tests/test_check_doc_governance.py",
}
_SOURCE_EXCLUDED_PARTS = {"node_modules", "dist", "build", ".git", ".venv", "__pycache__"}
_SOURCE_DOC_REF_RE = re.compile(r"(?<![\w/.-])((?:analysis|docs)/[\w./-]+\.md)")
_LEGACY_CLAUDE_SECTION_RE = re.compile(
    r"CLAUDE\.md\s*(?:§\s*[\d.]*|Rule\s*\d+(?:\.\d+)*|规则\s*#?\d+(?:\.\d+)*)",
    re.IGNORECASE,
)

# 豁免: 历史叙事 (dated changelog) + 模板占位。
#
# **2026-08-14 收窄**: 原来还有一支裸关键词豁免 (已删|退役|移除|retired|deprecated|
# deleted|陈旧|stale)，只要一行里出现任一词，**整行**连同行上的活指针一起被跳过。
# 实证代价: `docs/README.md` 两处指向已归零的 `analysis/` 的引用被它同时藏住 ——
# 第 24 行表格含 "kept/deleted"、第 25 行含 "已经退役"，而后者是一句**活的阅读指令**
# (「跨账号续作时另读 `../analysis/xxx.md`」)，不是历史叙述。两道文档门当时全绿。
# 这是本仓第三次同款 bug (C7 按 basename 豁免吞掉真悬空 / moth 门 elif 短路):
# **豁免的作用域比它的意图大**。
# 收窄依据是实测: 去掉该支后本仓新增 finding = **0**，即它当前不保护任何合法内容;
# 而带日期的历史叙事仍豁免 —— 这也正是本仓既有约定 (历史叙述带日期, 见
# doc_runtime_state.yaml 的同款判据)。想提一个已删路径而不被判红, 就给它加个日期。
_HIST_RE = re.compile(
    r"(^\s*-\s*\*\*20\d\d-\d\d-\d\d"
    r"|20\d\d-\d\d-\d\d.*(批|log|退役|已删|removed|reset|reclaim|归档|retired|deprecated|deleted))"
)
_TPL_RE = re.compile(r"(your_file|example|示例|<[a-z_]+>|占位|placeholder)")
# 文档级豁免: 头部 "状态:" 头注声明自身 deprecated/偏离 的整档 = 历史归档 (lifecycle 保留+冻结)。
# 必须是 "状态:" 声明 (项目约定头注), 不是提及别处 deprecated (防 PROJECT_INDEX 描述别档时被误豁免)。
_DEPRECATED_DOC_RE = re.compile(r"状态:.{0,16}(偏离|研究档|deprecated|archived|归档)")


def _is_deprecated_doc(text: str) -> bool:
    head = "\n".join(text.splitlines()[:15])
    return bool(_DEPRECATED_DOC_RE.search(head))


def _missing_local_link(raw_target: str, *, doc_path: Path, root: Path) -> bool:
    target = raw_target.strip("<>")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return False
    decoded = unquote(parsed.path)
    candidate = Path(decoded)
    resolved = candidate if candidate.is_absolute() else (doc_path.parent / candidate)
    return not resolved.exists()


def _owner_ref(match: re.Match[str]) -> str | None:
    value = (match.group(1) or match.group(2) or "").strip()
    path_like = value.startswith(_OWNER_ROOTS) or value in _OWNER_ROOT_FILES
    if not path_like:
        return None
    extension_match = _OWNER_EXT_RE.match(value)
    return extension_match.group(1) if extension_match else value.rstrip("。.)]}")


def _source_files(root: Path) -> list[Path]:
    """Return governed source/config surfaces that may name document owners."""
    files: list[Path] = []
    for rel_root in _SOURCE_ROOTS:
        base = root / rel_root
        if not base.exists():
            continue
        files.extend(
            path for path in base.rglob("*")
            if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES
            and not (_SOURCE_EXCLUDED_PARTS & set(path.parts))
            and path.relative_to(root).as_posix() not in _SOURCE_EXCLUDES
        )
    return sorted(files)


def audit(root: Path = REPO) -> dict:
    """查活文档引用的代码文件路径是否存在; 豁免: 整档 deprecated + 行级历史叙事/模板占位 (提已删文件合法)。"""
    stale: list[dict] = []
    skipped_deprecated: list[str] = []
    human_docs = active_docs(root)
    docs = governed_docs(root)
    for doc in docs:
        p = root / doc
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if _is_deprecated_doc(text):
            skipped_deprecated.append(doc)
            continue  # 整档 deprecated: 历史归档, 不扫
        in_fence = False
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if not in_fence:
                link_matches = list(MARKDOWN_LINK_RE.finditer(line))
                reference_match = MARKDOWN_REFERENCE_DEF_RE.match(line)
                raw_targets = [match.group(1) for match in link_matches]
                if reference_match:
                    raw_targets.append(reference_match.group(1))
                for raw_target in raw_targets:
                    ref = raw_target.strip("<>")
                    if _missing_local_link(ref, doc_path=p, root=root):
                        stale.append({"doc": doc, "line": i, "ref": ref, "kind": "markdown_link"})
                for match in OWNER_VALUE_RE.finditer(line):
                    ref = _owner_ref(match)
                    if ref is None:
                        continue
                    candidate = ((p.parent / ref) if ref.startswith(("./", "../")) else (root / ref))
                    if not candidate.exists():
                        stale.append({"doc": doc, "line": i, "ref": ref, "kind": "owner"})
            if _HIST_RE.search(line) or _TPL_RE.search(line):
                continue  # 历史叙事/模板: 提已删文件合法, 不算漂移
            for m in PATH_RE.finditer(line):
                ref = m.group(1)
                if not (root / ref).exists():
                    stale.append({"doc": doc, "line": i, "ref": ref, "kind": "code_path"})
    source_files = _source_files(root)
    for source_path in source_files:
        rel_source = source_path.relative_to(root).as_posix()
        text = source_path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            for ref in sorted(set(_SOURCE_DOC_REF_RE.findall(line))):
                if not (root / ref).exists():
                    stale.append({"doc": rel_source, "line": i, "ref": ref, "kind": "source_doc_ref"})
            legacy_match = _LEGACY_CLAUDE_SECTION_RE.search(line)
            if legacy_match:
                stale.append({
                    "doc": rel_source,
                    "line": i,
                    "ref": legacy_match.group(0),
                    "kind": "legacy_claude_owner",
                })
    return {"overall": "PASS" if not stale else "FAIL",
            "stale_count": len(stale), "n_docs": len(human_docs),
            "n_generated_docs": len(docs) - len(human_docs),
            "n_source_files": len(source_files),
            "skipped_deprecated": skipped_deprecated, "stale": stale}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="moth 闸: JSON")
    args = ap.parse_args()
    r = audit()
    if args.check:
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r["overall"] == "PASS" else 1)
    print(f"=== 文档漂移检查 ({r['n_docs']} 活文档 + {r['n_generated_docs']} 生成投影) ===")
    print(f"  无效代码路径/本地链接/owner/source-doc: {r['stale_count']} | overall={r['overall']}")
    for s in r["stale"][:60]:
        print(f"    {s['doc']}:{s['line']} [{s['kind']}] {s['ref']}")
    sys.exit(0 if r["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
