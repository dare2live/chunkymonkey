"""Measured B2 market-sensing block vs B0 (identical snapshot/folds/costs).

Honesty rules
-------------
- Prefer project-universe / board-filtered nominal breadth computed from the
  same bars used by B0 — never silent latest fallback.
- Legacy ``market_pulse`` mart / margin aggregates are UNTRUSTED
  (``cutover_allowed=false``); requesting them fails closed.
- ``MarketContextSnapshot.available_at`` is required; missing → day inactive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from services.institution_follow_b0_measure import (
    B0Prereg,
    BareKPaperMetrics,
    MeasuredB0Result,
    UNKNOWN,
    WalkForwardPlan,
    evaluate_claimable,
    measure_b0_paper,
)
from services.institution_follow_edge_gates import evaluate_accept_edge_gates
from services.market_pulse_scope import attest_market_pulse_scope
from services.universe import ACTIVE_A_SHARE_PREFIXES

DEFINITION_VERSION = "market_sensing_project_breadth_v0"
METHOD_ID = "signal_day_board_filtered_nominal_breadth"
POPULATION_KIND = "project_universe_pit_shadow"
SOURCE_NOMINAL_BARS = "accepted_nominal_ohlcv_daily_bars_by_day"
SOURCE_PULSE_MART = "mart_market_pulse_daily"
MIN_CONTEXT_DAY_COVERAGE = 0.90
# Risk-on: more advancers than decliners (ratio >= 1.0); flat-only days inactive.
MIN_ADV_DEC_RATIO_RISK_ON = 1.0
BOARD_PREFIXES = tuple(ACTIVE_A_SHARE_PREFIXES)

REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT = (
    "b2_market_context_coverage_insufficient"
)
REASON_B2_PULSE_UNTRUSTED = "b2_pulse_mart_untrusted_fail_closed"
REASON_B2_PULSE_NO_AVAILABLE_AT = "b2_pulse_lacks_available_at_for_historical"
REASON_B2_PAPER_MEASURED = "measured_b2_paper_market_sensing_gated"
REASON_B2_NO_B0_CONTEXT = "b2_requires_measured_b0_context"

TrustStatus = Literal["READY", "UNTRUSTED", "UNAVAILABLE", "BLOCKED"]


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _code6(ts_code: str) -> str:
    return str(ts_code or "").split(".", 1)[0]


def _board_ok(ts_code: str, prefixes: Sequence[str] = BOARD_PREFIXES) -> bool:
    digits = _code6(ts_code)
    return len(digits) >= 2 and digits[:2] in set(prefixes)


def _pct(row: Mapping[str, Any]) -> float | None:
    if row.get("pct_chg") is not None:
        try:
            return float(row["pct_chg"])
        except (TypeError, ValueError):
            return None
    close = row.get("close")
    pre = row.get("pre_close")
    if close is None or pre is None:
        return None
    try:
        pre_f = float(pre)
        if pre_f == 0.0:
            return None
        return (float(close) / pre_f - 1.0) * 100.0
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MarketContextSnapshot:
    """Decision-time market context — availability + trust required."""

    decision_time: str
    available_at: str | None
    trust_status: TrustStatus
    source: str
    population_kind: str
    method: str
    adv_n: int | None
    dec_n: int | None
    flat_n: int | None
    adv_dec_ratio: float | None
    risk_on: bool | None
    refuse_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_time": self.decision_time,
            "available_at": self.available_at,
            "trust_status": self.trust_status,
            "source": self.source,
            "population_kind": self.population_kind,
            "method": self.method,
            "adv_n": self.adv_n,
            "dec_n": self.dec_n,
            "flat_n": self.flat_n,
            "adv_dec_ratio": self.adv_dec_ratio,
            "risk_on": self.risk_on,
            "refuse_reason": self.refuse_reason,
            "details": dict(self.details),
            "definition_version": DEFINITION_VERSION,
        }


@dataclass(frozen=True)
class MarketContextCoverage:
    status: str
    trading_days: tuple[str, ...]
    days_with_ready_context: int
    day_coverage: float
    risk_on_days: int
    sufficient: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trading_days": list(self.trading_days),
            "days_with_ready_context": self.days_with_ready_context,
            "day_coverage": self.day_coverage,
            "risk_on_days": self.risk_on_days,
            "sufficient": self.sufficient,
            "reason": self.reason,
            "details": dict(self.details),
            "min_context_day_coverage": MIN_CONTEXT_DAY_COVERAGE,
            "min_adv_dec_ratio_risk_on": MIN_ADV_DEC_RATIO_RISK_ON,
            "definition_version": DEFINITION_VERSION,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
        }


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
            "b_pit_cutover_allowed": False,
        }


def refuse_pulse_mart_as_market_context(
    trade_date: str,
    *,
    available_at: str | None = None,
) -> MarketContextSnapshot:
    """Fail closed: legacy pulse must not feed historical B2 features."""

    day = _norm_day(trade_date)
    attest = attest_market_pulse_scope(day)
    return MarketContextSnapshot(
        decision_time=day,
        available_at=available_at,
        trust_status="UNTRUSTED",
        source=SOURCE_PULSE_MART,
        population_kind="raw_evidence",
        method="refused_legacy_pulse_mart",
        adv_n=None,
        dec_n=None,
        flat_n=None,
        adv_dec_ratio=None,
        risk_on=None,
        refuse_reason=REASON_B2_PULSE_UNTRUSTED,
        details={
            "pulse_overall_status": attest.overall_status,
            "cutover_allowed": False,
            "missing_available_at": not bool(available_at),
            "available_at_reason": (
                None
                if available_at
                else REASON_B2_PULSE_NO_AVAILABLE_AT
            ),
            "note": (
                "B-pit cutover still false; pulse breadth/margin UNTRUSTED; "
                "use project-universe shadow breadth instead"
            ),
        },
    )


def build_market_context_from_nominal_bars(
    trade_date: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    prefixes: Sequence[str] = BOARD_PREFIXES,
    min_adv_dec_ratio: float = MIN_ADV_DEC_RATIO_RISK_ON,
) -> MarketContextSnapshot:
    """EOD project-board breadth from the same nominal bars as B0.

    ``available_at`` = decision_time (compact). Signal is EOD; entry is T+1
    open — same PIT posture as B0 cross-sectional momentum.
    """

    day = _norm_day(trade_date)
    if len(day) != 8:
        return MarketContextSnapshot(
            decision_time=str(trade_date),
            available_at=None,
            trust_status="BLOCKED",
            source=SOURCE_NOMINAL_BARS,
            population_kind=POPULATION_KIND,
            method=METHOD_ID,
            adv_n=None,
            dec_n=None,
            flat_n=None,
            adv_dec_ratio=None,
            risk_on=None,
            refuse_reason="invalid_trade_date",
        )

    adv = dec = flat = 0
    used = 0
    skipped_off_board = 0
    skipped_no_pct = 0
    for row in rows:
        code = str(row.get("ts_code") or "")
        if not code:
            continue
        if not _board_ok(code, prefixes):
            skipped_off_board += 1
            continue
        pct = _pct(row)
        if pct is None:
            skipped_no_pct += 1
            continue
        used += 1
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            flat += 1

    if used == 0:
        return MarketContextSnapshot(
            decision_time=day,
            available_at=None,
            trust_status="UNAVAILABLE",
            source=SOURCE_NOMINAL_BARS,
            population_kind=POPULATION_KIND,
            method=METHOD_ID,
            adv_n=0,
            dec_n=0,
            flat_n=0,
            adv_dec_ratio=None,
            risk_on=None,
            refuse_reason="no_board_filtered_bars",
            details={
                "skipped_off_board": skipped_off_board,
                "skipped_no_pct": skipped_no_pct,
            },
        )

    ratio = (float(adv) / float(dec)) if dec else (None if adv == 0 else float("inf"))
    # Finite risk-on: need a defined ratio >= threshold (exclude all-advance
    # edge as inf only when dec==0 and adv>0 — treat as risk_on=True).
    if dec == 0 and adv > 0:
        risk_on = True
        ratio_out: float | None = None
    elif ratio is None:
        risk_on = False
        ratio_out = None
    else:
        risk_on = float(ratio) >= float(min_adv_dec_ratio)
        ratio_out = float(ratio)

    return MarketContextSnapshot(
        decision_time=day,
        available_at=day,
        trust_status="READY",
        source=SOURCE_NOMINAL_BARS,
        population_kind=POPULATION_KIND,
        method=METHOD_ID,
        adv_n=adv,
        dec_n=dec,
        flat_n=flat,
        adv_dec_ratio=ratio_out,
        risk_on=risk_on,
        refuse_reason=None,
        details={
            "row_count_used": used,
            "skipped_off_board": skipped_off_board,
            "skipped_no_pct": skipped_no_pct,
            "b_pit_cutover_allowed": False,
            "note": (
                "shadow project-board breadth from accepted nominal bars; "
                "not legacy pulse mart; cutover_allowed=false"
            ),
        },
    )


def build_context_by_day(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    trading_days: Sequence[str],
    *,
    source: str = SOURCE_NOMINAL_BARS,
    pulse_available_at_by_day: Mapping[str, str] | None = None,
) -> dict[str, MarketContextSnapshot]:
    """Build per-day MarketContextSnapshot; pulse source fails closed."""

    out: dict[str, MarketContextSnapshot] = {}
    for day in trading_days:
        d = _norm_day(day)
        if source == SOURCE_PULSE_MART:
            avail = None
            if pulse_available_at_by_day is not None:
                avail = pulse_available_at_by_day.get(d)
            out[d] = refuse_pulse_mart_as_market_context(d, available_at=avail)
            continue
        if source != SOURCE_NOMINAL_BARS:
            out[d] = MarketContextSnapshot(
                decision_time=d,
                available_at=None,
                trust_status="BLOCKED",
                source=str(source),
                population_kind="unknown",
                method="unsupported_source",
                adv_n=None,
                dec_n=None,
                flat_n=None,
                adv_dec_ratio=None,
                risk_on=None,
                refuse_reason=f"unsupported_market_context_source:{source}",
            )
            continue
        out[d] = build_market_context_from_nominal_bars(
            d, bars_by_day.get(d) or []
        )
    return out


def measure_market_context_coverage(
    trading_days: Sequence[str],
    context_by_day: Mapping[str, MarketContextSnapshot],
) -> MarketContextCoverage:
    days = tuple(_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8)
    if not days:
        return MarketContextCoverage(
            status="EMPTY",
            trading_days=(),
            days_with_ready_context=0,
            day_coverage=0.0,
            risk_on_days=0,
            sufficient=False,
            reason=REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT,
            details={"note": "no_trading_days"},
        )

    ready = 0
    risk_on = 0
    untrusted = 0
    missing_avail = 0
    for day in days:
        ctx = context_by_day.get(day)
        if ctx is None:
            continue
        if ctx.trust_status == "UNTRUSTED":
            untrusted += 1
            continue
        if ctx.trust_status != "READY":
            continue
        if not ctx.available_at:
            missing_avail += 1
            continue
        ready += 1
        if ctx.risk_on:
            risk_on += 1

    day_cov = ready / len(days)
    # If the requested source is pulse/untrusted for the window, fail closed
    # with the pulse reason rather than a soft coverage miss.
    if untrusted == len(days) and untrusted > 0:
        reason = REASON_B2_PULSE_UNTRUSTED
        sufficient = False
    elif ready == 0 and missing_avail == len(days):
        reason = REASON_B2_PULSE_NO_AVAILABLE_AT
        sufficient = False
    else:
        sufficient = day_cov >= MIN_CONTEXT_DAY_COVERAGE
        reason = (
            "market_context_coverage_ready"
            if sufficient
            else REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT
        )

    return MarketContextCoverage(
        status="MEASURED",
        trading_days=days,
        days_with_ready_context=ready,
        day_coverage=day_cov,
        risk_on_days=risk_on,
        sufficient=sufficient,
        reason=reason,
        details={
            "untrusted_days": untrusted,
            "missing_available_at_days": missing_avail,
            "source_preferred": SOURCE_NOMINAL_BARS,
            "pulse_cutover_allowed": False,
        },
    )


def eligible_by_day_from_context(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    context_by_day: Mapping[str, MarketContextSnapshot],
) -> dict[str, set[str]]:
    """Risk-on days: all board codes present that day; else empty (no trade)."""

    out: dict[str, set[str]] = {}
    for day, rows in bars_by_day.items():
        d = _norm_day(day)
        ctx = context_by_day.get(d)
        if (
            ctx is None
            or ctx.trust_status != "READY"
            or not ctx.available_at
            or not ctx.risk_on
        ):
            out[d] = set()
            continue
        chosen: set[str] = set()
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code and _board_ok(code):
                chosen.add(_code6(code))
        out[d] = chosen
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
) -> MeasuredB2Result:
    """Gate B0 top-K on risk-on MarketContextSnapshot under same WF/costs."""

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

    eligible = eligible_by_day_from_context(bars_by_day, ctx)
    measured = measure_b0_paper(
        bars_by_day,
        days,
        prereg=cfg,
        st_codes=st_codes,
        eligible_by_day=eligible,
        walk_forward=plan,
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
        eligible_by_day={k: tuple(sorted(v)) for k, v in eligible.items()},
        measured=measured,
        b0_metrics=b0_measured.metrics,
        b0_holdout_metrics=b0_measured.holdout_metrics,
        delta=delta,
        claimable=claimable,
        reason=reason,
        edge_gates=edge.as_dict(),
    )


__all__ = [
    "BOARD_PREFIXES",
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
    "UNKNOWN",
    "B0B2DeltaMetrics",
    "MarketContextCoverage",
    "MarketContextSnapshot",
    "MeasuredB2Result",
    "build_context_by_day",
    "build_market_context_from_nominal_bars",
    "eligible_by_day_from_context",
    "measure_b2_paper",
    "measure_market_context_coverage",
    "refuse_pulse_mart_as_market_context",
]
