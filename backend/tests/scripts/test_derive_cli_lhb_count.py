"""``chunkyctl derive market-pulse-lhb-count`` CLI 层单测（backend/scripts/derive_cli.py
的新分支, cut_lhb_count_finish 2026-09-19, build_lhb_count_finish.md）。

只测 CLI 层职责——参数解析/路由/dry-run 与 --execute 分支/writer_lock 接线/退出码/
打印——不测重算的 SQL 本身（业务逻辑归 backend/tests/test_market_pulse.py 的
recompute_lhb_count_history 用例组）。``services.market_pulse.
recompute_lhb_count_history`` 全程打桩，不碰真库（测试必须自带 fixture，不许触碰宿主
data/*.duckdb）。writer_lock 除外：L3 用真实 writer_lock + 私有 lock 文件路径（不
monkeypatch），证明 --execute 真的接了 writer_lock 而不是一个放行一切的空上下文管理器
（参照 test_cleanup_out_of_scope_rows.py 的 B2 写法）。

同时锁一条架构边界回归：market-pulse-lhb-count 是 derive_cli.py 内的独立分支，**不**进
services.derive_runtime.DERIVE_TARGETS —— 与 rally-gt 同一条边界
(test_derive_runtime_s5.py::test_s5_derive_targets_are_qfq_and_form_only)。
"""
from __future__ import annotations

import json

import pytest

from scripts import derive_cli
from services.writer_lock import WRITER_LOCK_PATH_ENV, writer_lock


# ── L2: dry-run (default) 与 --execute 路由 ──────────────────────────────


def test_lhb_count_default_is_dry_run(monkeypatch, capsys) -> None:
    calls: dict = {}

    def fake_recompute(*, dry_run=False):
        calls["dry_run"] = dry_run
        return {"dry_run": True, "rows_would_change": 46, "total_delta": -332, "diffs": []}

    monkeypatch.setattr("services.market_pulse.recompute_lhb_count_history", fake_recompute)
    rc = derive_cli.main(["market-pulse-lhb-count"])

    assert rc == 0
    assert calls == {"dry_run": True}, "不带 --execute 必须以 dry_run=True 调用, 不写库"
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"dry_run": True, "rows_would_change": 46, "total_delta": -332, "diffs": []}


def test_lhb_count_execute_calls_dry_run_false_inside_writer_lock(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "lhb_execute.lock"))
    calls: dict = {}

    def fake_recompute(*, dry_run=False):
        calls["dry_run"] = dry_run
        # writer_lock 必须已经持有 —— 断言真实锁文件此刻确实 busy。
        from services.writer_lock import writer_lock_status

        assert writer_lock_status().busy, "--execute 调用 recompute 时 writer_lock 必须已持有"
        return {"rows_recomputed": 46}

    monkeypatch.setattr("services.market_pulse.recompute_lhb_count_history", fake_recompute)
    rc = derive_cli.main(["market-pulse-lhb-count", "--execute"])

    assert rc == 0
    assert calls == {"dry_run": False}, "--execute 必须以 dry_run=False 调用"
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"rows_recomputed": 46}


def test_lhb_count_failure_is_nonzero_exit_and_reports_reason(monkeypatch, capsys) -> None:
    def boom(*, dry_run=False):
        raise RuntimeError("smartmoney db unreachable")

    monkeypatch.setattr("services.market_pulse.recompute_lhb_count_history", boom)
    rc = derive_cli.main(["market-pulse-lhb-count"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "smartmoney db unreachable" in err


# ── L3: writer_lock 忙 —— 真实锁, 不 monkeypatch (test_cleanup_out_of_scope_rows.py B2 写法) ──


def test_lhb_count_execute_lock_busy_exit_3_never_calls_recompute(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "lhb_busy.lock"))
    called = {"n": 0}

    def fake_recompute(*, dry_run=False):
        called["n"] += 1
        return {"rows_recomputed": 0}

    monkeypatch.setattr("services.market_pulse.recompute_lhb_count_history", fake_recompute)

    with writer_lock("other-owner-holding-the-window"):
        rc = derive_cli.main(["market-pulse-lhb-count", "--execute"])

    assert rc == 3
    assert called["n"] == 0, "锁被占用时 main() 不得进入 recompute —— 表不可能被动"
    err = capsys.readouterr().err
    assert "LOCK_BUSY" in err


def test_lhb_count_dry_run_does_not_touch_writer_lock(monkeypatch, tmp_path) -> None:
    """dry-run (无 --execute) 不该尝试获取 writer_lock —— 锁被占用时 dry-run 仍应成功。"""
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "lhb_dryrun.lock"))

    def fake_recompute(*, dry_run=False):
        return {"dry_run": True, "rows_would_change": 0, "total_delta": 0, "diffs": []}

    monkeypatch.setattr("services.market_pulse.recompute_lhb_count_history", fake_recompute)

    with writer_lock("other-owner-holding-the-window"):
        rc = derive_cli.main(["market-pulse-lhb-count"])

    assert rc == 0, "dry-run 不应该受 writer_lock 占用影响 (它根本不该去拿锁)"


# ── 参数校验: 与 qfq/form/rally-gt 互斥的 flags ──────────────────────────


@pytest.mark.parametrize(
    "bad_args",
    [
        ["market-pulse-lhb-count", "--rebuild"],
        ["market-pulse-lhb-count", "--check-only"],
        ["market-pulse-lhb-count", "--from-accepted"],
        ["market-pulse-lhb-count", "--allow-legacy-fill"],
        ["market-pulse-lhb-count", "--data-end", "20250531"],
    ],
)
def test_lhb_count_rejects_qfq_form_rally_gt_only_flags(bad_args) -> None:
    with pytest.raises(SystemExit):
        derive_cli.main(bad_args)


@pytest.mark.parametrize(
    "bad_args",
    [
        ["qfq", "--execute"],
        ["form", "--execute"],
        ["rally-gt", "--execute"],
    ],
)
def test_execute_rejected_outside_lhb_count(bad_args) -> None:
    with pytest.raises(SystemExit):
        derive_cli.main(bad_args)


def test_derive_runtime_targets_stay_qfq_form_only() -> None:
    """架构边界回归: market-pulse-lhb-count 不得混入 S5/S7 DERIVE_TARGETS 锁定契约。"""
    from services.derive_runtime import DERIVE_TARGETS

    assert set(DERIVE_TARGETS) == {"qfq", "form"}
    assert derive_cli.LHB_COUNT_TARGET == "market-pulse-lhb-count"
    assert "market-pulse-lhb-count" not in DERIVE_TARGETS
