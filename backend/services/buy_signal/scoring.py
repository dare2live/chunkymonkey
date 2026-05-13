"""Phase η+++++ — 综合 score 计算 (单一职责).

score = ∑ weight_i × factor_i × 100
范围: 0-100, 越大 = 买点越强.
"""
from __future__ import annotations

from services.buy_signal.configs import FactorWeights, WEIGHTS
from services.buy_signal.factor_aggregator import FactorScores


def compute_score(factors: FactorScores, weights: FactorWeights = WEIGHTS) -> float:
    """8 个因子加权求和 × 100. 范围 [0, 100]."""
    raw = (
        factors.trigger          * weights.trigger_weight +
        factors.bucket_match     * weights.bucket_match_weight +
        factors.historical_alpha * weights.historical_alpha_weight +
        factors.stage_fitness    * weights.stage_fitness_weight +
        factors.fundamental_stage * weights.fundamental_stage_weight +
        factors.sentiment        * weights.sentiment_weight +
        factors.stock_archetype  * weights.stock_archetype_weight +
        factors.primary_type     * weights.primary_type_weight
    )
    return round(raw * 100.0, 2)


def factor_contributions(factors: FactorScores, weights: FactorWeights = WEIGHTS) -> dict[str, float]:
    """每个 factor 对最终 score 的贡献 (供 UI / 调试)."""
    return {
        "trigger":           factors.trigger * weights.trigger_weight * 100.0,
        "bucket_match":      factors.bucket_match * weights.bucket_match_weight * 100.0,
        "historical_alpha":  factors.historical_alpha * weights.historical_alpha_weight * 100.0,
        "stage_fitness":     factors.stage_fitness * weights.stage_fitness_weight * 100.0,
        "fundamental_stage": factors.fundamental_stage * weights.fundamental_stage_weight * 100.0,
        "sentiment":         factors.sentiment * weights.sentiment_weight * 100.0,
        "stock_archetype":   factors.stock_archetype * weights.stock_archetype_weight * 100.0,
        "primary_type":      factors.primary_type * weights.primary_type_weight * 100.0,
    }
