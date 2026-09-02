"""``chunkyctl derive rally-gt`` CLI 层单测（backend/scripts/derive_cli.py 的新分支）。

只测 CLI 层职责——参数解析/路由/退出码/打印——不测业务逻辑（业务逻辑归
``backend/tests/test_rally_gt.py``）。``services.rally_gt.rebuild`` 全程打桩，
不碰真库（测试必须自带 fixture，不许触碰宿主 data/feature_store.duckdb）。

同时锁一条架构边界回归：rally-gt 是 derive_cli.py 内的独立分支，**不**进
``services.derive_runtime.DERIVE_TARGETS`` —— 那条 S5/S7 通道被
test_derive_runtime_s5.py::test_s5_derive_targets_are_qfq_and_form_only 锁定为
严格 {"qfq","form"}（accepted-only、独立于 acquire 的窄契约）。
"""
from __future__ import annotations

import json

import pytest

from scripts import derive_cli


def test_rally_gt_routes_to_rebuild_and_prints_stats(monkeypatch, capsys) -> None:
    calls: dict = {}

    def fake_rebuild(*, data_end=None):
        calls["data_end"] = data_end
        return {"n_pos": 1, "n_neg": 2, "data_end": "2025-05-31"}

    monkeypatch.setattr("services.rally_gt.rebuild", fake_rebuild)
    rc = derive_cli.main(["rally-gt"])

    assert rc == 0
    assert calls == {"data_end": None}, "未传 --data-end 时须原样传 None 给 rebuild（由它自己决定 cutoff）"
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"n_pos": 1, "n_neg": 2, "data_end": "2025-05-31"}


def test_rally_gt_forwards_data_end(monkeypatch) -> None:
    calls: dict = {}

    def fake_rebuild(*, data_end=None):
        calls["data_end"] = data_end
        return {"data_end": data_end}

    monkeypatch.setattr("services.rally_gt.rebuild", fake_rebuild)
    rc = derive_cli.main(["rally-gt", "--data-end", "20250531"])

    assert rc == 0
    assert calls == {"data_end": "20250531"}, "--data-end 必须原样透传, CLI 层不做业务解读"


def test_rally_gt_failure_is_nonzero_exit_and_reports_reason(monkeypatch, capsys) -> None:
    def boom(*, data_end=None):
        raise RuntimeError("holdout boundary violated")

    monkeypatch.setattr("services.rally_gt.rebuild", boom)
    rc = derive_cli.main(["rally-gt"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "holdout boundary violated" in err


@pytest.mark.parametrize(
    "bad_args",
    [
        ["rally-gt", "--rebuild"],
        ["rally-gt", "--check-only"],
        ["rally-gt", "--from-accepted"],
        ["rally-gt", "--allow-legacy-fill"],
    ],
)
def test_rally_gt_rejects_qfq_form_only_flags(bad_args) -> None:
    with pytest.raises(SystemExit):
        derive_cli.main(bad_args)


def test_data_end_rejected_outside_rally_gt() -> None:
    with pytest.raises(SystemExit):
        derive_cli.main(["qfq", "--data-end", "20250531"])


def test_derive_runtime_targets_stay_qfq_form_only() -> None:
    """架构边界回归: rally-gt 不得混入 S5/S7 DERIVE_TARGETS 锁定契约。"""
    from services.derive_runtime import DERIVE_TARGETS

    assert set(DERIVE_TARGETS) == {"qfq", "form"}
    assert derive_cli.RALLY_GT_TARGET == "rally-gt"
    assert "rally-gt" not in DERIVE_TARGETS
