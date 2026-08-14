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
    # 2026-08-14 b_pit 退役: 广度按真实来源(accepted canonical + 板块前缀)判 READY。
    # 此前它被判 raw_evidence/UNTRUSTED, 而那句「reads raw or unfiltered nominal」
    # 与 market_pulse._NOMINAL_DAILY_SQL 实际优先读 canonical 相矛盾 —— 是假证。
    assert by_field["adv_dec_ratio"].population_kind == "project_universe_pit"
    assert by_field["adv_dec_ratio"].status == "READY"
    assert by_field["rzrqye"].population_kind == "external_aggregate"
    assert by_field["rzrqye_chg"].status == "UNTRUSTED"
    assert "margin_sum_includes_BSE_external_venue" in report.notes
    assert "rzrqye_untrusted_until_promote_consumed" in report.notes
    assert "breadth_accepted_canonical_board_prefix" in report.notes


def test_missing_optional_evidence_still_untrusted_never_ready() -> None:
    report = attest_market_pulse_scope("20240102")
    assert report.overall_status == "UNTRUSTED"
    assert report.overall_status != "READY"
    # 广度已按真实来源 READY; 其余字段仍需各自的 promote 证据。
    assert all(
        item.status == "UNTRUSTED" for item in report.fields if item.field != "adv_dec_ratio"
    )

def test_promoted_rzrqye_ready_alongside_breadth() -> None:
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
    # 2026-08-14: 广度不再需要 b_pit promote 证据 —— 它按真实来源就是 READY。
    assert by_field["adv_dec_ratio"].status == "READY"
    assert report.overall_status == "READY"
    assert "rzrqye_promoted_external_aggregate_sse_szse" in report.notes


def test_promoted_breadth_and_rzrqye_overall_ready() -> None:
    report = attest_market_pulse_scope(
        "20260717",
        margin_exchange_ids=("SSE", "SZSE"),
        margin_source_accepted=True,
        margin_promoted=True,
    )
    by_field = {item.field: item for item in report.fields}
    assert by_field["adv_dec_ratio"].status == "READY"
    assert by_field["adv_dec_ratio"].population_kind == "project_universe_pit"
    assert by_field["rzrqye"].status == "READY"
    assert report.overall_status == "READY"
    assert "breadth_accepted_canonical_board_prefix" in report.notes
    assert "rzrqye_promoted_external_aggregate_sse_szse" in report.notes


def test_typed_empty_breadth_is_empty_not_untrusted() -> None:
    report = attest_market_pulse_scope(
        "20240108",
        breadth_empty=True,
        breadth_empty_reason="typed_empty_not_expected",
        margin_empty=True,
        margin_empty_reason="typed_empty_not_expected",
        margin_source_accepted=True,
    )
    by_field = {item.field: item for item in report.fields}
    assert by_field["adv_dec_ratio"].status == "EMPTY"
    assert by_field["rzrqye"].status == "EMPTY"
    assert "normal_absence_not_fail_closed" in by_field["adv_dec_ratio"].reason
    assert report.overall_status == "READY"  # EMPTY ranks with READY
    assert "breadth_typed_empty_normal_absence" in report.notes

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


def test_typed_empty_rzrqye_is_empty_not_untrusted() -> None:
    report = attest_market_pulse_scope(
        "20240108",
        margin_source_accepted=True,
        margin_empty=True,
        margin_empty_reason="typed_empty_not_expected",
    )
    by_field = {item.field: item for item in report.fields}
    assert by_field["rzrqye"].status == "EMPTY"
    assert "normal_absence_not_fail_closed" in by_field["rzrqye"].reason
    assert by_field["adv_dec_ratio"].status == "READY"
    assert report.overall_status == "READY"
    assert "rzrqye_typed_empty_normal_absence" in report.notes


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
