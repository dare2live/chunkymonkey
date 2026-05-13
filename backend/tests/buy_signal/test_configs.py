"""单测: buy_signal/configs.py"""
from __future__ import annotations
import pytest


class TestFactorWeights:
    def test_default_sums_to_one(self):
        """Phase η+++++ 8 因子 ∑=1.0."""
        from services.buy_signal.configs import WEIGHTS
        total = (WEIGHTS.trigger_weight + WEIGHTS.bucket_match_weight
                 + WEIGHTS.historical_alpha_weight + WEIGHTS.stage_fitness_weight
                 + WEIGHTS.fundamental_stage_weight + WEIGHTS.sentiment_weight
                 + WEIGHTS.stock_archetype_weight + WEIGHTS.primary_type_weight)
        assert abs(total - 1.0) < 0.001

    def test_invalid_weights_raise(self):
        from services.buy_signal.configs import FactorWeights
        with pytest.raises(ValueError):
            FactorWeights(trigger_weight=0.5, bucket_match_weight=0.5,
                          historical_alpha_weight=0.5)


class TestTierThresholds:
    def test_monotonic(self):
        from services.buy_signal.configs import TIER_THRESHOLDS
        assert TIER_THRESHOLDS.watch_min < TIER_THRESHOLDS.buy_min < TIER_THRESHOLDS.strong_buy_min
