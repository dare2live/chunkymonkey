"""Formula Engine — 统一选股公式接口 + 历史回测 + 适配矩阵。

Phase β (W2-W3) 产物，详见 开发手册.md §4.2 + goal.md §4。
Phase ψ.α (R-α): 加入短期反转因子族 (CLAUDE.md Rule 7 应用 — 公认 A 股最强 alpha).

子模块:
  base.py                 FormulaBase 抽象 + FormulaSignal / FormulaMetadata
  macd_golden_cross.py    公式 #1: MACD 金叉 (动量类)
  turtle_breakout.py      公式 #2/3: 海龟突破 20d / 55d (动量类)
  dynamic_ma_iterative.py 公式 #6: 动态均线迭代金叉 (动量类)
  reversal_short_term.py  公式 #8/9/10: 短期反转 1m_mild / 1m_deep / 1w (反转类, Phase ψ.α 新加)

每个公式实现接口 (FormulaBase Protocol):
  compute_signals(code, dates, opens, highs, lows, closes, volumes, amounts) -> list[FormulaSignal]

聚合输出:
  fact_technical_trigger
  mart_formula_horizon_evidence
  mart_stage_formula_fitness
"""
from services.formula_engine.base import (
    FormulaBase,
    FormulaMetadata,
    FormulaSignal,
    REGISTRY,
    register_formula,
)

__all__ = [
    "FormulaBase",
    "FormulaMetadata",
    "FormulaSignal",
    "REGISTRY",
    "register_formula",
]
