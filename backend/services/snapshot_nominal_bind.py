"""Bind DatasetSnapshot nominal accepted pointers to live accepted_partition.

Membership (date_set) alone is insufficient: bars must come from the same
accepted generation frozen in the snapshot (row_count + content_hash +
config_hash when present).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Mapping, Sequence

from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE,
    DATASET_ID as NOMINAL_DATASET,
    PROVIDER_FIELDS,
)
from services.data_sources.security_day_partition import canonical_content_hash


class SnapshotNominalBindError(RuntimeError):
    """Live accepted nominal pointer does not match frozen snapshot."""


class OfflineFixtureBars(dict):
    """Explicitly typed synthetic bars; never valid production evidence."""


def offline_fixture_bars(
    bars: Mapping[str, Any],
) -> OfflineFixtureBars:
    return OfflineFixtureBars({str(k): list(v) for k, v in bars.items()})


def require_offline_fixture_bars(
    bars: Mapping[str, Any],
) -> dict[str, list[Any]]:
    if not isinstance(bars, OfflineFixtureBars):
        raise SnapshotNominalBindError(
            "injected bars must use offline_fixture_bars(); "
            "plain mappings may not bypass snapshot-bound canonical loading"
        )
    return {str(k): list(v) for k, v in bars.items()}


def assert_b0_run_matches_snapshot(
    run: Any,
    snapshot: Mapping[str, Any],
) -> None:
    """Reject a caller-supplied B0 run from another frozen snapshot."""

    domains = snapshot.get("domains") or {}
    if not isinstance(domains, Mapping):
        raise SnapshotNominalBindError("snapshot domains must be a mapping")
    try:
        if "rally_gt" in domains or snapshot.get("phase_f_ablation") is not None:
            from services.main_rally_dataset_snapshot import (
                dataset_snapshot_from_main_rally,
            )

            expected_boundary = dataset_snapshot_from_main_rally(
                snapshot
            ).boundary_dict()
        else:
            from services.research_runtime import dataset_snapshot_from_disclosure

            expected_boundary = dataset_snapshot_from_disclosure(
                snapshot
            ).boundary_dict()
    except Exception as exc:
        raise SnapshotNominalBindError(
            f"cannot adapt frozen snapshot for B0 binding: {exc}"
        ) from exc

    artifact = getattr(run, "artifact_manifest", None)
    boundary = (
        artifact.get("research_runtime_snapshot")
        if isinstance(artifact, Mapping)
        else None
    )
    if not isinstance(boundary, Mapping):
        raise SnapshotNominalBindError(
            "B0 run missing research_runtime_snapshot evidence"
        )
    for field, expected in expected_boundary.items():
        if str(boundary.get(field) or "") != str(expected or ""):
            raise SnapshotNominalBindError(
                f"B0 {field} binding violated: "
                f"run={boundary.get(field)!r} expected={expected!r}"
            )


def _compact(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _accepted_rows_from_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    domains = snapshot.get("domains") or {}
    nominal = domains.get("nominal_ohlcv") or {}
    accepted = nominal.get("accepted") or []
    if not isinstance(accepted, list):
        raise SnapshotNominalBindError("snapshot nominal accepted must be a list")
    out: dict[str, dict[str, Any]] = {}
    for row in accepted:
        if not isinstance(row, Mapping):
            raise SnapshotNominalBindError("snapshot nominal accepted row must be a mapping")
        part = _compact(row.get("partition"))
        if len(part) != 8:
            raise SnapshotNominalBindError(
                f"snapshot nominal accepted has invalid partition={row.get('partition')!r}"
            )
        if part in out:
            raise SnapshotNominalBindError(
                f"snapshot nominal accepted duplicate partition={part}"
            )
        dataset_id = str(row.get("dataset_id") or NOMINAL_DATASET)
        if dataset_id != NOMINAL_DATASET:
            raise SnapshotNominalBindError(
                f"snapshot nominal dataset_id mismatch={dataset_id!r}"
            )
        for field in ("batch_id", "contract_hash", "config_hash", "content_hash"):
            if not str(row.get(field) or ""):
                raise SnapshotNominalBindError(
                    f"snapshot accepted row for {part} missing {field}"
                )
        try:
            if int(row.get("row_count")) <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise SnapshotNominalBindError(
                f"snapshot accepted row for {part} has invalid row_count"
            ) from exc
        out[part] = dict(row)
    return out


def _validated_live_nominal_pointers(
    snapshot: Mapping[str, Any],
    conn,
    *,
    days: Sequence[str] | None = None,
) -> tuple[
    dict[str, dict[str, Any]],
    list[str],
    dict[str, tuple[str, int, str, str, str]],
]:
    frozen = _accepted_rows_from_snapshot(snapshot)
    if not frozen:
        raise SnapshotNominalBindError(
            "snapshot domains.nominal_ohlcv.accepted missing; "
            "cannot bind live bars to a frozen generation"
        )
    want_days = sorted(
        {
            _compact(d)
            for d in (days if days is not None else frozen.keys())
            if len(_compact(d)) == 8
        }
    )
    if not want_days:
        raise SnapshotNominalBindError("no nominal days to bind")

    missing = [d for d in want_days if d not in frozen]
    if missing:
        raise SnapshotNominalBindError(
            f"snapshot nominal accepted missing days={missing[:5]}"
            + ("..." if len(missing) > 5 else "")
        )

    placeholders = ", ".join(["?"] * len(want_days))
    rows = conn.execute(
        f"""
        SELECT replace(CAST(partition_value AS VARCHAR), '-', '') AS partition,
               batch_id, row_count, content_hash, contract_hash, config_hash
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
           AND replace(CAST(partition_value AS VARCHAR), '-', '')
               IN ({placeholders})
        """,
        [NOMINAL_DATASET, *want_days],
    ).fetchall()
    live: dict[str, tuple[str, int, str, str, str]] = {}
    for row in rows:
        vals = tuple(row)
        part = _compact(vals[0])
        if part in live:
            raise SnapshotNominalBindError(
                f"live accepted_partition duplicate nominal day={part}"
            )
        live[part] = (
            str(vals[1] or ""),
            int(vals[2] or 0),
            str(vals[3] or ""),
            str(vals[4] or ""),
            str(vals[5] or ""),
        )

    for day in want_days:
        if day not in live:
            raise SnapshotNominalBindError(
                f"live accepted_partition missing for nominal day={day}"
            )
        live_batch, live_n, live_hash, live_contract, live_cfg = live[day]
        fr = frozen[day]
        snap_hash = str(fr["content_hash"])
        if live_hash != snap_hash:
            raise SnapshotNominalBindError(
                f"nominal content_hash drift day={day}: "
                f"live={live_hash!r} snapshot={snap_hash!r}"
            )
        snap_n = int(fr["row_count"])
        if snap_n != live_n:
            raise SnapshotNominalBindError(
                f"nominal row_count drift day={day}: "
                f"live={live_n} snapshot={snap_n}"
            )
        snap_cfg = str(fr["config_hash"])
        if live_cfg != snap_cfg:
            raise SnapshotNominalBindError(
                f"nominal config_hash drift day={day}: "
                f"live={live_cfg!r} snapshot={snap_cfg!r}"
            )
        for label, live_value, frozen_value in (
            ("batch_id", live_batch, str(fr["batch_id"])),
            ("contract_hash", live_contract, str(fr["contract_hash"])),
        ):
            if live_value != frozen_value:
                raise SnapshotNominalBindError(
                    f"nominal {label} drift day={day}: "
                    f"live={live_value!r} snapshot={frozen_value!r}"
                )
    return frozen, want_days, live


def assert_live_nominal_pointer_matches_snapshot(
    snapshot: Mapping[str, Any],
    conn,
    *,
    days: Sequence[str] | None = None,
) -> None:
    """Metadata-only preflight; safe before spending the one-touch budget."""

    _validated_live_nominal_pointers(snapshot, conn, days=days)


def load_snapshot_bound_nominal_bars_by_day(
    snapshot: Mapping[str, Any],
    conn,
    *,
    days: Sequence[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Load canonical outcomes once and validate them against the snapshot.

    This reads OHLCV outcomes and therefore belongs strictly after holdout
    consumption on formal research paths.
    """

    _, want_days, live = _validated_live_nominal_pointers(
        snapshot, conn, days=days
    )
    placeholders = ", ".join(["?"] * len(want_days))
    canonical_rows = conn.execute(
        f"""
        SELECT {", ".join(PROVIDER_FIELDS)}
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '')
               IN ({placeholders})
         ORDER BY trade_date, ts_code
        """,
        want_days,
    ).fetchall()
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in canonical_rows:
        vals = tuple(row)
        item = dict(zip(PROVIDER_FIELDS, vals, strict=True))
        raw_day = item["trade_date"]
        day = (
            raw_day.strftime("%Y%m%d")
            if isinstance(raw_day, date)
            else _compact(raw_day)
        )
        item["ts_code"] = str(item["ts_code"])
        by_day[day].append(item)
    for day in want_days:
        rebuilt = by_day.get(day, [])
        live_n = live[day][1]
        if len(rebuilt) != live_n:
            raise SnapshotNominalBindError(
                f"nominal canonical row_count drift day={day}: "
                f"canonical={len(rebuilt)} pointer={live_n}"
            )
        rebuilt_hash = canonical_content_hash(rebuilt, PROVIDER_FIELDS)
        if rebuilt_hash != live[day][2]:
            raise SnapshotNominalBindError(
                f"nominal canonical content_hash drift day={day}: "
                f"canonical={rebuilt_hash!r} pointer={live[day][2]!r}"
            )
    return {day: list(by_day.get(day, [])) for day in want_days}


def assert_live_nominal_matches_snapshot(
    snapshot: Mapping[str, Any],
    conn,
    *,
    days: Sequence[str] | None = None,
) -> None:
    """Compatibility validator for callers that do not need the loaded bars."""

    load_snapshot_bound_nominal_bars_by_day(snapshot, conn, days=days)


__all__ = [
    "SnapshotNominalBindError",
    "assert_live_nominal_pointer_matches_snapshot",
    "assert_live_nominal_matches_snapshot",
    "load_snapshot_bound_nominal_bars_by_day",
    "assert_b0_run_matches_snapshot",
    "OfflineFixtureBars",
    "offline_fixture_bars",
    "require_offline_fixture_bars",
]
