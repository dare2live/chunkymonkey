"""E0 research read policy: prefer accepted canonical when shadow MATCH.

``/api/v3/inst`` and disclosure provider-field consumers default to canonical
tables for domains whose shadow status is MATCH on the partitions serving the
response.  Missing/diverging canonical coverage fails closed to legacy with an
explicit NONCONFORMING or PARTIAL label.

``cutover_allowed`` is true only when all three inventory domains MATCH.
Feature-store institution profiles use a canonical-spine + typed enrichment
projection (field-level PARTIAL where historical canary lacks enrichment).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from services.data_sources.disclosure_boundaries import disclosure_domains
from services.data_sources.disclosure_enrichment_projection import (
    feature_store_profiles_attestation,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE as HOLDERS_CANONICAL,
    COMPATIBILITY_TABLE as HOLDERS_LEGACY,
)
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE as ORG_CANONICAL,
    COMPATIBILITY_TABLE as ORG_LEGACY,
)
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE as STK_CANONICAL,
    COMPATIBILITY_TABLE as STK_LEGACY,
)

ReadSource = Literal["canonical", "legacy_fallback"]
DomainConformity = Literal["ACCEPTED", "NONCONFORMING", "PARTIAL"]
PolicyStatus = Literal["PARTIAL", "NONCONFORMING", "NOT_EVALUATED", "ACCEPTED"]

_TABLES: dict[str, tuple[str, str]] = {
    "holders_top10": (HOLDERS_CANONICAL, HOLDERS_LEGACY),
    "org_holding": (ORG_CANONICAL, ORG_LEGACY),
    "stk_holdertrade": (STK_CANONICAL, STK_LEGACY),
}


@dataclass(frozen=True)
class DomainReadDecision:
    domain: str
    partition: str | None
    shadow_status: str
    source: ReadSource
    conformity: DomainConformity
    table: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "partition": self.partition,
            "shadow_status": self.shadow_status,
            "source": self.source,
            "conformity": self.conformity,
            "table": self.table,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DisclosureReadPolicy:
    """Resolved research read policy for disclosure domains + profile residual."""

    overall_status: PolicyStatus
    cutover_allowed: bool
    domains: tuple[DomainReadDecision, ...]
    feature_store_profiles_status: PolicyStatus
    feature_store_profiles_reason: str
    feature_store_field_status: tuple[dict[str, str], ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "cutover_allowed": self.cutover_allowed,
            "domains": [item.as_dict() for item in self.domains],
            "feature_store_profiles_status": self.feature_store_profiles_status,
            "feature_store_profiles_reason": self.feature_store_profiles_reason,
            "feature_store_field_status": list(self.feature_store_field_status),
            "notes": list(self.notes),
        }


def _as_mapping(shadow: Any) -> Mapping[str, Any]:
    if hasattr(shadow, "as_dict"):
        return shadow.as_dict()
    if isinstance(shadow, Mapping):
        return shadow
    raise TypeError(f"unsupported shadow payload type: {type(shadow)!r}")


def cutover_allowed_from_shadow(shadow: Any) -> bool:
    """True only when every E0 inventory domain MATCH on serving partitions."""

    payload = _as_mapping(shadow)
    domains = {str(item.get("domain")): item for item in payload.get("domains") or ()}
    required = set(disclosure_domains())
    if set(domains) != required:
        return False
    if str(payload.get("overall_status") or "") != "MATCH":
        return False
    for name in required:
        item = domains[name]
        if str(item.get("status") or "") != "MATCH":
            return False
        if not item.get("rows_match", False):
            return False
        if not item.get("partition"):
            return False
    return True


def _decide_domain(item: Mapping[str, Any]) -> DomainReadDecision:
    domain = str(item.get("domain") or "")
    status = str(item.get("status") or "UNAVAILABLE")
    partition = item.get("partition")
    partition_s = str(partition) if partition else None
    canonical, legacy = _TABLES[domain] if domain in _TABLES else ("", "")

    if status == "MATCH" and item.get("rows_match", False) and partition_s:
        return DomainReadDecision(
            domain=domain,
            partition=partition_s,
            shadow_status=status,
            source="canonical",
            conformity="ACCEPTED",
            table=canonical,
            reason="shadow_match_prefer_accepted_canonical",
        )
    if status == "MISMATCH":
        return DomainReadDecision(
            domain=domain,
            partition=partition_s,
            shadow_status=status,
            source="legacy_fallback",
            conformity="PARTIAL",
            table=legacy,
            reason="canonical_legacy_diverge_fail_closed_legacy",
        )
    return DomainReadDecision(
        domain=domain,
        partition=partition_s,
        shadow_status=status,
        source="legacy_fallback",
        conformity="NONCONFORMING",
        table=legacy,
        reason="canonical_unavailable_or_skipped_fail_closed_legacy",
    )


def build_disclosure_read_policy(shadow: Any) -> DisclosureReadPolicy:
    """Build per-domain read decisions from a shadow compare report/dict."""

    payload = _as_mapping(shadow)
    raw_domains = list(payload.get("domains") or ())
    by_name = {str(item.get("domain")): item for item in raw_domains}
    decisions = tuple(
        _decide_domain(
            by_name.get(
                name,
                {
                    "domain": name,
                    "status": "UNAVAILABLE",
                    "partition": None,
                    "rows_match": False,
                },
            )
        )
        for name in disclosure_domains()
    )
    allowed = cutover_allowed_from_shadow(payload)
    profiles = feature_store_profiles_attestation()
    profile_status: PolicyStatus = (
        "PARTIAL" if profiles["status"] == "PARTIAL" else "ACCEPTED"
    )
    field_status = tuple(profiles["fields"])

    if allowed:
        overall: PolicyStatus = "PARTIAL" if profile_status == "PARTIAL" else "ACCEPTED"
        notes = (
            "disclosure_provider_fields_prefer_canonical_on_match",
            "cutover_allowed_three_domain_match",
            "formal_writes_formal_only_no_default_legacy_mirror",
            "feature_store_profiles_typed_enrichment_projection",
            "phase_e_smoke_eligible_ablation_still_blocked",
        )
    elif any(item.source == "canonical" for item in decisions):
        overall = "PARTIAL"
        notes = (
            "partial_canonical_read_with_legacy_fallback",
            "cutover_blocked_until_three_domain_match",
            "feature_store_profiles_typed_enrichment_projection",
        )
    else:
        overall = "NONCONFORMING"
        notes = (
            "all_disclosure_domains_legacy_fallback",
            "cutover_blocked",
            "feature_store_profiles_typed_enrichment_projection",
        )

    return DisclosureReadPolicy(
        overall_status=overall,
        cutover_allowed=allowed,
        domains=decisions,
        feature_store_profiles_status=profile_status,
        feature_store_profiles_reason=str(profiles["reason"]),
        feature_store_field_status=field_status,
        notes=notes,
    )


def preferred_provider_table(domain: str, policy: DisclosureReadPolicy) -> str:
    """Return the table name research provider-field reads should use."""

    for item in policy.domains:
        if item.domain == domain:
            return item.table
    if domain in _TABLES:
        return _TABLES[domain][1]
    raise KeyError(f"unknown disclosure domain: {domain}")


__all__ = [
    "DisclosureReadPolicy",
    "DomainReadDecision",
    "build_disclosure_read_policy",
    "cutover_allowed_from_shadow",
    "preferred_provider_table",
]
