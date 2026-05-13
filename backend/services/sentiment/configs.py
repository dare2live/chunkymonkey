"""Phase η++++ — 情绪因子参数唯一源.

所有阈值/桶划分/IC 判定标准都在这里. 改参数 → 只动这一处.
不允许下游代码硬编码具体数值.

设计原则:
  - frozen dataclass: 防止运行时被改
  - 每个常量都附 IC 实测来源 (validate_sentiment_ic.py 的报告)
  - 阈值改动需在此修改, 配套更新 ic_proof_date
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ─────────────────────────────────────────────────────────────────────
# 桶划分阈值
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SurveyBinThresholds:
    """调研热度桶 (count_60d → 冷/温/热/狂).

    边界遵循 [lo, hi) 半开区间.
    实测 IC (validate_sentiment_ic.py 2026-05-12, 60d horizon):
      n=0:    无调研 (基线)
      n=1-2:  弱热度
      n=3-5:  中等热度
      n>=6:   强热度 (pos_pct 72%)
    """
    cold_max: int = 1        # [0, 1) → 冷
    warm_max: int = 3        # [1, 3) → 温
    hot_max:  int = 6        # [3, 6) → 热
                             # [6, ∞)  → 狂

    LABELS: tuple[str, ...] = ("冷", "温", "热", "狂")


# ─────────────────────────────────────────────────────────────────────
# IC 判定标准
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FactorICThresholds:
    """因子是否进桶的判定阈值.

    判定逻辑 (见 sentiment/factor_registry.py::is_factor_eligible):
      |IC_mean| ≥ strong_threshold  AND  pos_pct ≥ pos_pct_threshold → 强信号, 进桶
      |IC_mean| ≥ weak_threshold                                    → 弱信号, 仅辅助过滤
      其他                                                          → 砍, 不进主链
    """
    strong_threshold: float = 0.03    # |IC| ≥ 0.03 算"强"
    weak_threshold: float = 0.02      # |IC| ≥ 0.02 算"弱"
    pos_pct_threshold: float = 0.55   # 日 IC 正向比例 ≥ 55% 才算稳定


# ─────────────────────────────────────────────────────────────────────
# Profile × Factor 映射
# ─────────────────────────────────────────────────────────────────────

ProfileId = Literal["short", "mid", "long"]


@dataclass(frozen=True)
class ProfileFactorPolicy:
    """每个 profile 启用哪些 sentiment 因子.

    实测来源: validate_sentiment_ic.py (2026-05-12).
      survey_count_60d × 5d  IC=-0.001 → short 不启用
      survey_count_60d × 20d IC=+0.043 → mid 弱启用 (临界)
      survey_count_60d × 60d IC=+0.086 → long 强启用
    """
    short_factors: tuple[str, ...] = ()                              # 短期不启用任何 sentiment
    mid_factors:   tuple[str, ...] = ()                              # 中期暂不启用 (IR 偏弱)
    long_factors:  tuple[str, ...] = ("survey_count_60d",)           # 长期启用调研热度

    def get_eligible(self, profile_id: str) -> tuple[str, ...]:
        if profile_id == "short": return self.short_factors
        if profile_id == "mid":   return self.mid_factors
        if profile_id == "long":  return self.long_factors
        raise ValueError(f"unknown profile_id: {profile_id}")


# ─────────────────────────────────────────────────────────────────────
# 窗口配置
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WindowConfig:
    """滚动窗口长度 (日历日, 不是交易日)."""
    survey_short_days: int = 30   # 短窗口 30 自然日
    survey_long_days:  int = 60   # 长窗口 60 自然日 (主因子)


# ─────────────────────────────────────────────────────────────────────
# 全局单例 (可在测试中通过 dataclasses.replace 派生新实例)
# ─────────────────────────────────────────────────────────────────────

SURVEY_BIN = SurveyBinThresholds()
IC_GATE = FactorICThresholds()
PROFILE_POLICY = ProfileFactorPolicy()
WINDOWS = WindowConfig()
