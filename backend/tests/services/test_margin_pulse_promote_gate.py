"""F4: margin pulse promote gate — evidence path toward rzrqye trust."""
from __future__ import annotations

import pytest

from services.margin_pulse_promote_gate import evaluate_margin_pulse_promote_gate
from services.market_pulse_shadow_reconcile import (
    PulseShadowVerdict,
    reconcile_market_pulse_shadow,
)


def test_promote_gate_pending_when_pulse_still_raw() -> None:
    shadow = reconcile_market_pulse_shadow(
        "20260722",
        margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 10.0},
            {"exchange_id": "SZSE", "rzrqye": 20.0},
        ),
    )
    assert shadow.verdict == PulseShadowVerdict.EXTERNAL_HONEST_SHADOW
    report = evaluate_margin_pulse_promote_gate(
        "20260722",
        shadow=shadow,
        accepted_margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 10.0},
            {"exchange_id": "SZSE", "rzrqye": 20.0},
        ),
        pulse_source_accepted=False,
        promote_allowed=False,
    )
    assert report.status == "PENDING_SERVE_CUTOVER"
    assert report.product_trust_would_be == "UNTRUSTED"
    assert "need_pulse_serve_accepted_margin_not_raw_bse" in report.remaining
    assert report.population_kind == "external_aggregate"


def test_promote_gate_accepted_ready_even_if_legacy_shadow_blocked() -> None:
    """Accepted v3 SSE+SZSE alone advances to PENDING_SERVE_CUTOVER."""
    shadow = reconcile_market_pulse_shadow("20260722", margin_rows=())
    assert shadow.verdict == PulseShadowVerdict.BLOCKED
    report = evaluate_margin_pulse_promote_gate(
        "20260722",
        shadow=shadow,
        accepted_margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 10.0},
            {"exchange_id": "SZSE", "rzrqye": 20.0},
        ),
        pulse_source_accepted=False,
        promote_allowed=False,
    )
    assert report.status == "PENDING_SERVE_CUTOVER"
    assert report.accepted_rzrqye == pytest.approx(30.0)
    assert "accepted_v3_ready_legacy_raw_shadow_not_honest" in report.notes
    assert report.product_trust_would_be == "UNTRUSTED"


def test_promote_gate_ready_still_untrusted_until_cutover() -> None:
    shadow = reconcile_market_pulse_shadow(
        "20260722",
        margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 1.0},
            {"exchange_id": "SZSE", "rzrqye": 2.0},
        ),
    )
    report = evaluate_margin_pulse_promote_gate(
        "20260722",
        shadow=shadow,
        accepted_margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 1.0},
            {"exchange_id": "SZSE", "rzrqye": 2.0},
        ),
        pulse_source_accepted=True,
        promote_allowed=True,
    )
    assert report.status == "READY_TO_PROMOTE"
    # Gate never invents product TRUSTED — separate cutover knife required.
    assert report.product_trust_would_be == "UNTRUSTED"
    assert report.remaining == ()


def test_promote_gate_blocked_on_bse_scope_mismatch() -> None:
    shadow = reconcile_market_pulse_shadow(
        "20260722",
        margin_rows=(
            {"exchange_id": "SSE", "rzrqye": 10.0},
            {"exchange_id": "SZSE", "rzrqye": 20.0},
            {"exchange_id": "BSE", "rzrqye": 5.0},
        ),
    )
    assert shadow.verdict == PulseShadowVerdict.SCOPE_MISMATCH
    # Without accepted rows, BSE mismatch keeps CRITERIA_PENDING.
    report = evaluate_margin_pulse_promote_gate(
        "20260722",
        shadow=shadow,
        accepted_margin_rows=(),
        pulse_source_accepted=False,
        promote_allowed=False,
    )
    assert report.status == "CRITERIA_PENDING"
    assert "need_accepted_sse_szse_for_day" in report.remaining
