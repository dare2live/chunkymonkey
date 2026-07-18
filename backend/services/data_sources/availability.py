"""Typed publication eligibility and operation-window contracts.

Transport batching (``by_trade_date``, ``by_ann_date`` and friends) does not
define when a partition is publishable.  Formal datasets declare that policy
explicitly; legacy registry entries continue through the legacy resolver until
their provider timing has independent evidence.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo


AvailabilityAxis = Literal["trading_day", "calendar_day"]
AvailabilityRule = Literal[
    "same_day_at",
    "next_trading_session_at",
    "next_calendar_day_at",
]

_POLICY_KEYS = frozenset({"axis", "rule", "at"})
_ALLOWED_COMBINATIONS = frozenset(
    {
        ("trading_day", "same_day_at"),
        ("trading_day", "next_trading_session_at"),
        ("calendar_day", "same_day_at"),
        ("calendar_day", "next_calendar_day_at"),
    }
)


class SyncWindowError(ValueError):
    """The requested operation window is not proven publishable."""


@dataclass(frozen=True)
class AvailabilityPolicy:
    axis: AvailabilityAxis
    rule: AvailabilityRule
    at: time

    def payload(self) -> dict[str, str]:
        return {
            "axis": self.axis,
            "rule": self.rule,
            "at": self.at.strftime("%H:%M"),
        }


@dataclass(frozen=True)
class DomainEligibility:
    """Latest partition proven publishable at the evaluated instant."""

    eligible_end: str | None
    pending_today: bool
    reason: str


@dataclass(frozen=True)
class OperationWindow:
    """One request cap while retaining the domain's real live frontier."""

    eligibility: DomainEligibility
    requested_start: str | None
    requested_end: str | None
    effective_end: str | None


@dataclass(frozen=True)
class TradingSessionIndex:
    """Normalized immutable sessions reusable across publication proofs."""

    days: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.days, tuple):
            raise SyncWindowError("trading session index days must be an immutable tuple")
        previous: str | None = None
        for day in self.days:
            if not isinstance(day, str) or len(day) != 8 or not day.isdigit():
                raise SyncWindowError(f"invalid trading session index day={day!r}")
            try:
                datetime.strptime(day, "%Y%m%d")
            except ValueError as exc:
                raise SyncWindowError(
                    f"invalid trading session index day={day!r}"
                ) from exc
            if previous is not None and day <= previous:
                raise SyncWindowError(
                    "trading session index days must be strictly increasing and unique"
                )
            previous = day


def prepare_trading_session_index(
    trading_day_values: Sequence[str],
) -> TradingSessionIndex:
    return TradingSessionIndex(
        tuple(sorted({_compact_date(value, "trading_day") for value in trading_day_values}))
    )


def _text(value: Any, field: str, owner: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner}: {field} must be a non-empty string")
    return value.strip()


def _clock(value: Any, field: str, owner: str) -> time:
    raw = _text(value, field, owner)
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{owner}: {field} must be HH:MM") from exc


def availability_policy_from_mapping(
    value: Any, *, owner: str = "availability_policy"
) -> AvailabilityPolicy:
    if not isinstance(value, Mapping):
        raise ValueError(f"{owner}: availability_policy must be a mapping")
    missing = sorted(_POLICY_KEYS - set(value))
    unknown = sorted(set(value) - _POLICY_KEYS)
    if missing:
        raise ValueError(
            f"{owner}: missing availability_policy keys: {', '.join(missing)}"
        )
    if unknown:
        raise ValueError(
            f"{owner}: unknown availability_policy keys: {', '.join(unknown)}"
        )
    axis = _text(value["axis"], "availability_policy.axis", owner)
    rule = _text(value["rule"], "availability_policy.rule", owner)
    if (axis, rule) not in _ALLOWED_COMBINATIONS:
        raise ValueError(
            f"{owner}: unsupported availability axis/rule combination "
            f"{axis!r}/{rule!r}"
        )
    return AvailabilityPolicy(
        axis=axis,  # type: ignore[arg-type]
        rule=rule,  # type: ignore[arg-type]
        at=_clock(value["at"], "availability_policy.at", owner),
    )


def _compact_date(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern).strftime("%Y%m%d")
        except ValueError:
            continue
    raise SyncWindowError(f"{field} must be a real YYYYMMDD date: {value!r}")


def _calendar_date(value: datetime, offset_days: int = 0) -> str:
    return (value.date() + timedelta(days=offset_days)).strftime("%Y%m%d")


