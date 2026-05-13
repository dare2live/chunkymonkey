"""Phase ε.1 — 卖出价格模型 (单一职责).

⚠ 唯一负责: 给定出场原因 + 当日 OHLC, 算出实际卖出价.

不同出场原因 (stop_loss / trailing / target / hp_expired) 用不同执行价格:
  - stop_loss     : 当日 low ≤ stop_price 触发 → 按 stop_price 成交 (假设限价单挂住)
                    或加 slippage_pct (反向, 因实际可能跌穿)
  - trailing_stop : 当日 close 较 high_since_buy 回撤超阈值 → close 成交
  - target_hit    : 当日 high ≥ target_price 触发 → target_price 成交
                    实际上常常涨更多, 这是保守估计
  - hp_expired    : 持仓到期 → 当日 close 成交
  - one_word_limit: 当日一字涨停/跌停, 无法成交 → 延迟下一日 (None 返回)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


ExitReason = Literal["stop_loss", "trailing_stop", "target_hit", "hp_expired", "one_word_limit"]
StopLossMode = Literal["trigger_price", "trigger_with_slippage", "next_open"]
TargetHitMode = Literal["target_price", "high", "close"]
TrailingMode = Literal["close", "intraday_high_drawdown"]
HpExpiryMode = Literal["close", "next_open"]


@dataclass(frozen=True)
class SellPricingConfig:
    """卖出执行模型."""
    # 止损执行
    stop_loss_mode: StopLossMode = "trigger_with_slippage"
    stop_loss_slippage_pct: float = -0.003  # 负值 = 跌破触发价多 0.3% (保守)

    # 止盈触发
    target_hit_mode: TargetHitMode = "target_price"

    # Trailing
    trailing_mode: TrailingMode = "close"

    # 持仓到期
    hp_expiry_mode: HpExpiryMode = "close"


def compute_sell_price(
    reason: ExitReason,
    *,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    today_open: Optional[float] = None,
    today_high: Optional[float] = None,
    today_low: Optional[float] = None,
    today_close: Optional[float] = None,
    next_open: Optional[float] = None,
    config: SellPricingConfig,
) -> Optional[float]:
    """根据出场原因 + 当日 OHLC, 计算实际卖出价.

    Returns:
        float 卖出价, 或 None (一字板等无法成交)
    """
    if reason == "stop_loss":
        if config.stop_loss_mode == "trigger_price":
            return stop_price
        if config.stop_loss_mode == "trigger_with_slippage":
            if stop_price is None:
                return None
            return stop_price * (1.0 + config.stop_loss_slippage_pct)
        if config.stop_loss_mode == "next_open":
            return next_open
        raise ValueError(f"unknown stop_loss_mode: {config.stop_loss_mode!r}")

    if reason == "target_hit":
        if config.target_hit_mode == "target_price":
            return target_price
        if config.target_hit_mode == "high":
            return today_high
        if config.target_hit_mode == "close":
            return today_close
        raise ValueError(f"unknown target_hit_mode: {config.target_hit_mode!r}")

    if reason == "trailing_stop":
        if config.trailing_mode == "close":
            return today_close
        if config.trailing_mode == "intraday_high_drawdown":
            return today_close   # 简化: trailing 用当日 close
        raise ValueError(f"unknown trailing_mode: {config.trailing_mode!r}")

    if reason == "hp_expired":
        if config.hp_expiry_mode == "close":
            return today_close
        if config.hp_expiry_mode == "next_open":
            return next_open
        raise ValueError(f"unknown hp_expiry_mode: {config.hp_expiry_mode!r}")

    if reason == "one_word_limit":
        return None  # 一字板无法成交, 调用方延迟

    raise ValueError(f"unknown exit reason: {reason!r}")
