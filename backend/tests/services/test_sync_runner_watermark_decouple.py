"""watermark 推进与失败记录解耦单测 (2026-07-06 全面数据审计根因根治).

背景: _record_outcome 此前用同一个 `ok` 字段同时决定"要不要推进 watermark"和"要不要记
failure_queue"——range 内任一批失败 (哪怕只是一个历史日的 suspicious_empty/below_min_rows),
整个域的 watermark 时间戳就冻结不动, 即便 last_date 已经正确前移到本轮真正成功写到的
最新日期。实测 stk_factor_pro 冻结 17 天 / block_trade 曾冻结 9.5 个月, 根因都是这个:
只要该域某个(通常是历史)批次持续失败, 后续每次跑批哪怕新日子都写成功了, watermark 也
永远推不动——冻结的是"监控信号"本身。

本门锁定: (1) 部分失败但 last_date 有前移时, watermark 必须照常推进; (2) 同时仍要记录
这轮的失败 (不能假装全清, 不 resolve 掉历史失败记录); (3) 完全失败 (last_date=None) 时
watermark 不该凭空产生一个 None 日期。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema, upsert_watermark


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


def _watermark_row(conn, domain_key):
    row = conn.execute(
        "SELECT last_data_date, row_count FROM mart_data_source_watermark WHERE data_domain = ?",
        [domain_key],
    ).fetchone()
    return row


def test_record_outcome_advances_watermark_on_partial_failure(monkeypatch):
    """核心红线: ok=False (本轮存在失败批) 但 last_date 已前移时, watermark 必须推进到
    last_date, 不能因为另一个不相关批次失败就整体冻结。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "stk_factor_pro_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date="20260706", rows=5200,
                        error='{"still_failed": ["20260101"]}')

    row = _watermark_row(c, "sync:stk_factor_pro_probe")
    assert row is not None, "watermark 必须被推进, 不能因 ok=False 就整体跳过"
    assert row[0] == "20260706", f"watermark 必须前移到本轮真实写到的最新日期, 实得 {row}"
    c.close()


def test_record_outcome_can_project_the_accepted_timestamp_exactly(monkeypatch):
    """Accepted projections must not replace durable accepted_at with wall-clock now."""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _NoClose(c))

    sr._record_outcome(
        {"domain": "margin", "source": "tushare"},
        ok=True,
        last_date="20260715",
        rows=3,
        success_at="2026-07-16T01:05:00+00:00",
    )

    row = c.execute(
        "SELECT last_data_date, row_count, last_success_at "
        "FROM mart_data_source_watermark WHERE data_domain = 'sync:margin'"
    ).fetchone()
    assert tuple(row[index] for index in range(2)) == ("20260715", 3)
    assert str(row[2]).startswith("2026-07-16 01:05:00")
    c.close()


def test_record_outcome_does_not_resolve_failures_on_partial_success(monkeypatch):
    """部分成功时不应清除历史失败记录 (真失败还在, 不能假装解决了)——failure_queue
    里这个域应仍有未 resolve 的记录。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "block_trade_probe", "source": "tushare"}
    # 先记一次失败 (模拟历史失败记录已存在)
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="boom")
    # 本轮: 部分成功 (last_date 前移了, 但仍有失败批)
    sr._record_outcome(spec, ok=False, last_date="20260706", rows=100, error='{"still_failed": ["20250917"]}')

    row = _watermark_row(c, "sync:block_trade_probe")
    assert row[0] == "20260706", "watermark 应推进到本轮真实前移到的日期"
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain = ? AND status != 'resolved'",
        ["sync:block_trade_probe"],
    ).fetchone()[0]
    assert open_failures > 0, "仍有失败批时不应把历史失败记录 resolve 掉"
    c.close()


def test_record_outcome_full_success_advances_and_resolves(monkeypatch):
    """全清 (ok=True) 时: watermark 推进 + 历史失败记录被 resolve (回归防护, 不能因为
    本次修复反而破坏了原本干净的路径)。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "daily_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="boom")
    sr._record_outcome(
        spec, ok=True, last_date="20260706", rows=5200, error=None,
        resolve_failures=True,
    )

    row = _watermark_row(c, "sync:daily_probe")
    assert row[0] == "20260706"
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain = ? AND status != 'resolved'",
        ["sync:daily_probe"],
    ).fetchone()[0]
    assert open_failures == 0, "全清后历史失败记录必须被 resolve"
    c.close()


