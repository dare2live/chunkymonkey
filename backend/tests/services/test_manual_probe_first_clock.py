"""Lock-in: 「数据更新」(manual) probes the provider for *today* regardless of the
conservative ``available_after`` clock; the clock only (a) governs the automatic
consumer/continuity frontier for PIT safety and (b) classifies an empty pull as
typed ``pending_publish`` (before window) vs fail-closed (after window).

Owner 2026-07-22: reject "clock < 18:00 ⇒ never ask". This test proves the code
already validates reality on click — daily/moneyflow do not dead-wait.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from services.data_sources import sync_runner as sr

_SH = ZoneInfo("Asia/Shanghai")


def _hhmm_spec(available_after: str = "18:00") -> dict:
    # Legacy HH:MM domain (no typed availability_policy) — e.g. moneyflow.
    return {
        "domain": "probe_demo",
        "batch_mode": "by_trade_date",
        "date_param": "trade_date",
        "data_start": "20260701",
        "available_after": available_after,
    }


def _days() -> list[str]:
    # today = 20260722 is the last (open) trading day in the injected calendar.
    return ["20260721", "20260722"]


def test_manual_click_makes_today_eligible_before_window():
    """UI/chunkyctl manual: today is calendar-eligible even at 16:00 (< 18:00)."""
    now = datetime(2026, 7, 22, 16, 0, tzinfo=_SH)
    elig = sr.eligible_end_date(
        _hhmm_spec("18:00"), now=now, trading_day_values=_days(), trigger_mode="manual"
    )
    assert elig.eligible_end == "20260722"
    assert elig.reason == "manual_calendar_eligible"


def test_automatic_scheduled_keeps_clock_before_window():
    """Automatic consumer/continuity frontier stays clocked (PIT): today pends."""
    now = datetime(2026, 7, 22, 16, 0, tzinfo=_SH)
    elig = sr.eligible_end_date(
        _hhmm_spec("18:00"), now=now, trading_day_values=_days(), trigger_mode="automatic"
    )
    assert elig.eligible_end == "20260721"  # prior session, not today
    assert elig.reason == "pending_publish"


def test_automatic_after_window_publishes_today():
    now = datetime(2026, 7, 22, 18, 30, tzinfo=_SH)
    elig = sr.eligible_end_date(
        _hhmm_spec("18:00"), now=now, trading_day_values=_days(), trigger_mode="automatic"
    )
    assert elig.eligible_end == "20260722"
    assert elig.reason == "published"


def test_empty_before_window_is_pending_publish_not_failure():
    """Probe today, empty, before 18:00 → typed soft pending_publish (retryable)."""
    now = datetime(2026, 7, 22, 16, 0, tzinfo=_SH)
    assert sr._is_pre_publish_same_day_zero(
        _hhmm_spec("18:00"), {"trade_date": "20260722"}, now=now
    ) is True


def test_empty_after_window_is_fail_closed_not_pending():
    """Past the window an empty pull must NOT be excused as pending — fail-closed."""
    now = datetime(2026, 7, 22, 18, 30, tzinfo=_SH)
    assert sr._is_pre_publish_same_day_zero(
        _hhmm_spec("18:00"), {"trade_date": "20260722"}, now=now
    ) is False
