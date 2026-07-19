"""B-ext: shadow reconcile for legacy pulse wrong-scope margin/breadth."""
from __future__ import annotations

import pytest

from services.market_pulse_shadow_reconcile import (
    CORE_MARGIN_VENUES,
    PulseShadowVerdict,
    reconcile_market_pulse_shadow,
)


def test_bse_in_legacy_sum_is_scope_mismatch_not_parity() -> None:
    report = reconcile_market_pulse_shadow(
        "20260709",
        margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 100.0},
            {"exchange_id": "SZSE", "rzrqye": 200.0},
            {"exchange_id": "BSE", "rzrqye": 50.0},
        ),
        raw_daily_row_count=4000,
    )
    assert report.verdict == PulseShadowVerdict.SCOPE_MISMATCH
    assert report.legacy_rzrqye == pytest.approx(350.0)
    assert report.honest_external_rzrqye == pytest.approx(300.0)
    assert report.bse_rzrqye == pytest.approx(50.0)
    assert report.delta_legacy_minus_honest == pytest.approx(50.0)
    assert "BSE" in report.venues_present
    assert report.cutover_allowed is False
    assert report.scope.overall_status == "UNTRUSTED"
    assert "legacy_includes_BSE_in_market_sum" in report.issues


def test_sse_szse_only_still_blocks_project_universe_cutover() -> None:
    report = reconcile_market_pulse_shadow(
        "20240102",
        margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 10.0},
            {"exchange_id": "SZSE", "rzrqye": 20.0},
        ),
        raw_daily_row_count=100,
        project_universe_available=False,
    )
    assert report.verdict == PulseShadowVerdict.EXTERNAL_HONEST_SHADOW
    assert report.legacy_rzrqye == pytest.approx(30.0)
    assert report.honest_external_rzrqye == pytest.approx(30.0)
    assert report.bse_rzrqye is None
    assert report.cutover_allowed is False
    assert "project_universe_pit_unavailable" in report.issues
    assert "breadth_not_project_universe_pit" in report.issues


def test_project_universe_flag_alone_does_not_authorize_cutover() -> None:
    """Even with PIT available, legacy pulse numbers remain untrusted until cutover."""
    report = reconcile_market_pulse_shadow(
        "20240102",
        margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 1.0},
            {"exchange_id": "SZSE", "rzrqye": 2.0},
        ),
        raw_daily_row_count=10,
        project_universe_available=True,
    )
    assert report.cutover_allowed is False
    assert report.verdict != PulseShadowVerdict.PARITY
    assert "legacy_pulse_untrusted_pending_consumer_cutover" in report.issues


def test_insufficient_core_venues_blocks() -> None:
    report = reconcile_market_pulse_shadow(
        "20240102",
        margin_rows=({"exchange_id": "SSE", "rzrqye": 99.0},),
        raw_daily_row_count=10,
    )
    assert report.verdict == PulseShadowVerdict.BLOCKED
    assert report.legacy_rzrqye is None
    assert report.honest_external_rzrqye is None
    assert set(CORE_MARGIN_VENUES) - set(report.venues_present)
    assert "margin_core_venues_incomplete" in report.issues


def test_invalid_date_blocked() -> None:
    report = reconcile_market_pulse_shadow("bad", margin_rows=())
    assert report.verdict == PulseShadowVerdict.BLOCKED
    assert report.cutover_allowed is False
