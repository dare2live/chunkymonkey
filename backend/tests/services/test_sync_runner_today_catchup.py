"""按域发布时间计算 eligible horizon 的单一 owner 回归门。

``eligible_end_date`` 决定普通增量和 gap drain 能看到的最晚交易日。主流程不得再用
第二套 ``available_after`` 判断补抓“今天”，否则同一手工 drain 会对已发布日期重复调用
provider。只有 drain 不适用的域才允许一次 ``run_domain`` fallback。
"""
from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.data_sources import sync_runner as sr


@pytest.fixture(autouse=True)
def _successful_cli_authorization(monkeypatch):
    monkeypatch.setattr(sr, "_authorization_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(sr, "_calendar_preflight", lambda _domains: None)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 6, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))


def _at_july_15(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_domain_eligibility_uses_domain_publish_time_not_global_close_cutoff():
    """同一交易日是否 eligible 只看域自己的 available_after；09:20 域不能被 16:00 clamp。"""
    days = ["20260714", "20260715"]
    before = sr.eligible_end_date(
        {"available_after": "18:00", "data_start": "20200101"},
        now=_at_july_15(17, 59),
        trading_day_values=days,
    )
    assert before.eligible_end == "20260714" and before.pending_today is True

    morning = sr.eligible_end_date(
        {"available_after": "09:20", "data_start": "20200101"},
        now=_at_july_15(9, 21),
        trading_day_values=days,
    )
    assert morning.eligible_end == "20260715" and morning.pending_today is False


def test_t_plus_one_domain_never_targets_current_trade_date():
    result = sr.eligible_end_date(
        {
            "available_after": "t+1",
            "availability_policy": {
                "axis": "trading_day",
                "rule": "next_trading_session_at",
                "at": "09:00",
            },
            "data_start": "20200101",
        },
        now=_at_july_15(23, 59),
        trading_day_values=["20260714", "20260715"],
    )
    assert result.eligible_end == "20260714"
    assert result.pending_today is True


def test_t_plus_one_domain_waits_for_next_trading_day_on_weekend():
    """周末不是 T+1 发布日；周五数据要等下一交易日盘前才能进入 eligible horizon。"""
    result = sr.eligible_end_date(
        {
            "available_after": "t+1",
            "availability_policy": {
                "axis": "trading_day",
                "rule": "next_trading_session_at",
                "at": "09:00",
            },
            "data_start": "20200101",
        },
        now=datetime(2026, 7, 18, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        trading_day_values=["20260715", "20260716", "20260717"],
    )
    assert result.eligible_end == "20260716"
    assert result.pending_today is True
    assert result.reason == "next_trading_session_awaiting_session"


@pytest.mark.parametrize(
    ("hour", "minute", "expected", "reason"),
    [
        (8, 59, "20260716", "next_trading_session_pending"),
        (9, 0, "20260717", "next_trading_session_published"),
    ],
)
def test_next_trading_session_policy_has_an_exact_monday_boundary(
    hour, minute, expected, reason
):
    result = sr.eligible_end_date(
        {
            "available_after": "t+1",
            "availability_policy": {
                "axis": "trading_day",
                "rule": "next_trading_session_at",
                "at": "09:00",
            },
            "data_start": "20200101",
        },
        now=datetime(
            2026, 7, 20, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        trading_day_values=["20260716", "20260717", "20260720"],
    )
    assert result.eligible_end == expected
    assert result.reason == reason


def test_t_plus_one_announcement_axis_includes_weekend_before_monday():
    """公告日域按自然日推进；周一可查询周日公告，不能被交易日历截到周五。"""
    result = sr.eligible_end_date(
        {
            "batch_mode": "by_ann_date",
            "available_after": "t+1",
            "availability_policy": {
                "axis": "calendar_day",
                "rule": "next_calendar_day_at",
                "at": "09:00",
            },
            "data_start": "20200101",
        },
        now=datetime(2026, 7, 20, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        trading_day_values=["20260717", "20260720"],
    )
    assert result.eligible_end == "20260719"
    assert result.pending_today is True
    assert result.reason == "next_calendar_day_published"


def test_hhmm_announcement_axis_can_publish_on_weekend():
    """自然日公告域在周六声明时刻后应纳入周六，而非退回最近交易日。"""
    result = sr.eligible_end_date(
        {
            "batch_mode": "by_ann_date",
            "available_after": "22:30",
            "availability_policy": {
                "axis": "calendar_day",
                "rule": "same_day_at",
                "at": "22:30",
            },
            "data_start": "20200101",
        },
        now=datetime(2026, 7, 18, 22, 31, tzinfo=ZoneInfo("Asia/Shanghai")),
        trading_day_values=["20260716", "20260717"],
    )
    assert result.eligible_end == "20260718"
    assert result.pending_today is False
    assert result.reason == "published"


def test_run_domain_passes_domain_eligible_end_to_calendar(monkeypatch):
    """run_domain 不得把 end=None 留给通用日历函数再按 16:00 自行截断。"""
    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "adj_factor": {
                "source": "tushare",
                "api": "adj_factor",
                "target_table": "raw_adj_factor",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "data_start": "20260714",
                "available_after": "09:20",
            }
        },
    }
    calendar_calls = []
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda spec: sr.DomainEligibility("20260715", False, "published"),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end=None: calendar_calls.append((start, end)) or ["20260715"],
    )
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: None)
    monkeypatch.setattr(sr, "_adapter", lambda source: object())
    monkeypatch.setattr(sr, "_fetch_paged", lambda adapter, spec, params: [])
    monkeypatch.setattr(sr, "_record_outcome", lambda *args, **kwargs: None)

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(sr, "_target_conn", lambda spec: _Conn())
    result = sr.run_domain("adj_factor", registry=reg)

    assert calendar_calls == [("20260714", "20260715")]
    assert result["failed_batches"] == 0


def test_nonformal_future_end_is_rejected_before_provider_adapter(monkeypatch):
    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "demo": {
                "source": "tushare",
                "api": "demo",
                "target_table": "raw_demo",
                "grain": ["trade_date"],
                "batch_mode": "by_trade_date",
                "data_start": "20260715",
                "available_after": "18:00",
            }
        },
    }
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260716", False, "published"),
    )
    monkeypatch.setattr(
        sr,
        "_adapter",
        lambda _source: pytest.fail("future request reached provider adapter"),
    )

    with pytest.raises(ValueError, match="eligible horizon"):
        sr.run_domain(
            "demo",
            backfill=True,
            start="20260717",
            end="20260717",
            registry=reg,
        )


