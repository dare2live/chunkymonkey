"""Phase B-ext: population-scope attestation for market pulse surfaces.

Legacy ``market_pulse`` marts may still materialize breadth from canonical∪raw
fill.  Product trust for ``adv_dec_ratio`` becomes READY only when B-pit
production read is ``MART_CUTOVER`` (project_universe_pit evidence) — never from
raw mart numbers alone.  Margin ``rzrqye`` may be READY as ``external_aggregate``
only after F4 serve→accepted cutover (``pulse_source_accepted`` + promote
criteria).  This module is read-only: it does not rewrite marts.

Typed empty (owner 2026-07-23): when a field is not expected for the day
(pre-coverage / outside attested window / not eligible / confirmed empty),
status is ``EMPTY`` — normal absence (like no K on a non-trade / before series
starts), not UNTRUSTED fail-closed scare. In-window unexpected gaps stay
UNTRUSTED.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

TrustStatus = Literal["EMPTY", "UNTRUSTED", "BLOCKED", "NOT_EVALUATED", "READY"]

# Same semantics as margin: not_expected / confirmed_empty = normal EMPTY.


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
    # EMPTY = typed normal absence (OK) — does not scare above READY.
    order = {
        "READY": 0,
        "EMPTY": 0,
        "NOT_EVALUATED": 1,
        "UNTRUSTED": 2,
        "BLOCKED": 3,
    }
    return order[status]


def attest_market_pulse_scope(
    trade_date: str,
    *,
    margin_exchange_ids: Sequence[str] | None = None,
    raw_daily_row_count: int | None = None,
    margin_source_accepted: bool = False,
    margin_promoted: bool = False,
    margin_empty: bool = False,
    margin_empty_reason: str | None = None,
    breadth_empty: bool = False,
    breadth_empty_reason: str | None = None,
) -> MarketPulseScopeReport:
    """Attest pulse field population scope.

    Breadth 直接按其真实来源(accepted canonical + 板块前缀白名单)判 READY;
    2026-08-14 前它取决于已退役的 b_pit cutover。Typed empty (not expected /
    confirmed empty) → EMPTY. Unexpected missing promote evidence → UNTRUSTED —
    never fake READY from raw mart numbers.

    Margin becomes READY only when serve reads accepted SSE+SZSE **and**
    promote gate consumed (``margin_promoted``). Same EMPTY / UNTRUSTED rules.
    """

    day = str(trade_date or "").replace("-", "")
    if len(day) != 8 or not day.isdigit():
        return MarketPulseScopeReport(
            trade_date=str(trade_date),
            overall_status="BLOCKED",
            fields=(),
            notes=("invalid_trade_date",),
        )

    if breadth_empty:
        breadth_status: TrustStatus = "EMPTY"
        breadth_kind = "project_universe_pit"
        breadth_reason = (
            f"{breadth_empty_reason or 'typed_empty_not_expected_or_confirmed'}; "
            "normal_absence_not_fail_closed; "
            "not_raw_tushare_daily_masquerade"
        )
        breadth_surface = "observation_membership.traded_on_observation_date"
    else:
        # 2026-08-14 b_pit 退役: 此前这里是三分支, 而**两个非空分支都在说假话** ——
        #   promoted 分支声明 source_surface=observation_membership.traded_on_observation_date,
        #     但 router 自己的 docstring 写明「days 数值不改」, 那个数从没经过 membership;
        #   untrusted 分支声明 breadth_surface=tr.raw_tushare_daily / "reads raw or unfiltered",
        #     但 market_pulse._NOMINAL_DAILY_SQL 优先读 canonical_nominal_ohlcv_daily,
        #     raw 只补缺日, 且影子实测整窗 raw 腿贡献 0 行。
        # 现在只描述这个数**实际**是怎么来的, 不再声明它没走过的来源。
        breadth_status = "READY"
        breadth_kind = "project_universe_pit"
        breadth_reason = (
            "accepted_canonical_nominal_with_board_prefix_whitelist; "
            "raw_tushare_daily_only_backfills_missing_days; "
            "not_raw_tushare_daily_masquerade"
        )
        breadth_surface = "tr.canonical_nominal_ohlcv_daily(sql_where_active_a_share)"

    if margin_empty:
        reason = margin_empty_reason or "typed_empty_not_expected_or_confirmed"
        rzrqye_status: TrustStatus = "EMPTY"
        rzrqye_reason = (
            f"{reason}; normal_absence_not_fail_closed; "
            "not_project_universe_pit"
        )
        rzrqye_surface = "tr.canonical_margin_exchange_daily"
        chg_reason = "derived_from_typed_empty_rzrqye_normal_absence"
    elif margin_promoted and margin_source_accepted:
        rzrqye_status = "READY"
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
            status=breadth_status,
            population_kind=breadth_kind,
            reason=breadth_reason,
            source_surface=breadth_surface,
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
        "no_consumer_payload_rewrite_by_scope_attestation",
    ]
    if breadth_empty:
        notes.append("breadth_typed_empty_normal_absence")
    else:
        notes.append("breadth_accepted_canonical_board_prefix")
    if margin_empty:
        notes.append("rzrqye_typed_empty_normal_absence")
    elif margin_promoted and margin_source_accepted:
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
