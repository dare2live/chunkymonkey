"""Phase η++++++ — K 线形态特征 (纯函数, 单一职责).

⚠ 6 个连续特征 + 1 个 N-bar 突破强度.
⚠ 不命名"锤子线 / 十字星" — 让数据决定什么形态有 alpha.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class CandleFeatures:
    """单 bar 6 维特征 + 信号日特殊指标."""
    body_ratio:           float   # |close - open| / (high - low)
    upper_shadow_ratio:   float   # (high - max(open,close)) / (high - low)
    lower_shadow_ratio:   float   # (min(open,close) - low) / (high - low)
    close_position:       float   # (close - low) / (high - low)  [0,1]
    volume_relative:      float   # vol / vol_ma20
    breakout_strength_20: float   # (close - max(close[-20:])) / max[-20:]
    # 派生 binary 指标 (用于 reasoning)
    is_bullish:           bool    # close > open
    is_doji:              bool    # body_ratio < 0.1
    is_long_lower_shadow: bool    # lower_shadow_ratio > 0.6
    is_long_upper_shadow: bool    # upper_shadow_ratio > 0.6
    is_marubozu:          bool    # body_ratio > 0.9  (大实体, 无影线)
    is_high_volume:       bool    # volume_relative > 2.0


def compute_features_for_signal(
    open_p: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    vol_ma20: float,
    close_max_20: float,
) -> Optional[CandleFeatures]:
    """单 bar 特征. 任何数据缺失返回 None."""
    if not all(x and x > 0 for x in (open_p, high, low, close, vol_ma20)):
        return None
    if high < low:
        return None
    full = high - low
    if full < 1e-9:
        # 一字板 (open=high=low=close) — 形态特征不可计算
        return None
    body = abs(close - open_p)
    upper = high - max(open_p, close)
    lower = min(open_p, close) - low

    body_r = body / full
    upper_r = upper / full
    lower_r = lower / full
    close_pos = (close - low) / full
    vol_rel = volume / vol_ma20 if vol_ma20 > 0 else 1.0
    breakout = (close - close_max_20) / close_max_20 if close_max_20 > 0 else 0.0

    return CandleFeatures(
        body_ratio=body_r,
        upper_shadow_ratio=upper_r,
        lower_shadow_ratio=lower_r,
        close_position=close_pos,
        volume_relative=vol_rel,
        breakout_strength_20=breakout,
        is_bullish=close > open_p,
        is_doji=body_r < 0.10,
        is_long_lower_shadow=lower_r > 0.60,
        is_long_upper_shadow=upper_r > 0.60,
        is_marubozu=body_r > 0.90,
        is_high_volume=vol_rel >= 2.0,
    )


def compute_features_from_bars(
    bars: list,   # list[Bar] 同 realistic_engine.Bar
    signal_idx: int,
    ma_window: int = 20,
) -> Optional[CandleFeatures]:
    """从 Bar 列表 + signal_idx 算 features (含 vol_ma20 + close_max_20)."""
    if signal_idx < ma_window or signal_idx >= len(bars):
        return None
    b = bars[signal_idx]
    closes  = np.array([bars[i].close for i in range(signal_idx - ma_window + 1, signal_idx + 1)])
    volumes = np.array([bars[i].volume for i in range(signal_idx - ma_window + 1, signal_idx + 1)])
    vol_ma20 = float(volumes.mean())
    close_max_20 = float(closes[:-1].max())   # 不含当日, 求"突破前 N 日最高"
    return compute_features_for_signal(
        open_p=b.open, high=b.high, low=b.low, close=b.close,
        volume=b.volume, vol_ma20=vol_ma20, close_max_20=close_max_20,
    )
