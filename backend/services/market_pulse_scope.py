"""Phase B-ext: population-scope attestation for market pulse surfaces.

Legacy ``market_pulse`` marts still read raw daily + raw margin (including BSE).
Until shadow evidence supports cutover, those fields are ``UNTRUSTED`` for any
``project_universe_pit`` claim.  This module is read-only: it does not rewrite
marts or change consumer payloads.
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
) -> MarketPulseScopeReport:
    """Attest that legacy pulse fields must not be treated as project-universe.

    Optional evidence inputs document live wrong-scope facts (BSE in margin
    sum; raw daily breadth).  Missing optional evidence still yields UNTRUSTED
    for the known raw surfaces — never READY.
    """

    day = str(trade_date or "").replace("-", "")
    if len(day) != 8 or not day.isdigit():
        return MarketPulseScopeReport(
            trade_date=str(trade_date),
            overall_status="BLOCKED",
            fields=(),
            notes=("invalid_trade_date",),
        )

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
            status="UNTRUSTED",
            population_kind="external_aggregate",
            reason=(
                "margin_is_venue_reported_external_aggregate; "
                "must not masquerade as project_universe_pit"
            ),
            source_surface="tr.raw_tushare_margin",
        ),
        PulseFieldScopeAttestation(
            field="rzrqye_chg",
            status="UNTRUSTED",
            population_kind="external_aggregate",
            reason="derived_from_untrusted_rzrqye_external_aggregate",
            source_surface="tr.raw_tushare_margin",
        ),
    ]
    notes: list[str] = [
        "legacy_market_pulse_mart_untrusted_until_shadow_cutover",
        "no_consumer_payload_change",
    ]
    venues = tuple(sorted({str(v).upper() for v in (margin_exchange_ids or ()) if v}))
    if "BSE" in venues:
        notes.append("margin_sum_includes_BSE_external_venue")
    if venues and not {"SSE", "SZSE"} <= set(venues):
        notes.append(f"margin_incomplete_core_venues venues={list(venues)}")
    if raw_daily_row_count is not None and int(raw_daily_row_count) <= 0:
        notes.append("raw_daily_breadth_zero_rows")

    overall: TrustStatus = "UNTRUSTED"
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
