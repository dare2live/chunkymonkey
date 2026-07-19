"""Phase B-ext: read-only shadow reconcile for legacy market-pulse scope.

Compares what legacy pulse publishes (raw daily breadth + raw margin sum that
may include BSE) against an honest external_aggregate view (SSE+SZSE only) and
records that project_universe_pit cutover is not authorized.

No mart rewrite. No consumer cutover. Fail closed without inventing READY/PARITY
for project-universe claims.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from services.market_pulse_scope import (
    MarketPulseScopeReport,
    attest_market_pulse_scope,
)

CORE_MARGIN_VENUES: frozenset[str] = frozenset({"SSE", "SZSE"})


class PulseShadowVerdict(str, Enum):
    """Shadow outcome for one observation date — never authorizes cutover alone."""

    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    EXTERNAL_HONEST_SHADOW = "EXTERNAL_HONEST_SHADOW"
    BLOCKED = "BLOCKED"
    # Reserved: never emitted while legacy mart remains the serve surface.
    PARITY = "PARITY"


@dataclass(frozen=True)
class PulseShadowReconcileReport:
    trade_date: str
    verdict: PulseShadowVerdict
    legacy_rzrqye: float | None
    honest_external_rzrqye: float | None
    bse_rzrqye: float | None
    delta_legacy_minus_honest: float | None
    venues_present: tuple[str, ...]
    cutover_allowed: bool
    issues: tuple[str, ...]
    scope: MarketPulseScopeReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "verdict": self.verdict.value,
            "legacy_rzrqye": self.legacy_rzrqye,
            "honest_external_rzrqye": self.honest_external_rzrqye,
            "bse_rzrqye": self.bse_rzrqye,
            "delta_legacy_minus_honest": self.delta_legacy_minus_honest,
            "venues_present": list(self.venues_present),
            "cutover_allowed": self.cutover_allowed,
            "issues": list(self.issues),
            "scope": self.scope.as_dict(),
        }


def _as_day(trade_date: str) -> str | None:
    day = str(trade_date or "").replace("-", "")
    if len(day) != 8 or not day.isdigit():
        return None
    return day


def _venue_balances(
    margin_rows: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in margin_rows:
        venue = str(row.get("exchange_id") or "").upper()
        if not venue:
            continue
        raw = row.get("rzrqye")
        if raw is None:
            continue
        out[venue] = float(raw)
    return out


def reconcile_market_pulse_shadow(
    trade_date: str,
    *,
    margin_rows: Sequence[Mapping[str, Any]] = (),
    raw_daily_row_count: int | None = None,
    project_universe_available: bool = False,
) -> PulseShadowReconcileReport:
    """Reconcile one day of legacy pulse scope vs honest external aggregate.

    ``project_universe_available`` only records whether PIT population could be
    loaded; it never flips ``cutover_allowed`` while legacy numbers remain the
    serve surface.
    """

    day = _as_day(trade_date)
    if day is None:
        scope = attest_market_pulse_scope(str(trade_date))
        return PulseShadowReconcileReport(
            trade_date=str(trade_date),
            verdict=PulseShadowVerdict.BLOCKED,
            legacy_rzrqye=None,
            honest_external_rzrqye=None,
            bse_rzrqye=None,
            delta_legacy_minus_honest=None,
            venues_present=(),
            cutover_allowed=False,
            issues=("invalid_trade_date",),
            scope=scope,
        )

    balances = _venue_balances(margin_rows)
    venues = tuple(sorted(balances))
    scope = attest_market_pulse_scope(
        day,
        margin_exchange_ids=venues,
        raw_daily_row_count=raw_daily_row_count,
    )
    issues: list[str] = [
        "legacy_pulse_untrusted_pending_consumer_cutover",
        "breadth_not_project_universe_pit",
    ]
    if not project_universe_available:
        issues.append("project_universe_pit_unavailable")

    core_present = CORE_MARGIN_VENUES <= set(balances)
    bse = balances.get("BSE")
    honest = (
        float(balances["SSE"] + balances["SZSE"]) if core_present else None
    )
    # Mirror legacy pulse coverage gate: need >=2 distinct venues to publish a sum.
    legacy = float(sum(balances.values())) if len(balances) >= 2 else None
    if not core_present:
        issues.append("margin_core_venues_incomplete")
        return PulseShadowReconcileReport(
            trade_date=day,
            verdict=PulseShadowVerdict.BLOCKED,
            legacy_rzrqye=legacy,
            honest_external_rzrqye=honest,
            bse_rzrqye=bse,
            delta_legacy_minus_honest=None,
            venues_present=venues,
            cutover_allowed=False,
            issues=tuple(issues),
            scope=scope,
        )

    assert honest is not None and legacy is not None
    delta = legacy - honest
    if bse is not None and abs(delta) > 1e-9:
        issues.append("legacy_includes_BSE_in_market_sum")
        verdict = PulseShadowVerdict.SCOPE_MISMATCH
    else:
        verdict = PulseShadowVerdict.EXTERNAL_HONEST_SHADOW

    return PulseShadowReconcileReport(
        trade_date=day,
        verdict=verdict,
        legacy_rzrqye=legacy,
        honest_external_rzrqye=honest,
        bse_rzrqye=bse,
        delta_legacy_minus_honest=delta,
        venues_present=venues,
        cutover_allowed=False,
        issues=tuple(issues),
        scope=scope,
    )


__all__ = [
    "CORE_MARGIN_VENUES",
    "PulseShadowReconcileReport",
    "PulseShadowVerdict",
    "reconcile_market_pulse_shadow",
]
