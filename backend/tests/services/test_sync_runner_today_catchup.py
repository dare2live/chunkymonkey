"""by_trade_date 域"今日补拉"机制单测 (2026-07-06 全面数据审计手工全流程走查抓获的根因修复).

背景: drain_domain 故意排除"今天"(避免误判尚未发布的当日数据), 但此前 main() --drain
分支只在 drain 返回 unsupported/drain_inapplicable 时才 fallback 到 run_domain 补今日
增量——by_trade_date 域 (daily/daily_basic/adj_factor/stk_limit 等 21 个) drain 永远
返回受支持状态, 从不 fallback, 导致"今天"的数据在跑批当天永远不会真正入库, 只能等
下一次手工跑批时被当成"昨天的缺口"补上 (无 cron/launchd, 可能几天后)。

本门锁定: (1) _available_after_passed 时刻判断 (HH:MM 边界/t+1 恒False/异常格式保守
False); (2) main() --drain 分支只在 available_after 已过时才对 by_trade_date 域触发
今日补拉, 未到不产生噪音失败; (3) 今日补拉真失败会反映到最终 bad/exit code。
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


def test_available_after_passed_hh_mm_boundary():
    spec = {"available_after": "18:00"}
    assert sr._available_after_passed(spec, now=_at(17, 59)) is False
    assert sr._available_after_passed(spec, now=_at(18, 0)) is True
    assert sr._available_after_passed(spec, now=_at(18, 1)) is True


def test_available_after_passed_t_plus_1_always_false():
    """t+1 (次日才发布) 域: 无论多晚都不该同日补, 交给 drain 下次当历史缺口自然捕获。"""
    spec = {"available_after": "t+1"}
    assert sr._available_after_passed(spec, now=_at(23, 59)) is False


def test_available_after_passed_missing_or_malformed_is_conservative_false():
    assert sr._available_after_passed({}, now=_at(23, 59)) is False
    assert sr._available_after_passed({"available_after": "garbage"}, now=_at(23, 59)) is False
    assert sr._available_after_passed({"available_after": ""}, now=_at(23, 59)) is False


def test_domain_eligibility_uses_domain_publish_time_not_global_close_cutoff():
    """同一交易日是否 eligible 只看域自己的 available_after；09:20 域不能被 16:00 clamp。"""
    days = ["20260714", "20260715"]
    before = sr.eligible_end_date(
        {"available_after": "18:00", "data_start": "20200101"},
        now=_at_july_15(17, 59),
        trading_days=days,
    )
    assert before.eligible_end == "20260714" and before.pending_today is True

    morning = sr.eligible_end_date(
        {"available_after": "09:20", "data_start": "20200101"},
        now=_at_july_15(9, 21),
        trading_days=days,
    )
    assert morning.eligible_end == "20260715" and morning.pending_today is False


def test_t_plus_one_domain_never_targets_current_trade_date():
    result = sr.eligible_end_date(
        {"available_after": "t+1", "data_start": "20200101"},
        now=_at_july_15(23, 59),
        trading_days=["20260714", "20260715"],
    )
    assert result.eligible_end == "20260714"
    assert result.pending_today is True


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
        "_trading_days",
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


def test_drain_main_triggers_today_catchup_when_available_after_passed(monkeypatch):
    """主流程: by_trade_date 域 drain 后, available_after 已过时必须再触发一次 run_domain
    今日补拉 (且结果挂在 today_catchup 键, 不与 drain 自身结果混淆)。"""
    reg = {"domains": {"daily": {"batch_mode": "by_trade_date", "available_after": "08:40"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: True)
    monkeypatch.setattr(sr, "drain_domain",
                        lambda d, registry=None, max_dates=None: {"domain": d, "status": "clean"})
    catchup_calls = []

    def _fake_run_domain(d, registry=None):
        catchup_calls.append(d)
        return {"domain": d, "batches": 1, "rows": 5194, "failed_batches": 0, "ok": True}

    monkeypatch.setattr(sr, "run_domain", _fake_run_domain)
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    rc = sr.main()
    assert catchup_calls == ["daily"], "available_after 已过时必须触发今日补拉"
    assert rc == 0


def test_drain_main_skips_today_catchup_when_not_yet_available(monkeypatch):
    """available_after 未到 (如 18:00 域, 现在才 16:00) 时不应触发补拉, 避免噪音失败。"""
    reg = {"domains": {"dc_member": {"batch_mode": "by_trade_date", "available_after": "18:00"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: False)
    monkeypatch.setattr(sr, "drain_domain",
                        lambda d, registry=None, max_dates=None: {"domain": d, "status": "clean"})
    catchup_calls = []
    monkeypatch.setattr(sr, "run_domain", lambda d, registry=None: catchup_calls.append(d))
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    rc = sr.main()
    assert catchup_calls == [], "available_after 未到不应触发今日补拉"
    assert rc == 0


def test_drain_main_today_catchup_real_failure_marks_bad(monkeypatch):
    """available_after 已过但今日补拉仍然失败 (真问题, 非"还没发布") — 必须让整体 exit 非0。"""
    reg = {"domains": {"daily": {"batch_mode": "by_trade_date", "available_after": "08:40"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: True)
    monkeypatch.setattr(sr, "drain_domain",
                        lambda d, registry=None, max_dates=None: {"domain": d, "status": "clean"})
    monkeypatch.setattr(sr, "run_domain",
                        lambda d, registry=None: {"domain": d, "batches": 1, "rows": 0,
                                                   "failed_batches": 1, "ok": False})
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])
    rc = sr.main()
    assert rc == 1, "available_after 已过仍失败必须反映到 exit code"


def test_drain_main_fallback_incremental_domain_not_double_called(monkeypatch):
    """已走 fallback_incremental 路径 (drain_inapplicable/unsupported) 的域不应再额外触发
    今日补拉 (if/elif 互斥, 防重复调用 run_domain)。"""
    reg = {"domains": {"margin": {"batch_mode": "by_trade_date", "available_after": "08:40",
                                   "allow_empty_batch": True}}}
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "_available_after_passed", lambda spec, now=None: True)
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
    assert run_domain_calls == ["margin"], f"应只调用一次 (fallback_incremental 路径), 实际 {run_domain_calls}"
