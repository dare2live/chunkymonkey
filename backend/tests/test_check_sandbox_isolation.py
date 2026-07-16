"""check_sandbox_isolation 单测 (2026-06-21) — 隔离门 C1/C3 正则+命名 + 当前仓库集成 PASS。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import scripts.check_sandbox_isolation as sandbox_gate  # noqa: E402

from scripts.check_sandbox_isolation import (  # noqa: E402
    C1_PAT,
    check_c1,
    check_c3,
    control_doc_paths,
)


def test_c1_pat_flags_sandbox_refs():
    assert C1_PAT.search("from sandbox.d4_exp import foo")
    assert C1_PAT.search("import sandbox")
    assert C1_PAT.search("p = 'sandbox/d4_exp/scratch.duckdb'")
    assert C1_PAT.search('open("sandbox/x/results.json")')


def test_c1_pat_no_false_positive_on_guard():
    # guard 的正常 import 不该误判 (from services.sandbox_guard / enable_sandbox_guard)
    assert not C1_PAT.search("from services.sandbox_guard import enable_sandbox_guard")
    assert not C1_PAT.search("    enable_sandbox_guard()")
    assert not C1_PAT.search("# 探索写 sandbox scratch 用 sandbox_scratch()")


def test_current_repo_passes():
    # 集成: 全清后当前仓库 C1(backend不引用sandbox) + C3(无探索runner) 应空
    assert check_c1() == [], f"C1 漏码: {check_c1()}"
    assert check_c3() == [], f"C3 探索runner: {check_c3()}"


def test_control_docs_follow_live_doc_registry(tmp_path):
    (tmp_path / "AGENTS.md").write_text("policy\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("legacy\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    contract = tmp_path / "docs" / "strategy_validation_contract.md"
    contract.write_text("contract\n", encoding="utf-8")

    assert control_doc_paths(tmp_path) == [tmp_path / "AGENTS.md", contract]


def test_c2_warning_is_blocking_and_never_reported_as_pass(monkeypatch, capsys):
    monkeypatch.setattr(sandbox_gate, "check_c1", lambda: [])
    monkeypatch.setattr(sandbox_gate, "check_c3", lambda: [])
    monkeypatch.setattr(sandbox_gate, "check_c2", lambda: ["UNVERIFIED evidence"])

    assert sandbox_gate.main() == 1
    output = capsys.readouterr().out
    assert "[C2 WARN]" in output
    assert "[C2 OK]" not in output
