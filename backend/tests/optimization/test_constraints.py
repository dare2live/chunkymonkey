"""单测: optimization/constraints.py — 硬约束"""
from __future__ import annotations

import pytest

from services.backtest.result import TradeResult


def _mk_trade(net_ret: float, max_dd: float = -0.05) -> TradeResult:
    return TradeResult(
        stock_code="A", signal_date="2026-05-01",
        buy_date="2026-05-02", buy_price=10.0,
        sell_date="2026-05-10", sell_price=10.0 * (1 + net_ret),
        holding_days=8, exit_reason="hp_expired",
        gross_ret=net_ret, net_ret=net_ret, max_drawdown=max_dd,
    )


class TestPassesHardConstraints:
    def test_normal_trades_pass(self):
        from services.optimization.constraints import passes_hard_constraints
        trades = [_mk_trade(0.05, -0.05) for _ in range(10)]
        ok, reason = passes_hard_constraints(trades)
        assert ok is True
        assert reason is None

    def test_empty_fails(self):
        from services.optimization.constraints import passes_hard_constraints
        ok, reason = passes_hard_constraints([])
        assert ok is False
        assert reason == "no_trades"

    def test_too_few_trades_fails(self):
        from services.optimization.constraints import passes_hard_constraints
        trades = [_mk_trade(0.05) for _ in range(2)]
        ok, reason = passes_hard_constraints(trades)
        assert ok is False
        assert "min_traded" in reason

    def test_deep_dd_fails(self):
        """avg max_dd < -25% → reject."""
        from services.optimization.constraints import passes_hard_constraints
        trades = [_mk_trade(0.05, -0.30) for _ in range(10)]
        ok, reason = passes_hard_constraints(trades)
        assert ok is False
        assert "avg_dd" in reason

    def test_worst_single_loss_fails(self):
        from services.optimization.constraints import passes_hard_constraints
        trades = [_mk_trade(0.05) for _ in range(9)] + [_mk_trade(-0.35)]
        ok, reason = passes_hard_constraints(trades)
        assert ok is False
        assert "worst_loss" in reason

    def test_loss_streak_fails(self):
        """连续 6 笔亏损 → reject."""
        from services.optimization.constraints import passes_hard_constraints
        trades = ([_mk_trade(0.05) for _ in range(3)] +
                  [_mk_trade(-0.05) for _ in range(6)] +
                  [_mk_trade(0.10) for _ in range(3)])
        ok, reason = passes_hard_constraints(trades)
        assert ok is False
        assert "loss_streak" in reason

    def test_custom_constraints(self):
        """可注入自定义约束."""
        from services.optimization.constraints import passes_hard_constraints, HardConstraints
        relax = HardConstraints(max_acceptable_drawdown=-0.50,
                                worst_single_loss=-0.50,
                                max_loss_streak=10, min_traded=2)
        trades = [_mk_trade(0.05, -0.35) for _ in range(5)]
        ok, reason = passes_hard_constraints(trades, relax)
        assert ok is True
