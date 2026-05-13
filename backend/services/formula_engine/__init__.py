"""Formula Engine — 统一选股公式接口 + 历史回测 + 适配矩阵。

Phase β (W2-W3) 产物，详见 开发手册.md §4.2 + goal.md §4。

子模块:
  base.py                 FormulaBase 抽象 + FormulaSignal / FormulaMetadata
  macd_golden_cross.py    公式 #1: MACD 金叉 (DIF 上穿 DEA + DIFF>=0)
  turtle_breakout.py      公式 #2/3: 海龟突破 20d / 55d
  ma_breakout_long_low.py 公式 #4: 长期低位突破 (chunky F1)
  iterative_signal.py     公式 #5: 多级迭代信号 (chunky F3)
  dynamic_ma_iterative.py 公式 #6: 动态均线迭代金叉 (用户 MQL)
  sector_dual_confirm.py  公式 #7: 板块动量 + 双确认

每个公式实现接口:
  evaluate_historical(conn, mkt_conn, start_date, end_date) -> Iterator[FormulaSignal]

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
