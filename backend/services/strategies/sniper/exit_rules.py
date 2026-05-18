"""Exit rules for MSAF Scheme 6 Sniper positions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class ExitSignal:
    exit_type: str
    exit_price: float
    exit_reason: str


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def update_running_high(
    entry_price: float,
    running_high_price: float | None,
    current_high_price: float | None = None,
    current_price: float | None = None,
) -> float:
    """Track highest observable price since entry."""
    candidates = [entry_price]
    if running_high_price is not None:
        candidates.append(running_high_price)
    if current_high_price is not None:
        candidates.append(current_high_price)
    if current_price is not None:
        candidates.append(current_price)
    return max(float(v) for v in candidates if v is not None)


def evaluate_exit(
    *,
    entry_date: str | date | datetime,
    entry_price: float,
    current_date: str | date | datetime,
    current_price: float,
    running_high_price: float | None = None,
    current_high_price: float | None = None,
    trailing_stop_pct: float = 0.08,
    target_pct: float = 0.20,
    time_stop_days: int = 20,
) -> ExitSignal | None:
    """Return the first full-position Sniper exit signal, if any.

    Priority is target, trailing stop, then time stop.  Daily bars cannot prove
    intraday ordering, so target touch is honored first at the target price.
    """
    if entry_price <= 0 or current_price <= 0:
        return None

    high = update_running_high(
        entry_price,
        running_high_price,
        current_high_price,
        current_price,
    )

    target_price = entry_price * (1.0 + target_pct)
    high_today = current_high_price if current_high_price is not None else current_price
    if high_today >= target_price or current_price >= target_price:
        return ExitSignal(
            exit_type="target_exit",
            exit_price=target_price,
            exit_reason=f"target_exit:+{target_pct:.0%}_from_entry",
        )

    stop_price = high * (1.0 - trailing_stop_pct)
    if current_price <= stop_price:
        return ExitSignal(
            exit_type="trailing_stop",
            exit_price=current_price,
            exit_reason=f"trailing_stop:-{trailing_stop_pct:.0%}_from_entry_high",
        )

    days_held = (_parse_date(current_date) - _parse_date(entry_date)).days
    if days_held >= time_stop_days:
        return ExitSignal(
            exit_type="time_stop",
            exit_price=current_price,
            exit_reason=f"time_stop:{time_stop_days}_calendar_days",
        )
    return None

