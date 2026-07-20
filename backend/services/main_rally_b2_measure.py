"""Measured B2: market-sensing gate of main_rally setups vs B0 (Phase F / F3).

Adds one named FeatureBlock on top of B0 pivot-confirmed setups under the
identical ``DatasetSnapshot``, folds, costs and paper execution as B0. Gates
signal-day eligibility on EOD project-board breadth risk-on
(``MarketContextSnapshot``) for that day; risk-off / untrusted / missing
``available_at`` days trade nothing.

Reuses the canonical Tier2 market-sensing primitives from
``institution_follow_b2_measure`` (project-board breadth computed from
accepted nominal bars; legacy ``market_pulse`` mart refused ``UNTRUSTED``;
missing ``available_at`` fails closed) — one writer for ``MarketContextSnapshot``
across strategy packages, not a second implementation of the same sensing
block. Only the setup-eligible intersection and B0-paper wiring below are
main_rally-specific.

Never reads rally GT/negative label tables; never widens B0's setup
eligibles, only narrows by day (same posture as B1's stock-state intersect).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.institution_follow_b0_measure import (
    B0Prereg,
    BareKPaperMetrics,
    MeasuredB0Result,
    WalkForwardPlan,
    evaluate_claimable,
)
from services.institution_follow_b2_measure import (
    MarketContextCoverage,
    MarketContextSnapshot,
    MIN_ADV_DEC_RATIO_RISK_ON,
    MIN_CONTEXT_DAY_COVERAGE,
    REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT,
    REASON_B2_PULSE_NO_AVAILABLE_AT,
    REASON_B2_PULSE_UNTRUSTED,
    SOURCE_NOMINAL_BARS,
    SOURCE_PULSE_MART,
    build_context_by_day,
    build_market_context_from_nominal_bars,
    measure_market_context_coverage,
    refuse_pulse_mart_as_market_context,
)
from services.institution_follow_edge_gates import evaluate_accept_edge_gates
from services.main_rally_b0_measure import (
    eligible_codes_by_signal_day,
    measure_main_rally_b0_paper,
)

DEFINITION_VERSION = "market_sensing_project_breadth_v0"
METHOD_ID = "signal_day_board_filtered_nominal_breadth"
POPULATION_KIND = "project_universe_pit_shadow"
REASON_B2_PAPER_MEASURED = "measured_main_rally_b2_paper_market_sensing_gated"
REASON_B2_NO_B0_CONTEXT = "main_rally_b2_requires_measured_b0_context"


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


@dataclass(frozen=True)
class B0B2DeltaMetrics:
    total_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    payoff_ratio: float | None
    turnover: float | None
    n_trades_completed: int | None
    holdout_total_return: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "payoff_ratio": self.payoff_ratio,
            "turnover": self.turnover,
            "n_trades_completed": self.n_trades_completed,
            "holdout_total_return": self.holdout_total_return,
            "unit": "b2_minus_b0",
        }


@dataclass(frozen=True)
class MeasuredB2Result:
    coverage: MarketContextCoverage
    context_by_day: dict[str, MarketContextSnapshot]
    eligible_by_day: dict[str, tuple[str, ...]]
    measured: MeasuredB0Result | None
    b0_metrics: BareKPaperMetrics | None
    b0_holdout_metrics: BareKPaperMetrics | None
    delta: B0B2DeltaMetrics | None
    claimable: bool
    reason: str
    edge_gates: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.as_dict(),
            "context_day_count": len(self.context_by_day),
            "eligible_day_count": len(self.eligible_by_day),
            "measured": self.measured.as_dict() if self.measured else None,
            "b0_metrics": self.b0_metrics.as_dict() if self.b0_metrics else None,
            "b0_holdout_metrics": (
                self.b0_holdout_metrics.as_dict()
                if self.b0_holdout_metrics
                else None
            ),
            "delta_b2_minus_b0": self.delta.as_dict() if self.delta else None,
            "claimable": self.claimable,
            "reason": self.reason,
            "accept_edge_gates": dict(self.edge_gates),
            "paper_fills": "measured" if self.measured else "not_run",
            "definition_version": DEFINITION_VERSION,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
        }


def eligible_by_day_from_context_and_setup(
    setup_eligible_by_day: Mapping[str, Any],
    context_by_day: Mapping[str, MarketContextSnapshot],
) -> dict[str, set[str]]:
    """Risk-on days keep B0's own setup eligibles unchanged; else empty.

    Market sensing only ever narrows B0's setup-eligible codes by trading
    day; it never adds a code B0's own detector did not already select.
    Fails closed (empty day) on missing context, non-READY trust, missing
    ``available_at``, or risk-off.
    """

    out: dict[str, set[str]] = {}
    days = set(setup_eligible_by_day) | set(context_by_day)
    for day in days:
        d = _norm_day(day)
        ctx = context_by_day.get(d) or context_by_day.get(day)
        if (
            ctx is None
            or ctx.trust_status != "READY"
            or not ctx.available_at
            or not ctx.risk_on
        ):
            out[d] = set()
            continue
        out[d] = set(setup_eligible_by_day.get(d) or setup_eligible_by_day.get(day) or set())
    return out


def _delta(
    b2: BareKPaperMetrics,
    b0: BareKPaperMetrics,
    *,
    b2_holdout: BareKPaperMetrics,
    b0_holdout: BareKPaperMetrics,
) -> B0B2DeltaMetrics:
    def sub(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return float(a) - float(b)

    return B0B2DeltaMetrics(
        total_return=sub(b2.total_return, b0.total_return),
        max_drawdown=sub(b2.max_drawdown, b0.max_drawdown),
        win_rate=sub(b2.win_rate, b0.win_rate),
        payoff_ratio=sub(b2.payoff_ratio, b0.payoff_ratio),
        turnover=sub(b2.turnover, b0.turnover),
        n_trades_completed=(
            int(b2.n_trades_completed) - int(b0.n_trades_completed)
        ),
        holdout_total_return=sub(
            b2_holdout.total_return, b0_holdout.total_return
        ),
    )


def measure_b2_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    b0_measured: MeasuredB0Result,
    context_by_day: Mapping[str, MarketContextSnapshot] | None = None,
    source: str = SOURCE_NOMINAL_BARS,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> MeasuredB2Result:
    """Gate B0's setup eligibles on risk-on MarketContextSnapshot under same WF."""

    cfg = prereg or b0_measured.prereg
    plan: WalkForwardPlan = b0_measured.walk_forward
    days = list(plan.trading_days)
    ctx = (
        dict(context_by_day)
        if context_by_day is not None
        else build_context_by_day(bars_by_day, days, source=source)
    )
    coverage = measure_market_context_coverage(days, ctx)
    if not coverage.sufficient:
        return MeasuredB2Result(
            coverage=coverage,
            context_by_day=dict(ctx),
            eligible_by_day={},
            measured=None,
            b0_metrics=b0_measured.metrics,
            b0_holdout_metrics=b0_measured.holdout_metrics,
            delta=None,
            claimable=False,
            reason=coverage.reason,
            edge_gates={},
        )

    setup_eligible = eligible_codes_by_signal_day(bars_by_day, thresholds=thresholds)
    intersected_all = eligible_by_day_from_context_and_setup(setup_eligible, ctx)
    intersected = {d: intersected_all.get(d, set()) for d in days}

    measured = measure_main_rally_b0_paper(
        bars_by_day,
        days,
        prereg=cfg,
        st_codes=st_codes,
        walk_forward=plan,
        eligible_by_day=intersected,
    )
    claimable, _ = evaluate_claimable(
        measured.walk_forward, measured.metrics, prereg=cfg
    )
    edge = evaluate_accept_edge_gates(
        measured.walk_forward,
        measured.metrics,
        measured.holdout_metrics,
        prereg=cfg,
    )
    delta = _delta(
        measured.metrics,
        b0_measured.metrics,
        b2_holdout=measured.holdout_metrics,
        b0_holdout=b0_measured.holdout_metrics,
    )
    reason = REASON_B2_PAPER_MEASURED if claimable else measured.reason
    return MeasuredB2Result(
        coverage=coverage,
        context_by_day=dict(ctx),
        eligible_by_day={k: tuple(sorted(v)) for k, v in intersected.items()},
        measured=measured,
        b0_metrics=b0_measured.metrics,
        b0_holdout_metrics=b0_measured.holdout_metrics,
        delta=delta,
        claimable=claimable,
        reason=reason,
        edge_gates=edge.as_dict(),
    )


__all__ = [
    "DEFINITION_VERSION",
    "METHOD_ID",
    "MIN_ADV_DEC_RATIO_RISK_ON",
    "MIN_CONTEXT_DAY_COVERAGE",
    "POPULATION_KIND",
    "REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT",
    "REASON_B2_NO_B0_CONTEXT",
    "REASON_B2_PAPER_MEASURED",
    "REASON_B2_PULSE_NO_AVAILABLE_AT",
    "REASON_B2_PULSE_UNTRUSTED",
    "SOURCE_NOMINAL_BARS",
    "SOURCE_PULSE_MART",
    "B0B2DeltaMetrics",
    "MarketContextCoverage",
    "MarketContextSnapshot",
    "MeasuredB2Result",
    "build_context_by_day",
    "build_market_context_from_nominal_bars",
    "eligible_by_day_from_context_and_setup",
    "measure_b2_paper",
    "measure_market_context_coverage",
    "refuse_pulse_mart_as_market_context",
]
