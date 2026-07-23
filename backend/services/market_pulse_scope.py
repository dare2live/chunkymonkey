"""Phase B-ext: population-scope attestation for market pulse surfaces.

Legacy ``market_pulse`` marts still read raw daily breadth.  Margin ``rzrqye``
may be READY as ``external_aggregate`` only after F4 serve→accepted cutover
(``pulse_source_accepted`` + promote criteria).  Project-universe claims remain
refused.  This module is read-only: it does not rewrite marts.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

TrustStatus = Literal["UNTRUSTED", "BLOCKED", "NOT_EVALUATED", "READY"]


@dataclass(frozen=True)
class PulseFieldScopeAttestation:
    field: str
    status: TrustStatus
    population_kind: str
    reason: str
    source_surface: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "status": self.status,
            "population_kind": self.population_kind,
            "reason": self.reason,
            "source_surface": self.source_surface,
        }


@dataclass(frozen=True)
class MarketPulseScopeReport:
    """Typed trust report for one market-pulse observation date."""

    trade_date: str
    overall_status: TrustStatus
    fields: tuple[PulseFieldScopeAttestation, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "overall_status": self.overall_status,
            "fields": [item.as_dict() for item in self.fields],
            "notes": list(self.notes),
        }


def _rank(status: TrustStatus) -> int:
    order = {"READY": 0, "NOT_EVALUATED": 1, "UNTRUSTED": 2, "BLOCKED": 3}
    return order[status]


def attest_market_pulse_scope(
    trade_date: str,
    *,
    margin_exchange_ids: Sequence[str] | None = None,
    raw_daily_row_count: int | None = None,
    margin_source_accepted: bool = False,
    margin_promoted: bool = False,
) -> MarketPulseScopeReport:
    """Attest pulse field population scope.

    Breadth stays UNTRUSTED (raw).  Margin becomes READY only when serve reads
    accepted SSE+SZSE **and** promote gate consumed (``margin_promoted``).
    Missing promote evidence → UNTRUSTED with typed reason — never fake READY.
    """

    day = str(trade_date or "").replace("-", "")
    if len(day) != 8 or not day.isdigit():
        return MarketPulseScopeReport(
            trade_date=str(trade_date),
            overall_status="BLOCKED",
            fields=(),
            notes=("invalid_trade_date",),
        )

    if margin_promoted and margin_source_accepted:
        rzrqye_status: TrustStatus = "READY"
        rzrqye_reason = (
            "accepted_sse_szse_external_aggregate_promoted; "
            "not_project_universe_pit"
        )
        rzrqye_surface = "tr.canonical_margin_exchange_daily"
        chg_reason = "derived_from_promoted_accepted_rzrqye_external_aggregate"
    elif margin_source_accepted:
        rzrqye_status = "UNTRUSTED"
        rzrqye_reason = (
            "pulse_source_accepted_but_promote_gate_not_consumed; "
            "stay_untrusted_until_criteria_pass"
        )
        rzrqye_surface = "tr.canonical_margin_exchange_daily"
        chg_reason = "derived_from_untrusted_rzrqye_pending_promote"
    else:
        rzrqye_status = "UNTRUSTED"
        rzrqye_reason = (
            "margin_is_venue_reported_external_aggregate; "
            "must not masquerade as project_universe_pit"
        )
        rzrqye_surface = "tr.raw_tushare_margin"
        chg_reason = "derived_from_untrusted_rzrqye_external_aggregate"

    fields: list[PulseFieldScopeAttestation] = [
        PulseFieldScopeAttestation(
            field="adv_dec_ratio",
            status="UNTRUSTED",
            population_kind="raw_evidence",
            reason=(
                "breadth_reads_raw_tushare_daily; not accepted "
                "traded_on_observation_date project_universe_pit"
            ),
            source_surface="tr.raw_tushare_daily",
        ),
        PulseFieldScopeAttestation(
            field="rzrqye",
            status=rzrqye_status,
            population_kind="external_aggregate",
            reason=rzrqye_reason,
            source_surface=rzrqye_surface,
        ),
        PulseFieldScopeAttestation(
            field="rzrqye_chg",
            status=rzrqye_status,
            population_kind="external_aggregate",
            reason=chg_reason,
            source_surface=rzrqye_surface,
        ),
    ]
    notes: list[str] = [
        "breadth_untrusted_raw_until_project_universe_cutover",
        "no_consumer_payload_rewrite_by_scope_attestation",
    ]
    if margin_promoted and margin_source_accepted:
        notes.append("rzrqye_promoted_external_aggregate_sse_szse")
    else:
        notes.append("rzrqye_untrusted_until_promote_consumed")
    venues = tuple(sorted({str(v).upper() for v in (margin_exchange_ids or ()) if v}))
    if "BSE" in venues:
        notes.append("margin_sum_includes_BSE_external_venue")
    if venues and not {"SSE", "SZSE"} <= set(venues):
        notes.append(f"margin_incomplete_core_venues venues={list(venues)}")
    if raw_daily_row_count is not None and int(raw_daily_row_count) <= 0:
        notes.append("raw_daily_breadth_zero_rows")

    overall: TrustStatus = "READY"
    for item in fields:
        if _rank(item.status) > _rank(overall):
            overall = item.status
    return MarketPulseScopeReport(
        trade_date=day,
        overall_status=overall,
        fields=tuple(fields),
        notes=tuple(notes),
    )


def refuse_project_universe_claim_for_legacy_pulse(
    claim: Mapping[str, Any] | str,
) -> None:
    """Hard wall: legacy pulse numbers cannot satisfy a project-universe claim."""

    label = (
        str(claim.get("population_kind") or claim.get("kind") or claim)
        if isinstance(claim, Mapping)
        else str(claim)
    )
    if label == "project_universe_pit":
        raise RuntimeError(
            "legacy_market_pulse_cannot_satisfy_project_universe_pit; "
            "use attest_market_pulse_scope + accepted observation population"
        )


__all__ = [
    "MarketPulseScopeReport",
    "PulseFieldScopeAttestation",
    "TrustStatus",
    "attest_market_pulse_scope",
    "refuse_project_universe_claim_for_legacy_pulse",
]
