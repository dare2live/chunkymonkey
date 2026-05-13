"""Phase π.2 — 市场环境识别 (regime classification).

⚠ 单一职责: HS300 滚动 60d 收益率 → 牛/熊/震荡.
⚠ 改阈值: 改这一处.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RegimeLabel = Literal["bull", "bear", "sideways"]


@dataclass(frozen=True)
class RegimeConfig:
    """regime 阈值."""
    bull_threshold:  float = 0.10   # 60d ≥ +10% = 牛
    bear_threshold:  float = -0.10  # 60d ≤ -10% = 熊
    window_days:     int = 60       # 滚动窗口


def classify_regime(hs300_60d_return: float, config: RegimeConfig = RegimeConfig()) -> RegimeLabel:
    """HS300 60d 收益 → regime label."""
    if hs300_60d_return >= config.bull_threshold:
        return "bull"
    if hs300_60d_return <= config.bear_threshold:
        return "bear"
    return "sideways"
