"""单测: slippage.py (交易成本) — default-free + yaml 单一真相源.

2026-06-11 成本双源收敛收尾: TradingCostConfig 不再携带默认值 (旧默认印花税
10bps 是 2023-08 减半前的失真值), 数值唯一来源 = paper_sim_config.yaml::tx_cost
经 registry._cost_from_paper_sim() 派生.
"""
from __future__ import annotations

import pytest


def _explicit_cost(**overrides) -> "TradingCostConfig":
    """测试用显式构造 (default-free 后唯一合法构造方式), 行为测试与数值真相解耦."""
    from services.trading_config.slippage import TradingCostConfig

    base = dict(
        buy_commission_bps=2.5,
        buy_transfer_bps=0.641,
        buy_impact_bps=8.0,
        sell_commission_bps=2.5,
        sell_transfer_bps=0.641,
        sell_stamp_duty_bps=5.0,
        sell_impact_bps=8.0,
    )
    base.update(overrides)
    return TradingCostConfig(**base)


class TestTradingCostConfig:
    def test_default_free_bare_construction_rejected(self):
        """防回退核心: 裸 TradingCostConfig() 必须失败 — 不允许第二真相源."""
        from services.trading_config.slippage import TradingCostConfig

        with pytest.raises(TypeError):
            TradingCostConfig()

    def test_yaml_derived_round_trip_matches_cost_after(self):
        """registry 派生值与 labels.cost_after 口径一致 (单一真相源闭环).

        # evidence: paper_sim_config.yaml tx_cost — commission 0.00025×2 +
        #   (transfer 0.00001 + exchange 0.0000341 + regulatory 0.00002)×2 +
        #   slippage 0.0008×2 + stamp_sell 0.0005 = 0.0027282
        # 交叉证据: build_p0a_label_panel 实跑日志 round_trip_cost_pct=0.002728
        """
        from services.trading_config.registry import _cost_from_paper_sim

        c = _cost_from_paper_sim()
        assert c.round_trip_cost_pct() == pytest.approx(0.0027282, abs=1e-6)

    def test_yaml_derived_stamp_is_5bps(self):
        """印花税 = 现行税率 5bps (2023-08 减半), 不是旧默认 10bps.

        # evidence: paper_sim_config.yaml tx_cost.stamp_duty_sell_pct=0.0005
        """
        from services.trading_config.registry import _cost_from_paper_sim

        c = _cost_from_paper_sim()
        assert c.sell_stamp_duty_bps == pytest.approx(5.0, abs=1e-9)
        # 卖侧含印花 > 买侧 (其余对称)
        assert c.sell_cost_pct() - c.buy_cost_pct() == pytest.approx(0.0005, abs=1e-9)

    def test_custom_impact(self):
        c = _explicit_cost(buy_impact_bps=50.0, sell_impact_bps=50.0)
        rt = c.round_trip_cost_pct()
        assert rt > 0.01

    def test_apply_costs(self):
        from services.trading_config.slippage import apply_costs_to_return

        c = _explicit_cost()
        # 毛收益 5%, 扣 27.282 bps 双边
        net = apply_costs_to_return(0.05, c)
        assert net == pytest.approx(0.05 - c.round_trip_cost_pct(), abs=1e-9)

    def test_winrate_inflation_explained(self):
        """audit Bug #5: 不扣成本, 一个 +27 bps 内的票被错误算 win."""
        from services.trading_config.slippage import apply_costs_to_return

        raw = 0.001
        net = apply_costs_to_return(raw, _explicit_cost())
        assert raw > 0 and net < 0