def resolve_availability_frontier(
    policy: AvailabilityPolicy,
    *,
    now: datetime,
    trading_day_values: Sequence[str] = (),
) -> DomainEligibility:
    """Resolve a typed publication frontier without inspecting batch mode."""

    passed = now.time().replace(tzinfo=None) >= policy.at
    if policy.axis == "calendar_day":
        if policy.rule == "same_day_at":
            return DomainEligibility(
                _calendar_date(now, 0 if passed else -1),
                not passed,
                "published" if passed else "pending_publish",
            )
        offset = -1 if passed else -2
        return DomainEligibility(
            _calendar_date(now, offset),
            True,
            "next_calendar_day_published" if passed else "next_calendar_day_pending",
        )

    today = now.strftime("%Y%m%d")
    days = sorted(
        {
            str(value).replace("-", "")
            for value in trading_day_values
            if str(value).replace("-", "") <= today
        }
    )
    if not days:
        return DomainEligibility(None, False, "calendar_empty")
    today_is_trading = days[-1] == today
    if policy.rule == "same_day_at":
        if today_is_trading and passed:
            return DomainEligibility(today, False, "published")
        index = -2 if today_is_trading else -1
        return DomainEligibility(
            days[index] if len(days) >= abs(index) else None,
            today_is_trading,
            "pending_publish" if today_is_trading else "latest_prior_trading_day",
        )

    # A trade-date partition becomes visible only at the following trading
    # session's configured boundary.  Weekends/holidays never advance it.
    if today_is_trading:
        index = -2 if passed else -3
        reason = (
            "next_trading_session_published"
            if passed
            else "next_trading_session_pending"
        )
    else:
        index = -2
        reason = "next_trading_session_awaiting_session"
    return DomainEligibility(
        days[index] if len(days) >= abs(index) else None,
        True,
        reason,
    )


def publication_cutoff(
    policy: AvailabilityPolicy,
    *,
    partition_value: Any,
    trading_day_values: Sequence[str] | TradingSessionIndex = (),
) -> datetime:
    """Return the earliest policy-approved publication instant in Shanghai.

    This is the acceptance-side counterpart to ``resolve_availability_frontier``.
    The frontier decides which partition may be requested at a wall-clock
    instant; this function proves that one landed observation did not precede
    its partition's declared release boundary.
    """

    partition = _compact_date(partition_value, "partition")
    if policy.axis == "calendar_day":
        offset = 0 if policy.rule == "same_day_at" else 1
        cutoff_day = (
            datetime.strptime(partition, "%Y%m%d").date()
            + timedelta(days=offset)
        )
    else:
        index = (
            trading_day_values
            if isinstance(trading_day_values, TradingSessionIndex)
            else prepare_trading_session_index(trading_day_values)
        )
        position = bisect_left(index.days, partition)
        if position >= len(index.days) or index.days[position] != partition:
            raise SyncWindowError(
                f"partition={partition} is not present in the trading calendar"
            )
        if policy.rule == "same_day_at":
            cutoff_day = datetime.strptime(partition, "%Y%m%d").date()
        else:
            successor = position + 1
            if successor >= len(index.days):
                raise SyncWindowError(
                    f"trading calendar has no session after partition={partition}"
                )
            cutoff_day = datetime.strptime(index.days[successor], "%Y%m%d").date()
    return datetime.combine(
        cutoff_day,
        policy.at,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


def resolve_operation_window(
    eligibility: DomainEligibility,
    *,
    requested_start: Any = None,
    requested_end: Any = None,
) -> OperationWindow:
    """Validate request bounds against the live frontier before side effects."""

    start = (
        _compact_date(requested_start, "start")
        if requested_start is not None
        else None
    )
    end = _compact_date(requested_end, "end") if requested_end is not None else None
    frontier = eligibility.eligible_end
    if frontier is None:
        if start is not None or end is not None:
            raise SyncWindowError(
                "requested window cannot be proven within the eligible horizon: "
                f"reason={eligibility.reason}"
            )
        return OperationWindow(eligibility, start, end, None)
    if end is not None and end > frontier:
        raise SyncWindowError(
            f"requested end={end} exceeds eligible horizon={frontier} "
            f"({eligibility.reason})"
        )
    effective_end = end or frontier
    if start is not None and start > effective_end:
        raise SyncWindowError(
            f"requested start={start} exceeds effective end={effective_end} "
            f"within eligible horizon={frontier}"
        )
    return OperationWindow(eligibility, start, end, effective_end)


__all__ = [
    "AvailabilityPolicy",
    "DomainEligibility",
    "OperationWindow",
    "SyncWindowError",
    "TradingSessionIndex",
    "availability_policy_from_mapping",
    "prepare_trading_session_index",
    "publication_cutoff",
    "resolve_availability_frontier",
    "resolve_operation_window",
]
