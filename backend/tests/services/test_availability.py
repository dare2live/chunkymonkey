from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.availability import (
    DomainEligibility,
    SyncWindowError,
    TradingSessionIndex,
    availability_policy_from_mapping,
    prepare_trading_session_index,
    publication_cutoff,
    resolve_availability_frontier,
    resolve_operation_window,
    resolve_sync_eligibility_frontier,
)


TZ = ZoneInfo("Asia/Shanghai")


def _policy(axis: str, rule: str, at: str = "09:00"):
    return availability_policy_from_mapping(
        {"axis": axis, "rule": rule, "at": at}, owner="test"
    )


@pytest.mark.parametrize(
    ("now", "expected", "reason"),
    [
        (datetime(2026, 7, 18, 12, 0, tzinfo=TZ), "20260716", "next_trading_session_awaiting_session"),
        (datetime(2026, 7, 20, 8, 59, tzinfo=TZ), "20260716", "next_trading_session_pending"),
        (datetime(2026, 7, 20, 9, 0, tzinfo=TZ), "20260717", "next_trading_session_published"),
    ],
)
def test_next_trading_session_frontier_is_clocked(now, expected, reason):
    result = resolve_availability_frontier(
        _policy("trading_day", "next_trading_session_at"),
        now=now,
        trading_day_values=["20260716", "20260717", "20260720"],
    )
    assert (result.eligible_end, result.reason) == (expected, reason)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(8, 59, "20260718"), (9, 0, "20260719")],
)
def test_next_calendar_day_frontier_preserves_weekend_announcements(
    hour, minute, expected
):
    result = resolve_availability_frontier(
        _policy("calendar_day", "next_calendar_day_at"),
        now=datetime(2026, 7, 20, hour, minute, tzinfo=TZ),
    )
    assert result.eligible_end == expected


def test_publication_cutoff_uses_next_real_session_across_weekend_and_holiday():
    cutoff = publication_cutoff(
        _policy("trading_day", "next_trading_session_at"),
        partition_value="20260930",
        trading_day_values=["20260930", "20261009"],
    )
    assert cutoff == datetime(2026, 10, 9, 9, 0, tzinfo=TZ)


def test_publication_cutoff_fails_closed_without_successor_session():
    with pytest.raises(SyncWindowError, match="no session after"):
        publication_cutoff(
            _policy("trading_day", "next_trading_session_at"),
            partition_value="20260717",
            trading_day_values=["20260717"],
        )


def test_prepared_trading_sessions_are_normalized_once_not_per_partition(
    monkeypatch,
):
    from services.data_sources import availability

    sessions = tuple(
        (datetime(2026, 1, 1) + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(100)
    )
    calls = 0
    original = availability._compact_date

    def counted(value, field):
        nonlocal calls
        calls += 1
        return original(value, field)

    monkeypatch.setattr(availability, "_compact_date", counted)
    index = prepare_trading_session_index(sessions)
    for partition in sessions[:-1]:
        publication_cutoff(
            _policy("trading_day", "next_trading_session_at"),
            partition_value=partition,
            trading_day_values=index,
        )

    assert calls == len(sessions) + len(sessions) - 1


@pytest.mark.parametrize(
    "days",
    [
        ("20260715", "20260717", "20260716"),
        ("20260715", "20260715", "20260716"),
        ("20260715", "20260716", "not-a-date"),
    ],
    ids=("unordered", "duplicate", "invalid"),
)
def test_direct_trading_session_index_construction_fails_closed(days):
    """A caller must not bypass normalization by instantiating the type directly."""

    with pytest.raises(SyncWindowError, match="trading session index"):
        publication_cutoff(
            _policy("trading_day", "next_trading_session_at"),
            partition_value="20260715",
            trading_day_values=TradingSessionIndex(days),
        )


def test_historical_operation_cap_keeps_real_domain_frontier():
    eligibility = DomainEligibility(
        "20260716", True, "next_trading_session_awaiting_session"
    )
    window = resolve_operation_window(
        eligibility,
        requested_start="20260715",
        requested_end="20260715",
    )
    assert window.effective_end == "20260715"
    assert window.eligibility == eligibility


def test_manual_same_day_at_allows_open_trading_day_before_clock():
    """Click/chunkyctl sync may fetch today's session; 18:00 is not a manual gate."""

    days = ["20260716", "20260717", "20260720"]
    now = datetime(2026, 7, 20, 14, 19, tzinfo=TZ)
    policy = _policy("trading_day", "same_day_at", "18:00")
    consumer = resolve_availability_frontier(
        policy, now=now, trading_day_values=days
    )
    manual = resolve_sync_eligibility_frontier(
        policy, now=now, trading_day_values=days, trigger_mode="manual"
    )
    automatic = resolve_sync_eligibility_frontier(
        policy, now=now, trading_day_values=days, trigger_mode="automatic"
    )
    assert (consumer.eligible_end, consumer.reason) == (
        "20260717",
        "pending_publish",
    )
    assert (manual.eligible_end, manual.reason) == (
        "20260720",
        "manual_calendar_eligible",
    )
    assert (automatic.eligible_end, automatic.reason) == (
        "20260717",
        "pending_publish",
    )
    window = resolve_operation_window(
        manual, requested_start="20260720", requested_end="20260720"
    )
    assert window.effective_end == "20260720"
    with pytest.raises(SyncWindowError, match="pending_publish"):
        resolve_operation_window(
            automatic, requested_start="20260720", requested_end="20260720"
        )


def test_manual_and_automatic_both_refuse_weekend_same_day_at():
    days = ["20260716", "20260717"]
    now = datetime(2026, 7, 18, 14, 0, tzinfo=TZ)
    policy = _policy("trading_day", "same_day_at", "18:00")
    for mode in ("manual", "automatic"):
        result = resolve_sync_eligibility_frontier(
            policy, now=now, trading_day_values=days, trigger_mode=mode
        )
        assert result.eligible_end == "20260717"
        assert result.reason == "latest_prior_trading_day"
        with pytest.raises(SyncWindowError):
            resolve_operation_window(
                result, requested_start="20260718", requested_end="20260718"
            )


def test_manual_does_not_bypass_next_trading_session_gate():
    """T+1 / next-session policies stay clocked for every trigger mode."""

    result = resolve_sync_eligibility_frontier(
        _policy("trading_day", "next_trading_session_at", "09:00"),
        now=datetime(2026, 7, 20, 8, 59, tzinfo=TZ),
        trading_day_values=["20260716", "20260717", "20260720"],
        trigger_mode="manual",
    )
    assert (result.eligible_end, result.reason) == (
        "20260716",
        "next_trading_session_pending",
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("20260715", "20260717"),
        ("20260717", None),
        ("20260716", "2026-02-30"),
    ],
)
def test_operation_window_rejects_unproven_or_invalid_bounds(start, end):
    with pytest.raises(SyncWindowError):
        resolve_operation_window(
            DomainEligibility("20260716", True, "pending"),
            requested_start=start,
            requested_end=end,
        )
