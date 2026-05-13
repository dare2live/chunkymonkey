"""Phase η++++++ + ψ — 多目标 → 单目标 综合 score (Config-driven, 单一职责).

⚠ 改聚合公式 → 改这一处.
⚠ 改权重 → 改 backend/config/optuna_config.yaml.composite. Rule 7: 不许 hardcode.

目标设计:
  - 主目标: Calmar (用户最关心的 "稳定收益最大化")
  - 辅佐: Sortino (下行波动控制) + Stability (winrate 一致性)
  - 惩罚: Pain Index + Ulcer Index + 负 Tail Risk
  - 加权: log(1+n_traded) (样本量信心)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from services.optimization.config import OptunaConfig, get_optuna_config
from services.optimization.objectives import ObjectiveValues


@dataclass(frozen=True)
class CompositeWeights:
    """多目标聚合权重 (∑ = 1.0)."""
    calmar_w:    float
    sortino_w:   float
    sharpe_w:    float
    stability_w: float
    pain_w:      float
    ulcer_w:     float
    tail_w:      float

    def __post_init__(self):
        total = (self.calmar_w + self.sortino_w + self.sharpe_w + self.stability_w
                 + self.pain_w + self.ulcer_w + self.tail_w)
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"CompositeWeights ∑应=1.0, 实 {total:.4f}")

    @classmethod
    def from_config(cls, cfg: Optional[OptunaConfig] = None) -> "CompositeWeights":
        cfg = cfg or get_optuna_config()
        c = cfg.composite
        return cls(
            calmar_w=c.calmar_w,
            sortino_w=c.sortino_w,
            sharpe_w=c.sharpe_w,
            stability_w=c.stability_w,
            pain_w=c.pain_w,
            ulcer_w=c.ulcer_w,
            tail_w=c.tail_w,
        )


# 模块级单例 (从 yaml 读)
DEFAULT_OBJECTIVE_WEIGHTS = CompositeWeights.from_config()


def composite_score(
    obj: ObjectiveValues,
    weights: Optional[CompositeWeights] = None,
) -> float:
    """8 个 metric → 综合 score (越大越好).

    Logic:
        正贡献: calmar / sortino / sharpe / stability (越大越好)
        负贡献: pain / ulcer (越大越糟, 减分)
        tail_risk 本身负数, 越接近 0 越好 → 加上 tail_risk × tail_w 即可
        最后乘 log(1+n) 加权样本量
    """
    w = weights or DEFAULT_OBJECTIVE_WEIGHTS
    raw = (
        obj.calmar    * w.calmar_w +
        obj.sortino   * w.sortino_w +
        obj.sharpe    * w.sharpe_w +
        obj.stability * w.stability_w -
        obj.pain_index  * w.pain_w -
        obj.ulcer_index * w.ulcer_w +
        obj.tail_risk * w.tail_w     # tail_risk ≤ 0, 加它等于减惩罚
    )
    sample_w = math.log(1.0 + obj.n_traded)
    return raw * sample_w


def score_contributions(
    obj: ObjectiveValues,
    weights: Optional[CompositeWeights] = None,
) -> dict[str, float]:
    """每个 metric 对 composite 的贡献 (供 UI / 调试)."""
    w = weights or DEFAULT_OBJECTIVE_WEIGHTS
    sample_w = math.log(1.0 + obj.n_traded)
    return {
        "calmar":     obj.calmar * w.calmar_w * sample_w,
        "sortino":    obj.sortino * w.sortino_w * sample_w,
        "sharpe":     obj.sharpe * w.sharpe_w * sample_w,
        "stability":  obj.stability * w.stability_w * sample_w,
        "pain_penalty":  -obj.pain_index * w.pain_w * sample_w,
        "ulcer_penalty": -obj.ulcer_index * w.ulcer_w * sample_w,
        "tail_bonus":    obj.tail_risk * w.tail_w * sample_w,
        "n_traded":      obj.n_traded,
        "sample_log_w":  sample_w,
    }
