"""Shared next-tradable-open hold paper for follow and formula smoke.

One name, one position: buy the first tradable open after an entry signal;
sell the first tradable open after an exit signal or max_hold_calendar_days.
Re-entry is allowed only after the previous exit. Sells are never on the
entry day (A-share T+1). Follow maps increase/decrease disclosures onto this
loop. Formulas map daily entry/exit bools onto the same loop and must not
call `simulate_follow_hold_paper`. Overnight B0/B4 `simulate_paper_fills`
stays on institution_follow_paper.py. This is not `vwap_tradable_v1`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Mapping, Sequence

from services.institution_follow_b0_measure import (
    COMMISSION_RATE,
    SLIPPAGE_RATE,
    STAMP_TAX_RATE,
)
from services.institution_follow_paper import (
    is_limit_down_open,
    is_limit_up_open,
    is_suspended,
)
from services.strategy_spec import StrategySpec


FOLLOWER_PNL_SOURCE = "follower_next_open_to_exit_open"


class StrategyPaperError(RuntimeError):
    """Hold paper refused a spec that would steal institution alpha."""


@dataclass(frozen=True)
class SignalEvent:
    ts_code: str
    available_at: str
    kind: Literal["entry", "exit"]


@dataclass(frozen=True)
class DisclosureEvent:
    ts_code: str
    available_at: str
    kind: Literal["increase", "decrease"]


@dataclass(frozen=True)
class HoldPaperFill:
    ts_code: str
    signal_available_at: str
    entry_date: str | None
    exit_date: str | None
    entry_px: float | None
    exit_px: float | None
    net_return: float | None
    status: str
    exit_reason: str
    pnl_source: str = FOLLOWER_PNL_SOURCE


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _add_calendar_days(day: str, days: int) -> str:
    stamp = datetime.strptime(day, "%Y%m%d")
    return (stamp + timedelta(days=int(days))).strftime("%Y%m%d")


def _bar_index(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, Mapping[str, Any]]]:
    out: dict[str, dict[str, Mapping[str, Any]]] = {}
    for day, rows in bars_by_day.items():
        compact = _compact_day(day)
        by_code: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code:
                by_code[code] = row
        out[compact] = by_code
    return out


def _pre_close(
    by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    days: Sequence[str],
    day: str,
    code: str,
    bar: Mapping[str, Any],
) -> float:
    raw = bar.get("pre_close")
    if raw not in (None, ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    idx = list(days).index(day)
    if idx <= 0:
        return 0.0
    prev = (by_day.get(days[idx - 1]) or {}).get(code)
    if not prev:
        return 0.0
    try:
        return float(prev.get("close") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _net_return(entry_px: float, exit_px: float, *, buy_cost: float, sell_cost: float) -> float:
    return (exit_px * (1.0 - sell_cost)) / (entry_px * (1.0 + buy_cost)) - 1.0


def _first_tradable(
    *,
    days: Sequence[str],
    by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    code: str,
    after_day: str,
    max_chase_days: int,
    side: Literal["buy", "sell"],
    on_or_after: bool = False,
) -> tuple[str, float] | None:
    if on_or_after:
        candidates = [day for day in days if day >= after_day]
    else:
        candidates = [day for day in days if day > after_day]
    for chase, day in enumerate(candidates):
        if chase > max_chase_days:
            return None
        bar = (by_day.get(day) or {}).get(code)
        if bar is None:
            continue
        try:
            open_px = float(bar["open"])
        except (TypeError, ValueError, KeyError):
            continue
        if open_px <= 0 or is_suspended(bar):
            continue
        pre = _pre_close(by_day, days, day, code, bar)
        if side == "buy" and is_limit_up_open(open_px, pre, code):
            continue
        if side == "sell" and is_limit_down_open(open_px, pre, code):
            continue
        return day, open_px
    return None


def _first_sell_after_entry(
    *,
    days: Sequence[str],
    by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    code: str,
    entry_date: str,
    after_day: str,
    max_chase_days: int,
    on_or_after: bool,
) -> tuple[str, float] | None:
    """Sell next tradable open, never on the entry day (A-share T+1)."""

    found = _first_tradable(
        days=days,
        by_day=by_day,
        code=code,
        after_day=after_day,
        max_chase_days=max_chase_days,
        side="sell",
        on_or_after=on_or_after,
    )
    if found is None:
        return None
    sell_day, sell_px = found
    if sell_day > entry_date:
        return sell_day, sell_px
    return _first_tradable(
        days=days,
        by_day=by_day,
        code=code,
        after_day=entry_date,
        max_chase_days=max_chase_days,
        side="sell",
        on_or_after=False,
    )


def simulate_signal_hold_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    events: Sequence[SignalEvent],
    spec: StrategySpec,
    *,
    pnl_source: str,
    event_exit_reason: str,
    commission_rate: float = COMMISSION_RATE,
    stamp_tax_rate: float = STAMP_TAX_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> tuple[HoldPaperFill, ...]:
    """Buy next tradable open after entry; sell next tradable after exit or max hold."""

    if spec.pnl_source in {
        "vwap_tradable_v1",
        "optuna_adoption",
        "optuna_params",
    }:
        raise StrategyPaperError("legacy_optuna_or_vwap_is_not_paper")
    if spec.max_hold_calendar_days is None:
        raise StrategyPaperError("missing_exit")
    if spec.pnl_source != pnl_source:
        raise StrategyPaperError("pnl_source_mismatch")

    by_day = _bar_index(bars_by_day)
    days = tuple(sorted(by_day))
    buy_cost = commission_rate + slippage_rate
    sell_cost = commission_rate + stamp_tax_rate + slippage_rate
    max_chase = int(spec.max_chase_days)
    max_hold = int(spec.max_hold_calendar_days)

    by_code: dict[str, list[SignalEvent]] = {}
    for event in events:
        code = str(event.ts_code)
        by_code.setdefault(code, []).append(
            SignalEvent(
                ts_code=code,
                available_at=_compact_day(event.available_at),
                kind=event.kind,
            )
        )

    fills: list[HoldPaperFill] = []
    for code in sorted(by_code):
        ordered = sorted(
            by_code[code],
            key=lambda item: (item.available_at, 0 if item.kind == "entry" else 1),
        )
        last_exit_date: str | None = None
        used_entries: set[int] = set()
        for idx, entry_event in enumerate(ordered):
            if entry_event.kind != "entry" or idx in used_entries:
                continue
            if last_exit_date is not None and entry_event.available_at < last_exit_date:
                continue
            used_entries.add(idx)
            buy_after = entry_event.available_at
            if last_exit_date is not None:
                buy_after = max(buy_after, last_exit_date)
            entry = _first_tradable(
                days=days,
                by_day=by_day,
                code=code,
                after_day=buy_after,
                max_chase_days=max_chase,
                side="buy",
            )
            if entry is None:
                fills.append(
                    HoldPaperFill(
                        ts_code=code,
                        signal_available_at=entry_event.available_at,
                        entry_date=None,
                        exit_date=None,
                        entry_px=None,
                        exit_px=None,
                        net_return=None,
                        status="unfilled",
                        exit_reason="chase_expired",
                        pnl_source=pnl_source,
                    )
                )
                last_exit_date = entry_event.available_at
                continue
            entry_date, entry_px = entry
            exit_event = next(
                (
                    item
                    for item in ordered
                    if item.kind == "exit"
                    and item.available_at >= entry_event.available_at
                ),
                None,
            )
            expiry = _add_calendar_days(entry_date, max_hold)
            event_exit = (
                _first_sell_after_entry(
                    days=days,
                    by_day=by_day,
                    code=code,
                    entry_date=entry_date,
                    after_day=exit_event.available_at,
                    max_chase_days=max_chase,
                    on_or_after=False,
                )
                if exit_event is not None
                else None
            )
            hold_exit = _first_sell_after_entry(
                days=days,
                by_day=by_day,
                code=code,
                entry_date=entry_date,
                after_day=expiry,
                max_chase_days=max_chase,
                on_or_after=True,
            )
            chosen: tuple[str, str, float] | None = None
            if event_exit is not None and hold_exit is not None:
                if event_exit[0] <= hold_exit[0]:
                    chosen = (event_exit_reason, event_exit[0], event_exit[1])
                else:
                    chosen = ("max_hold", hold_exit[0], hold_exit[1])
            elif event_exit is not None:
                chosen = (event_exit_reason, event_exit[0], event_exit[1])
            elif hold_exit is not None:
                chosen = ("max_hold", hold_exit[0], hold_exit[1])
            if chosen is None:
                fills.append(
                    HoldPaperFill(
                        ts_code=code,
                        signal_available_at=entry_event.available_at,
                        entry_date=entry_date,
                        exit_date=None,
                        entry_px=entry_px,
                        exit_px=None,
                        net_return=None,
                        status="unfilled",
                        exit_reason="window_end",
                        pnl_source=pnl_source,
                    )
                )
                break
            reason, exit_date, exit_px = chosen
            fills.append(
                HoldPaperFill(
                    ts_code=code,
                    signal_available_at=entry_event.available_at,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    entry_px=entry_px,
                    exit_px=exit_px,
                    net_return=_net_return(
                        entry_px, exit_px, buy_cost=buy_cost, sell_cost=sell_cost
                    ),
                    status="filled",
                    exit_reason=reason,
                    pnl_source=pnl_source,
                )
            )
            last_exit_date = exit_date
    return tuple(fills)


def simulate_follow_hold_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    events: Sequence[DisclosureEvent],
    spec: StrategySpec,
    *,
    commission_rate: float = COMMISSION_RATE,
    stamp_tax_rate: float = STAMP_TAX_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> tuple[HoldPaperFill, ...]:
    if spec.package_id != "institution_follow_v1":
        raise StrategyPaperError("hold_paper_requires_institution_follow_spec")
    if spec.pnl_source != FOLLOWER_PNL_SOURCE:
        raise StrategyPaperError("follower_pnl_must_not_use_institution_alpha")
    mapped = tuple(
        SignalEvent(
            ts_code=event.ts_code,
            available_at=event.available_at,
            kind="entry" if event.kind == "increase" else "exit",
        )
        for event in events
    )
    return simulate_signal_hold_paper(
        bars_by_day,
        mapped,
        spec,
        pnl_source=FOLLOWER_PNL_SOURCE,
        event_exit_reason="event_decrease",
        commission_rate=commission_rate,
        stamp_tax_rate=stamp_tax_rate,
        slippage_rate=slippage_rate,
    )


__all__ = [
    "DisclosureEvent",
    "FOLLOWER_PNL_SOURCE",
    "HoldPaperFill",
    "SignalEvent",
    "StrategyPaperError",
    "simulate_follow_hold_paper",
    "simulate_signal_hold_paper",
]
