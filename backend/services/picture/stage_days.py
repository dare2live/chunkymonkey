"""Phase γ — stage_days 计算: 当前阶段已持续天数。

输入: 按 date 升序的 (date, stage) 序列
输出: 整段序列每一天的 stage_days 数组 (numpy)

算法: numpy run-length encoding
  - 遇到 stage 变化, 计数重置为 1
  - 同 stage, 计数 +1
  - 不需要交易日历 (基于 数据已有的日期序列, 每行 = 1 个交易日)

示例:
  dates  = ['01-02', '01-03', '01-04', '01-05']
  stages = ['1',     '1',     '2',     '2']
  days   = [1,        2,       1,       2]

边界:
  - 空数组 → 空数组
  - 全相同 → [1, 2, 3, ..., N]
  - 全不同 → [1, 1, 1, ..., 1]
  - 最后一天变化 → 仍正确计数为 1
"""
from __future__ import annotations

import numpy as np


def compute_stage_days(stages: np.ndarray) -> np.ndarray:
    """给定按日期升序的 stage 序列, 返回每天"当前阶段已持续天数"。

    Args:
        stages: 1D 数组 (TEXT/object dtype), 长度 N

    Returns:
        np.ndarray (int64), 长度 N, 第 i 位 = 第 i 天该 stage 已持续天数
    """
    n = len(stages)
    if n == 0:
        return np.array([], dtype=np.int64)
    days = np.zeros(n, dtype=np.int64)
    days[0] = 1
    for i in range(1, n):
        if stages[i] == stages[i - 1]:
            days[i] = days[i - 1] + 1
        else:
            days[i] = 1
    return days


def latest_stage_days(stages: np.ndarray) -> int:
    """便捷封装: 只取最后一天的 stage_days。

    >>> latest_stage_days(np.array(['1', '1', '2', '2', '2']))
    3
    """
    days = compute_stage_days(stages)
    return int(days[-1]) if len(days) > 0 else 0
