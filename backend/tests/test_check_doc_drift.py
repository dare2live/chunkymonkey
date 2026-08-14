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


def test_hist_re_exempts_dated_retirement_only():
    """历史叙事豁免**必须带日期** —— 裸关键词豁免会把活指针一起吞掉。

    2026-08-14 实证: 原来只要一行里出现「已删/退役/deleted」等词, **整行**连同行上的
    活指针一起跳过。`docs/README.md` 两处指向已归零 `analysis/` 的引用就是这样被藏住的,
    其中一处还是活的阅读指令(「跨账号续作时另读 X」), 而两道文档门当时全绿。
    收窄后本仓新增 finding = 0, 即那支豁免不保护任何合法内容。
    """
    assert _HIST_RE.search("- **2026-06-16 reset**: 删 build_x")
    assert _HIST_RE.search("2026-06-16 已删 audit_x.py")
    assert _HIST_RE.search("the former audit_x.py was retired in the 2026-06-16 reset")

    # 无日期的裸关键词**不再**豁免 —— 这正是曾经藏住真悬空的那条路径。
    assert not _HIST_RE.search("audit_x.py 已删")
    assert not _HIST_RE.search("deprecated helper")
    assert not _HIST_RE.search(
        "旧体系已经退役；跨账号续作时另读 `../analysis/account_switch_handoff.md`"
    ), "活的阅读指令不得因同行提到「退役」而被整行豁免"
    assert not _HIST_RE.search("run backend/scripts/foo.py for the live gate")


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


def test_missing_local_markdown_link_fails_without_scanning_analysis(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text(
        "[missing](missing.md) [web](https://example.com) [anchor](#here)\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "old.md").write_text(
        "[historical missing](also_missing.md)\n",
        encoding="utf-8",
    )

    r = audit(tmp_path)

    assert r["overall"] == "FAIL"
    assert r["stale"] == [
        {"doc": "docs/README.md", "line": 1, "ref": "missing.md", "kind": "markdown_link"}
    ]


def test_nonexistent_explicit_owner_path_fails(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "contract.md").write_text(
        "Current route owner=`analysis/missing_plan.md`.\n",
        encoding="utf-8",
    )

    r = audit(tmp_path)

    assert r["stale"] == [
        {
            "doc": "docs/contract.md",
            "line": 1,
            "ref": "analysis/missing_plan.md",
            "kind": "owner",
        }
    ]


def test_owner_parser_stops_at_filename_before_prose(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "plan.md").write_text("evidence\n", encoding="utf-8")
    (tmp_path / "docs" / "contract.md").write_text(
        "owner=analysis/plan.md。**= current**\n",
        encoding="utf-8",
    )

    assert audit(tmp_path)["stale"] == []


def test_path_like_owner_with_wildcard_is_not_silently_ignored(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "contract.md").write_text(
        "owner=analysis/purge_*.yaml\n",
        encoding="utf-8",
    )

    assert audit(tmp_path)["stale"] == [
        {
            "doc": "docs/contract.md",
            "line": 1,
            "ref": "analysis/purge_*.yaml",
            "kind": "owner",
        }
    ]


def test_missing_reference_style_markdown_link_target_fails(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "contract.md").write_text(
        "See [plan][current].\n\n[current]: missing.md\n",
        encoding="utf-8",
    )

    assert audit(tmp_path)["stale"] == [
        {
            "doc": "docs/contract.md",
            "line": 3,
            "ref": "missing.md",
            "kind": "markdown_link",
        }
    ]


def test_generated_feature_map_is_scanned_but_not_counted_as_human_doc(tmp_path):
    (tmp_path / "AGENTS.md").write_text("live\n", encoding="utf-8")
    (tmp_path / "FEATURE_MAP.md").write_text(
        "[missing projection](docs/missing.md)\n",
        encoding="utf-8",
    )

    r = audit(tmp_path)

    assert r["n_docs"] == 1
    assert r["n_generated_docs"] == 1
    assert r["stale"] == [
        {
            "doc": "FEATURE_MAP.md",
            "line": 1,
            "ref": "docs/missing.md",
            "kind": "markdown_link",
        }
    ]


def test_existing_absolute_local_markdown_link_is_allowed(tmp_path):
    (tmp_path / "docs").mkdir()
    target = tmp_path / "evidence file.md"
    target.write_text("evidence\n", encoding="utf-8")
    encoded_target = str(target).replace(" ", "%20")
    (tmp_path / "docs" / "contract.md").write_text(
        f"[evidence](<{encoded_target}>)\n",
        encoding="utf-8",
    )

    assert audit(tmp_path)["stale"] == []


def test_source_config_reference_to_deleted_doc_fails(tmp_path):
    (tmp_path / "backend" / "config").mkdir(parents=True)
    (tmp_path / "backend" / "config" / "registry.yaml").write_text(
        "# owner=analysis/deleted_design.md\n",
        encoding="utf-8",
    )

    assert audit(tmp_path)["stale"] == [
        {
            "doc": "backend/config/registry.yaml",
            "line": 1,
            "ref": "analysis/deleted_design.md",
            "kind": "source_doc_ref",
        }
    ]


def test_source_cannot_keep_legacy_claude_section_as_owner(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gate.sh").write_text(
        "# follow CLAUDE.md Rule 10.6\n",
        encoding="utf-8",
    )

    assert audit(tmp_path)["stale"] == [
        {
            "doc": "scripts/gate.sh",
            "line": 1,
            "ref": "CLAUDE.md Rule 10.6",
            "kind": "legacy_claude_owner",
        }
    ]


def test_third_party_and_built_frontend_sources_are_not_scanned(tmp_path):
    for rel in ("frontend/node_modules/pkg/index.js", "frontend/dist/app.js"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// owner=analysis/vendor_missing.md\n", encoding="utf-8")

    result = audit(tmp_path)

    assert result["stale"] == []
    assert result["n_source_files"] == 0
