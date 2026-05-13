"""Phase η++++++ — 多目标 → 单目标 综合 score (单一职责).

⚠ 改聚合公式或权重 → 改这一处.
⚠ 权重本身也可以是 Optuna 寻优的 (configs.composite.weights_search_space)

目标设计:
  - 主目标: Calmar (用户最关心的 "稳定收益最大化")
  - 辅佐: Sortino (下行波动控制) + Stability (winrate 一致性)
  - 惩罚: Pain Index + Ulcer Index + 负 Tail Risk
  - 加权: log(1+n_traded) (样本量信心)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from services.optimization.objectives import ObjectiveValues


@dataclass(frozen=True)
class CompositeWeights:
    """多目标聚合权重 (∑ = 1.0, 但可 Optuna 寻优偏好)."""
    calmar_w:    float = 0.35   # 用户最关心
    sortino_w:   float = 0.25   # 下行控制
    sharpe_w:    float = 0.15   # 传统 (但权重低)
    stability_w: float = 0.10   # winrate 稳定性
    # 惩罚项 (负贡献)
    pain_w:      float = 0.05   # 路径痛苦度
    ulcer_w:     float = 0.05   # 累积痛苦度
    tail_w:      float = 0.05   # 尾部风险

    def __post_init__(self):
        total = (self.calmar_w + self.sortino_w + self.sharpe_w + self.stability_w
                 + self.pain_w + self.ulcer_w + self.tail_w)
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"CompositeWeights ∑应=1.0, 实 {total:.4f}")


DEFAULT_OBJECTIVE_WEIGHTS = CompositeWeights()


def composite_score(
    obj: ObjectiveValues,
    weights: CompositeWeights = DEFAULT_OBJECTIVE_WEIGHTS,
) -> float:
    """8 个 metric → 综合 score (越大越好).

    Logic:
        正贡献: calmar / sortino / sharpe / stability (越大越好)
        负贡献: pain / ulcer (越大越糟, 减分)
        tail_risk 本身负数, 越接近 0 越好 → 加上 tail_risk × tail_w 即可 (越接近 0 加分越多)
        最后乘 log(1+n) 加权样本量
    """
    raw = (
        obj.calmar    * weights.calmar_w +
        obj.sortino   * weights.sortino_w +
        obj.sharpe    * weights.sharpe_w +
        obj.stability * weights.stability_w -
        obj.pain_index  * weights.pain_w -
        obj.ulcer_index * weights.ulcer_w +
        obj.tail_risk * weights.tail_w     # tail_risk ≤ 0, 加它等于减惩罚
    )
    # 样本量加权
    sample_w = math.log(1.0 + obj.n_traded)
    return raw * sample_w


def score_contributions(
    obj: ObjectiveValues,
    weights: CompositeWeights = DEFAULT_OBJECTIVE_WEIGHTS,
) -> dict[str, float]:
    """每个 metric 对 composite 的贡献 (供 UI / 调试)."""
    sample_w = math.log(1.0 + obj.n_traded)
    return {
        "calmar":     obj.calmar * weights.calmar_w * sample_w,
        "sortino":    obj.sortino * weights.sortino_w * sample_w,
        "sharpe":     obj.sharpe * weights.sharpe_w * sample_w,
        "stability":  obj.stability * weights.stability_w * sample_w,
        "pain_penalty":  -obj.pain_index * weights.pain_w * sample_w,
        "ulcer_penalty": -obj.ulcer_index * weights.ulcer_w * sample_w,
        "tail_bonus":    obj.tail_risk * weights.tail_w * sample_w,
        "n_traded":      obj.n_traded,
        "sample_log_w":  sample_w,
    }