def test_full_refresh_success_is_complete_replay_and_resolves_open_failure(monkeypatch):
    """完整快照成功已覆盖整域，run_domain 必须自行关掉旧整批失败。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda *_args: [{"exchange": "SSE", "cal_date": "20261231"}],
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "trade_cal_probe",
        "source": "tushare",
        "api": "trade_cal",
        "target_table": "raw_trade_cal_probe",
        "grain": ["exchange", "cal_date"],
        "batch_mode": "full_refresh",
        "freshness_date_column": "cal_date",
    }
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="old snapshot failed")

    result = sr.run_domain(
        "trade_cal_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"trade_cal_probe": spec},
        },
    )

    assert result["ok"] is True and result["last_date"] == "20261231"
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:trade_cal_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert open_failures == 0
    c.close()


def test_undated_full_refresh_updates_success_without_fabricating_data_date(monkeypatch):
    """静态快照无日期列时刷新成功时间/行数，但不把运行日伪装成数据日期。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda *_args: [{"name": "alpha"}, {"name": "beta"}],
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    upsert_watermark(
        c,
        {
            "data_domain": "sync:hm_list_probe",
            "source_name": "tushare",
            "source_tier": sr.SOURCE_TIER_TUSHARE,
            "last_success_at": "2026-07-06T00:00:00+00:00",
            "last_data_date": None,
            "row_count": 999,
            "parser_version": "sync_runner_v1",
        },
    )
    spec = {
        "domain": "hm_list_probe",
        "source": "tushare",
        "api": "hm_list",
        "target_table": "raw_hm_list_probe",
        "grain": ["name"],
        "batch_mode": "full_refresh",
    }

    result = sr.run_domain(
        "hm_list_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"hm_list_probe": spec},
        },
    )

    row = c.execute(
        "SELECT last_success_at, last_data_date, row_count "
        "FROM mart_data_source_watermark WHERE data_domain='sync:hm_list_probe'"
    ).fetchone()
    assert result["ok"] is True and result["last_date"] is None
    assert str(row[0]) > "2026-07-06" and row[1] is None and row[2] == 2
    c.close()


def test_failed_full_refresh_neither_resolves_failure_nor_refreshes_success(monkeypatch):
    """完整快照没抓成时，旧 failure 与旧 success 时间都必须保留。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_fetch_logical_batch", lambda *_args: None)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    upsert_watermark(
        c,
        {
            "data_domain": "sync:stock_basic_probe",
            "source_name": "tushare",
            "source_tier": sr.SOURCE_TIER_TUSHARE,
            "last_success_at": "2026-07-06T00:00:00+00:00",
            "last_data_date": None,
            "row_count": 999,
            "parser_version": "sync_runner_v1",
        },
    )
    spec = {
        "domain": "stock_basic_probe",
        "source": "tushare",
        "api": "stock_basic",
        "target_table": "raw_stock_basic_probe",
        "grain": ["ts_code"],
        "batch_mode": "full_refresh",
    }
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="old snapshot failed")

    result = sr.run_domain(
        "stock_basic_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"stock_basic_probe": spec},
        },
    )

    row = c.execute(
        "SELECT last_success_at, row_count FROM mart_data_source_watermark "
        "WHERE data_domain='sync:stock_basic_probe'"
    ).fetchone()
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:stock_basic_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert result["ok"] is False and result["failed_batches"] == 1
    assert str(row[0]).startswith("2026-07-06") and row[1] == 999
    assert open_failures == 1
    c.close()


def test_record_outcome_incremental_success_does_not_resolve_historical_failure(monkeypatch):
    """普通增量成功不是历史 gap 重扫证据，不能洗掉既有 failure。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "block_trade_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="historical_gap")
    sr._record_outcome(spec, ok=True, last_date="20260714", rows=83, error=None)

    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain = ? AND status != 'resolved'",
        ["sync:block_trade_probe"],
    ).fetchone()[0]
    assert open_failures > 0
    c.close()


def test_record_outcome_total_failure_no_last_date_does_not_fabricate_watermark(monkeypatch):
    """完全失败 (last_date=None, 一行都没写成) 时不应产生一条 last_data_date=None 的
    watermark 记录——没有真实前移就不该有 upsert 动作。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)

    spec = {"domain": "moneyflow_hsgt_probe", "source": "tushare"}
    sr._record_outcome(spec, ok=False, last_date=None, rows=0, error="boom")

    row = _watermark_row(c, "sync:moneyflow_hsgt_probe")
    assert row is None, "完全失败时不该凭空产生 watermark 记录"
    c.close()


def test_historical_replay_never_regresses_existing_watermark(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    spec = {"domain": "daily_probe", "source": "tushare"}

    sr._record_outcome(spec, ok=True, last_date="20260714", rows=100)
    sr._record_outcome(spec, ok=True, last_date="20260710", rows=50)

    assert _watermark_row(c, "sync:daily_probe")[0] == "20260714"
    c.close()


def test_by_ann_date_failure_does_not_advance_frontier_past_gap(monkeypatch):
    """无 drain 的公告日域必须把 watermark 留在首个失败之前，下一轮才能重试。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_pending_failure_start", lambda _spec: None)
    monkeypatch.setattr(sr, "_calendar_days", lambda _start, _end: ["20260710", "20260711"])
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260711", False, "test"),
    )
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda _adapter, _spec, params: (
            None if params["ann_date"] == "20260710" else [{"ann_date": "20260711"}]
        ),
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "forecast_probe",
        "source": "tushare",
        "api": "forecast",
        "target_table": "raw_forecast_probe",
        "grain": ["ann_date"],
        "batch_mode": "by_ann_date",
        "data_start": "20260709",
        "date_param": "ann_date",
    }
    sr._record_outcome(spec, ok=True, last_date="20260709", rows=1)

    result = sr.run_domain(
        "forecast_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"forecast_probe": spec},
        },
    )

    assert result["failed_batches"] == 1
    assert result["last_date"] is None
    assert _watermark_row(c, "sync:forecast_probe")[0] == "20260709"
    c.close()


