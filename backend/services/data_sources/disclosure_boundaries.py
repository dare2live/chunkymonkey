"""E0 disclosure-domain strangler: typed contracts + fail-closed formal claims.

Transport/research boundary.  All three disclosure domains declare formal
landing→validate→accept writers.  Production new writes are ``formal_only``
(no default legacy mirror).  Explicit test/emergency mirror remains via
``authorize_legacy_mirror_write(..., allow_test_escape=True)``.

Research provider-field reads prefer accepted canonical when shadow MATCH.
Feature-store profiles use a canonical-spine + typed enrichment projection
(field-level PARTIAL where historical canary lacks enrichment columns).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from services.data_sources.disclosure_research_read import DisclosureReadPolicy

Conformity = Literal["NONCONFORMING", "ACCEPTED", "PARTIAL"]
RuntimeState = Literal[
    "direct_write_strangler",
    "formal_path_ready_legacy_direct_write",
    "formal_default_legacy_mirror",
    "formal_only",
]
TrustStatus = Literal["NONCONFORMING", "BLOCKED", "NOT_EVALUATED", "READY", "PARTIAL"]

_ACCEPTED_CLAIMS = frozenset(
    {
        "accepted",
        "canonical",
        "landing",
        "DatasetSnapshot",
        "accepted_partition",
        "formal",
        "ACCEPTED",
    }
)
_FORMAL_CONFORMITY = frozenset(
    {
        "ACCEPTED",
        "accepted",
        "formal",
        "landing",
        "canonical",
        "DatasetSnapshot",
    }
)
_PATH_CLAIMS = frozenset({"accepted", "canonical", "landing", "accepted_partition", "formal", "ACCEPTED"})
_SNAPSHOT_CLAIMS = frozenset({"DatasetSnapshot"})
_STRANGLER_STATES = frozenset(
    {
        "direct_write_strangler",
        "formal_path_ready_legacy_direct_write",
        "formal_default_legacy_mirror",
        "formal_only",
    }
)
_FORMAL_WRITE_STATES = frozenset({"formal_only", "formal_default_legacy_mirror"})
_TEST_ESCAPE_ENV = "DISCLOSURE_ALLOW_NONCONFORMING_ESCAPE"
_MIRROR_ESCAPE_ENV = "DISCLOSURE_ALLOW_LEGACY_MIRROR"


@dataclass(frozen=True)
class DisclosureDomainBoundary:
    domain: str
    dataset_id: str
    adapter: str
    target_table: str
    availability_axis: str
    availability_rule: str
    population_kind: Literal["raw_evidence"] = "raw_evidence"
    conformity: Conformity = "NONCONFORMING"
    runtime_state: RuntimeState = "direct_write_strangler"
    landing_writer: str | None = None
    canonical_writer: str | None = None
    formal_write: Literal["forbidden"] = "forbidden"
    legacy_mirror_deprecated: bool = True
    legacy_mirror_default: bool = False


@dataclass(frozen=True)
class DisclosureWritePermit:
    """Token proving a legacy write was explicitly authorized."""

    domain: str
    conformity: Conformity
    target_table: str
    publication: Literal[
        "nonconforming_direct_write",
        "legacy_mirror_of_formal_accept",
    ] = "nonconforming_direct_write"


@dataclass(frozen=True)
class DisclosureDomainAttestation:
    domain: str
    status: TrustStatus
    population_kind: str
    adapter: str
    target_table: str
    availability_axis: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "status": self.status,
            "population_kind": self.population_kind,
            "adapter": self.adapter,
            "target_table": self.target_table,
            "availability_axis": self.availability_axis,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DisclosureResearchSurfaceReport:
    overall_status: TrustStatus
    cutover_allowed: bool
    e0_phase: Literal["in_progress", "gate_closed_canary"]
    domains: tuple[DisclosureDomainAttestation, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "cutover_allowed": self.cutover_allowed,
            "e0_phase": self.e0_phase,
            "domains": [item.as_dict() for item in self.domains],
            "notes": list(self.notes),
        }


_DISCLOSURE_BOUNDARIES: dict[str, DisclosureDomainBoundary] = {
    "holders_top10": DisclosureDomainBoundary(
        domain="holders_top10",
        dataset_id="tier0.disclosure.top10_float_holders_period",
        adapter="miaoxiang",
        target_table="fact_top10_holder_period",
        availability_axis="notice_date",
        availability_rule="event_time_notice_or_page_update",
        runtime_state="formal_only",
        landing_writer=(
            "services.data_sources.holders_top10_acceptance.land_holders_top10_batch"
        ),
        canonical_writer=(
            "services.data_sources.holders_top10_acceptance.accept_holders_top10_batch"
        ),
        legacy_mirror_deprecated=True,
        legacy_mirror_default=False,
    ),
    "org_holding": DisclosureDomainBoundary(
        domain="org_holding",
        dataset_id="tier0.disclosure.org_holding_detail_period",
        adapter="miaoxiang",
        target_table="raw_org_holding_aif10",
        availability_axis="available_date",
        availability_rule="disclosure_deadline_upper_bound",
        runtime_state="formal_only",
        landing_writer=(
            "services.data_sources.org_holding_acceptance.land_org_holding_batch"
        ),
        canonical_writer=(
            "services.data_sources.org_holding_acceptance.accept_org_holding_batch"
        ),
        legacy_mirror_deprecated=True,
        legacy_mirror_default=False,
    ),
    "stk_holdertrade": DisclosureDomainBoundary(
        domain="stk_holdertrade",
        dataset_id="tier0.disclosure.stock_holder_trade_announcement",
        adapter="tushare",
        target_table="raw_tushare_stk_holdertrade",
        availability_axis="ann_date",
        availability_rule="announcement_date_event_time",
        runtime_state="formal_only",
        landing_writer=(
            "services.data_sources.stk_holdertrade_acceptance.land_stk_holdertrade_batch"
        ),
        canonical_writer=(
            "services.data_sources.stk_holdertrade_acceptance.accept_stk_holdertrade_batch"
        ),
        legacy_mirror_deprecated=True,
        legacy_mirror_default=False,
    ),
}


class DisclosureBoundaryError(RuntimeError):
    """A disclosure transport/research boundary was violated."""

    def __init__(self, domain: str, *, reason: str, detail: str):
        self.domain = domain
        self.reason = reason
        self.detail = detail
        super().__init__(f"domain={domain} reason={reason} {detail}")


def disclosure_boundary(domain: str) -> DisclosureDomainBoundary | None:
    return _DISCLOSURE_BOUNDARIES.get(str(domain or "").strip())


def disclosure_domains() -> tuple[str, ...]:
    return tuple(sorted(_DISCLOSURE_BOUNDARIES))


def require_disclosure_boundary(domain: str) -> DisclosureDomainBoundary:
    boundary = disclosure_boundary(domain)
    if boundary is None:
        raise DisclosureBoundaryError(
            str(domain or ""),
            reason="unknown_disclosure_domain",
            detail="domain is not in the E0 disclosure inventory",
        )
    return boundary


def _test_escape_enabled(allow_test_escape: bool) -> bool:
    if allow_test_escape:
        return True
    return str(os.environ.get(_TEST_ESCAPE_ENV, "")).strip() in {"1", "true", "TRUE", "yes"}


def _mirror_escape_enabled(allow_test_escape: bool) -> bool:
    if allow_test_escape:
        return True
    if _test_escape_enabled(False):
        return True
    return str(os.environ.get(_MIRROR_ESCAPE_ENV, "")).strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
    }


def authorize_nonconforming_direct_write(
    domain: str,
    *,
    conformity: str,
    allow_test_escape: bool = False,
) -> DisclosureWritePermit:
    """Permit naked legacy-only writes solely via explicit test/emergency escape.

    Production writers must use formal land→accept (``disclosure_dual_write``
    formal-only path).  Cutover (leaving strangler states) retires this permit.
    """

    boundary = require_disclosure_boundary(domain)
    label = str(conformity or "").strip()
    if label in _FORMAL_CONFORMITY or label != "NONCONFORMING":
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="formal_write_without_accepted_path",
            detail=(
                f"conformity={label!r} is not allowed for direct write; "
                f"landing_writer={boundary.landing_writer!r} "
                f"canonical_writer={boundary.canonical_writer!r}; "
                "use formal dual-write or test escape"
            ),
        )
    if not _test_escape_enabled(allow_test_escape):
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="naked_nonconforming_escape_retired_from_production",
            detail=(
                "production paths must not call authorize_nonconforming_direct_write; "
                "use formal land→accept, or set "
                f"{_TEST_ESCAPE_ENV}=1 / allow_test_escape=True for tests"
            ),
        )
    if boundary.runtime_state not in _STRANGLER_STATES:
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="direct_write_retired_after_formalization",
            detail=(
                f"runtime_state={boundary.runtime_state!r} is not a strangler; "
                f"formal writers landing={boundary.landing_writer} "
                f"canonical={boundary.canonical_writer}; "
                "legacy direct write is forbidden"
            ),
        )
    if boundary.formal_write != "forbidden":
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="invalid_boundary_declaration",
            detail="formal_write must remain forbidden during strangler",
        )
    return DisclosureWritePermit(
        domain=boundary.domain,
        conformity="NONCONFORMING",
        target_table=boundary.target_table,
        publication="nonconforming_direct_write",
    )


def authorize_legacy_mirror_write(
    domain: str, *, allow_test_escape: bool = False
) -> DisclosureWritePermit:
    """Permit legacy mirror only via explicit test/emergency escape.

    Default production runtime is ``formal_only`` — mirror is off unless escape
    is set (``allow_test_escape`` / ``DISCLOSURE_ALLOW_LEGACY_MIRROR`` /
    ``DISCLOSURE_ALLOW_NONCONFORMING_ESCAPE``).
    """

    boundary = require_disclosure_boundary(domain)
    if boundary.runtime_state not in _FORMAL_WRITE_STATES:
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="legacy_mirror_not_in_runtime_state",
            detail=f"runtime_state={boundary.runtime_state!r}",
        )
    if boundary.runtime_state == "formal_only" and not _mirror_escape_enabled(
        allow_test_escape
    ):
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="legacy_mirror_retired_from_default_writes",
            detail=(
                "production formal writes are formal_only; pass "
                "enable_legacy_mirror with allow_test_escape, or set "
                f"{_MIRROR_ESCAPE_ENV}=1 / {_TEST_ESCAPE_ENV}=1"
            ),
        )
    if boundary.landing_writer is None or boundary.canonical_writer is None:
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="legacy_mirror_without_formal_writers",
            detail="mirror requires formal land→accept writers",
        )
    return DisclosureWritePermit(
        domain=boundary.domain,
        conformity="NONCONFORMING",
        target_table=boundary.target_table,
        publication="legacy_mirror_of_formal_accept",
    )


def refuse_accepted_publication_claim(
    domain: str,
    claim: str,
    *,
    cutover_allowed: bool = False,
) -> None:
    """Hard wall for publication claims that disclosure research cannot satisfy."""

    boundary = require_disclosure_boundary(domain)
    label = str(claim or "").strip()
    if label not in _ACCEPTED_CLAIMS:
        return
    if label in _SNAPSHOT_CLAIMS:
        if cutover_allowed:
            return
        raise DisclosureBoundaryError(
            boundary.domain,
            reason="dataset_snapshot_blocked_until_e0_cutover",
            detail=(
                "DatasetSnapshot freeze requires three-domain shadow MATCH "
                "(cutover_allowed) on partitions serving the response; "
                f"runtime_state={boundary.runtime_state} "
                f"conformity={boundary.conformity}"
            ),
        )
    if label in _PATH_CLAIMS:
        if boundary.landing_writer is None or boundary.canonical_writer is None:
            raise DisclosureBoundaryError(
                boundary.domain,
                reason="accepted_claim_without_formal_path",
                detail=(
                    f"claim={label!r} requires landing→validate→accept; "
                    f"current conformity={boundary.conformity} "
                    f"table={boundary.target_table} has no formal writers"
                ),
            )
        return


def refuse_formal_disclosure_write_without_accepted_path(
    domain: str,
    *,
    publication_claim: str,
    cutover_allowed: bool = False,
) -> None:
    """Alias wall used by DatasetSnapshot / formal publish entrypoints."""

    refuse_accepted_publication_claim(
        domain, publication_claim, cutover_allowed=cutover_allowed
    )


def attest_disclosure_research_surface(
    read_policy: DisclosureReadPolicy | None = None,
) -> DisclosureResearchSurfaceReport:
    """Read-only trust report for institution research UI."""

    if read_policy is not None and read_policy.cutover_allowed:
        profile_status = read_policy.feature_store_profiles_status
        domains = tuple(
            DisclosureDomainAttestation(
                domain=item.domain,
                status=(
                    "READY"
                    if item.conformity == "ACCEPTED"
                    else (
                        "PARTIAL"
                        if item.conformity == "PARTIAL"
                        else "NONCONFORMING"
                    )
                ),
                population_kind="raw_evidence",
                adapter=require_disclosure_boundary(item.domain).adapter,
                target_table=item.table,
                availability_axis=require_disclosure_boundary(
                    item.domain
                ).availability_axis,
                reason=(
                    f"source={item.source}; conformity={item.conformity}; "
                    f"{item.reason}; feature_store_profiles={profile_status}"
                ),
            )
            for item in read_policy.domains
        )
        return DisclosureResearchSurfaceReport(
            overall_status="PARTIAL",
            cutover_allowed=True,
            e0_phase="gate_closed_canary",
            domains=domains,
            notes=(
                "e0_disclosure_cutover_allowed_three_domain_match",
                "research_provider_fields_prefer_canonical",
                "formal_writes_formal_only_no_default_legacy_mirror",
                "feature_store_profiles_typed_enrichment_projection",
                "dataset_snapshot_canary_scope_freezable",
                "phase_e_smoke_eligible_ablation_still_blocked",
                "b_pit_cutover_remains_blocked_separately",
            ),
        )

    domains = tuple(
        DisclosureDomainAttestation(
            domain=item.domain,
            status="NONCONFORMING",
            population_kind=item.population_kind,
            adapter=item.adapter,
            target_table=item.target_table,
            availability_axis=item.availability_axis,
            reason=(
                "formal_only_writes_research_fallback_until_shadow_match; "
                f"availability_axis={item.availability_axis}; "
                "cutover_blocked_until_three_domain_shadow_match"
            ),
        )
        for item in (_DISCLOSURE_BOUNDARIES[name] for name in disclosure_domains())
    )
    return DisclosureResearchSurfaceReport(
        overall_status="NONCONFORMING",
        cutover_allowed=False,
        e0_phase="in_progress",
        domains=domains,
        notes=(
            "e0_disclosure_formalization_in_progress",
            "three_domains_formal_only_no_default_mirror",
            "legacy_research_fallback_with_nonconforming_label",
            "institution_follow_blocked_until_e0_gate",
            "dataset_snapshot_blocked_until_three_domain_match",
            "b_pit_cutover_remains_blocked_separately",
        ),
    )


def disclosure_inventory() -> tuple[dict[str, Any], ...]:
    """Static inventory for audits/unit tests; not a readiness certificate."""

    return tuple(
        {
            "domain": item.domain,
            "dataset_id": item.dataset_id,
            "adapter": item.adapter,
            "target_table": item.target_table,
            "population_kind": item.population_kind,
            "availability_axis": item.availability_axis,
            "availability_rule": item.availability_rule,
            "conformity": item.conformity,
            "runtime_state": item.runtime_state,
            "landing_writer": item.landing_writer,
            "canonical_writer": item.canonical_writer,
            "formal_write": item.formal_write,
            "legacy_mirror_deprecated": item.legacy_mirror_deprecated,
            "legacy_mirror_default": item.legacy_mirror_default,
        }
        for item in (_DISCLOSURE_BOUNDARIES[name] for name in disclosure_domains())
    )


__all__ = [
    "DisclosureBoundaryError",
    "DisclosureDomainAttestation",
    "DisclosureDomainBoundary",
    "DisclosureResearchSurfaceReport",
    "DisclosureWritePermit",
    "TrustStatus",
    "attest_disclosure_research_surface",
    "authorize_legacy_mirror_write",
    "authorize_nonconforming_direct_write",
    "disclosure_boundary",
    "disclosure_domains",
    "disclosure_inventory",
    "refuse_accepted_publication_claim",
    "refuse_formal_disclosure_write_without_accepted_path",
    "require_disclosure_boundary",
]
