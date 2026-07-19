"""Prereg accept edge gates for institution_follow B0/B1/B2/B4 paper verdicts."""
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
REASON_HOLDOUT_LIFT_UNMET = "holdout_lift_vs_b0_unmet"
MIN_HOLDOUT_NET_RETURN_ACCEPT = 0.0
MAX_DRAWDOWN_ACCEPT = 0.25
REQUIRE_EVAL_TOTAL_RETURN_POSITIVE = True
# Cheap short-window stability: claimable accept needs strict holdout lift vs B0.
REQUIRE_HOLDOUT_LIFT_VS_B0 = True


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


@dataclass(frozen=True)
class HoldoutLiftStabilityResult:
    """Strict holdout lift vs B0 — challenges short-window coincidental accepts."""

    passed: bool
    reason: str
    checks: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "checks": dict(self.checks),
            "require_holdout_lift_vs_b0": REQUIRE_HOLDOUT_LIFT_VS_B0,
        }


def evaluate_holdout_lift_vs_b0(
    block_holdout: _MetricsProto,
    b0_holdout: _MetricsProto | None,
    *,
    require_strict_lift: bool = REQUIRE_HOLDOUT_LIFT_VS_B0,
) -> HoldoutLiftStabilityResult:
    """Fail closed when block holdout return does not strictly beat B0 holdout.

    Equal holdout (common when the block does not change holdout-day
    eligibility) is **not** independent lift — reject claimable accept.
    """

    block_ret = block_holdout.total_return
    b0_ret = b0_holdout.total_return if b0_holdout is not None else None
    checks = {
        "block_holdout_total_return": block_ret,
        "b0_holdout_total_return": b0_ret,
        "require_strict_lift": require_strict_lift,
        "b0_holdout_present": b0_holdout is not None,
    }
    if not require_strict_lift:
        return HoldoutLiftStabilityResult(
            passed=True,
            reason="holdout_lift_gate_disabled",
            checks=checks,
        )
    if b0_holdout is None or b0_ret is None or block_ret is None:
        checks["lift"] = None
        return HoldoutLiftStabilityResult(
            passed=False,
            reason=REASON_HOLDOUT_LIFT_UNMET,
            checks=checks,
        )
    lift = float(block_ret) - float(b0_ret)
    checks["lift"] = lift
    checks["lift_ok"] = lift > 0.0
    if lift > 0.0:
        return HoldoutLiftStabilityResult(
            passed=True,
            reason="holdout_lift_vs_b0_passed",
            checks=checks,
        )
    return HoldoutLiftStabilityResult(
        passed=False,
        reason=REASON_HOLDOUT_LIFT_UNMET,
        checks=checks,
    )


__all__ = [
    "AcceptEdgeGateResult",
    "HoldoutLiftStabilityResult",
    "MAX_DRAWDOWN_ACCEPT",
    "MIN_HOLDOUT_NET_RETURN_ACCEPT",
    "REASON_EDGE_GATES_PASSED",
    "REASON_EDGE_GATES_UNMET",
    "REASON_HOLDOUT_LIFT_UNMET",
    "REASON_SHORT_WINDOW",
    "REQUIRE_EVAL_TOTAL_RETURN_POSITIVE",
    "REQUIRE_HOLDOUT_LIFT_VS_B0",
    "evaluate_accept_edge_gates",
    "evaluate_holdout_lift_vs_b0",
    "evaluate_protocol_power",
]