def test_open_non_drain_failure_is_replayed_and_resolved(monkeypatch):
    """旧失败日期即使早于 watermark，也必须从 failure queue 找回并在全绿重放后关闭。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_calendar_days", lambda start, _end: [start, "20260711"])
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260711", False, "test"),
    )
    calls = []

    def _fetch(_adapter, _spec, params):
        calls.append(params["ann_date"])
        return [{"ann_date": params["ann_date"]}]

    monkeypatch.setattr(sr, "_fetch_logical_batch", _fetch)
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "forecast_probe",
        "source": "tushare",
        "api": "forecast",
        "target_table": "raw_forecast_probe",
        "grain": ["ann_date"],
        "batch_mode": "by_ann_date",
        "data_start": "20260701",
        "date_param": "ann_date",
    }
    sr._record_outcome(
        spec,
        ok=False,
        last_date="20260711",
        rows=1,
        error='[{"ann_date": "20260710", "suspect": "batch_incomplete"}]',
    )

    result = sr.run_domain(
        "forecast_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"forecast_probe": spec},
        },
    )

    assert result["ok"] is True
    assert calls[0] == "20260710"
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:forecast_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert open_failures == 0
    c.close()


def test_future_pending_failure_is_not_resolved_before_eligible_end(monkeypatch):
    """失败日在发布边界之后时，本轮没有覆盖它，哪怕其余批全绿也不能关账。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_calendar_days", lambda _start, _end: ["20260714"])
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260714", True, "pending_publish"),
    )
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda *_args: [{"ann_date": "20260714"}],
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "forecast_probe",
        "source": "tushare",
        "api": "forecast",
        "target_table": "raw_forecast_probe",
        "grain": ["ann_date"],
        "batch_mode": "by_ann_date",
        "data_start": "20260701",
        "date_param": "ann_date",
    }
    sr._record_outcome(
        spec,
        ok=False,
        last_date="20260714",
        rows=1,
        error='[{"ann_date": "20260715", "suspect": "not_yet_replayed"}]',
    )

    result = sr.run_domain(
        "forecast_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"forecast_probe": spec},
        },
    )

    assert result["ok"] is True and result["pending_today"] is True
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:forecast_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert open_failures == 1
    c.close()


def test_quota_halt_does_not_overwrite_pending_batch_date(monkeypatch):
    """配额失败有独立 failure type，不能抹掉 sync_batch_failed 的重放锚点。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "trading_days", lambda _start, _end=None: ["20260714"])
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260714", False, "test"),
    )
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda *_args: (_ for _ in ()).throw(sr.QuotaExhaustedError("quota")),
    )
    spec = {
        "domain": "daily_probe",
        "source": "tushare",
        "api": "daily",
        "target_table": "raw_daily_probe",
        "grain": ["ts_code", "trade_date"],
        "batch_mode": "by_trade_date",
        "data_start": "20260701",
    }
    sr._record_outcome(
        spec,
        ok=False,
        last_date=None,
        rows=0,
        error='[{"trade_date": "20260710", "suspect": "fetch_failed"}]',
    )

    with pytest.raises(sr.QuotaExhaustedError):
        sr.run_domain(
            "daily_probe",
            start="20260714",
            end="20260714",
            registry={
                "defaults": {"fetch_timeout_seconds": 120},
                "domains": {"daily_probe": spec},
            },
        )

    assert sr._pending_failure_start(spec) == "20260710"
    types = {
        row[0]
        for row in c.execute(
            "SELECT error_type FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:daily_probe' AND status != 'resolved'"
        ).fetchall()
    }
    assert types == {"sync_batch_failed", "sync_quota_halt"}

    sr._record_outcome(
        spec,
        ok=True,
        last_date="20260714",
        rows=1,
        provider_succeeded=True,
    )
    remaining_types = {
        row[0]
        for row in c.execute(
            "SELECT error_type FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:daily_probe' AND status != 'resolved'"
        ).fetchall()
    }
    assert remaining_types == {"sync_batch_failed"}, (
        "真实 provider 恢复应精确关闭 quota，不能顺带洗掉待重放批失败"
    )
    c.close()


def test_long_failure_payload_keeps_parseable_earliest_date(monkeypatch):
    """失败摘要即使原文超过 1000 字符，落库 JSON 也必须完整且可恢复重放锚点。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    spec = {"domain": "forecast_probe", "source": "tushare"}
    failures = [
        {
            "ann_date": f"202607{day:02d}",
            "error": "provider-detail-" + ("x" * 260),
        }
        for day in range(1, 6)
    ]

    sr._record_outcome(
        spec,
        ok=False,
        last_date=None,
        rows=0,
        error=json.dumps(failures),
    )

    stored = c.execute(
        "SELECT last_error FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:forecast_probe' AND error_type='sync_batch_failed'"
    ).fetchone()[0]
    assert len(stored) < 1000
    assert json.loads(stored)["earliest_failed_date"] == "20260701"
    assert sr._pending_failure_start(spec) == "20260701"
    c.close()


