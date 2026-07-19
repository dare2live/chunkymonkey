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
    evidence = SimpleNamespace(
        generation_id="cal-gen-1",
        content_hash="d" * 64,
        usable_at=DECISION.replace(hour=7),
    )

    class _Truth:
        def is_open(self, value):
            day = value if isinstance(value, date) else date.fromisoformat(str(value))
            return day.weekday() < 5

    truth = _Truth()
    truth.evidence = evidence
    return truth


def test_policy_truth_sources_align_with_accepted_dataset_ids() -> None:
    policy = _policy()
    assert policy.policy_version == 3
    assert policy.trading_calendar_source == CALENDAR_DATASET_ID
    assert policy.nominal_kline_source == NOMINAL_KLINE_DATASET_ID
    assert policy.st_membership_source == ST_MEMBERSHIP_DATASET_ID


def test_live_kline_and_st_loaders_fail_closed_without_live_partitions() -> None:
    """Writers exist; live DB without accepted partitions still fail closed."""

    policy = _policy()
    with pytest.raises(ObservationPopulationUnavailable) as kline:
        load_accepted_nominal_kline_membership(OPEN_DAY, DECISION, policy)
    assert kline.value.status in {"NOT_EVALUATED", "BLOCKED"}
    assert "nominal_ohlcv" in kline.value.reason or "no_accepted" in kline.value.reason

    with pytest.raises(ObservationPopulationUnavailable) as st:
        load_accepted_st_membership(OPEN_DAY, DECISION, policy)
    assert st.value.status in {"NOT_EVALUATED", "BLOCKED"}
    assert "stock_st" in st.value.reason or "no_accepted" in st.value.reason


def test_resolve_excludes_st_and_wrong_board_with_injected_accepted_sources() -> None:
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

    assert membership.ts_codes == ("000001.SZ", "600000.SH")
    assert membership.excluded_st_count == 1
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
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY, rows=0),
                frozenset(),
            ),
        )
    assert caught.value.status == "BLOCKED"
    assert "zero_rows" in caught.value.reason


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
                _partition(ST_MEMBERSHIP_DATASET_ID, OPEN_DAY),
                frozenset(),
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
    assert any("nominal_ohlcv" in reason for reason in readiness.reasons)
    assert any("stock_st" in reason for reason in readiness.reasons)
    assert any("calendar" in reason for reason in readiness.reasons)


def test_legacy_surfaces_are_hard_walled() -> None:
    with pytest.raises(ObservationPopulationUnavailable) as caught:
        refuse_legacy_population_surface("raw_tushare_daily")
    assert caught.value.status == "BLOCKED"
    assert "legacy_population_surface_forbidden" in caught.value.reason
