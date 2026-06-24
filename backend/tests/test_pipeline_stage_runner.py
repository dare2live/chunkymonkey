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
    # force=True 绕过件3 upstream 门 (本测专测 degraded→1 路径, 非上游门; clean 上游 acquire 在真 manifest=not_run 会先 refuse)
    rc = stage_runner.run_stage("clean", dry=True, date="20990101", force=True)
    assert rc == 1


def test_all_four_stages_registered() -> None:
    assert set(stage_runner.STAGES) == {"acquire", "clean", "process", "store"}


# ── 件3: refuse-if-upstream-not-pass 门 + --force 绕过 ──

class _FakeConn:
    def close(self) -> None:  # noqa: D401
        pass


def test_upstream_refusal_first_stage_passes() -> None:
    assert stage_runner._upstream_refusal("acquire") is None  # 首阶段无上游=放行


def test_upstream_refusal_none_when_upstream_pass(monkeypatch) -> None:
    from services.pipeline import stage_status as ss
    monkeypatch.setattr(ss, "upstream_ok", lambda conn, stage: True)
    monkeypatch.setattr(ss, "upstream_status", lambda conn, stage: {"status": "check_pass"})
    monkeypatch.setattr("services.db_connection.get_conn", lambda *a, **k: _FakeConn())
    assert stage_runner._upstream_refusal("clean") is None


def test_upstream_refusal_blocks_when_not_pass(monkeypatch) -> None:
    from services.pipeline import stage_status as ss
    monkeypatch.setattr(ss, "upstream_ok", lambda conn, stage: False)
    monkeypatch.setattr(ss, "upstream_status", lambda conn, stage: {"status": "check_fail"})
    monkeypatch.setattr("services.db_connection.get_conn", lambda *a, **k: _FakeConn())
    msg = stage_runner._upstream_refusal("clean")
    assert msg is not None and "REFUSE" in msg and "acquire" in msg


def test_upstream_refusal_passes_on_read_error(monkeypatch) -> None:
    """状态读失败 = 放行 (best-effort 门, 不因状态库不可达卡死运维)。"""
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("services.db_connection.get_conn", _boom)
    assert stage_runner._upstream_refusal("clean") is None


def test_run_stage_refuses_when_upstream_not_pass(monkeypatch) -> None:
    monkeypatch.setattr(stage_runner, "_upstream_refusal", lambda s: "REFUSE upstream")
    rc = stage_runner.run_stage("clean", dry=True, date="20990101", force=False)
    assert rc == 2  # 被拒, 未跑


def test_run_stage_force_skips_gate(monkeypatch, tmp_path) -> None:
    from services.pipeline import context as ctx_mod
    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "alert.flag")
    called: list[str] = []
    monkeypatch.setattr(stage_runner, "_upstream_refusal", lambda s: called.append(s) or "REFUSE")
    monkeypatch.setattr(stage_runner, "run_and_record", lambda ctx, stage, fn: True)
    rc = stage_runner.run_stage("clean", dry=True, date="20990101", force=True)
    assert rc == 0 and called == []  # force → 跳过上游门
