"""Phase η++++++ — 多目标优化 package.

⚠ 唯一对外接口:
    from services.optimization.objectives import compute_all_objectives, ObjectiveValues
    from services.optimization.composite import composite_score, DEFAULT_OBJECTIVE_WEIGHTS
    from services.optimization.constraints import passes_hard_constraints

设计:
  - objectives.py:  8 个 metric (sharpe/calmar/sortino/pain_index/ulcer/tail_risk/stability/cvar)
  - composite.py:   多目标加权聚合 (权重也是 Optuna 可寻优的)
  - constraints.py: 硬约束 (max_dd ≤ 25% / 最长连亏 / 单笔最大亏)
"""
from services.optimization.composite import composite_score, DEFAULT_OBJECTIVE_WEIGHTS
from services.optimization.constraints import passes_hard_constraints, HardConstraints
from services.optimization.objectives import ObjectiveValues, compute_all_objectives

__all__ = [
    "ObjectiveValues", "compute_all_objectives",
    "composite_score", "DEFAULT_OBJECTIVE_WEIGHTS",
    "passes_hard_constraints", "HardConstraints",
]
