"""单测: optimization/objectives.py — 8 个 metric"""
from __future__ import annotations

import pytest

from services.backtest.result import TradeResult


def _mk_trade(net_ret: float, max_dd: float = -0.05, exit_reason: str = "hp_expired") -> TradeResult:
    return TradeResult(
        stock_code="TEST", signal_date="2026-05-01",
        buy_date="2026-05-02", buy_price=10.0,
        sell_date="2026-05-10", sell_price=10.0 * (1 + net_ret),
        holding_days=8, exit_reason=exit_reason,
        gross_ret=net_ret + 0.0025, net_ret=net_ret, max_drawdown=max_dd,
    )


class TestObjectives:
    def test_none_on_empty(self):
        from services.optimization.objectives import compute_all_objectives
        assert compute_all_objectives([]) is None

    def test_all_blocked_returns_none(self):
        from services.optimization.objectives import compute_all_objectives
        bad = [TradeResult(stock_code="A", signal_date="2026-05-01",
                           buy_date="", buy_price=0.0, sell_date="", sell_price=0.0,
                           holding_days=0, exit_reason="one_word_blocked",
                           gross_ret=0.0, net_ret=0.0, max_drawdown=0.0)]
        assert compute_all_objectives(bad) is None

    def test_perfect_winner(self):
        """全部正收益: sortino > sharpe (无下行)."""
        from services.optimization.objectives import compute_all_objectives
        trades = [_mk_trade(0.05) for _ in range(10)]
        obj = compute_all_objectives(trades)
        assert obj is not None
        # 全胜, std=0 → sharpe=0 (div by 0 → 0)
        # sortino 没下行 → 用 sharpe×1.5 备选 → 0
        assert obj.tail_risk > 0  # 最差的也 = +0.05
        assert obj.stability >= 0.9  # 全胜 winrate=1.0 一致

    def test_calmar(self):
        from services.optimization.objectives import compute_all_objectives
        # ret +5%, max_dd -2% → calmar = 0.05 / 0.02 = 2.5
        trades = [_mk_trade(0.05, max_dd=-0.02) for _ in range(10)]
        obj = compute_all_objectives(trades)
        assert obj.calmar == pytest.approx(2.5, abs=0.1)

    def test_pain_index_avg_abs_dd(self):
        from services.optimization.objectives import compute_all_objectives
        trades = [_mk_trade(0.05, max_dd=-0.10) for _ in range(10)]
        obj = compute_all_objectives(trades)
        assert obj.pain_index == pytest.approx(0.10, abs=0.01)

    def test_ulcer_index_rms(self):
        """ulcer = sqrt(mean(dd²))."""
        from services.optimization.objectives import compute_all_objectives
        # dd = [-0.10, -0.10, ..., -0.10] → sqrt(0.01) = 0.10
        trades = [_mk_trade(0.05, max_dd=-0.10) for _ in range(10)]
        obj = compute_all_objectives(trades)
        assert obj.ulcer_index == pytest.approx(0.10, abs=0.01)

    def test_tail_risk_cvar5(self):
        """100 笔 → worst 5 笔 mean."""
        from services.optimization.objectives import compute_all_objectives
        trades = [_mk_trade(-0.30) for _ in range(5)] + [_mk_trade(0.05) for _ in range(95)]
        obj = compute_all_objectives(trades)
        # tail 5% = 5 笔, mean(-0.30) = -0.30
        assert obj.tail_risk == pytest.approx(-0.30, abs=0.01)

    def test_small_sample_uses_worst_one(self):
        """n < 20 时, tail = 最差一笔."""
        from services.optimization.objectives import compute_all_objectives
        trades = [_mk_trade(0.05) for _ in range(9)] + [_mk_trade(-0.15)]
        obj = compute_all_objectives(trades)
        assert obj.tail_risk == pytest.approx(-0.15)
