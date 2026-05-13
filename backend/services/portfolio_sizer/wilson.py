"""Wilson Score 区间下界 — 修正小样本胜率。

朴素 win_rate = wins / n 在 n 小时不可靠 (8/8=100% 实际可能只是运气)。
Wilson Score 给出 95% 置信区间下界, 反映真实胜率的悲观估计。

公式 (95% CI):
  z = 1.96
  denom = 1 + z² / n
  center = (p + z²/(2n)) / denom
  margin = z × √(p(1-p)/n + z²/(4n²)) / denom
  lower = center - margin
"""
from __future__ import annotations

import math


def wilson_lower(wins: int, n: int, confidence: float = 0.95) -> float:
    """返回 Wilson Score 区间下界 (悲观胜率).

    Args:
        wins: 盈利次数
        n: 总样本数
        confidence: 置信度 (0.95 默认)

    Returns:
        修正后胜率 ∈ [0, 1]; n=0 时返回 0
    """
    if n <= 0:
        return 0.0
    if wins < 0 or wins > n:
        raise ValueError(f"invalid wins={wins} n={n}")
    z = {
        0.90: 1.645,
        0.95: 1.960,
        0.99: 2.576,
    }.get(confidence, 1.960)

    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n)) / denom
    lower = center - margin
    return max(0.0, min(1.0, lower))


def wilson_from_rate(win_rate: float, n: int, confidence: float = 0.95) -> float:
    """便捷封装: 已知 rate 和 n, 算 Wilson 下界."""
    if n <= 0 or win_rate is None:
        return 0.0
    wins = int(round(win_rate * n))
    return wilson_lower(wins, n, confidence)


def bayesian_win_rate(wins: int, n: int, prior_alpha: float = 2.0, prior_beta: float = 2.0) -> float:
    """Beta 共轭先验贝叶斯修正胜率.

    朴素 8/8=100% → 用 Beta(2,2) prior + 8 wins 0 losses:
      posterior mean = (2 + 8) / (2 + 2 + 8) = 0.833 (更保守)
    """
    if n < 0 or wins < 0:
        return 0.0
    return (prior_alpha + wins) / (prior_alpha + prior_beta + n)
