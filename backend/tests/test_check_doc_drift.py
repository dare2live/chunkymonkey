"""check_doc_drift 扩展版单测 (2026-06-20).

测最易出 bug 的正则+豁免逻辑 (本次扩展踩过的两坑):
  - PATH_RE mid-path 假阳性: bestchoice/scripts/x.py 不该被剥前缀匹配成 scripts/x.py
  - _is_deprecated_doc: "状态: deprecated" 头注豁免整档, 但提及别处 deprecated (PROJECT_INDEX) 不豁免
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.check_doc_drift import (  # noqa: E402
    PATH_RE,
    _HIST_RE,
    _TPL_RE,
    _is_deprecated_doc,
    audit,
)


def _refs(line: str) -> list[str]:
    return [m.group(1) for m in PATH_RE.finditer(line)]


def test_path_re_matches_full_paths():
    assert _refs("run `backend/scripts/foo.py`") == ["backend/scripts/foo.py"]
    assert _refs("see backend/services/a/b.py here") == ["backend/services/a/b.py"]
    assert _refs("`scripts/modal_push.py`") == ["scripts/modal_push.py"]
    assert _refs("scripts/chunkyctl.sh") == ["scripts/chunkyctl.sh"]


def test_path_re_no_midpath_false_positive():
    # bestchoice/scripts/x.py 不该匹配出 scripts/x.py (本次 scan 踩过的剥前缀坑)
    assert _refs("bestchoice/scripts/formula_parameter_search.py") == []
    assert _refs("vendor/backend/scripts/x.py") == []   # 中间路径不匹配


def test_hist_re_exempts_retirement_context():
    assert _HIST_RE.search("the former audit_x.py was retired in the 2026-06-16 reset")
    assert _HIST_RE.search("- **2026-06-16 reset**: 删 build_x")
    assert _HIST_RE.search("audit_x.py 已删")
    assert _HIST_RE.search("deprecated helper")
    assert not _HIST_RE.search("run backend/scripts/foo.py for the live gate")  # 无退役语境=不豁免


def test_deprecated_doc_only_on_status_headnote():
    # 状态: 头注声明自身 deprecated -> 整档豁免
    assert _is_deprecated_doc("# Foo\n> **状态: 大部偏离 / deprecated (2026-06-20)**\n正文")
    assert _is_deprecated_doc("# Bar\n> 状态: 研究档, 部分偏离\n正文")
    # 仅提及别处 deprecated (PROJECT_INDEX 描述别档) -> 不豁免 (防本次误豁免 PROJECT_INDEX 的 bug)
    assert not _is_deprecated_doc("# PROJECT_INDEX\n> implementation_plan 标 deprecated (留参考)\n表")
    assert not _is_deprecated_doc("# Active\n正常活文档无状态头注\n正文")


def test_tpl_re_exempts_placeholders():
    assert _TPL_RE.search("backend/scripts/<name>.py")
    assert _TPL_RE.search("your_file.py 示例")


def test_audit_current_repo_passes():
    # 集成: 当前仓库活文档应 PASS (本次保鲜后 0 悬空)
    r = audit()
    assert r["overall"] == "PASS", f"悬空: {r['stale']}"
    assert r["n_docs"] >= 10
