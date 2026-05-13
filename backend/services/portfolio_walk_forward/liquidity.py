"""Phase π.3c — 流动性 + 资金量过滤.

⚠ 实盘约束:
  - 日均成交 < 5000 万 → 流动性差, 大资金推高价格
  - 单股价格 > 1000 元 → 100 万 / 15 股 = 6.67 万, 不够买一手
  - 当日停牌 → 跳过
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LiquidityConfig:
    min_avg_amount_yuan:   float = 50_000_000   # 日均成交额 ≥ 5000 万
    max_price_per_share:   float = 500.0        # 单股 ≤ 500 元 (100 万 ÷ 15 ÷ 100 股)
    require_today_traded:  bool  = True         # 当日必须有成交


def passes_liquidity(
    today_amount: Optional[float],
    today_price: Optional[float],
    today_volume: Optional[float],
    avg_amount_20d: Optional[float],
    config: LiquidityConfig = LiquidityConfig(),
) -> tuple[bool, Optional[str]]:
    """实盘可买性检查.

    Returns:
        (passes: bool, reject_reason: str or None)
    """
    if config.require_today_traded and (not today_volume or today_volume <= 0):
        return False, "suspended"
    if today_price is None or today_price <= 0:
        return False, "no_price"
    if today_price > config.max_price_per_share:
        return False, f"price>{config.max_price_per_share}"
    if avg_amount_20d is not None and avg_amount_20d < config.min_avg_amount_yuan:
        return False, f"liquidity<{config.min_avg_amount_yuan/1e6:.0f}M"
    return True, None


def round_to_lots(target_cny: float, price: float, lot_size: int = 100) -> int:
    """A 股最少一手 (100 股). 算出能买几手 (整手)."""
    if price <= 0:
        return 0
    n_shares = target_cny / price
    n_lots = int(n_shares // lot_size)
    return n_lots * lot_size
