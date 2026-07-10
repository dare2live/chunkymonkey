"""§8 切片 b/c-lite 件1 单测: 阶段状态复用 manifest (stage_status).

验证 record/read + 派生 stale + upstream_ok 门, 用 :memory: manifest (不碰真库)。
"""
from __future__ import annotations

import duckdb
import pytest

from services.pipeline_manifest import ensure_pipeline_manifest_schema
from services.pipeline import stage_status as ss


def _conn():
    conn = duckdb.connect()
    ensure_pipeline_manifest_schema(conn)
    return conn


def test_not_run_when_empty() -> None:
    conn = _conn()
    st = ss.get_stage_status(conn)
    assert set(st) == set(ss.STAGE_ORDER)
    assert all(st[s]["status"] == "not_run" for s in ss.STAGE_ORDER)
    assert ss.upstream_ok(conn, "acquire") is True  # 首阶段无上游恒 True


def test_record_and_read_latest() -> None:
    conn = _conn()
    ss.record_stage(conn, "acquire", ss.STATUS_CHECK_PASS, started_at="2026-06-25T01:00:00")
    ss.record_stage(conn, "acquire", ss.STATUS_CHECK_FAIL, started_at="2026-06-25T02:00:00")  # 更晚=当前
    st = ss.get_stage_status(conn)
    assert st["acquire"]["status"] == ss.STATUS_CHECK_FAIL  # 取最新


def test_upstream_gate() -> None:
    conn = _conn()
    # acquire check_pass → clean 可跑
    ss.record_stage(conn, "acquire", ss.STATUS_CHECK_PASS, started_at="2026-06-25T01:00:00")
    assert ss.upstream_ok(conn, "clean") is True
    # acquire check_fail → clean 上游门 False
    ss.record_stage(conn, "acquire", ss.STATUS_CHECK_FAIL, started_at="2026-06-25T02:00:00")
    assert ss.upstream_ok(conn, "clean") is False


def test_derived_stale_when_upstream_rerun_later() -> None:
    conn = _conn()
    # clean 先跑 (01:00), 之后 acquire 重跑 (03:00) → clean 过时
    ss.record_stage(conn, "acquire", ss.STATUS_CHECK_PASS, started_at="2026-06-25T00:00:00")
    ss.record_stage(conn, "clean", ss.STATUS_CHECK_PASS, started_at="2026-06-25T01:00:00")
    assert ss.get_stage_status(conn)["clean"]["stale"] is False
    ss.record_stage(conn, "acquire", ss.STATUS_CHECK_PASS, started_at="2026-06-25T03:00:00")  # 上游重跑
    st = ss.get_stage_status(conn)
    assert st["clean"]["stale"] is True   # 上游 started_at > clean → 派生 stale
    assert st["acquire"]["stale"] is False  # 首阶段无上游


def test_unknown_stage_rejected() -> None:
    conn = _conn()
    with pytest.raises(ValueError):
        ss.record_stage(conn, "bogus", ss.STATUS_CHECK_PASS)


# ── 件2: run_and_record 状态判定 + best-effort 记录器 ──

def test_run_and_record_status(monkeypatch, tmp_path) -> None:
    from services.pipeline import context as ctx_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "alert.flag")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(ss, "_record_stage_best_effort",
                        lambda ctx, stage, status, **k: calls.append((stage, status)))
    ctx = PipelineContext(dry=False, date="20990101")
    try:
        assert ss.run_and_record(ctx, "acquire", lambda c: None) is True          # 无 degraded → pass
        assert ss.run_and_record(ctx, "clean", lambda c: c.degraded("boom")) is False  # degrade → fail
    finally:
        ctx.close()
    assert calls == [("acquire", ss.STATUS_CHECK_PASS), ("clean", ss.STATUS_CHECK_FAIL)]


def test_record_best_effort_skips_dry(monkeypatch, tmp_path) -> None:
    """dry-run 不开 conn 不写 DB。"""
    from services.pipeline import context as ctx_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "alert.flag")
    opened: list[str] = []
    monkeypatch.setattr("services.db_connection.get_conn", lambda *a, **k: opened.append("x"))
    ctx = PipelineContext(dry=True, date="20990101")
    try:
        ss._record_stage_best_effort(ctx, "acquire", ss.STATUS_CHECK_PASS)
    finally:
        ctx.close()
    assert opened == []  # dry → 跳过, 不开 smartmoney conn


def test_record_best_effort_never_raises(monkeypatch, tmp_path) -> None:
    """记状态失败 (conn 异常) 绝不破链 — 吞错只记日志。"""
    from services.pipeline import context as ctx_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "alert.flag")

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("services.db_connection.get_conn", _boom)
    ctx = PipelineContext(dry=False, date="20990101")
    try:
        ss._record_stage_best_effort(ctx, "acquire", ss.STATUS_CHECK_PASS)  # 不应 raise
    finally:
        ctx.close()


def test_run_and_record_crash_still_records(monkeypatch, tmp_path) -> None:
    """硬崩 (fn raise, 非 degrade 续跑) 也必须留 check_fail 痕迹再抛 — 否则 manifest
    缺该阶段行, 崩溃在状态面不可见 (全栈审计LOW, 2026-07-10)。"""
    from services.pipeline import context as ctx_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(ctx_mod, "DEGRADED_FLAG", tmp_path / "alert.flag")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(ss, "_record_stage_best_effort",
                        lambda ctx, stage, status, **k: calls.append((stage, status)))

    def _boom(c):
        raise RuntimeError("boom")

    ctx = PipelineContext(dry=False, date="20990101")
    try:
        with pytest.raises(RuntimeError, match="boom"):
            ss.run_and_record(ctx, "process", _boom)
    finally:
        ctx.close()
    assert calls == [("process", ss.STATUS_CHECK_FAIL)]  # 崩了也记了, 且如实 fail
