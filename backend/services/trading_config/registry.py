"""Phase ε.1 — 全局执行模型实例 (一处声明).

⚠ 改参数: 改本文件的 DEFAULT_EXECUTION_MODEL.
⚠ 不要在业务代码里自建 ExecutionModel — 永远用这里的全局实例.
"""
from __future__ import annotations

from services.trading_config.buy_pricing import BuyPricingConfig
from services.trading_config.execution_model import ExecutionModel
from services.trading_config.filters import LimitBoardConfig
from services.trading_config.horizon import HorizonUnit
from services.trading_config.sell_pricing import SellPricingConfig
from services.trading_config.slippage import TradingCostConfig


# ─────────────────────────────────────────────────────────────────────
# 全局默认执行模型 — 改这里全局生效
# ─────────────────────────────────────────────────────────────────────

DEFAULT_EXECUTION_MODEL = ExecutionModel(
    version="ε.1-2026-05-12",

    # 买入: T+1 VWAP (= amount / volume×100), 不加额外滑点
    # 选 vwap 而非 open: 因为 open 受当日跳空影响, VWAP 更代表实际平均成交价
    buy_pricing=BuyPricingConfig(
        mode="vwap",
        slippage_pct=0.0,
    ),

    # 卖出执行
    sell_pricing=SellPricingConfig(
        # 止损: 触发价 - 0.3% (保守, 实际可能跌穿)
        stop_loss_mode="trigger_with_slippage",
        stop_loss_slippage_pct=-0.003,
        # 止盈: target_price 直接成交 (保守, 实际常涨更多)
        target_hit_mode="target_price",
        # Trailing: 当日 close
        trailing_mode="close",
        # 持仓到期: 收盘卖
        hp_expiry_mode="close",
    ),

    # 交易成本: A 股标准 (双边约 35 bps)
    cost=TradingCostConfig(
        buy_commission_bps=2.5,
        buy_transfer_bps=0.2,
        buy_impact_bps=5.0,
        sell_commission_bps=2.5,
        sell_transfer_bps=0.2,
        sell_stamp_duty_bps=10.0,
        sell_impact_bps=5.0,
    ),

    # 涨跌停过滤
    limit_board=LimitBoardConfig(
        reject_buy_on_limit_up_one_word=True,
        allow_sell_through_limit_down=True,
    ),

    # 持仓周期单位: 交易日
    horizon_unit=HorizonUnit.TRADING_DAYS,
)


def get_execution_model() -> ExecutionModel:
    """全局唯一获取入口 (便于测试 monkey patch)."""
    return DEFAULT_EXECUTION_MODEL
