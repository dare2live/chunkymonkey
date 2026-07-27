"""Snapshot-bound nominal measurement runtime for institution-follow B0."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from services.holdout_guard import consume_holdout_single_touch
from services.institution_follow_b0_measure import MeasuredB0Result, measure_b0_paper


class MeasuredCoverage(Protocol):
    sufficient_for_measured_b0: bool
    accepted_nominal_partitions: Sequence[str]


def run_measured_paper(
    coverage: MeasuredCoverage,
    *,
    snapshot: Mapping[str, Any],
    single_touch_token: str,
    walk_forward,
    prereg_store_dir: Path | str | None = None,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    drift_error: type[Exception] = RuntimeError,
    consume_holdout=consume_holdout_single_touch,
    measure=measure_b0_paper,
) -> MeasuredB0Result | None:
    """Measure one frozen plan; live outcomes are read only after consumption."""

    if not coverage.sufficient_for_measured_b0:
        return None
    days = list(coverage.accepted_nominal_partitions)
    if not days:
        return None

    owned_conn = False
    conn = nominal_conn
    try:
        if bars_by_day is None:
            if conn is None:
                from services.data_access.resolver import connect_ro

                conn = connect_ro("tushare_raw")
                owned_conn = True
            from services.snapshot_nominal_bind import (
                assert_live_nominal_pointer_matches_snapshot,
                load_snapshot_bound_nominal_bars_by_day,
            )

            assert_live_nominal_pointer_matches_snapshot(snapshot, conn, days=days)
            if walk_forward.holdout_dates:
                consume_holdout(
                    single_touch_token, store_dir=prereg_store_dir
                )
            bars = load_snapshot_bound_nominal_bars_by_day(snapshot, conn, days=days)
        else:
            from services.snapshot_nominal_bind import require_offline_fixture_bars

            bars = require_offline_fixture_bars(bars_by_day)

        measured = measure(bars, days, walk_forward=walk_forward)
        if measured.walk_forward.as_dict() != walk_forward.as_dict():
            raise drift_error("measured walk-forward plan drifted from prereg")
        return measured
    finally:
        if owned_conn and conn is not None:
            conn.close()


__all__ = ["MeasuredCoverage", "run_measured_paper"]
