"""Phase ε.1 — 买入价格模型 (单一职责).

⚠ 唯一负责: 给定 K 线窗口 + 信号日, 算出 T+N 买入价.

回测和实盘必须用同一函数, 避免 audit Bug #3 (买入价定义脱节).

支持模式 (BuyPricingMode):
  - vwap                 : T+1 全日 VWAP = amount / (volume × 100)  (默认, 最贴近实际)
  - open                 : T+1 开盘价
  - signal_close_plus_pct: T 日 close × (1 + pct)  (粗略估算, 用于实盘信号下发)
  - open_plus_slippage   : T+1 open × (1 + slippage)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

BuyPricingMode = Literal["vwap", "open", "signal_close_plus_pct", "open_plus_slippage"]


@dataclass(frozen=True)
class BuyPricingConfig:
    """买入价格模型参数. 改 mode 改 slippage → 所有回测 + 实盘自动同步."""
    mode: BuyPricingMode = "vwap"
    slippage_pct: float = 0.0
    signal_close_pct: float = 0.005  # 仅 signal_close_plus_pct 用


def compute_buy_price(
    signal_close: float,
    *,
    next_open: Optional[float] = None,
    next_amount: Optional[float] = None,
    next_volume: Optional[float] = None,
    config: BuyPricingConfig,
) -> Optional[float]:
    """给定 T 日 close + T+1 OHLC/amount/volume, 算买入价.

    Args:
        signal_close: T 日收盘 (signal day)
        next_open:    T+1 开盘
        next_amount:  T+1 全日成交额 (元)
        next_volume:  T+1 全日成交量 (手)
        config:       BuyPricingConfig

    Returns:
        float 买入价, 或 None (数据不足时)

    Raises:
        ValueError: config.mode 非法
    """
    if config.mode == "signal_close_plus_pct":
        if signal_close is None or signal_close <= 0:
            return None
        return signal_close * (1.0 + config.signal_close_pct)
    if config.mode == "open":
        if next_open is None or next_open <= 0:
            return None
        return next_open * (1.0 + config.slippage_pct)
    if config.mode == "open_plus_slippage":
        if next_open is None or next_open <= 0:
            return None
        return next_open * (1.0 + config.slippage_pct)
    if config.mode == "vwap":
        if next_amount is None or next_volume is None or next_volume <= 0:
            return None
        # A 股 1 手 = 100 股
        vwap = next_amount / (next_volume * 100.0)
        if vwap <= 0:
            return None
        return vwap * (1.0 + config.slippage_pct)
    raise ValueError(f"unknown buy pricing mode: {config.mode!r}")
