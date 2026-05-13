"""单测: portfolio_backtest regime/cash/liquidity/metrics"""
from __future__ import annotations
import pytest


class TestRegime:
    def test_bull(self):
        from services.portfolio_walk_forward.regime import classify_regime
        assert classify_regime(0.15) == "bull"
        assert classify_regime(0.10) == "bull"

    def test_bear(self):
        from services.portfolio_walk_forward.regime import classify_regime
        assert classify_regime(-0.15) == "bear"
        assert classify_regime(-0.10) == "bear"

    def test_sideways(self):
        from services.portfolio_walk_forward.regime import classify_regime
        assert classify_regime(0.0) == "sideways"
        assert classify_regime(0.05) == "sideways"
        assert classify_regime(-0.05) == "sideways"


class TestCashManager:
    def test_strong_buy_full_invest(self):
        from services.portfolio_walk_forward.cash_manager import dynamic_cash_pct
        assert dynamic_cash_pct(n_strong_buy=5, n_buy=20) == 0.10

    def test_strong_buy_half_cash(self):
        from services.portfolio_walk_forward.cash_manager import dynamic_cash_pct
        assert dynamic_cash_pct(n_strong_buy=3, n_buy=20) == 0.30

    def test_strong_buy_high_cash(self):
        from services.portfolio_walk_forward.cash_manager import dynamic_cash_pct
        assert dynamic_cash_pct(n_strong_buy=1, n_buy=20) == 0.60

    def test_no_strong_but_many_buy(self):
        from services.portfolio_walk_forward.cash_manager import dynamic_cash_pct
        assert dynamic_cash_pct(n_strong_buy=0, n_buy=15) == 0.50

    def test_no_signals_empty_position(self):
        from services.portfolio_walk_forward.cash_manager import dynamic_cash_pct
        assert dynamic_cash_pct(n_strong_buy=0, n_buy=5) == 0.95


class TestLiquidity:
    def test_normal_passes(self):
        from services.portfolio_walk_forward.liquidity import passes_liquidity
        ok, _ = passes_liquidity(today_amount=1e8, today_price=20, today_volume=1e6, avg_amount_20d=1e8)
        assert ok

    def test_suspended_fails(self):
        from services.portfolio_walk_forward.liquidity import passes_liquidity
        ok, reason = passes_liquidity(today_amount=0, today_price=20, today_volume=0, avg_amount_20d=1e8)
        assert not ok
        assert reason == "suspended"

    def test_high_price_fails(self):
        from services.portfolio_walk_forward.liquidity import passes_liquidity
        ok, reason = passes_liquidity(today_amount=1e8, today_price=1500, today_volume=1e6, avg_amount_20d=1e8)
        assert not ok
        assert "price" in reason

    def test_low_liquidity_fails(self):
        from services.portfolio_walk_forward.liquidity import passes_liquidity
        ok, reason = passes_liquidity(today_amount=1e7, today_price=20, today_volume=1e5, avg_amount_20d=1e7)
        assert not ok
        assert "liquidity" in reason


class TestRoundToLots:
    def test_normal(self):
        from services.portfolio_walk_forward.liquidity import round_to_lots
        # 100 万 → 50 元/股 → 应能买 20000 股 = 200 手 → 20000
        n = round_to_lots(1_000_000, 50)
        assert n == 20000

    def test_partial_lot(self):
        from services.portfolio_walk_forward.liquidity import round_to_lots
        # 6670 元 / 50 元/股 = 133.4 股 → 取整到 100 (1 手)
        n = round_to_lots(6670, 50)
        assert n == 100


class TestMetrics:
    def test_compute_basic(self):
        from services.portfolio_walk_forward.metrics import compute_metrics
        nav = [1.0, 1.05, 1.10, 1.08, 1.15, 1.20]
        m = compute_metrics(nav)
        assert m.total_return == pytest.approx(0.20, abs=0.01)
        assert m.max_drawdown < 0   # 1.08 vs 1.10 has dd
        assert m.calmar > 0

    def test_compute_dropped(self):
        from services.portfolio_walk_forward.metrics import compute_metrics
        nav = [1.0, 0.8, 0.7, 0.85]
        m = compute_metrics(nav)
        assert m.total_return < 0
        assert m.max_drawdown <= -0.3

    def test_excess_alpha(self):
        from services.portfolio_walk_forward.metrics import compute_excess_alpha
        strategy = [1.0, 1.05, 1.10, 1.20]
        benchmark = [1.0, 1.01, 1.02, 1.03]
        ex = compute_excess_alpha(strategy, benchmark)
        assert ex["excess_total_return"] > 0.15
