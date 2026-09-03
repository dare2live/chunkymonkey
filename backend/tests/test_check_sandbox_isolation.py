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


def _force_store_unreadable(monkeypatch, store_path):
    """让 connect_ro 抛错逼 check_c2 走 except 分支; 并把 manifest 指向给定路径。

    路径经 database_manifest 解析 (而非 REPO 拼字面量) —— 硬编码 duckdb 路径会被
    check_rule_compliance 的 DB 边界检查挡下, 那道门是对的。
    """
    import services.data_access.resolver as resolver
    import services.database_manifest as dbm

    def _boom(alias):
        raise RuntimeError("IO Error: simulated")

    class _FakeManifest:
        def path_for(self, alias):
            assert alias == "experiment_store"
            return store_path

    monkeypatch.setattr(resolver, "connect_ro", _boom)
    monkeypatch.setattr(dbm, "get_database_manifest", lambda: _FakeManifest())


def test_c2_missing_store_is_not_a_violation(monkeypatch, tmp_path):
    """库**不存在** → 一个 run_id 都不存在 → 没有文档能嵌入未 promote 的 run_id → 放行。

    2026-09-03 加。原实现把「库不存在」判成 UNVERIFIED 并阻断, 犯的是
    「门问的是我能不能核实, 却把不能核实当成了违规」。
    实测后果: data/*.duckdb 在 .gitignore, 该库不在版本控制, 全仓只有
    build_experiment_store.py 能造它 → 任何 fresh clone 都提交不了
    (safe_commit.sh 有 set -o pipefail, 会传播本脚本退出码)。
    """
    _force_store_unreadable(monkeypatch, tmp_path / "data" / "experiment_store.duckdb")
    assert sandbox_gate.check_c2() == []


def test_c2_present_but_unreadable_still_fails_closed(monkeypatch, tmp_path):
    """库**存在却读不了** (损坏/权限/schema 不符) 是真的不能核实 → 仍然阻断。

    与上一条配对: 修复只放宽「不存在」这一种情形, 不许顺手把 fail-closed 整个拆掉。
    """
    (tmp_path / "data").mkdir()
    store = tmp_path / "data" / "experiment_store.duckdb"
    store.write_text("not a duckdb file")
    _force_store_unreadable(monkeypatch, store)
    out = sandbox_gate.check_c2()
    assert out and "UNVERIFIED" in out[0]
    assert "存在但不可读" in out[0]
