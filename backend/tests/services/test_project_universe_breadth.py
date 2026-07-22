"""B-pit: project-universe breadth from observation membership (fail-closed)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from services.data_sources.observation_population import (
    NOMINAL_KLINE_DATASET_ID,
    ST_MEMBERSHIP_DATASET_ID,
    AcceptedPartitionRef,
    ObservationMembership,
)
from services.data_sources.project_universe_breadth import (
    ProjectUniverseBreadthUnavailable,
    aggregate_breadth_shadow_window,
    compare_legacy_vs_project_universe_breadth,
    compute_project_universe_breadth,
    measure_breadth_shadow_day,
    refuse_legacy_raw_daily_as_project_universe_breadth,
    unfiltered_breadth_from_rows,
)


def _ref(dataset_id: str, day: date, rows: int = 2) -> AcceptedPartitionRef:
    stamp = datetime(2024, 1, 2, 18, 0, tzinfo=timezone.utc)
    return AcceptedPartitionRef(
        dataset_id=dataset_id,
        partition_value=day.strftime("%Y%m%d"),
        batch_id="batch-1",
        contract_hash="c" * 64,
        config_hash="f" * 64,
        content_hash="a" * 64,
        row_count=rows,
        available_at=stamp,
        accepted_at=stamp,
    )


def _membership(codes: tuple[str, ...]) -> ObservationMembership:
    day = date(2024, 1, 2)
    return ObservationMembership(
        observation_date=day,
        decision_time=datetime(2024, 1, 2, 18, 0, tzinfo=timezone.utc),
        ts_codes=codes,
        calendar_generation_id="cal-1",
        calendar_content_hash="b" * 64,
        nominal_kline=_ref(NOMINAL_KLINE_DATASET_ID, day, rows=len(codes)),
        st_membership=_ref(ST_MEMBERSHIP_DATASET_ID, day, rows=1),
        universe_policy_id="traded_on_observation_date",
        universe_policy_version=1,
        universe_policy_hash="p" * 64,
        st_member_count=0,
        excluded_board_count=0,
    )


def test_breadth_uses_only_membership_codes() -> None:
    report = compute_project_universe_breadth(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 1.2},
            {"ts_code": "000001.SZ", "pct_chg": -0.5},
            {"ts_code": "830001.BJ", "pct_chg": 9.0},  # out of membership
            {"ts_code": "600001.SH", "pct_chg": 2.0},  # out of membership
        ),
    )
    assert report.adv_n == 1
    assert report.dec_n == 1
    assert report.flat_n == 0
    assert report.adv_dec_ratio == pytest.approx(1.0)
    assert report.population_kind == "project_universe_pit"
    assert report.row_count_used == 2
    assert report.ignored_outside_membership == 2


def test_missing_membership_rows_fail_closed() -> None:
    with pytest.raises(ProjectUniverseBreadthUnavailable, match="incomplete_membership_bars"):
        compute_project_universe_breadth(
            _membership(("600000.SH", "000001.SZ")),
            rows=({"ts_code": "600000.SH", "pct_chg": 1.0},),
        )


def test_empty_membership_blocked() -> None:
    with pytest.raises(ProjectUniverseBreadthUnavailable, match="empty_membership"):
        compute_project_universe_breadth(_membership(()), rows=())


def test_refuse_legacy_raw_daily_claim() -> None:
    with pytest.raises(RuntimeError, match="cannot_satisfy_project_universe"):
        refuse_legacy_raw_daily_as_project_universe_breadth("project_universe_pit")


def test_shadow_compare_never_allows_cutover_even_on_match() -> None:
    project = compute_project_universe_breadth(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "pct_chg": -1.0},
        ),
    )
    report = compare_legacy_vs_project_universe_breadth(
        trade_date="20240102",
        legacy_adv_dec_ratio=1.0,
        project=project,
    )
    assert report.ratios_match is True
    assert report.cutover_allowed is False
    assert "cutover_requires_accepted_live_partitions_and_gate" in report.issues


def test_shadow_compare_flags_divergence() -> None:
    project = compute_project_universe_breadth(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "pct_chg": -1.0},
        ),
    )
    report = compare_legacy_vs_project_universe_breadth(
        trade_date="20240102",
        legacy_adv_dec_ratio=2.5,
        project=project,
    )
    assert report.ratios_match is False
    assert report.cutover_allowed is False
    assert "baseline_ratio_diverges_from_project_universe" in report.issues
    assert "legacy_raw_ratio_diverges_from_project_universe" in report.issues


def test_unfiltered_counts_ignore_missing_pct() -> None:
    counts = unfiltered_breadth_from_rows(
        (
            {"ts_code": "600000.SH", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "pct_chg": -1.0},
            {"ts_code": "300001.SZ", "pct_chg": None},
            {"ts_code": "830001.BJ", "pct_chg": 2.0},
        )
    )
    assert counts.adv_n == 2
    assert counts.dec_n == 1
    assert counts.row_count_used == 3
    assert counts.adv_dec_ratio == pytest.approx(2.0)


def test_measure_day_match_ignores_off_universe_semantic_delta() -> None:
    """Off-universe rows move unfiltered ratio but must not break MATCH baseline."""

    measure = measure_breadth_shadow_day(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "pct_chg": -1.0},
            {"ts_code": "830001.BJ", "pct_chg": 5.0},
            {"ts_code": "830002.BJ", "pct_chg": 5.0},
        ),
    )
    assert measure.project.adv_dec_ratio == pytest.approx(1.0)
    assert measure.membership_proxy.adv_dec_ratio == pytest.approx(1.0)
    assert measure.unfiltered.adv_dec_ratio == pytest.approx(3.0)
    assert measure.semantic_delta_vs_unfiltered == pytest.approx(2.0)
    assert measure.compare.ratios_match is True
    assert measure.compare.baseline_kind == "membership_restricted_proxy"
    assert measure.compare.cutover_allowed is False


def test_window_aggregate_never_allows_cutover_even_when_all_match() -> None:
    m1 = measure_breadth_shadow_day(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "pct_chg": -1.0},
            {"ts_code": "830001.BJ", "pct_chg": 9.0},
        ),
    )
    m2 = measure_breadth_shadow_day(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 2.0},
            {"ts_code": "000001.SZ", "pct_chg": -0.5},
        ),
    )
    window = aggregate_breadth_shadow_window((m1, m2))
    assert window.match_day_count == 2
    assert window.diverge_day_count == 0
    assert window.ratios_match_all is True
    assert window.cutover_allowed is False
    assert "match_alone_insufficient_for_cutover" in window.issues


def test_window_aggregate_flags_errors_and_keeps_cutover_false() -> None:
    match = measure_breadth_shadow_day(
        _membership(("600000.SH", "000001.SZ")),
        rows=(
            {"ts_code": "600000.SH", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "pct_chg": -1.0},
            {"ts_code": "830001.BJ", "pct_chg": 9.0},
        ),
    )
    window = aggregate_breadth_shadow_window(
        (match,),
        errors=({"trade_date": "20240103", "error": "incomplete_membership_bars"},),
    )
    assert window.match_day_count == 1
    assert window.diverge_day_count == 0
    assert window.error_day_count == 1
    assert window.ratios_match_all is False
    assert window.cutover_allowed is False
    assert "window_ratios_diverge_or_errors" in window.issues
