"""Measured B1 stock-state conditioning vs B0 (identical snapshot/folds/costs).

Production Tier1 reads go through ``resolve_tier12_production_read`` (cutover
resolver). Default ``cutover_allowed=false`` keeps the legacy
``fact_stock_form_daily`` path unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.institution_follow_b0_measure import (
    B0Prereg,
    BareKPaperMetrics,
    MeasuredB0Result,
    UNKNOWN,
    WalkForwardPlan,
    evaluate_claimable,
    measure_b0_paper,
    plan_walk_forward,
)
from services.institution_follow_edge_gates import evaluate_accept_edge_gates
from services.tier12_consumer_cutover import (
    Tier12ConsumerCutoverConfig,
    resolve_tier12_production_read,
    stock_states_from_accepted_payload,
)

STOCK_STATE_TABLE = "fact_stock_form_daily"
DEFINITION_VERSION = "stock_state_stage_pattern_v0"
# Eligible when signal-day EOD state shows uptrend or a breakout event.
ELIGIBLE_TREND_VALUES = frozenset({"up"})
MIN_STATE_DAY_COVERAGE = 0.90
MIN_AVG_BAR_STATE_OVERLAP = 0.50
REASON_B1_STATE_COVERAGE_INSUFFICIENT = "b1_stock_state_coverage_insufficient"
REASON_B1_PAPER_MEASURED = "measured_b1_paper_stock_state_conditioned"
REASON_B1_NO_B0_CONTEXT = "b1_requires_measured_b0_context"


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _code6(ts_code: str) -> str:
    return str(ts_code or "").split(".", 1)[0]


@dataclass(frozen=True)
class StockStateCoverage:
    status: str
    trading_days: tuple[str, ...]
    days_with_state: int
    day_coverage: float
    avg_bar_state_overlap: float
    sufficient: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trading_days": list(self.trading_days),
            "days_with_state": self.days_with_state,
            "day_coverage": self.day_coverage,
            "avg_bar_state_overlap": self.avg_bar_state_overlap,
            "sufficient": self.sufficient,
            "reason": self.reason,
            "details": dict(self.details),
            "min_state_day_coverage": MIN_STATE_DAY_COVERAGE,
            "min_avg_bar_state_overlap": MIN_AVG_BAR_STATE_OVERLAP,
            "definition_version": DEFINITION_VERSION,
        }


@dataclass(frozen=True)
class B0B1DeltaMetrics:
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
            "unit": "b1_minus_b0",
        }


@dataclass(frozen=True)
class MeasuredB1Result:
    coverage: StockStateCoverage
    eligible_by_day: dict[str, tuple[str, ...]]
    measured: MeasuredB0Result | None
    b0_metrics: BareKPaperMetrics | None
    b0_holdout_metrics: BareKPaperMetrics | None
    delta: B0B1DeltaMetrics | None
    claimable: bool
    reason: str
    edge_gates: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.as_dict(),
            "eligible_day_count": len(self.eligible_by_day),
            "measured": self.measured.as_dict() if self.measured else None,
            "b0_metrics": self.b0_metrics.as_dict() if self.b0_metrics else None,
            "b0_holdout_metrics": (
                self.b0_holdout_metrics.as_dict()
                if self.b0_holdout_metrics
                else None
            ),
            "delta_b1_minus_b0": self.delta.as_dict() if self.delta else None,
            "claimable": self.claimable,
            "reason": self.reason,
            "accept_edge_gates": dict(self.edge_gates),
            "paper_fills": "measured" if self.measured else "not_run",
            "definition_version": DEFINITION_VERSION,
        }


def state_row_eligible(row: Mapping[str, Any]) -> bool:
    trend = str(row.get("axis_trend") or "").strip().lower()
    if trend in ELIGIBLE_TREND_VALUES:
        return True
    brk = row.get("is_breakout_event")
    return brk is True or brk == 1 or str(brk).lower() in {"true", "1"}


def eligible_by_day_from_state(
    state_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, set[str]]:
    """Build signal-day eligible 6-digit code sets from loaded state rows."""

    out: dict[str, set[str]] = {}
    for day, by_code in state_by_day.items():
        d = _norm_day(day)
        chosen: set[str] = set()
        for code, row in by_code.items():
            if state_row_eligible(row):
                chosen.add(_code6(code))
        out[d] = chosen
    return out


def measure_stock_state_coverage(
    trading_days: Sequence[str],
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    state_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> StockStateCoverage:
    days = tuple(_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8)
    if not days:
        return StockStateCoverage(
            status="EMPTY",
            trading_days=(),
            days_with_state=0,
            day_coverage=0.0,
            avg_bar_state_overlap=0.0,
            sufficient=False,
            reason=REASON_B1_STATE_COVERAGE_INSUFFICIENT,
            details={"note": "no_trading_days"},
        )
    days_with = 0
    overlaps: list[float] = []
    for day in days:
        state_codes = {_code6(c) for c in (state_by_day.get(day) or {})}
        if state_codes:
            days_with += 1
        bar_codes = {
            _code6(str(r.get("ts_code") or ""))
            for r in (bars_by_day.get(day) or [])
            if r.get("ts_code")
        }
        if not bar_codes:
            overlaps.append(0.0)
            continue
        overlaps.append(len(state_codes & bar_codes) / len(bar_codes))
    day_cov = days_with / len(days)
    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
    sufficient = (
        day_cov >= MIN_STATE_DAY_COVERAGE
        and avg_overlap >= MIN_AVG_BAR_STATE_OVERLAP
    )
    return StockStateCoverage(
        status="MEASURED",
        trading_days=days,
        days_with_state=days_with,
        day_coverage=day_cov,
        avg_bar_state_overlap=avg_overlap,
        sufficient=sufficient,
        reason=(
            "stock_state_coverage_ready"
            if sufficient
            else REASON_B1_STATE_COVERAGE_INSUFFICIENT
        ),
        details={
            "missing_state_days": [
                d for d in days if not (state_by_day.get(d) or {})
            ],
            "source_table": STOCK_STATE_TABLE,
        },
    )


def _delta(
    b1: BareKPaperMetrics,
    b0: BareKPaperMetrics,
    *,
    b1_holdout: BareKPaperMetrics,
    b0_holdout: BareKPaperMetrics,
) -> B0B1DeltaMetrics:
    def sub(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return float(a) - float(b)

    return B0B1DeltaMetrics(
        total_return=sub(b1.total_return, b0.total_return),
        max_drawdown=sub(b1.max_drawdown, b0.max_drawdown),
        win_rate=sub(b1.win_rate, b0.win_rate),
        payoff_ratio=sub(b1.payoff_ratio, b0.payoff_ratio),
        turnover=sub(b1.turnover, b0.turnover),
        n_trades_completed=(
            int(b1.n_trades_completed) - int(b0.n_trades_completed)
        ),
        holdout_total_return=sub(
            b1_holdout.total_return, b0_holdout.total_return
        ),
    )


def _load_legacy_stock_state_by_day(
    conn,
    days: Sequence[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Legacy ``fact_stock_form_daily`` SQL path (pre-cutover scaffold)."""

    if not days:
        return {}
    placeholders = ", ".join(["?"] * len(days))
    sql = f"""
        SELECT trade_date, stock_code, axis_trend, axis_pos, form_name,
               is_breakout_event
          FROM {STOCK_STATE_TABLE}
         WHERE trade_date IN ({placeholders})
         ORDER BY 1, 2
    """
    out: dict[str, dict[str, dict[str, Any]]] = {d: {} for d in days}
    for row in conn.execute(sql, list(days)).fetchall():
        if hasattr(row, "keys"):
            d = _norm_day(row["trade_date"])
            code = _code6(str(row["stock_code"]))
            item = {
                "axis_trend": row["axis_trend"],
                "axis_pos": row["axis_pos"],
                "form_name": row["form_name"],
                "is_breakout_event": row["is_breakout_event"],
            }
        else:
            d = _norm_day(row[0])
            code = _code6(str(row[1]))
            item = {
                "axis_trend": row[2],
                "axis_pos": row[3],
                "form_name": row[4],
                "is_breakout_event": row[5],
            }
        out.setdefault(d, {})[code] = item
    return out


