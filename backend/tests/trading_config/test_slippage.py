"""单测: slippage.py (交易成本)"""
from __future__ import annotations

import pytest


class TestTradingCostConfig:
    def test_default_round_trip(self):
        """默认 A 股成本: 双边 ~35 bps."""
        from services.trading_config.slippage import TradingCostConfig
        c = TradingCostConfig()
        # buy: 2.5 + 0.2 + 5.0 = 7.7 bps
        # sell: 2.5 + 0.2 + 10.0 + 5.0 = 17.7 bps
        # 双边: 25.4 bps = 0.00254
        rt = c.round_trip_cost_pct()
        assert rt == pytest.approx(0.00254, abs=1e-6)

    def test_buy_cost(self):
        from services.trading_config.slippage import TradingCostConfig
        c = TradingCostConfig()
        assert c.buy_cost_pct() == pytest.approx(0.00077, abs=1e-7)

    def test_sell_cost_includes_stamp(self):
        from services.trading_config.slippage import TradingCostConfig
        c = TradingCostConfig()
        # 印花税 10 bps + 佣金 2.5 + 过户 0.2 + impact 5.0 = 17.7
        assert c.sell_cost_pct() == pytest.approx(0.00177, abs=1e-6)

    def test_custom_impact(self):
        from services.trading_config.slippage import TradingCostConfig
        c = TradingCostConfig(buy_impact_bps=50.0, sell_impact_bps=50.0)
        # 总 impact 100 bps + 其他 25.4 = 比 default 多 90 bps
        rt = c.round_trip_cost_pct()
        assert rt > 0.01

    def test_apply_costs(self):
        from services.trading_config.slippage import TradingCostConfig, apply_costs_to_return
        c = TradingCostConfig()
        # 毛收益 5%, 扣 25.4 bps 双边 = 4.746%
        net = apply_costs_to_return(0.05, c)
        assert net == pytest.approx(0.05 - 0.00254, abs=1e-6)

    def test_winrate_inflation_explained(self):
        """audit Bug #5: 不扣成本, 一个 +25.4 bps 内的票被错误算 win.

        加成本前: 0.001 (毛收益) > 0 → win
        加成本后: 0.001 - 0.00254 < 0 → loss
        这是回测 win_rate 虚高的直接来源.
        """
        from services.trading_config.slippage import TradingCostConfig, apply_costs_to_return
        raw = 0.001
        net = apply_costs_to_return(raw, TradingCostConfig())
        assert raw > 0 and net < 0
