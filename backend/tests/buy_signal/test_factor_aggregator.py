"""单测: buy_signal/factor_aggregator.py"""
from __future__ import annotations
import pytest


class TestScoreTrigger:
    def test_triggered(self):
        from services.buy_signal.factor_aggregator import score_trigger
        assert score_trigger(True) == 1.0
        assert score_trigger(False) == 0.0


class TestScoreBucketMatch:
    def test_no_match(self):
        from services.buy_signal.factor_aggregator import score_bucket_match
        assert score_bucket_match(None, False, 0) == 0.0

    def test_strong_match(self):
        from services.buy_signal.factor_aggregator import score_bucket_match
        assert score_bucket_match(("温量","额温","中位","2"), True, 20) == 1.0

    def test_small_sample(self):
        from services.buy_signal.factor_aggregator import score_bucket_match
        assert score_bucket_match(("温量","额温","中位","2"), True, 3) == 0.4


class TestScoreHistoricalAlpha:
    def test_strong_alpha(self):
        from services.buy_signal.factor_aggregator import score_historical_alpha
        # sharpe=1.0, win=0.9 → 1.0
        assert score_historical_alpha(1.0, 0.9, 20) == pytest.approx(1.0, abs=0.01)

    def test_weak_alpha(self):
        from services.buy_signal.factor_aggregator import score_historical_alpha
        assert score_historical_alpha(0.0, 0.50, 10) == pytest.approx(0.0, abs=0.01)

    def test_insufficient_samples(self):
        from services.buy_signal.factor_aggregator import score_historical_alpha
        assert score_historical_alpha(2.0, 0.95, 3) == 0.0   # n < 5

    def test_none_safe(self):
        from services.buy_signal.factor_aggregator import score_historical_alpha
        assert score_historical_alpha(None, None, None) == 0.0


class TestScoreStageFitness:
    """Phase η+++++: factor 4 改为 stage_fitness, 数据驱动."""
    def test_data_driven_lookup(self):
        """fitness_lookup 命中时, sharpe 标准化 (Phase η++++++ 调整后)."""
        from services.buy_signal.factor_aggregator import score_stage_fitness
        lookup = {("温和验证", "2", "turtle_breakout_20"): 1.0}
        # sharpe=1.0 → (1.0+1.0)/2.0 = 1.0
        assert score_stage_fitness("温和验证", "2", "turtle_breakout_20", lookup) == 1.0

    def test_data_driven_neutral_sharpe_is_half(self):
        """新 normalize: sharpe=0 → 0.5 (中性), 不严重拉低."""
        from services.buy_signal.factor_aggregator import score_stage_fitness
        lookup = {("中性", "2", "turtle_breakout_20"): 0.0}
        assert score_stage_fitness("中性", "2", "turtle_breakout_20", lookup) == 0.5

    def test_data_driven_negative_sharpe(self):
        from services.buy_signal.factor_aggregator import score_stage_fitness
        lookup = {("失效破坏", "4", "turtle_breakout_20"): -0.6}
        # sharpe=-0.6 → (-0.6+1.0)/2.0 = 0.2
        assert score_stage_fitness("失效破坏", "4", "turtle_breakout_20", lookup) == pytest.approx(0.2, abs=0.01)

    def test_fallback_when_no_data(self):
        """lookup 缺失 → 回退硬编码 dict."""
        from services.buy_signal.factor_aggregator import score_stage_fitness
        # macd_below 偏好 1/1.5
        assert score_stage_fitness(None, "1", "macd_golden_cross_below_zero", {}) == 1.0

    def test_fallback_stage_4_breakout(self):
        from services.buy_signal.factor_aggregator import score_stage_fitness
        assert score_stage_fitness(None, "4", "turtle_breakout_20", {}) == 0.0


class TestScoreStockArchetype:
    """Phase η+++++: factor 7 — 股票原型 × 公式偏好."""
    def test_macd_below_high_quality(self):
        from services.buy_signal.factor_aggregator import score_stock_archetype
        # macd_below 偏好 高质量稳健型 (1.0)
        assert score_stock_archetype("高质量稳健型", "macd_golden_cross_below_zero") == 1.0

    def test_turtle_cycle_event_driven(self):
        from services.buy_signal.factor_aggregator import score_stock_archetype
        assert score_stock_archetype("周期/事件驱动型", "turtle_breakout_20") == 1.0

    def test_none_returns_neutral(self):
        from services.buy_signal.factor_aggregator import score_stock_archetype
        assert score_stock_archetype(None, "turtle_breakout_20") == 0.5


class TestScorePrimaryType:
    """Phase η+++++: factor 8 — 股票类型评分."""
    def test_performance_driven_highest(self):
        from services.buy_signal.factor_aggregator import score_primary_type
        assert score_primary_type("业绩驱动") == 1.0

    def test_empty_default_low(self):
        from services.buy_signal.factor_aggregator import score_primary_type
        assert score_primary_type("—") == 0.4
        assert score_primary_type(None) == 0.4


class TestScoreFundamentalStage:
    def test_growth_validates(self):
        from services.buy_signal.factor_aggregator import score_fundamental_stage
        assert score_fundamental_stage("周期复苏") == 1.0
        assert score_fundamental_stage("温和验证") == 0.9

    def test_failure_clamped_to_zero(self):
        """失效破坏 = -1.0 → clamp 到 0."""
        from services.buy_signal.factor_aggregator import score_fundamental_stage
        assert score_fundamental_stage("失效破坏") == 0.0
        assert score_fundamental_stage("已充分演绎") == 0.0

    def test_none_neutral(self):
        from services.buy_signal.factor_aggregator import score_fundamental_stage
        assert score_fundamental_stage(None) == 0.5


class TestScoreSentiment:
    def test_long_profile_uses_sentiment(self):
        from services.buy_signal.factor_aggregator import score_sentiment
        assert score_sentiment("狂", "long") == 1.0
        assert score_sentiment("热", "long") == 0.7
        assert score_sentiment("温", "long") == 0.4
        assert score_sentiment("冷", "long") == 0.2

    def test_short_mid_neutral(self):
        from services.buy_signal.factor_aggregator import score_sentiment
        assert score_sentiment("狂", "short") == 0.5
        assert score_sentiment("狂", "mid") == 0.5


class TestAggregateFactors:
    def test_full_aggregate(self):
        from services.buy_signal.factor_aggregator import aggregate_factors
        f = aggregate_factors(
            triggered_today=True,
            today_bucket=("温量","额温","中位","2"),
            is_best_bucket=True,
            historical_n_signals=20,
            sharpe=0.8, win_rate=0.85, n_traded=25,
            today_technical_stage="2",
            formula_variant="turtle_breakout_20",
            fundamental_stage="温和验证",
            survey_bin="热",
            profile_id="long",
            stock_archetype="周期/事件驱动型",
            primary_type="业绩驱动",
        )
        assert f.trigger == 1.0
        assert f.bucket_match == 1.0
        assert f.historical_alpha > 0.7
        assert f.stage_fitness == 1.0  # 公式偏好 stage 2
        assert f.fundamental_stage == 0.9
        assert f.sentiment == 0.7
        assert f.stock_archetype == 1.0  # turtle 偏好 周期/事件驱动型
        assert f.primary_type == 1.0     # 业绩驱动最高分
