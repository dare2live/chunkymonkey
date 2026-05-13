"""Phase ε.1 — trading_config package.

唯一对外接口:

    from services.trading_config import EXECUTION_MODEL
    # 用 EXECUTION_MODEL.buy_pricing / .sell_pricing / .cost / .limit_board / .horizon_unit

    from services.trading_config.buy_pricing import compute_buy_price
    from services.trading_config.sell_pricing import compute_sell_price
    from services.trading_config.horizon import add_holding_period, count_holding_period
    from services.trading_config.slippage import apply_costs_to_return
    from services.trading_config.filters import (
        is_limit_up, is_one_word_limit_up, infer_board, is_suspended,
    )

⚠ 任何业务脚本不允许自建 BuyPricingConfig / ExecutionModel 等 — 必须用此处导出.
"""
from services.trading_config.execution_model import ExecutionModel
from services.trading_config.registry import DEFAULT_EXECUTION_MODEL, get_execution_model

# 别名 (语义清晰)
EXECUTION_MODEL = DEFAULT_EXECUTION_MODEL

__all__ = ["EXECUTION_MODEL", "ExecutionModel", "get_execution_model"]
