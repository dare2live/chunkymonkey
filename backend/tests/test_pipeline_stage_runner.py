"""§8 切片 a 单测: chunkyctl pipeline 单阶段独立触发 (stage_runner).

验证 dispatch + 返回码, 不跑真实重阶段 (monkeypatch STAGES 注入 fake)。
"""
from __future__ import annotations

import pytest

from services.pipeline import stage_runner
from services.pipeline import context as ctx_mod


def test_run_stage_unknown_raises() -> None:
    with pytest.raises(SystemExit):
        stage_runner.run_stage("bogus", date="20990101")


def test_run_stage_clean_returns_0(monkeypatch) -> None:
    """阶段函数被正确 dispatch + 拿到 ctx; 无 degraded → 0。"""
    seen = {}

    def fake(ctx) -> None:
        seen["date"] = ctx.date
        seen["dry"] = ctx.dry

    monkeypatch.setitem(stage_runner.STAGES, "acquire", fake)
    rc = stage_runner.run_stage("acquire", dry=True, date="20990101")
    assert rc == 0
    assert seen == {"date": "20990101", "dry": True}


def test_run_stage_degraded_returns_1(monkeypatch, tmp_path) -> None:
    """阶段内 degraded → 返回 1 (运维信号)。flag 重定向到 tmp 防污染真实告警 flag。"""
    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "alert.flag")

    def fake(ctx) -> None:
        ctx.degraded("boom")

    monkeypatch.setitem(stage_runner.STAGES, "clean", fake)
    rc = stage_runner.run_stage("clean", dry=True, date="20990101")
    assert rc == 1


def test_all_four_stages_registered() -> None:
    assert set(stage_runner.STAGES) == {"acquire", "clean", "process", "store"}