def test_full_refresh_rejects_date_bounds_before_provider_or_target_db(monkeypatch):
    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "snapshot": {
                "source": "tushare",
                "api": "stock_basic",
                "target_table": "raw_stock_basic",
                "grain": ["ts_code"],
                "batch_mode": "full_refresh",
                "fixed_params": {"list_status": "L"},
            }
        },
    }
    monkeypatch.setattr(
        sr,
        "_adapter",
        lambda _source: pytest.fail("invalid full_refresh bounds reached adapter"),
    )
    monkeypatch.setattr(
        sr,
        "_target_conn",
        lambda _spec: pytest.fail("invalid full_refresh bounds reached target DB"),
    )

    with pytest.raises(sr.SyncWindowError, match="does not accept date bounds"):
        sr.run_domain(
            "snapshot",
            backfill=True,
            start="20990101",
            end="20990101",
            registry=reg,
        )


def test_public_cli_rejects_future_window_before_any_precondition_side_effect(
    monkeypatch, capsys
):
    import services.writer_lock as writer_lock_module

    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "daily": {
                "source": "tushare",
                "api": "daily",
                "target_table": "raw_daily",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "data_start": "20200101",
                "available_after": "18:00",
            }
        },
    }
    events = []
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(
        sr, "_calendar_preflight", lambda _domains: events.append("calendar")
    )
    monkeypatch.setattr(
        sr, "_authorization_preflight", lambda *_args, **_kwargs: events.append("auth")
    )
    monkeypatch.setattr(
        writer_lock_module,
        "writer_lock",
        lambda *_args, **_kwargs: pytest.fail("future request acquired writer lock"),
    )
    monkeypatch.setattr(
        sr, "_adapter", lambda _source: pytest.fail("future request reached adapter")
    )
    monkeypatch.setattr(
        sr, "_target_conn", lambda _spec: pytest.fail("future request reached target DB")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync_runner.py",
            "--domain",
            "daily",
            "--backfill",
            "--start",
            "20990101",
            "--end",
            "20990101",
        ],
    )

    assert sr.main() == 5
    assert events == []
    assert "operation_window_blocked" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--start", "20260715", "--end", "20260716"],
        ["--backfill"],
        ["--resume"],
    ],
)
def test_public_cli_rejects_bounded_drain_flags_before_side_effects(
    monkeypatch, capsys, extra_args
):
    import services.writer_lock as writer_lock_module

    reg = {
        "defaults": {},
        "domains": {
            "daily": {
                "batch_mode": "by_trade_date",
                "available_after": "18:00",
            }
        },
    }
    events = []
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(
        sr, "_calendar_preflight", lambda _domains: events.append("calendar")
    )
    monkeypatch.setattr(
        sr, "_authorization_preflight", lambda *_args, **_kwargs: events.append("auth")
    )
    monkeypatch.setattr(
        writer_lock_module,
        "writer_lock",
        lambda *_args, **_kwargs: pytest.fail("invalid drain acquired writer lock"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_runner.py", "--domain", "daily", "--drain", *extra_args],
    )

    assert sr.main() == 5
    assert events == []
    assert "cannot be combined" in capsys.readouterr().out


@pytest.mark.parametrize(
    "bound_args",
    [
        [],
        ["--start", "20260715"],
        ["--end", "20260716"],
    ],
)
def test_on_demand_cli_requires_both_bounds_before_side_effects(
    monkeypatch, capsys, bound_args
):
    import services.writer_lock as writer_lock_module

    reg = {
        "defaults": {},
        "domains": {
            "factor": {
                "batch_mode": "by_ts_code",
                "sync_policy": "on_demand",
                "fixed_params": {},
            }
        },
    }
    events = []
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(
        sr, "_calendar_preflight", lambda _domains: events.append("calendar")
    )
    monkeypatch.setattr(
        sr, "_authorization_preflight", lambda *_args, **_kwargs: events.append("auth")
    )
    monkeypatch.setattr(
        writer_lock_module,
        "writer_lock",
        lambda *_args, **_kwargs: pytest.fail("unbounded request acquired writer lock"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_runner.py", "--domain", "factor", *bound_args],
    )

    assert sr.main() == 5
    assert events == []
    assert "requires both" in capsys.readouterr().out


@pytest.mark.parametrize(
    "values",
    [
        ["SSE", "SZSE", "BSE", "BSE"],
        ["SSE", "SZSE", "bse"],
        [],
    ],
)
def test_formal_margin_cli_rejects_invalid_split_groups_before_side_effects(
    monkeypatch, capsys, values
):
    import services.writer_lock as writer_lock_module

    source = sr.load_registry()
    margin = {
        **source["domains"]["margin"],
        "split_by": {"param": "exchange_id", "values": values},
    }
    registry = {
        **source,
        "domains": {**source["domains"], "margin": margin},
    }
    events = []
    monkeypatch.setattr(sr, "load_registry", lambda: registry)
    monkeypatch.setattr(
        sr, "_calendar_preflight", lambda _domains: events.append("calendar")
    )
    monkeypatch.setattr(
        sr,
        "_authorization_preflight",
        lambda *_args, **_kwargs: events.append("authorization"),
    )
    monkeypatch.setattr(
        writer_lock_module,
        "writer_lock",
        lambda *_args, **_kwargs: pytest.fail("invalid formal plan acquired lock"),
    )
    monkeypatch.setattr(
        sys, "argv", ["sync_runner.py", "--domain", "margin"]
    )

    assert sr.main() == 5
    assert events == []
    assert "split_by.values" in capsys.readouterr().out


def test_by_code_list_explicit_start_and_end_reach_provider(monkeypatch):
    calls = []

    class _Adapter:
        def fetch_raw(self, _api, **params):
            calls.append(dict(params))
            return []

    class _Conn:
        def close(self):
            pass

    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "index_daily": {
                "source": "tushare",
                "api": "index_daily",
                "target_table": "raw_index_daily",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_code_list",
                "code_param": "ts_code",
                "code_list": ["000001.SH"],
                "fixed_params": {"start_date": "20050101"},
                "data_start": "20050101",
                "available_after": "18:00",
            }
        },
    }
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility("20260716", False, "published"),
    )
    monkeypatch.setattr(sr, "_adapter", lambda _source: _Adapter())
    monkeypatch.setattr(sr, "_target_conn", lambda _spec: _Conn())
    monkeypatch.setattr(sr, "_record_outcome", lambda *_args, **_kwargs: None)

    sr.run_domain(
        "index_daily",
        backfill=True,
        start="20260715",
        end="20260716",
        registry=reg,
    )

    assert calls
    assert calls[0]["start_date"] == "20260715"
    assert calls[0]["end_date"] == "20260716"


