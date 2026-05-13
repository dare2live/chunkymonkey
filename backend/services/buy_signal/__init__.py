"""Phase η+++++ — 形态识别 + 买点判定 package.

⚠ 严格模块化:
  - configs.py      : 评分权重 / 阈值 唯一源
  - factor_aggregator.py: 6 个 factor 各自打分 (0-1)
  - scoring.py      : 综合 score (加权求和)
  - classifier.py   : score → tier
  - reasoning.py    : 生成中文理由文本
  - ddl.py          : mart_stock_formula_buy_signal_daily schema
"""
from services.buy_signal.classifier import classify_tier
from services.buy_signal.factor_aggregator import FactorScores, aggregate_factors
from services.buy_signal.reasoning import generate_reasoning
from services.buy_signal.scoring import compute_score, factor_contributions

__all__ = [
    "FactorScores", "aggregate_factors",
    "compute_score", "factor_contributions",
    "classify_tier",
    "generate_reasoning",
]
