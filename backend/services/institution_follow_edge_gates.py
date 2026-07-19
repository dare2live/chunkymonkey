"""Prereg accept edge gates for institution_follow B0/B1 paper verdicts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class _PlanProto(Protocol):
    claimable_protocol: bool
    folds: tuple[Any, ...]


class _MetricsProto(Protocol):
    total_return: float | None
    max_drawdown: float | None
    n_trades_completed: int


class _PreregProto(Protocol):
    min_folds_claimable: int
    min_trades_claimable: int
    min_holdout_net_return: float
    max_drawdown_accept: float
    min_trades_accept: int
    require_eval_total_return_positive: bool


REASON_SHORT_WINDOW = "measured_short_window_insufficient_power"
REASON_EDGE_GATES_UNMET = "accept_edge_gates_unmet"
REASON_EDGE_GATES_PASSED = "accept_edge_gates_passed"
MIN_HOLDOUT_NET_RETURN_ACCEPT = 0.0
MAX_DRAWDOWN_ACCEPT = 0.25
REQUIRE_EVAL_TOTAL_RETURN_POSITIVE = True


@dataclass(frozen=True)
class AcceptEdgeGateResult:
    passed: bool
    reason: str
    checks: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "checks": dict(self.checks),
        }


def evaluate_protocol_power(
    plan: _PlanProto,
    metrics: _MetricsProto,
    *,
    prereg: _PreregProto,
) -> tuple[bool, str]:
    if not plan.claimable_protocol:
        return False, REASON_SHORT_WINDOW
    if metrics.n_trades_completed < prereg.min_trades_claimable:
        return False, REASON_SHORT_WINDOW
    if len(plan.folds) < prereg.min_folds_claimable:
        return False, REASON_SHORT_WINDOW
    return True, "protocol_power_sufficient"


def evaluate_accept_edge_gates(
    plan: _PlanProto,
    metrics: _MetricsProto,
    holdout_metrics: _MetricsProto,
    *,
    prereg: _PreregProto,
) -> AcceptEdgeGateResult:
    """Fail closed. Protocol power first; then holdout/eval/DD/trades."""

    protocol_ok, protocol_reason = evaluate_protocol_power(
        plan, metrics, prereg=prereg
    )
    holdout_ret = holdout_metrics.total_return
    eval_ret = metrics.total_return
    max_dd = metrics.max_drawdown
    n_trades = int(metrics.n_trades_completed)
    holdout_ok = (
        holdout_ret is not None and float(holdout_ret) > prereg.min_holdout_net_return
    )
    dd_ok = max_dd is not None and float(max_dd) <= prereg.max_drawdown_accept
    trades_ok = n_trades >= prereg.min_trades_accept
    if prereg.require_eval_total_return_positive:
        eval_ok = eval_ret is not None and float(eval_ret) > 0.0
    else:
        eval_ok = eval_ret is not None
    checks = {
        "protocol_power": protocol_ok,
        "protocol_reason": protocol_reason,
        "holdout_net_return": holdout_ret,
        "holdout_net_return_gt": prereg.min_holdout_net_return,
        "holdout_ok": holdout_ok,
        "eval_total_return": eval_ret,
        "require_eval_total_return_positive": (
            prereg.require_eval_total_return_positive
        ),
        "eval_ok": eval_ok,
        "max_drawdown": max_dd,
        "max_drawdown_accept": prereg.max_drawdown_accept,
        "drawdown_ok": dd_ok,
        "n_trades_completed": n_trades,
        "min_trades_accept": prereg.min_trades_accept,
        "trades_ok": trades_ok,
    }
    if not protocol_ok:
        return AcceptEdgeGateResult(
            passed=False, reason=REASON_SHORT_WINDOW, checks=checks
        )
    if holdout_ok and dd_ok and trades_ok and eval_ok:
        return AcceptEdgeGateResult(
            passed=True, reason=REASON_EDGE_GATES_PASSED, checks=checks
        )
    return AcceptEdgeGateResult(
        passed=False, reason=REASON_EDGE_GATES_UNMET, checks=checks
    )


__all__ = [
    "AcceptEdgeGateResult",
    "MAX_DRAWDOWN_ACCEPT",
    "MIN_HOLDOUT_NET_RETURN_ACCEPT",
    "REASON_EDGE_GATES_PASSED",
    "REASON_EDGE_GATES_UNMET",
    "REASON_SHORT_WINDOW",
    "REQUIRE_EVAL_TOTAL_RETURN_POSITIVE",
    "evaluate_accept_edge_gates",
    "evaluate_protocol_power",
]