def test_drain_main_supported_domain_never_double_fetches_today(monkeypatch):
    """drain 已按 eligible horizon 覆盖今日时，main 不得再调用 run_domain。"""
    reg = {"domains": {"daily": {"batch_mode": "by_trade_date", "available_after": "00:00"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    # Force the retired branch on the old implementation so this test first
    # proves red; ``raising=False`` keeps the assertion valid after deletion.
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: True, raising=False)
    drain_calls = []

    def _fake_drain(d, registry=None, max_dates=None):
        drain_calls.append(d)
        return {"domain": d, "status": "clean"}

    monkeypatch.setattr(sr, "drain_domain", _fake_drain)
    run_domain_calls = []

    def _fake_run_domain(d, registry=None):
        run_domain_calls.append(d)
        return {"domain": d, "batches": 1, "rows": 5194, "failed_batches": 0, "ok": True}

    monkeypatch.setattr(sr, "run_domain", _fake_run_domain)
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    rc = sr.main()
    assert drain_calls == ["daily"]
    assert run_domain_calls == [], "supported drain 必须是日期抓取的唯一 owner"
    assert rc == 0


def test_drain_main_uses_only_drain_before_publish(monkeypatch):
    """发布时间以前也只执行 drain；eligibility 由 drain 内部统一决定。"""
    reg = {"domains": {"dc_member": {"batch_mode": "by_trade_date", "available_after": "18:00"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: False, raising=False)
    monkeypatch.setattr(sr, "drain_domain",
                        lambda d, registry=None, max_dates=None: {"domain": d, "status": "clean"})
    catchup_calls = []
    monkeypatch.setattr(sr, "run_domain", lambda d, registry=None: catchup_calls.append(d))
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    rc = sr.main()
    assert catchup_calls == [], "main 不得维护第二套发布时间补抓分支"
    assert rc == 0


def test_drain_main_propagates_supported_domain_failure_without_second_fetch(monkeypatch):
    """同日抓取失败由 drain 的 partial 传播，不能再抓一次掩盖或放大失败。"""
    reg = {"domains": {"daily": {"batch_mode": "by_trade_date", "available_after": "00:00"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: True, raising=False)
    monkeypatch.setattr(sr, "drain_domain",
                        lambda d, registry=None, max_dates=None:
                            {"domain": d, "status": "partial", "still_failed": ["20260717"]})
    run_domain_calls = []
    monkeypatch.setattr(sr, "run_domain", lambda d, registry=None: run_domain_calls.append(d))
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    rc = sr.main()
    assert run_domain_calls == []
    assert rc == 1, "drain partial 必须反映到 exit code"


def test_drain_main_fallback_incremental_domain_not_double_called(monkeypatch):
    """已走 fallback_incremental 路径 (drain_inapplicable/unsupported) 的域不应再额外触发
    今日补拉 (if/elif 互斥, 防重复调用 run_domain)。"""
    reg = {"domains": {"allow_empty_events": {
        "batch_mode": "by_trade_date",
        "available_after": "08:40",
        "allow_empty_batch": True,
    }}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: True, raising=False)
    monkeypatch.setattr(sr, "drain_domain",
                        lambda d, registry=None, max_dates=None:
                            {"domain": d, "status": "drain_inapplicable", "reason": "allow_empty 域走增量"})
    run_domain_calls = []

    def _fake_run_domain(d, registry=None):
        run_domain_calls.append(d)
        return {"domain": d, "batches": 1, "rows": 0, "failed_batches": 0, "ok": True}

    monkeypatch.setattr(sr, "run_domain", _fake_run_domain)
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    sr.main()
    assert run_domain_calls == ["allow_empty_events"], (
        f"应只调用一次 (fallback_incremental 路径), 实际 {run_domain_calls}"
    )
