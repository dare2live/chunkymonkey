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
