"""Phase η+++++ — 买点判定 评分权重 唯一源.

⚠ 改评分权重/阈值 → 改这一处, 全局自动同步.
⚠ 6 个因子各自有 weight (合计加权), tier 分档阈值.

设计原则:
  - 每个因子 0-1 标准化, 然后乘权重求和 → 综合 score 0-100
  - tier 由阈值划分: NO_SIGNAL / WATCH / BUY / STRONG_BUY
  - 因子权重之和 = 1.0 (便于解释 "强势度的贡献来源")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BuySignalTier = Literal["NO_SIGNAL", "WATCH", "BUY", "STRONG_BUY"]


@dataclass(frozen=True)
class FactorWeights:
    """8 个因子的权重 (∑ = 1.0).

    Phase η+++++ 修正: 用户指出"形态识别"应包括已派生的 stock_archetype + primary_type,
    所以拆 factor 4 为更细致的 2 个 + 加 2 个新因子 (archetype + primary_type).
    """
    # 1. 公式当日触发 (核心 hard gate)
    trigger_weight:           float = 0.25
    # 2. 5 维桶吻合度
    bucket_match_weight:      float = 0.13
    # 3. 历史 Optuna 寻优 alpha
    historical_alpha_weight:  float = 0.22
    # 4. stage 适配度 (数据驱动: mart_stage_formula_fitness)
    stage_fitness_weight:     float = 0.12
    # 5. 基本面阶段风险 (失效破坏/已充分演绎)
    fundamental_stage_weight: float = 0.05
    # 6. 情绪 (调研热度, 仅长期 profile)
    sentiment_weight:         float = 0.10
    # 7. stock_archetype (3 类股票原型)
    stock_archetype_weight:   float = 0.08
    # 8. primary_type (5 类股票类型)
    primary_type_weight:      float = 0.05

    def __post_init__(self):
        total = (self.trigger_weight + self.bucket_match_weight + self.historical_alpha_weight
                 + self.stage_fitness_weight + self.fundamental_stage_weight
                 + self.sentiment_weight + self.stock_archetype_weight
                 + self.primary_type_weight)
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"FactorWeights 权重之和应 =1.0, 实 {total:.4f}")


@dataclass(frozen=True)
class TierThresholds:
    """综合 score → tier 的阈值边界."""
    watch_min: float = 30        # score ≥ 30 (满分 100) → WATCH (可关注)
    buy_min:   float = 55        # ≥ 55 → BUY
    strong_buy_min: float = 75   # ≥ 75 → STRONG_BUY


@dataclass(frozen=True)
class HistoricalAlphaGates:
    """因子 3 (历史 alpha) 的门槛 — 用于把 sharpe/win_rate 标准化到 0-1."""
    sharpe_min: float = 0.0       # sharpe < 0 → 该因子 = 0
    sharpe_max: float = 1.0       # sharpe ≥ 1.0 → 该因子 = 1
    win_rate_min: float = 0.50    # win < 50% → 0
    win_rate_max: float = 0.90    # win ≥ 90% → 1
    n_traded_min: int = 5         # 样本 < 5 → 0


# ─────────────────────────────────────────────────────────────────────
# 公式与阶段的偏好映射 (factor 4: technical_stage 匹配)
# ─────────────────────────────────────────────────────────────────────

FORMULA_TECH_STAGE_PREF: dict[str, tuple[str, ...]] = {
    # macd 金叉底部 = 期待阶段 1/1.5 (底部反转)
    "macd_golden_cross_below_zero": ("1", "1.5"),
    # macd 金叉高位 = 期待 2 (上升趋势中)
    "macd_golden_cross_above_zero": ("2",),
    # turtle 20/55 突破 = 期待 2/3 (上升 / 加速)
    "turtle_breakout_20": ("2", "3"),
    "turtle_breakout_55": ("2", "3"),
    # dynamic_ma 多重均线 = 期待 2 (健康上升)
    "dynamic_ma_iterative_cross": ("2",),
}


# 基本面阶段风险标签 (factor 5)
FUND_STAGE_RISK: dict[str, float] = {
    # 0.0 = 完全屏蔽 (negative weight contribution)
    "失效破坏":  -1.0,    # 强烈不推荐
    "已充分演绎": -0.5,    # 谨慎
    # 0-1 = 推荐程度
    "周期复苏":  1.0,     # 强烈推荐
    "温和验证":  0.9,
    "未充分演绎": 0.6,    # 中性偏正
    "中性":      0.3,
}


# factor 7: stock_archetype × formula 偏好 (业界常识 default, 数据可后续微调)
# 3 类 archetype: 高质量稳健型 / 成长兑现型 / 周期/事件驱动型
FORMULA_ARCHETYPE_PREF: dict[str, dict[str, float]] = {
    "macd_golden_cross_below_zero": {
        "高质量稳健型":   1.0,    # 底部反转, 高质量股最佳
        "成长兑现型":     0.85,
        "周期/事件驱动型": 0.6,
    },
    "macd_golden_cross_above_zero": {
        "成长兑现型":     1.0,    # 上升趋势中, 成长股最佳
        "高质量稳健型":   0.7,
        "周期/事件驱动型": 0.7,
    },
    "turtle_breakout_20": {
        "周期/事件驱动型": 1.0,   # 突破型, 事件驱动股最佳
        "成长兑现型":     0.8,
        "高质量稳健型":   0.6,
    },
    "turtle_breakout_55": {
        "周期/事件驱动型": 1.0,
        "成长兑现型":     0.8,
        "高质量稳健型":   0.6,
    },
    "dynamic_ma_iterative_cross": {
        "高质量稳健型":   1.0,    # 多重金叉, 健康趋势, 高质量股最佳
        "成长兑现型":     0.85,
        "周期/事件驱动型": 0.55,
    },
}


# factor 8: primary_type 价值分数 (依据业绩支撑度排序)
PRIMARY_TYPE_SCORE: dict[str, float] = {
    "业绩驱动": 1.0,   # 财报支撑 (最强)
    "价值修复": 0.85,  # 估值低位
    "周期复苏": 0.75,  # 行业景气
    "事件驱动": 0.60,  # 政策/并购等
    "技术突破": 0.50,  # 仅价格突破
    "—":      0.40,   # 空 (未分类)
}


# ─────────────────────────────────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────────────────────────────────

WEIGHTS = FactorWeights()
TIER_THRESHOLDS = TierThresholds()
HISTORICAL_GATES = HistoricalAlphaGates()
