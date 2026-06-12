"""文档治理执法器单测 — 可红性三路 (幽灵引用/断链/超限) + 误报防线."""
from __future__ import annotations

from pathlib import Path

from scripts.check_doc_governance import run


def _mk(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_clean_tree_passes(tmp_path):
    _mk(tmp_path, "goal.md", "# goal\n详见 analysis/evidence_a.md\n")
    _mk(tmp_path, "analysis/evidence_a.md", "# 证据\n")
    _mk(tmp_path, "docs/README.md", "# map\n")
    fails, warns = run(tmp_path)
    assert fails == [] and warns == []


def test_ghost_reference_fails(tmp_path):
    _mk(tmp_path, "goal.md", "# goal\nowner = analysis/nope_missing.md\n")
    fails, _ = run(tmp_path)
    assert any("幽灵引用" in f and "nope_missing" in f for f in fails)


def test_absolute_cross_repo_path_not_flagged(tmp_path):
    # 误报防线: 跨仓绝对路径中段的 analysis/ 不算本仓引用 (AGENTS.md bestchoice 实例)
    _mk(tmp_path, "goal.md", "# goal\n/Users/x/other_repo/analysis/foreign.md\n")
    fails, _ = run(tmp_path)
    assert fails == []


def test_broken_superseded_chain_fails(tmp_path):
    _mk(tmp_path, "analysis/old_plan.md", "# old\n> 状态: superseded-by: docs/nope.md\n")
    fails, _ = run(tmp_path)
    assert any("C4" in f and "old_plan" in f for f in fails)


def test_retired_reference_warns(tmp_path):
    _mk(tmp_path, "goal.md", "# goal\n见 analysis/dead.md\n")
    _mk(tmp_path, "analysis/dead.md", "# dead\n> 状态: retired\n")
    fails, warns = run(tmp_path)
    assert fails == [] and any("dead.md" in w for w in warns)


def test_goal_over_limit_fails(tmp_path):
    _mk(tmp_path, "goal.md", "x\n" * 200)
    fails, _ = run(tmp_path)
    assert any("C1" in f for f in fails)
