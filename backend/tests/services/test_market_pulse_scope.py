"""B-ext adversarial tests: legacy pulse must not claim project-universe scope."""
from __future__ import annotations

import pytest

from services.market_pulse_scope import (
    attest_market_pulse_scope,
    refuse_project_universe_claim_for_legacy_pulse,
)


def test_legacy_pulse_fields_are_untrusted_external_or_raw() -> None:
    report = attest_market_pulse_scope(
        "20260709",
        margin_exchange_ids=("SSE", "SZSE", "BSE"),
        raw_daily_row_count=5000,
    )
    assert report.overall_status == "UNTRUSTED"
    by_field = {item.field: item for item in report.fields}
    assert by_field["adv_dec_ratio"].population_kind == "raw_evidence"
    assert by_field["rzrqye"].population_kind == "external_aggregate"
    assert by_field["rzrqye_chg"].status == "UNTRUSTED"
    assert "margin_sum_includes_BSE_external_venue" in report.notes
    assert "rzrqye_untrusted_until_promote_consumed" in report.notes


def test_missing_optional_evidence_still_untrusted_never_ready() -> None:
    report = attest_market_pulse_scope("20240102")
    assert report.overall_status == "UNTRUSTED"
    assert report.overall_status != "READY"
    assert all(item.status == "UNTRUSTED" for item in report.fields)


def test_promoted_rzrqye_ready_breadth_still_untrusted() -> None:
    report = attest_market_pulse_scope(
        "20260722",
        margin_exchange_ids=("SSE", "SZSE"),
        margin_source_accepted=True,
        margin_promoted=True,
    )
    by_field = {item.field: item for item in report.fields}
    assert by_field["rzrqye"].status == "READY"
    assert by_field["rzrqye"].population_kind == "external_aggregate"
    assert by_field["rzrqye"].source_surface == "tr.canonical_margin_exchange_daily"
    assert by_field["adv_dec_ratio"].status == "UNTRUSTED"
    # overall stays UNTRUSTED while breadth is raw
    assert report.overall_status == "UNTRUSTED"
    assert "rzrqye_promoted_external_aggregate_sse_szse" in report.notes


def test_serve_accepted_without_promote_stays_untrusted() -> None:
    report = attest_market_pulse_scope(
        "20260722",
        margin_source_accepted=True,
        margin_promoted=False,
    )
    by_field = {item.field: item for item in report.fields}
    assert by_field["rzrqye"].status == "UNTRUSTED"
    assert "promote_gate_not_consumed" in by_field["rzrqye"].reason
    assert report.overall_status == "UNTRUSTED"


def test_refuse_project_universe_claim_on_legacy_pulse() -> None:
    with pytest.raises(RuntimeError, match="cannot_satisfy_project_universe_pit"):
        refuse_project_universe_claim_for_legacy_pulse("project_universe_pit")
    with pytest.raises(RuntimeError, match="cannot_satisfy_project_universe_pit"):
        refuse_project_universe_claim_for_legacy_pulse(
            {"population_kind": "project_universe_pit"}
        )


def test_invalid_trade_date_is_blocked() -> None:
    report = attest_market_pulse_scope("not-a-date")
    assert report.overall_status == "BLOCKED"
    assert "invalid_trade_date" in report.notes
