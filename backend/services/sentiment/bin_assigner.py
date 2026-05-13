"""Phase η++++ — 桶分配器 (纯函数, 单一职责).

把数值映射到桶标签. 阈值全部从 configs 注入, 不硬编码.

测试覆盖:
  - 边界值 (恰好等于 cold_max 应入"温")
  - None / NaN 输入
  - 负数输入 (异常)
"""
from __future__ import annotations

import math
from typing import Optional

from services.sentiment.configs import SurveyBinThresholds, SURVEY_BIN


def assign_survey_bin(
    count: Optional[float],
    thresholds: SurveyBinThresholds = SURVEY_BIN,
) -> str:
    """调研次数 → 桶标签 (冷/温/热/狂).

    Args:
        count: 60 日调研次数 (可为 None / NaN, 视为 0)
        thresholds: 桶阈值参数 (默认全局 SURVEY_BIN, 测试可注入派生)

    Returns:
        4 档桶标签之一: 冷 / 温 / 热 / 狂

    Raises:
        ValueError: count < 0
    """
    if count is None or (isinstance(count, float) and math.isnan(count)):
        return thresholds.LABELS[0]  # 冷
    c = float(count)
    if c < 0:
        raise ValueError(f"count must be non-negative, got {c}")
    if c < thresholds.cold_max:
        return thresholds.LABELS[0]  # 冷
    if c < thresholds.warm_max:
        return thresholds.LABELS[1]  # 温
    if c < thresholds.hot_max:
        return thresholds.LABELS[2]  # 热
    return thresholds.LABELS[3]      # 狂


def all_bin_labels(thresholds: SurveyBinThresholds = SURVEY_BIN) -> tuple[str, ...]:
    """全部 4 桶标签 (供 Optuna 枚举)."""
    return thresholds.LABELS


def bin_edges(thresholds: SurveyBinThresholds = SURVEY_BIN) -> list[tuple[int, float, str]]:
    """[(lo, hi, label), ...] — 供 SQL CASE 派生."""
    return [
        (0,                      thresholds.cold_max, thresholds.LABELS[0]),
        (thresholds.cold_max,    thresholds.warm_max, thresholds.LABELS[1]),
        (thresholds.warm_max,    thresholds.hot_max,  thresholds.LABELS[2]),
        (thresholds.hot_max,     float("inf"),        thresholds.LABELS[3]),
    ]
