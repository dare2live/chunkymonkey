"""Phase η++++++ — 形态特征匹配评分 (纯函数).

给定 CandleFeatures + Optuna 寻优出的阈值 → 0/1 (是否通过过滤) 或 0-1 (匹配度).
"""
from __future__ import annotations

from typing import Optional

from services.candle_pattern.features import CandleFeatures


def score_pattern_match(
    features: Optional[CandleFeatures],
    body_ratio_min: float = 0.0,
    lower_shadow_min: float = 0.0,
    close_position_min: float = 0.0,
    volume_relative_min: float = 0.0,
) -> float:
    """0-1: 当日 K 线形态与所选过滤参数的匹配度.

    Logic:
        - 任一 hard 阈值不过 → 0
        - 全过 → 1.0 - 距阈值平均偏离 (越远阈值越好, 但 cap 至 1)
        - 数据缺失 (一字板等) → 0.3 (中性偏低)
    """
    if features is None:
        return 0.3
    if features.body_ratio < body_ratio_min:
        return 0.0
    if features.lower_shadow_ratio < lower_shadow_min:
        return 0.0
    if features.close_position < close_position_min:
        return 0.0
    if features.volume_relative < volume_relative_min:
        return 0.0
    # 全过则 reward
    return 1.0
