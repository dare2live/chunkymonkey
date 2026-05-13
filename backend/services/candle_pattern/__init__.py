"""Phase η++++++ — K 线形态特征向量化 package.

⚠ 不命名 "锤子线 / 十字星 / 头肩底" — 直接量化为连续特征向量,
   Optuna 寻优最佳阈值组合, 让数据告诉我们什么形态有 alpha.

⚠ 6 个核心特征 (per-bar):
   1. body_ratio           实体长度 / 全长 (大实体=方向明确, 小实体=犹豫)
   2. upper_shadow_ratio   上影线 / 全长 (长上影=空头压制)
   3. lower_shadow_ratio   下影线 / 全长 (长下影=多头托底)
   4. close_position       close 在 (low, high) 中的位置 (0=最低, 1=最高)
   5. volume_relative      vol / ma20 (量比)
   6. breakout_strength    (close - max(close[-N:])) / max[-N:]  (突破强度)

⚠ Optuna 寻优每个特征的阈值范围, 选择最佳"形态过滤器".
"""
from services.candle_pattern.features import (
    CandleFeatures, compute_features_for_signal,
)
from services.candle_pattern.search_space import (
    PatternSearchSpace, DEFAULT_PATTERN_SEARCH_SPACE,
)
from services.candle_pattern.evaluator import score_pattern_match

__all__ = [
    "CandleFeatures", "compute_features_for_signal",
    "PatternSearchSpace", "DEFAULT_PATTERN_SEARCH_SPACE",
    "score_pattern_match",
]
