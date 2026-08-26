"""One-name canonical bars after live pointer preflight.

This is not a full-day generation bind and does not spend the holdout token.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from services.data_sources.nominal_ohlcv_schema import CANONICAL_TABLE, PROVIDER_FIELDS
from services.snapshot_nominal_bind import (
    SnapshotNominalBindError,
    assert_live_nominal_pointer_matches_snapshot,
)


class OneNamePointerError(RuntimeError):
    """One-name pointer load refused holdout, drift, or a multi-name row."""


def compact_day(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def assert_replay_before_holdout(
    days: Sequence[str],
    *,
    holdout_start: str | None = None,
) -> None:
    from services.holdout_guard import load_policy

    start = compact_day(holdout_start or load_policy()["holdout_start"])
    if any(compact_day(day) >= start for day in days if compact_day(day)):
        raise OneNamePointerError("holdout_partition_refused")


def nominal_days_from_snapshot(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    domains = snapshot.get("domains")
    if not isinstance(domains, Mapping):
        raise OneNamePointerError("missing_snapshot_nominal")
    nominal = domains.get("nominal_ohlcv")
    if not isinstance(nominal, Mapping):
        raise OneNamePointerError("missing_snapshot_nominal")
    accepted = nominal.get("accepted")
    if not isinstance(accepted, (list, tuple)) or not accepted:
        raise OneNamePointerError("missing_snapshot_nominal")
    days: list[str] = []
    for row in accepted:
        if not isinstance(row, Mapping):
            raise OneNamePointerError("missing_snapshot_nominal")
        day = compact_day(row.get("partition") or row.get("trade_date"))
        if len(day) != 8:
            raise OneNamePointerError("invalid_snapshot_nominal_partition")
        days.append(day)
    unique = tuple(sorted(set(days)))
    assert_replay_before_holdout(unique)
    return unique


def _canonical_row_to_bar(row: Any) -> dict[str, Any]:
    vals = tuple(row)
    item = dict(zip(PROVIDER_FIELDS, vals, strict=True))
    raw_day = item["trade_date"]
    day = (
        raw_day.strftime("%Y%m%d")
        if isinstance(raw_day, date)
        else compact_day(raw_day)
    )
    return {
        "ts_code": str(item["ts_code"]),
        "trade_date": day,
        "open": item["open"],
        "high": item["high"],
        "low": item["low"],
        "close": item["close"],
        "pre_close": item["pre_close"],
        "vol": item["vol"],
        "amount": item["amount"],
    }


def load_one_name_pointer_bars(
    snapshot: Mapping[str, Any],
    conn: Any,
    ts_code: str,
    *,
    days: Sequence[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Pointer preflight, then one-name canonical bars. Not full-day hash bind."""

    if days is not None:
        want = tuple(compact_day(day) for day in days)
        if not want:
            raise OneNamePointerError("missing_snapshot_nominal")
        assert_replay_before_holdout(want)
        frozen = set(nominal_days_from_snapshot(snapshot))
        extra = [day for day in want if day not in frozen]
        if extra:
            raise OneNamePointerError("bars_not_in_snapshot")
    else:
        frozen = set(nominal_days_from_snapshot(snapshot))
        want = tuple(sorted(frozen))
        if not want:
            raise OneNamePointerError("missing_snapshot_nominal")
    try:
        assert_live_nominal_pointer_matches_snapshot(snapshot, conn, days=want)
    except SnapshotNominalBindError as exc:
        raise OneNamePointerError("live_pointer_mismatch") from exc
    placeholders = ", ".join(["?"] * len(want))
    sql = f"""
        SELECT {", ".join(PROVIDER_FIELDS)}
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '')
               IN ({placeholders})
           AND ts_code = ?
         ORDER BY trade_date
    """
    try:
        canonical_rows = conn.execute(sql, [*want, ts_code]).fetchall()
    except OneNamePointerError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed on any driver error
        raise OneNamePointerError("one_name_canonical_unreadable") from exc
    by_day: dict[str, list[dict[str, Any]]] = {day: [] for day in want}
    for row in canonical_rows:
        bar = _canonical_row_to_bar(row)
        if bar["ts_code"] != ts_code:
            raise OneNamePointerError("live_pointer_loaded_other_names")
        by_day.setdefault(bar["trade_date"], []).append(bar)
    if not any(by_day[day] for day in want):
        raise OneNamePointerError("named_bars_missing_code")
    return by_day


__all__ = [
    "OneNamePointerError",
    "assert_replay_before_holdout",
    "compact_day",
    "load_one_name_pointer_bars",
    "nominal_days_from_snapshot",
]
