"""F4: margin pulse promote gate — evidence path toward rzrqye trust."""
from __future__ import annotations

import pytest

from services.margin_pulse_promote_gate import (
    evaluate_margin_pulse_promote_gate,
    load_margin_pulse_promote_config,
)
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


def test_promote_gate_promoted_ready_as_external_aggregate() -> None:
    """Serve cutover + promote_allowed + accepted → PROMOTED / READY (not TRUSTED)."""
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
    assert report.status == "PROMOTED"
    assert report.product_trust_would_be == "READY"
    assert report.population_kind == "external_aggregate"
    assert report.remaining == ()
    assert "promoted_external_aggregate_sse_szse_accepted" in report.notes


def test_promote_gate_serve_on_without_promote_stays_ready_to_promote() -> None:
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
        promote_allowed=False,
    )
    assert report.status == "READY_TO_PROMOTE"
    assert report.product_trust_would_be == "UNTRUSTED"
    assert "need_explicit_promote_allowed_config" in report.remaining


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


def test_load_promote_config_defaults_fail_closed(tmp_path) -> None:
    missing = tmp_path / "nope.yaml"
    cfg = load_margin_pulse_promote_config(missing)
    assert cfg.pulse_source_accepted is False
    assert cfg.promote_allowed is False


def test_production_promote_yaml_opts_into_serve_cutover() -> None:
    cfg = load_margin_pulse_promote_config()
    assert cfg.pulse_source_accepted is True
    assert cfg.promote_allowed is True
    assert cfg.contract_version == "3"


def test_promote_allowed_config_true_but_no_accepted_stays_untrusted() -> None:
    """Owner: fail-closed while gap open — config alone never invents READY."""
    shadow = reconcile_market_pulse_shadow("20260722", margin_rows=())
    report = evaluate_margin_pulse_promote_gate(
        "20260722",
        shadow=shadow,
        accepted_margin_rows=(),
        pulse_source_accepted=True,
        promote_allowed=True,  # would be ANDed false in router when rows empty
    )
    assert report.status != "PROMOTED"
    assert report.product_trust_would_be == "UNTRUSTED"
    assert "need_accepted_sse_szse_for_day" in report.remaining
