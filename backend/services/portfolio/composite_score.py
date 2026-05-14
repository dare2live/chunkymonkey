"""P2 composite scoring — strategy multi-metric utility function.

PLAN_V3 v3.2 P2 公式:
    composite = ret_w * ann_ret
              - dd_w * abs(max_dd)
              - hp_w * f(avg_hp)
              - turnover_w * turnover
              - cost_w * tx_cost_pct
              - capacity_w * concentration

权重 (CompositeWeights) 由 validation grid/Optuna 决定 (不预设最终权重).
f(avg_hp) 有 3 候选: 线性 / 分段 / log (OOS composite 决定哪个胜).

注意 (PLAN_V3 §99 P2):
- 容量、单票集中度、行业集中度、换手、滑点、涨跌停成交失败率 都进 composite
- 高换手伪收益要被淘汰 (cost / turnover 惩罚)
- 容量惩罚后 alpha 不能消失
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional


HpPenaltyMode = Literal["linear", "piecewise", "log"]


@dataclass(frozen=True)
class CompositeWeights:
    """Composite 权重 (PLAN_V3 §2 P2; ∑权重 不强制 1.0, Optuna 决定)."""
    ret_w: float = 1.0          # ann_ret 系数 (正)
    dd_w: float = 1.0           # |max_dd| 系数 (惩罚)
    hp_w: float = 0.0           # f(avg_hp) 系数 (惩罚短期)
    turnover_w: float = 0.5     # turnover 系数 (惩罚高换手)
    cost_w: float = 1.0         # tx_cost_pct 系数 (惩罚成本占比)
    capacity_w: float = 0.5     # concentration 系数 (惩罚单票/行业集中)
    hp_penalty_mode: HpPenaltyMode = "linear"
    # 分段 penalty 参数 (mode='piecewise' 时用)
    hp_piecewise_short_threshold: int = 5     # avg_hp < 5 → 重惩罚
    hp_piecewise_long_threshold: int = 60     # avg_hp > 60 → 轻惩罚
    hp_piecewise_short_penalty: float = 1.0
    hp_piecewise_long_penalty: float = 0.2


def _hp_penalty(avg_hp: float, weights: CompositeWeights) -> float:
    """f(avg_hp): 短期 hp 重罚, 长期 hp 轻罚 (or 反过来, 取决于 OOS)."""
    if avg_hp is None or avg_hp <= 0:
        return 0.0
    if weights.hp_penalty_mode == "linear":
        # 短期 hp 高 penalty (1/hp)
        return 1.0 / avg_hp
    if weights.hp_penalty_mode == "log":
        # log 平滑 short 严重, long 轻
        return 1.0 / math.log(avg_hp + math.e)
    if weights.hp_penalty_mode == "piecewise":
        if avg_hp < weights.hp_piecewise_short_threshold:
            return weights.hp_piecewise_short_penalty
        if avg_hp > weights.hp_piecewise_long_threshold:
            return weights.hp_piecewise_long_penalty
        return 0.5  # 中等 hp 中等 penalty
    return 0.0


def compute_composite_score(
    *,
    ann_ret: float,
    max_dd: float,
    avg_hp: float | None = None,
    turnover: float = 0.0,
    tx_cost_pct: float = 0.0,
    concentration: float = 0.0,
    weights: CompositeWeights | None = None,
) -> float:
    """Composite score (PLAN_V3 P2 公式).

    Args:
        ann_ret: 年化收益 (decimal, 例 0.30 = 30%).
        max_dd: 最大回撤 (signed, 例 -0.20 = -20%). 内部取 abs.
        avg_hp: 平均持仓天数.
        turnover: 年化换手率.
        tx_cost_pct: 成本占总 PnL 比例.
        concentration: 单票/行业集中度 (Herfindahl-Hirschman 类指标, 0~1).
        weights: CompositeWeights; 默认 CompositeWeights().

    Returns:
        composite score (无量纲, 业务: 大 = 好, 小 = 差).
    """
    w = weights or CompositeWeights()
    return (
        w.ret_w * ann_ret
        - w.dd_w * abs(max_dd)
        - w.hp_w * _hp_penalty(avg_hp or 0, w)
        - w.turnover_w * turnover
        - w.cost_w * tx_cost_pct
        - w.capacity_w * concentration
    )


@dataclass(frozen=True)
class StrategyRunMetrics:
    """单 strategy run 的 KPI 集合."""
    ann_ret: float
    max_dd: float
    avg_hp: Optional[float] = None
    turnover: float = 0.0
    tx_cost_pct: float = 0.0
    concentration: float = 0.0


def score_strategy_run(
    metrics: StrategyRunMetrics,
    weights: CompositeWeights | None = None,
) -> float:
    """Convenience wrapper for StrategyRunMetrics."""
    return compute_composite_score(
        ann_ret=metrics.ann_ret,
        max_dd=metrics.max_dd,
        avg_hp=metrics.avg_hp,
        turnover=metrics.turnover,
        tx_cost_pct=metrics.tx_cost_pct,
        concentration=metrics.concentration,
        weights=weights,
    )
