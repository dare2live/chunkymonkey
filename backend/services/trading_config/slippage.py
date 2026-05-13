"""Phase ε.1 — 交易成本 + 滑点模型 (单一职责).

⚠ 解决 audit Bug #5: 回测无成本, win_rate 虚高.

A 股标准成本结构:
  - 买入: 佣金 (券商, 一般万 2.5-3, 最低 5 元) + 过户费 (沪深, 万 0.2)
  - 卖出: 同上 + 印花税 (千 1, 仅卖)
  - 滑点: 流动性差异 (小盘股大单 50-100 bps, 大盘股 5-10 bps)

汇总: 双边约 30-50 bps (基准), 极端可达 100-200 bps.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingCostConfig:
    """单边交易成本 + 双边汇总 (bps 基点 = 万分之一)."""
    # 买入侧
    buy_commission_bps: float = 2.5     # 券商佣金 万 2.5
    buy_transfer_bps:   float = 0.2     # 过户费 万 0.2 (仅沪深)
    buy_impact_bps:     float = 5.0     # 价格冲击 (中等假设)

    # 卖出侧
    sell_commission_bps: float = 2.5
    sell_transfer_bps:   float = 0.2
    sell_stamp_duty_bps: float = 10.0   # 印花税 千 1
    sell_impact_bps:     float = 5.0

    def buy_cost_pct(self) -> float:
        """单边买入总成本 (% of trade value)."""
        return (self.buy_commission_bps + self.buy_transfer_bps + self.buy_impact_bps) / 10000.0

    def sell_cost_pct(self) -> float:
        """单边卖出总成本 (% of trade value)."""
        return (self.sell_commission_bps + self.sell_transfer_bps
                + self.sell_stamp_duty_bps + self.sell_impact_bps) / 10000.0

    def round_trip_cost_pct(self) -> float:
        """双边总成本."""
        return self.buy_cost_pct() + self.sell_cost_pct()


def apply_costs_to_return(
    raw_ret: float,
    config: TradingCostConfig,
) -> float:
    """把原始毛收益扣除双边成本.

    raw_ret = (sell - buy) / buy
    net_ret = raw_ret - round_trip_cost

    精度说明: 简化模型 (实际买卖均扣%, 但相对误差小)
    """
    return raw_ret - config.round_trip_cost_pct()
