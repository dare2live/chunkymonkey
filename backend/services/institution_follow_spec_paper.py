"""Follow StrategySpec paper from frozen stk_holdertrade announcements.

Overnight B0/B4 ablation stays on its own module. This path is not a
full-day generation bind.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from services.data_sources.stk_holdertrade_schema import CANONICAL_TABLE as HOLDERTRADE_TABLE
from services.one_name_pointer_bars import (
    OneNamePointerError,
    assert_replay_before_holdout,
    compact_day,
    load_one_name_pointer_bars,
    nominal_days_from_snapshot,
)
from services.strategy_paper import (
    FOLLOWER_PNL_SOURCE,
    DisclosureEvent,
    simulate_follow_hold_paper,
)
from services.strategy_spec import StrategySpec, load_strategy_spec


IN_DE_KIND: dict[str, Literal["increase", "decrease"]] = {
    "IN": "increase",
    "DE": "decrease",
}


class FollowSpecPaperError(RuntimeError):
    """Follow spec paper refused holdout, unknown in_de, or a mixed PnL path."""


def _raise_pointer(exc: OneNamePointerError) -> None:
    raise FollowSpecPaperError(str(exc)) from exc


def holdertrade_partition_days(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    domains = snapshot.get("domains")
    if not isinstance(domains, Mapping):
        raise FollowSpecPaperError("missing_snapshot_holdertrade")
    holdertrade = domains.get("stk_holdertrade")
    if not isinstance(holdertrade, Mapping):
        raise FollowSpecPaperError("missing_snapshot_holdertrade")
    accepted = holdertrade.get("accepted")
    if not isinstance(accepted, (list, tuple)) or not accepted:
        raise FollowSpecPaperError("missing_snapshot_holdertrade")
    days: list[str] = []
    for row in accepted:
        if not isinstance(row, Mapping):
            raise FollowSpecPaperError("missing_snapshot_holdertrade")
        day = compact_day(row.get("partition") or row.get("ann_date"))
        if len(day) != 8:
            raise FollowSpecPaperError("invalid_snapshot_holdertrade_partition")
        days.append(day)
    unique = tuple(sorted(set(days)))
    try:
        assert_replay_before_holdout(unique)
    except OneNamePointerError as exc:
        _raise_pointer(exc)
    return unique


def events_from_holdertrade_rows(
    rows: Sequence[Any],
    *,
    ts_code: str,
) -> tuple[DisclosureEvent, ...]:
    events: list[DisclosureEvent] = []
    for row in rows:
        if isinstance(row, Mapping):
            code = str(row.get("ts_code") or "")
            raw_ann = row.get("ann_date")
            in_de = str(row.get("in_de") or "").upper()
        else:
            vals = tuple(row)
            code = str(vals[0] if vals else "")
            raw_ann = vals[1] if len(vals) > 1 else ""
            in_de = str(vals[2] if len(vals) > 2 else "").upper()
        day = compact_day(raw_ann)
        if len(day) != 8:
            continue
        if code != ts_code:
            raise FollowSpecPaperError("holdertrade_loaded_other_names")
        kind = IN_DE_KIND.get(in_de)
        if kind is None:
            raise FollowSpecPaperError("unknown_in_de")
        events.append(DisclosureEvent(ts_code=ts_code, available_at=day, kind=kind))
    if events:
        try:
            assert_replay_before_holdout([event.available_at for event in events])
        except OneNamePointerError as exc:
            _raise_pointer(exc)
    return tuple(events)


def load_holdertrade_events(
    snapshot: Mapping[str, Any],
    conn: Any,
    ts_code: str,
) -> tuple[DisclosureEvent, ...]:
    partitions = holdertrade_partition_days(snapshot)
    placeholders = ", ".join(["?"] * len(partitions))
    sql = f"""
        SELECT ts_code, ann_date, in_de
          FROM {HOLDERTRADE_TABLE}
         WHERE replace(CAST(ann_date AS VARCHAR), '-', '')
               IN ({placeholders})
           AND ts_code = ?
         ORDER BY ann_date
    """
    try:
        rows = conn.execute(sql, [*partitions, ts_code]).fetchall()
    except FollowSpecPaperError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed on any driver error
        raise FollowSpecPaperError("holdertrade_canonical_unreadable") from exc
    return events_from_holdertrade_rows(rows, ts_code=ts_code)


def follow_bar_days(
    snapshot: Mapping[str, Any],
    events: Sequence[DisclosureEvent],
    spec: StrategySpec,
) -> tuple[str, ...]:
    try:
        frozen = set(nominal_days_from_snapshot(snapshot))
    except OneNamePointerError as exc:
        _raise_pointer(exc)
    max_chase = int(spec.max_chase_days)
    want: set[str] = set()
    for event in events:
        day = compact_day(event.available_at)
        if day in frozen:
            want.add(day)
        later = [item for item in sorted(frozen) if item > day][: max_chase + 1]
        want.update(later)
    if not want:
        raise FollowSpecPaperError("empty_follow_bar_window")
    extra = [day for day in want if day not in frozen]
    if extra:
        raise FollowSpecPaperError("bars_not_in_snapshot")
    try:
        assert_replay_before_holdout(sorted(want))
    except OneNamePointerError as exc:
        _raise_pointer(exc)
    return tuple(sorted(want))


def simulate_follow_spec_paper(
    *,
    ts_code: str,
    snapshot: Mapping[str, Any] | None = None,
    conn: Any = None,
    events: Sequence[DisclosureEvent] | None = None,
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    spec: StrategySpec | None = None,
):
    loaded = spec or load_strategy_spec("institution_follow_v1")
    if loaded.package_id != "institution_follow_v1":
        raise FollowSpecPaperError("follow_spec_paper_requires_institution_follow_spec")
    if loaded.pnl_source != FOLLOWER_PNL_SOURCE:
        raise FollowSpecPaperError("follower_pnl_must_not_use_institution_alpha")

    if events is None:
        if snapshot is None or conn is None:
            raise FollowSpecPaperError("missing_holdertrade_source")
        try:
            nominal_days_from_snapshot(snapshot)
        except OneNamePointerError as exc:
            _raise_pointer(exc)
        resolved_events = load_holdertrade_events(snapshot, conn, ts_code)
    else:
        resolved_events = tuple(events)
        try:
            assert_replay_before_holdout(
                [event.available_at for event in resolved_events]
            )
        except OneNamePointerError as exc:
            _raise_pointer(exc)

    if bars_by_day is not None:
        try:
            assert_replay_before_holdout(list(bars_by_day))
        except OneNamePointerError as exc:
            _raise_pointer(exc)
        bars = bars_by_day
    else:
        if snapshot is None or conn is None:
            raise FollowSpecPaperError("missing_bars_source")
        days = follow_bar_days(snapshot, resolved_events, loaded)
        try:
            bars = load_one_name_pointer_bars(
                snapshot, conn, ts_code, days=days
            )
        except OneNamePointerError as exc:
            _raise_pointer(exc)
    return simulate_follow_hold_paper(bars, resolved_events, loaded)


__all__ = [
    "FOLLOWER_PNL_SOURCE",
    "FollowSpecPaperError",
    "IN_DE_KIND",
    "events_from_holdertrade_rows",
    "follow_bar_days",
    "holdertrade_partition_days",
    "load_holdertrade_events",
    "simulate_follow_spec_paper",
]
