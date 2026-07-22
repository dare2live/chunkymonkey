"""A3 adversarial tests for traded_on_observation_date + trusted loaders."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from services.data_sources.calendar_schema import DATASET_ID as CALENDAR_DATASET_ID
from services.data_sources.observation_population import (
    NOMINAL_KLINE_DATASET_ID,
    AcceptedPartitionRef,
    ObservationPopulationUnavailable,
    ST_MEMBERSHIP_DATASET_ID,
    evaluate_observation_population_readiness,
    load_accepted_nominal_kline_membership,
    load_accepted_st_membership,
    refuse_legacy_population_surface,
    resolve_traded_on_observation_date,
)
from services.universe import load_universe_policy


DECISION = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
OPEN_DAY = date(2026, 7, 17)
CLOSED_DAY = date(2026, 7, 18)  # Saturday


def _policy():
    return load_universe_policy()


def _partition(dataset_id: str, day: date, *, rows: int = 2) -> AcceptedPartitionRef:
    return AcceptedPartitionRef(
        dataset_id=dataset_id,
        partition_value=day.strftime("%Y%m%d"),
        batch_id=f"batch-{dataset_id}-{day.isoformat()}",
        contract_hash="a" * 64,
        config_hash="b" * 64,
        content_hash="c" * 64,
        row_count=rows,
        available_at=DECISION.replace(hour=8),
        accepted_at=DECISION.replace(hour=9),
    )


def _open_calendar():
    from datetime import timedelta

    evidence = SimpleNamespace(
        generation_id="cal-gen-1",
        content_hash="d" * 64,
        usable_at=DECISION.replace(hour=7),
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 12, 31),
    )

    class _Truth:
        def is_open(self, value):
            day = value if isinstance(value, date) else date.fromisoformat(str(value))
            return day.weekday() < 5

        def open_dates(self, start, end):
            first = start if isinstance(start, date) else date.fromisoformat(str(start))
            last = end if isinstance(end, date) else date.fromisoformat(str(end))
            days = []
            cursor = first
            while cursor <= last:
                if cursor.weekday() < 5:
                    days.append(cursor)
                cursor += timedelta(days=1)
            return tuple(days)

    truth = _Truth()
    truth.evidence = evidence
    return truth


def test_policy_truth_sources_align_with_accepted_dataset_ids() -> None:
    policy = _policy()
    assert policy.policy_version == 4
    assert policy.trading_calendar_source == CALENDAR_DATASET_ID
    assert policy.nominal_kline_source == NOMINAL_KLINE_DATASET_ID
    assert policy.st_membership_source == ST_MEMBERSHIP_DATASET_ID


def test_live_kline_and_st_loaders_fail_closed_before_partition_visibility() -> None:
    """Live loaders stay fail-closed when decision_time precedes accepted visibility.

    After the authorized 20260717 canary, partitions may exist but must remain
    invisible to an earlier decision_time (PIT). Offline/CI without the canary
    still fail closed on missing partitions.
    """

    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as kline:
        load_accepted_nominal_kline_membership(OPEN_DAY, DECISION, policy)
    assert kline.value.status in {"NOT_EVALUATED", "BLOCKED"}
    assert (
        "nominal_ohlcv" in kline.value.reason
        or "no_accepted" in kline.value.reason
        or "not_visible_at_decision_time" in kline.value.reason
    )

    with pytest.raises(ObservationPopulationUnavailable) as st:
        load_accepted_st_membership(OPEN_DAY, DECISION, policy)
    assert st.value.status in {"NOT_EVALUATED", "BLOCKED"}
    assert (
        "stock_st" in st.value.reason
        or "no_accepted" in st.value.reason
        or "not_visible_at_decision_time" in st.value.reason
    )


def test_missing_tushare_raw_db_is_not_evaluated(monkeypatch) -> None:
    """CI/offline: missing DuckDB must not escape as raw IOException."""

    import duckdb

    from services.data_access import resolver as data_resolver
    from services.data_sources.observation_population import (
        evaluate_observation_population_readiness,
    )

    def _missing(_alias: str):
        raise duckdb.IOException(
            'Cannot open database "data/tushare_raw.duckdb" in read-only mode: '
            "database does not exist"
        )

    monkeypatch.setattr(data_resolver, "connect_ro", _missing)
    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as kline:
        load_accepted_nominal_kline_membership(OPEN_DAY, DECISION, policy)
    assert kline.value.status == "NOT_EVALUATED"
    assert "read_failed" in kline.value.reason

    readiness = evaluate_observation_population_readiness(
        policy,
        decision_time=DECISION,
        observation_date=OPEN_DAY,
        calendar_loader=lambda *_: (_ for _ in ()).throw(
            ObservationPopulationUnavailable(
                "NOT_EVALUATED", "no_accepted_calendar_generation"
            )
        ),
    )
    assert readiness.status == "NOT_EVALUATED"
    assert any("read_failed" in reason or "nominal_ohlcv" in reason for reason in readiness.reasons)


def test_live_loaders_fail_closed_when_raw_db_missing(monkeypatch) -> None:
    """CI/offline hosts without tushare_raw.duckdb must not raise IOException."""

    def _boom(*_a, **_k):
        raise OSError("Cannot open database in read-only mode: database does not exist")

    monkeypatch.setattr("services.data_access.resolver.connect_ro", _boom)
    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as kline:
        load_accepted_nominal_kline_membership(OPEN_DAY, DECISION, policy)
    assert kline.value.status == "NOT_EVALUATED"
    assert "read_failed" in kline.value.reason or "no_accepted" in kline.value.reason

    with pytest.raises(ObservationPopulationUnavailable) as st:
        load_accepted_st_membership(OPEN_DAY, DECISION, policy)
    assert st.value.status == "NOT_EVALUATED"


def test_resolve_keeps_st_and_excludes_wrong_board_with_injected_accepted_sources() -> None:
    """沪深A whitelist includes ST; only non-whitelist boards are dropped."""
    policy = _policy()
    kline_ref = _partition(NOMINAL_KLINE_DATASET_ID, OPEN_DAY, rows=4)
    st_ref = _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=1)
    traded = frozenset(
        {"600000.SH", "000001.SZ", "830001.BJ", "600001.SH"}
    )
    st_members = frozenset({"600001.SH"})

    membership = resolve_traded_on_observation_date(
        OPEN_DAY,
        DECISION,
        policy,
        calendar_loader=lambda *_: _open_calendar(),
        nominal_kline_loader=lambda *_: (kline_ref, traded),
        st_membership_loader=lambda *_: (st_ref, st_members),
    )

    assert membership.ts_codes == ("000001.SZ", "600000.SH", "600001.SH")
    assert membership.st_member_count == 1
    assert membership.excluded_board_count == 1
    assert membership.calendar_generation_id == "cal-gen-1"
    assert membership.universe_policy_hash == policy.config_hash


def test_closed_observation_day_is_blocked() -> None:
    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        resolve_traded_on_observation_date(
            CLOSED_DAY,
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
            nominal_kline_loader=lambda *_: (
                _partition(NOMINAL_KLINE_DATASET_ID, CLOSED_DAY),
                frozenset({"600000.SH"}),
            ),
            st_membership_loader=lambda *_: (
                _partition(ST_MEMBERSHIP_DATASET_ID, CLOSED_DAY),
                frozenset(),
            ),
        )
    assert caught.value.status == "BLOCKED"
    assert "not_an_open_trading_day" in caught.value.reason


def test_future_observation_is_blocked() -> None:
    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        resolve_traded_on_observation_date(
            date(2026, 7, 20),
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
        )
    assert caught.value.status == "BLOCKED"
    assert "future_observation" in caught.value.reason


def test_zero_row_kline_partition_is_blocked() -> None:
    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        resolve_traded_on_observation_date(
            OPEN_DAY,
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
            nominal_kline_loader=lambda *_: (
                _partition(NOMINAL_KLINE_DATASET_ID, OPEN_DAY, rows=0),
                frozenset(),
            ),
            st_membership_loader=lambda *_: (
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=1),
                frozenset({"600001.SH"}),
            ),
        )
    assert caught.value.status == "BLOCKED"
    assert "accepted_nominal_kline_partition_has_zero_rows" in caught.value.reason


def test_zero_row_st_partition_is_blocked() -> None:
    """Accepted ST with zero rows is not silent 'no exclusions' — fail closed.

    Empty ST membership requires a future explicit empty-partition attestation;
    until then a zero-row accepted ST partition is BLOCKED.
    """

    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        resolve_traded_on_observation_date(
            OPEN_DAY,
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
            nominal_kline_loader=lambda *_: (
                _partition(NOMINAL_KLINE_DATASET_ID, OPEN_DAY, rows=2),
                frozenset({"600000.SH", "000001.SZ"}),
            ),
            st_membership_loader=lambda *_: (
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=0),
                frozenset(),
            ),
        )
    assert caught.value.status == "BLOCKED"
    assert "accepted_stock_st_partition_has_zero_rows" in caught.value.reason


def test_row_count_membership_parity_is_required() -> None:
    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as kline:
        resolve_traded_on_observation_date(
            OPEN_DAY,
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
            nominal_kline_loader=lambda *_: (
                _partition(NOMINAL_KLINE_DATASET_ID, OPEN_DAY, rows=100),
                frozenset({"600000.SH"}),
            ),
            st_membership_loader=lambda *_: (
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=1),
                frozenset({"600001.SH"}),
            ),
        )
    assert kline.value.status == "BLOCKED"
    assert "row_count_membership_parity_failed" in kline.value.reason
    assert "nominal_kline" in kline.value.reason

    with pytest.raises(ObservationPopulationUnavailable) as st:
        resolve_traded_on_observation_date(
            OPEN_DAY,
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
            nominal_kline_loader=lambda *_: (
                _partition(NOMINAL_KLINE_DATASET_ID, OPEN_DAY, rows=1),
                frozenset({"600000.SH"}),
            ),
            st_membership_loader=lambda *_: (
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=5),
                frozenset(),
            ),
        )
    assert st.value.status == "BLOCKED"
    assert "row_count_membership_parity_failed" in st.value.reason
    assert "stock_st" in st.value.reason


def test_partition_not_visible_at_decision_time_is_not_evaluated() -> None:
    policy = _policy()
    future_ref = AcceptedPartitionRef(
        dataset_id=NOMINAL_KLINE_DATASET_ID,
        partition_value=OPEN_DAY.strftime("%Y%m%d"),
        batch_id="future-batch",
        contract_hash="a" * 64,
        config_hash="b" * 64,
        content_hash="c" * 64,
        row_count=1,
        available_at=DECISION.replace(hour=18),
        accepted_at=DECISION.replace(hour=19),
    )
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        resolve_traded_on_observation_date(
            OPEN_DAY,
            DECISION,
            policy,
            calendar_loader=lambda *_: _open_calendar(),
            nominal_kline_loader=lambda *_: (future_ref, frozenset({"600000.SH"})),
            st_membership_loader=lambda *_: (
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=1),
                frozenset({"600001.SH"}),
            ),
        )
    assert caught.value.status == "NOT_EVALUATED"
    assert "not_visible_at_decision_time" in caught.value.reason


def test_evaluate_readiness_runs_loaders_and_stays_not_evaluated_live() -> None:
    policy = _policy()
    readiness = evaluate_observation_population_readiness(
        policy,
        decision_time=DECISION,
        observation_date=OPEN_DAY,
        calendar_loader=lambda *_: (_ for _ in ()).throw(
            ObservationPopulationUnavailable(
                "NOT_EVALUATED", "no_accepted_calendar_generation"
            )
        ),
    )
    assert readiness.status == "NOT_EVALUATED"
    assert any(
        "nominal_ohlcv" in reason
        or "no_accepted_partition" in reason
        or "not_visible_at_decision_time" in reason
        for reason in readiness.reasons
    )
    assert any(
        "stock_st" in reason
        or "no_accepted_partition" in reason
        or "not_visible_at_decision_time" in reason
        for reason in readiness.reasons
    )
    assert any("calendar" in reason for reason in readiness.reasons)


def test_default_readiness_uses_eligible_frontier_not_calendar_today() -> None:
    """Weekend/holiday defaults must not demand non-existent calendar-today partitions."""

    from zoneinfo import ZoneInfo

    policy = _policy()
    # Saturday evening Shanghai — eligible K+ST frontier is Friday 20260717.
    saturday_sh = datetime(2026, 7, 18, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    seen: list[date] = []

    def _kline(day, _cutoff, _policy):
        seen.append(day)
        return (
            _partition(NOMINAL_KLINE_DATASET_ID, day, rows=1),
            frozenset({"600000.SH"}),
        )

    def _st(day, _cutoff, _policy):
        seen.append(day)
        return (
            _partition(ST_MEMBERSHIP_DATASET_ID, day, rows=1),
            frozenset({"600001.SH"}),
        )

    readiness = evaluate_observation_population_readiness(
        policy,
        decision_time=saturday_sh,
        calendar_loader=lambda *_: _open_calendar(),
        nominal_kline_loader=_kline,
        st_membership_loader=_st,
    )
    assert readiness.status == "READY"
    assert readiness.observation_date == OPEN_DAY
    assert readiness.as_dict()["observation_date"] == "20260717"
    assert seen == [OPEN_DAY, OPEN_DAY]
    assert CLOSED_DAY not in seen


def test_legacy_surfaces_are_hard_walled() -> None:
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        refuse_legacy_population_surface("raw_tushare_daily")
    assert caught.value.status == "BLOCKED"
    assert "legacy_population_surface_forbidden" in caught.value.reason
