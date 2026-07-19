"""Measured B0 bare-K WF + paper fills (short-window honest minimal protocol)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Sequence

from services.institution_follow_edge_gates import (
    MAX_DRAWDOWN_ACCEPT,
    MIN_HOLDOUT_NET_RETURN_ACCEPT,
    REASON_EDGE_GATES_PASSED,
    REASON_EDGE_GATES_UNMET,
    REASON_SHORT_WINDOW,
    REQUIRE_EVAL_TOTAL_RETURN_POSITIVE,
    AcceptEdgeGateResult,
    evaluate_accept_edge_gates,
    evaluate_protocol_power,
)
from services.institution_follow_nominal_bars import load_nominal_bars_by_day
from services.universe import ACTIVE_A_SHARE_PREFIXES

ProtocolKind = Literal["purged_walk_forward", "honest_minimal_short_window"]
UNKNOWN = "unknown"
LABEL_HORIZON_DAYS = 1
EMBARGO_DAYS = 1
IN_WINDOW_HOLDOUT_DAYS = 2
MIN_DAYS_FULL_PURGED_WF = 40
MIN_FOLDS_CLAIMABLE = 3
MIN_TRADES_CLAIMABLE = 30
TOP_K = 5
COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.0005
BOARD_PREFIXES = tuple(ACTIVE_A_SHARE_PREFIXES)
REASON_PAPER_MEASURED = "measured_b0_paper_short_window"


@dataclass(frozen=True)
class B0Prereg:
    signal: str = "cross_sectional_1d_momentum_top_k"
    label_horizon_days: int = LABEL_HORIZON_DAYS
    embargo_days: int = EMBARGO_DAYS
    in_window_holdout_days: int = IN_WINDOW_HOLDOUT_DAYS
    min_days_full_purged_wf: int = MIN_DAYS_FULL_PURGED_WF
    min_folds_claimable: int = MIN_FOLDS_CLAIMABLE
    min_trades_claimable: int = MIN_TRADES_CLAIMABLE
    top_k: int = TOP_K
    commission_rate: float = COMMISSION_RATE
    stamp_tax_rate: float = STAMP_TAX_RATE
    slippage_rate: float = SLIPPAGE_RATE
    entry: str = "t1_nominal_open"
    exit: str = "t2_nominal_open"
    # 0 = no chase (B0/B1/B2 default). B4 sets max_chase_days per §8.1.
    max_chase_days: int = 0
    board_prefixes: tuple[str, ...] = BOARD_PREFIXES
    min_holdout_net_return: float = MIN_HOLDOUT_NET_RETURN_ACCEPT
    max_drawdown_accept: float = MAX_DRAWDOWN_ACCEPT
    min_trades_accept: int = MIN_TRADES_CLAIMABLE
    require_eval_total_return_positive: bool = REQUIRE_EVAL_TOTAL_RETURN_POSITIVE

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["board_prefixes"] = list(self.board_prefixes)
        d["capacity_model"] = UNKNOWN
        d["suspend_limit_model"] = "stub_with_optional_chase"
        d["accept_edge_gates"] = {
            "min_holdout_net_return_exclusive": self.min_holdout_net_return,
            "max_drawdown_accept": self.max_drawdown_accept,
            "min_trades_accept": self.min_trades_accept,
            "require_eval_total_return_positive": (
                self.require_eval_total_return_positive
            ),
        }
        return d


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    train_dates: tuple[str, ...]
    embargo_dates: tuple[str, ...]
    eval_dates: tuple[str, ...]
    role: str

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("train_dates", "embargo_dates", "eval_dates"):
            d[key] = list(getattr(self, key))
        return d


@dataclass(frozen=True)
class WalkForwardPlan:
    protocol: ProtocolKind
    claimable_protocol: bool
    reason: str
    trading_days: tuple[str, ...]
    folds: tuple[WalkForwardFold, ...]
    holdout_dates: tuple[str, ...]
    embargo_days: int
    label_horizon_days: int
    one_touch_holdout: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "claimable_protocol": self.claimable_protocol,
            "reason": self.reason,
            "trading_days": list(self.trading_days),
            "folds": [f.as_dict() for f in self.folds],
            "holdout_dates": list(self.holdout_dates),
            "embargo_days": self.embargo_days,
            "label_horizon_days": self.label_horizon_days,
            "one_touch_holdout": self.one_touch_holdout,
        }


@dataclass(frozen=True)
class PaperFillRecord:
    signal_date: str
    entry_date: str
    exit_date: str
    ts_code: str
    entry_px: float | None
    exit_px: float | None
    gross_return: float | None
    net_return: float | None
    status: str
    fold_role: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BareKPaperMetrics:
    total_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    payoff_ratio: float | None
    turnover: float | None
    n_signals: int
    n_trades_completed: int
    n_unfilled: int
    n_incomplete_exit: int
    annualized_return: Any = UNKNOWN
    sharpe: Any = UNKNOWN
    capacity: Any = UNKNOWN
    excess_return: Any = UNKNOWN
    stability_by_year: Any = UNKNOWN
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["details"] = dict(self.details)
        return d


@dataclass(frozen=True)
class MeasuredB0Result:
    prereg: B0Prereg
    walk_forward: WalkForwardPlan
    fills: tuple[PaperFillRecord, ...]
    metrics: BareKPaperMetrics
    holdout_metrics: BareKPaperMetrics
    claimable: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "prereg": self.prereg.as_dict(),
            "walk_forward": self.walk_forward.as_dict(),
            "fills": [f.as_dict() for f in self.fills],
            "metrics": self.metrics.as_dict(),
            "holdout_metrics": self.holdout_metrics.as_dict(),
            "claimable": self.claimable,
            "reason": self.reason,
            "paper_fills": "measured",
        }


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _board_ok(ts_code: str, prefixes: Sequence[str]) -> bool:
    code = str(ts_code or "")
    digits = code.split(".", 1)[0]
    return len(digits) >= 2 and digits[:2] in set(prefixes)


def plan_walk_forward(
    trading_days: Sequence[str],
    *,
    prereg: B0Prereg | None = None,
) -> WalkForwardPlan:
    """Build purged WF plan, or honest minimal protocol for short windows."""

    cfg = prereg or B0Prereg()
    days = tuple(_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8)
    days = tuple(sorted(set(days)))
    n = len(days)
    horizon = cfg.label_horizon_days
    embargo = max(cfg.embargo_days, horizon)
    holdout_n = cfg.in_window_holdout_days

    if n < cfg.min_days_full_purged_wf:
        need = holdout_n + embargo + horizon + 2
        if n < need:
            return WalkForwardPlan(
                protocol="honest_minimal_short_window",
                claimable_protocol=False,
                reason=REASON_SHORT_WINDOW,
                trading_days=days,
                folds=(),
                holdout_dates=days[-min(holdout_n, n) :] if n else (),
                embargo_days=embargo,
                label_horizon_days=horizon,
                one_touch_holdout=True,
            )
        holdout = days[-holdout_n:]
        embargo_dates = days[-(holdout_n + embargo) : -holdout_n]
        train = days[: -(holdout_n + embargo)]
        eval_cut = len(train) - (horizon + 1)
        eval_dates = train[: max(eval_cut, 0)]
        fold = WalkForwardFold(
            fold_id="short_window_fold_0",
            train_dates=train,
            embargo_dates=embargo_dates,
            eval_dates=tuple(eval_dates),
            role="expanding_eval",
        )
        holdout_fold = WalkForwardFold(
            fold_id="short_window_holdout",
            train_dates=train,
            embargo_dates=embargo_dates,
            eval_dates=holdout,
            role="one_touch_holdout",
        )
        return WalkForwardPlan(
            protocol="honest_minimal_short_window",
            claimable_protocol=False,
            reason=REASON_SHORT_WINDOW,
            trading_days=days,
            folds=(fold, holdout_fold),
            holdout_dates=holdout,
            embargo_days=embargo,
            label_horizon_days=horizon,
            one_touch_holdout=True,
        )

    holdout = days[-holdout_n:]
    body = days[:-holdout_n]
    body_n = len(body)
    eval_len = max(horizon, 1)
    last_train_end = body_n - embargo - eval_len
    folds: list[WalkForwardFold] = []
    seen_train_ends: set[int] = set()
    for train_end in (
        max(body_n // 3, horizon + 2),
        max(2 * body_n // 3, horizon + 2),
        last_train_end,
    ):
        train_end = min(max(train_end, horizon + 2), last_train_end)
        if train_end in seen_train_ends or train_end < horizon + 2:
            continue
        seen_train_ends.add(train_end)
        train = body[:train_end]
        emb = body[train_end : train_end + embargo]
        eval_dates = body[train_end + embargo : train_end + embargo + eval_len]
        if train and eval_dates:
            folds.append(
                WalkForwardFold(
                    fold_id=f"purged_fold_{len(folds)}",
                    train_dates=tuple(train),
                    embargo_dates=tuple(emb),
                    eval_dates=tuple(eval_dates),
                    role="purged_eval",
                )
            )
    ok = len(folds) >= cfg.min_folds_claimable
    return WalkForwardPlan(
        protocol="purged_walk_forward",
        claimable_protocol=ok,
        reason="purged_walk_forward_ready" if ok else REASON_SHORT_WINDOW,
        trading_days=days,
        folds=tuple(folds),
        holdout_dates=holdout,
        embargo_days=embargo,
        label_horizon_days=horizon,
        one_touch_holdout=True,
    )


from services.institution_follow_paper import (
    is_limit_down_open,
    is_limit_up_open,
    is_suspended,
    limit_up_pct,
    metrics_from_fills as _metrics_from_fills,
    simulate_paper_fills,
)

def evaluate_claimable(
    plan: WalkForwardPlan,
    metrics: BareKPaperMetrics,
    *,
    prereg: B0Prereg | None = None,
) -> tuple[bool, str]:
    """Protocol-power gate only — not accept. See ``evaluate_accept_edge_gates``."""

    return evaluate_protocol_power(plan, metrics, prereg=prereg or B0Prereg())


def measure_b0_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    trading_days: Sequence[str] | None = None,
    *,
    prereg: B0Prereg | None = None,
    st_codes: Sequence[str] | None = None,
    eligible_by_day: Mapping[str, set[str]] | None = None,
    walk_forward: WalkForwardPlan | None = None,
) -> MeasuredB0Result:
    cfg = prereg or B0Prereg()
    days = (
        list(trading_days)
        if trading_days is not None
        else sorted({_norm_day(d) for d in bars_by_day})
    )
    plan = walk_forward or plan_walk_forward(days, prereg=cfg)
    fills = simulate_paper_fills(
        bars_by_day,
        plan,
        prereg=cfg,
        st_codes=st_codes,
        eligible_by_day=eligible_by_day,
    )
    metrics = _metrics_from_fills(
        fills, trading_day_count=len(plan.trading_days), top_k=cfg.top_k
    )
    holdout_fills = tuple(
        f for f in fills if f.fold_role == "one_touch_holdout"
    )
    holdout_metrics = _metrics_from_fills(
        holdout_fills,
        trading_day_count=len(plan.holdout_dates) or 1,
        top_k=cfg.top_k,
    )
    claimable, reason = evaluate_claimable(plan, metrics, prereg=cfg)
    if not claimable:
        reason = REASON_SHORT_WINDOW
    else:
        reason = REASON_PAPER_MEASURED
    return MeasuredB0Result(
        prereg=cfg,
        walk_forward=plan,
        fills=fills,
        metrics=metrics,
        holdout_metrics=holdout_metrics,
        claimable=claimable,
        reason=reason,
    )


__all__ = [
    "BOARD_PREFIXES",
    "B0Prereg",
    "AcceptEdgeGateResult",
    "BareKPaperMetrics",
    "EMBARGO_DAYS",
    "LABEL_HORIZON_DAYS",
    "MAX_DRAWDOWN_ACCEPT",
    "MIN_DAYS_FULL_PURGED_WF",
    "MIN_HOLDOUT_NET_RETURN_ACCEPT",
    "MIN_TRADES_CLAIMABLE",
    "MeasuredB0Result",
    "PaperFillRecord",
    "REASON_EDGE_GATES_PASSED",
    "REASON_EDGE_GATES_UNMET",
    "REASON_PAPER_MEASURED",
    "REASON_SHORT_WINDOW",
    "TOP_K",
    "UNKNOWN",
    "WalkForwardFold",
    "WalkForwardPlan",
    "evaluate_accept_edge_gates",
    "evaluate_claimable",
    "is_limit_down_open",
    "is_limit_up_open",
    "is_suspended",
    "limit_up_pct",
    "load_nominal_bars_by_day",
    "measure_b0_paper",
    "plan_walk_forward",
    "simulate_paper_fills",
]
