"""Phase ε.1 — 全局执行模型 (5 个 config 的组合).

⚠ 这是唯一对外接口. 任何脚本/服务通过:
    from services.trading_config import EXECUTION_MODEL
   获取所有交易时机参数. 改参数 = 改 registry.py 一处.

⚠ 也是 audit 5 bug 的统一解决方案 — 改 mode 全局生效, 回测和实盘必然一致.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.trading_config.buy_pricing import BuyPricingConfig
from services.trading_config.filters import LimitBoardConfig
from services.trading_config.horizon import HorizonUnit
from services.trading_config.sell_pricing import SellPricingConfig
from services.trading_config.slippage import TradingCostConfig


@dataclass(frozen=True)
class ExecutionModel:
    """所有交易时机参数的中央组合.

    版本管理: 改任何子 config → 必须升 version (用于 backtest 报告区分).
    """
    version: str
    buy_pricing: BuyPricingConfig
    sell_pricing: SellPricingConfig
    cost: TradingCostConfig
    limit_board: LimitBoardConfig
    horizon_unit: HorizonUnit = HorizonUnit.TRADING_DAYS

    def summary(self) -> dict:
        """供 UI / 调试 / report header 用."""
        return {
            "version": self.version,
            "horizon_unit": self.horizon_unit.value,
            "buy_mode": self.buy_pricing.mode,
            "buy_slippage_pct": self.buy_pricing.slippage_pct,
            "stop_loss_mode": self.sell_pricing.stop_loss_mode,
            "stop_loss_slippage_pct": self.sell_pricing.stop_loss_slippage_pct,
            "target_hit_mode": self.sell_pricing.target_hit_mode,
            "trailing_mode": self.sell_pricing.trailing_mode,
            "hp_expiry_mode": self.sell_pricing.hp_expiry_mode,
            "round_trip_cost_pct": self.cost.round_trip_cost_pct(),
            "reject_buy_one_word_limit_up": self.limit_board.reject_buy_on_limit_up_one_word,
        }