def test_later_failure_cannot_move_unresolved_frontier_forward(monkeypatch):
    """同 failure_id 后写会替换 payload，但未复核的旧最早日期必须被合并保留。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    spec = {"domain": "forecast_probe", "source": "tushare"}

    sr._record_outcome(
        spec, ok=False, last_date=None, rows=0,
        error='[{"ann_date": "20260701"}]',
    )
    sr._record_outcome(
        spec, ok=False, last_date=None, rows=0,
        error='[{"ann_date": "20260710"}]',
    )

    assert sr._pending_failure_start(spec) == "20260701"
    c.close()


def test_by_period_failure_keeps_frontier_before_first_failed_period(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260630", False, "test"),
    )
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda _adapter, _spec, params: (
            None if params["period"] == "20260331" else [{"period": params["period"]}]
        ),
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "express_probe",
        "source": "tushare",
        "api": "express_vip",
        "target_table": "raw_express_probe",
        "grain": ["period"],
        "batch_mode": "by_period",
        "data_start": "20251231",
        "date_param": "period",
    }
    sr._record_outcome(spec, ok=True, last_date="20251231", rows=1)

    result = sr.run_domain(
        "express_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"express_probe": spec},
        },
    )

    assert result["failed_batches"] == 1
    assert result["last_date"] == "20251231"
    assert _watermark_row(c, "sync:express_probe")[0] == "20251231"
    c.close()


def test_by_period_open_failure_replays_and_resolves(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260630", False, "test"),
    )
    periods = []

    def _fetch(_adapter, _spec, params):
        periods.append(params["period"])
        return [{"period": params["period"]}]

    monkeypatch.setattr(sr, "_fetch_logical_batch", _fetch)
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "express_probe",
        "source": "tushare",
        "api": "express_vip",
        "target_table": "raw_express_probe",
        "grain": ["period"],
        "batch_mode": "by_period",
        "data_start": "20200101",
        "date_param": "period",
    }
    sr._record_outcome(
        spec,
        ok=False,
        last_date="20260630",
        rows=1,
        error='[{"period": "20260331"}]',
    )

    result = sr.run_domain(
        "express_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"express_probe": spec},
        },
    )

    assert result["ok"] is True
    assert periods == ["20260331", "20260630"]
    open_failures = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:express_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert open_failures == 0
    c.close()


def test_by_period_future_pending_waits_for_eligibility_then_resolves(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: shared)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    eligible = ["20260331"]
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility(eligible[0], False, "test"),
    )
    monkeypatch.setattr(
        sr,
        "_fetch_logical_batch",
        lambda _adapter, _spec, params: [{"period": params["period"]}],
    )
    monkeypatch.setattr(sr, "_write_batch", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    spec = {
        "domain": "express_probe",
        "source": "tushare",
        "api": "express_vip",
        "target_table": "raw_express_probe",
        "grain": ["period"],
        "batch_mode": "by_period",
        "data_start": "20200101",
        "date_param": "period",
    }
    sr._record_outcome(
        spec,
        ok=False,
        last_date="20260331",
        rows=1,
        error='[{"period": "20260630"}]',
    )

    sr.run_domain(
        "express_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"express_probe": spec},
        },
    )
    still_open = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:express_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert still_open == 1

    eligible[0] = "20260630"
    sr.run_domain(
        "express_probe",
        registry={
            "defaults": {"fetch_timeout_seconds": 120},
            "domains": {"express_probe": spec},
        },
    )
    now_closed = c.execute(
        "SELECT COUNT(*) FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:express_probe' AND status != 'resolved'"
    ).fetchone()[0]
    assert now_closed == 0
    c.close()
