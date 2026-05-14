"""P2 portfolio optimization — composite scoring + capacity-aware selection.

PLAN_V3 v3.2 P2:
- composite = ret_w*ann_ret - dd_w*|max_dd| - hp_w*f(hp) - turnover_w*turnover
            - cost_w*tx_cost_pct - capacity_w*concentration
- 权重 由 validation grid/Optuna 决定 (不预设)
- f(avg_hp): 线性 / 分段 / log 三种候选, OOS composite 决定
- Acceptance: validation composite 高于 P1; 容量惩罚后 ann 不低于 P1; mdd/turnover/cost 受控
"""
from services.portfolio.composite_score import (
    CompositeWeights,
    HpPenaltyMode,
    compute_composite_score,
    score_strategy_run,
)

__all__ = [
    "CompositeWeights",
    "HpPenaltyMode",
    "compute_composite_score",
    "score_strategy_run",
]
