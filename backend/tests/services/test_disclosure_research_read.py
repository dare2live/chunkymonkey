"""E0 research read policy: canonical default on MATCH; fail-closed legacy fallback."""
from __future__ import annotations

from services.data_sources.disclosure_research_read import (
    build_disclosure_read_policy,
    cutover_allowed_from_shadow,
    preferred_provider_table,
)
from services.data_sources.disclosure_shadow_compare import (
    DisclosureDomainShadowReport,
    DisclosureShadowCompareReport,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE as HOLDERS_CANONICAL,
    COMPATIBILITY_TABLE as HOLDERS_LEGACY,
)


def _domain(
    name: str,
    *,
    status: str = "MATCH",
    partition: str | None = "20260429",
) -> DisclosureDomainShadowReport:
    return DisclosureDomainShadowReport(
        domain=name,
        partition=partition,
        status=status,  # type: ignore[arg-type]
        legacy_row_count=2 if status == "MATCH" else 0,
        canonical_row_count=2 if status == "MATCH" else 0,
        compared_fields=("stock_code",),
        rows_match=status == "MATCH",
        mismatch_count=0 if status == "MATCH" else 1,
        sample_mismatches=(),
        issues=("disclosure_shadow_compare_only",),
    )


def test_three_domain_match_allows_cutover() -> None:
    shadow = DisclosureShadowCompareReport(
        overall_status="MATCH",
        cutover_allowed=True,
        domains=(
            _domain("holders_top10", partition="20260717"),
            _domain("org_holding", partition="20190430"),
            _domain("stk_holdertrade", partition="20260706"),
        ),
        notes=("fixture",),
    )
    assert cutover_allowed_from_shadow(shadow) is True
    policy = build_disclosure_read_policy(shadow)
    assert policy.cutover_allowed is True
    assert policy.overall_status == "PARTIAL"
    assert policy.feature_store_profiles_status == "PARTIAL"
    by_domain = {item.domain: item for item in policy.domains}
    assert by_domain["holders_top10"].source == "canonical"
    assert by_domain["holders_top10"].conformity == "ACCEPTED"
    assert by_domain["org_holding"].source == "canonical"
    assert by_domain["stk_holdertrade"].source == "canonical"
    assert preferred_provider_table("holders_top10", policy) == HOLDERS_CANONICAL


def test_partial_match_falls_back_legacy_with_label() -> None:
    shadow = DisclosureShadowCompareReport(
        overall_status="PARTIAL",
        cutover_allowed=False,
        domains=(
            _domain("holders_top10"),
            _domain("org_holding", status="UNAVAILABLE", partition=None),
            _domain("stk_holdertrade", status="MISMATCH"),
        ),
        notes=("fixture",),
    )
    assert cutover_allowed_from_shadow(shadow) is False
    policy = build_disclosure_read_policy(shadow)
    assert policy.cutover_allowed is False
    by_domain = {item.domain: item for item in policy.domains}
    assert by_domain["holders_top10"].source == "canonical"
    assert by_domain["org_holding"].source == "legacy_fallback"
    assert by_domain["org_holding"].conformity == "NONCONFORMING"
    assert by_domain["stk_holdertrade"].source == "legacy_fallback"
    assert by_domain["stk_holdertrade"].conformity == "PARTIAL"
    assert preferred_provider_table("holders_top10", policy) == HOLDERS_CANONICAL
    assert preferred_provider_table("org_holding", policy) == (
        __import__(
            "services.data_sources.org_holding_schema", fromlist=["COMPATIBILITY_TABLE"]
        ).COMPATIBILITY_TABLE
    )


def test_dict_shadow_payload_resolves_same_policy() -> None:
    shadow = DisclosureShadowCompareReport(
        overall_status="MATCH",
        cutover_allowed=True,
        domains=(
            _domain("holders_top10"),
            _domain("org_holding"),
            _domain("stk_holdertrade"),
        ),
        notes=(),
    )
    policy = build_disclosure_read_policy(shadow.as_dict())
    assert policy.cutover_allowed is True
    assert preferred_provider_table("holders_top10", policy) != HOLDERS_LEGACY