def load_stock_state_by_day(
    conn,
    trading_days: Sequence[str],
    *,
    cutover_config: Tier12ConsumerCutoverConfig | Mapping[str, Any] | None = None,
    artifact_root: Path | str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load Tier1 state for the window via the Tier1/2 production-read boundary.

    Always calls ``resolve_tier12_production_read`` per day. ACCEPTED_CUTOVER
    days use accepted stock_states; LEGACY/BLOCKED days stay on
    ``fact_stock_form_daily`` (fail-closed fallback — not a dual bypass).
    """

    days = sorted({_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8})
    if not days:
        return {}

    art_root = Path(artifact_root) if artifact_root is not None else None
    out: dict[str, dict[str, dict[str, Any]]] = {d: {} for d in days}
    legacy_days: list[str] = []
    for day in days:
        read = resolve_tier12_production_read(
            day,
            config=cutover_config,
            artifact_root=art_root,
            config_path=config_path,
        )
        if (
            not read.uses_legacy
            and read.source == "accepted_partition"
            and read.accepted_payload is not None
        ):
            # CANARY_SCOPED / ACCEPTED_CUTOVER only — never silent JSON.
            out[day] = stock_states_from_accepted_payload(read.accepted_payload)
        else:
            legacy_days.append(day)

    if legacy_days:
        legacy = _load_legacy_stock_state_by_day(conn, legacy_days)
        for day, by_code in legacy.items():
            out[day] = by_code
    return out


def measure_b1_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    b0_measured: MeasuredB0Result,
    state_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
) -> MeasuredB1Result:
    """Condition B0 top-K on stock-state under the same WF plan/costs."""

    cfg = prereg or b0_measured.prereg
    plan: WalkForwardPlan = b0_measured.walk_forward
    days = list(plan.trading_days)
    coverage = measure_stock_state_coverage(days, bars_by_day, state_by_day)
    if not coverage.sufficient:
        return MeasuredB1Result(
            coverage=coverage,
            eligible_by_day={},
            measured=None,
            b0_metrics=b0_measured.metrics,
            b0_holdout_metrics=b0_measured.holdout_metrics,
            delta=None,
            claimable=False,
            reason=REASON_B1_STATE_COVERAGE_INSUFFICIENT,
            edge_gates={},
        )

    eligible = eligible_by_day_from_state(state_by_day)
    # Reuse identical walk-forward object (same folds/holdout/costs).
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
        b1_holdout=measured.holdout_metrics,
        b0_holdout=b0_measured.holdout_metrics,
    )
    reason = (
        REASON_B1_PAPER_MEASURED
        if claimable
        else measured.reason
    )
    return MeasuredB1Result(
        coverage=coverage,
        eligible_by_day={k: tuple(sorted(v)) for k, v in eligible.items()},
        measured=measured,
        b0_metrics=b0_measured.metrics,
        b0_holdout_metrics=b0_measured.holdout_metrics,
        delta=delta,
        claimable=claimable,
        reason=reason,
        edge_gates=edge.as_dict(),
    )


def open_stock_state_conn():
    """Read-only smartmoney connection for fact_stock_form_daily."""

    from services.data_access.resolver import connect_ro

    return connect_ro("smartmoney")


__all__ = [
    "DEFINITION_VERSION",
    "ELIGIBLE_TREND_VALUES",
    "MIN_AVG_BAR_STATE_OVERLAP",
    "MIN_STATE_DAY_COVERAGE",
    "REASON_B1_NO_B0_CONTEXT",
    "REASON_B1_PAPER_MEASURED",
    "REASON_B1_STATE_COVERAGE_INSUFFICIENT",
    "STOCK_STATE_TABLE",
    "B0B1DeltaMetrics",
    "MeasuredB1Result",
    "StockStateCoverage",
    "UNKNOWN",
    "eligible_by_day_from_state",
    "load_stock_state_by_day",
    "measure_b1_paper",
    "measure_stock_state_coverage",
    "open_stock_state_conn",
    "plan_walk_forward",
    "state_row_eligible",
]
