"""Phase η++++++ — K 线形态特征 Optuna 搜索空间.

⚠ Optuna 寻优每个特征的"理想阈值范围", 而非硬编码"锤子线 = lower>0.6 + body<0.3".
⚠ 让数据告诉我们什么阈值组合有 alpha.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatternSearchSpace:
    """4 个 K 线形态过滤参数的 Optuna 搜索范围 (与 5 维超参合并到 9 维)."""
    # 实体大小偏好 (0 = 不限, 1 = 必须 marubozu)
    body_ratio_min_lo: float = 0.0
    body_ratio_min_hi: float = 0.7

    # 下影线偏好 (反弹托底)
    lower_shadow_min_lo: float = 0.0
    lower_shadow_min_hi: float = 0.6

    # close 位置偏好 (0 = 必须收最低, 1 = 必须收最高)
    close_position_min_lo: float = 0.0
    close_position_min_hi: float = 0.95

    # 量比偏好
    volume_relative_min_lo: float = 0.0
    volume_relative_min_hi: float = 3.0

    def sample(self, trial) -> dict:
        """供 Optuna trial 调用."""
        return {
            "body_ratio_min":      trial.suggest_float("body_ratio_min",
                                    self.body_ratio_min_lo, self.body_ratio_min_hi),
            "lower_shadow_min":    trial.suggest_float("lower_shadow_min",
                                    self.lower_shadow_min_lo, self.lower_shadow_min_hi),
            "close_position_min":  trial.suggest_float("close_position_min",
                                    self.close_position_min_lo, self.close_position_min_hi),
            "volume_relative_min": trial.suggest_float("volume_relative_min",
                                    self.volume_relative_min_lo, self.volume_relative_min_hi),
        }


DEFAULT_PATTERN_SEARCH_SPACE = PatternSearchSpace()
