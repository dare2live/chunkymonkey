"""Phase η++++++ + ψ — K 线形态特征 Optuna 搜索空间 (Config-driven).

⚠ 改搜索范围 → 改 backend/config/optuna_config.yaml.search_space.candle_pattern
⚠ Rule 7: 不许 hardcode 区间.

⚠ Optuna 寻优每个特征的"理想阈值范围", 而非硬编码"锤子线 = lower>0.6 + body<0.3".
⚠ 让数据告诉我们什么阈值组合有 alpha.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.optimization.config import OptunaConfig, get_optuna_config


@dataclass(frozen=True)
class PatternSearchSpace:
    """4 个 K 线形态过滤参数. 实例化从 config 读, 不 hardcode."""
    body_ratio_min_lo: float
    body_ratio_min_hi: float
    lower_shadow_min_lo: float
    lower_shadow_min_hi: float
    close_position_min_lo: float
    close_position_min_hi: float
    volume_relative_min_lo: float
    volume_relative_min_hi: float

    @classmethod
    def from_config(cls, cfg: Optional[OptunaConfig] = None) -> "PatternSearchSpace":
        cfg = cfg or get_optuna_config()
        cp = cfg.search_space.candle_pattern
        return cls(
            body_ratio_min_lo=cp.body_ratio_min.lo,
            body_ratio_min_hi=cp.body_ratio_min.hi,
            lower_shadow_min_lo=cp.lower_shadow_min.lo,
            lower_shadow_min_hi=cp.lower_shadow_min.hi,
            close_position_min_lo=cp.close_position_min.lo,
            close_position_min_hi=cp.close_position_min.hi,
            volume_relative_min_lo=cp.volume_relative_min.lo,
            volume_relative_min_hi=cp.volume_relative_min.hi,
        )

    def sample(self, trial) -> dict:
        """供 Optuna trial 调用."""
        return {
            "body_ratio_min":      trial.suggest_float(
                "body_ratio_min", self.body_ratio_min_lo, self.body_ratio_min_hi),
            "lower_shadow_min":    trial.suggest_float(
                "lower_shadow_min", self.lower_shadow_min_lo, self.lower_shadow_min_hi),
            "close_position_min":  trial.suggest_float(
                "close_position_min", self.close_position_min_lo, self.close_position_min_hi),
            "volume_relative_min": trial.suggest_float(
                "volume_relative_min",
                self.volume_relative_min_lo, self.volume_relative_min_hi),
        }


# 模块级单例
DEFAULT_PATTERN_SEARCH_SPACE = PatternSearchSpace.from_config()
