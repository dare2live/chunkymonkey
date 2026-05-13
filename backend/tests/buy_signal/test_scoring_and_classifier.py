"""单测: buy_signal/scoring.py + classifier.py + reasoning.py"""
from __future__ import annotations
import pytest


class TestComputeScore:
    def test_perfect_factors(self):
        """所有 8 factor = 1 → score = 100."""
        from services.buy_signal.scoring import compute_score
        from services.buy_signal.factor_aggregator import FactorScores
        s = compute_score(FactorScores(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
        assert s == pytest.approx(100.0, abs=0.5)

    def test_no_signal(self):
        from services.buy_signal.scoring import compute_score
        from services.buy_signal.factor_aggregator import FactorScores
        s = compute_score(FactorScores(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        assert s == 0.0

    def test_trigger_only_25pct(self):
        """只有公式触发, 其他 factor=0 → trigger_weight × 100 = 25 (η+++++ 调权重后)."""
        from services.buy_signal.scoring import compute_score
        from services.buy_signal.factor_aggregator import FactorScores
        s = compute_score(FactorScores(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        assert s == pytest.approx(25.0, abs=0.5)


class TestClassifyTier:
    def test_no_signal(self):
        from services.buy_signal.classifier import classify_tier
        assert classify_tier(10.0) == "NO_SIGNAL"
        assert classify_tier(29.0) == "NO_SIGNAL"

    def test_watch(self):
        from services.buy_signal.classifier import classify_tier
        assert classify_tier(30.0) == "WATCH"
        assert classify_tier(54.0) == "WATCH"

    def test_buy(self):
        from services.buy_signal.classifier import classify_tier
        assert classify_tier(55.0) == "BUY"
        assert classify_tier(74.0) == "BUY"

    def test_strong_buy(self):
        from services.buy_signal.classifier import classify_tier
        assert classify_tier(75.0) == "STRONG_BUY"
        assert classify_tier(100.0) == "STRONG_BUY"


class TestReasoning:
    def test_no_trigger_returns_short_text(self):
        from services.buy_signal.reasoning import generate_reasoning
        from services.buy_signal.factor_aggregator import FactorScores
        f = FactorScores(0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.4)
        text = generate_reasoning(f, formula_variant="macd_golden_cross_below_zero")
        assert "无触发" in text

    def test_strong_signal_text(self):
        from services.buy_signal.reasoning import generate_reasoning
        from services.buy_signal.factor_aggregator import FactorScores
        f = FactorScores(trigger=1.0, bucket_match=1.0, historical_alpha=0.85,
                          stage_fitness=1.0, fundamental_stage=0.9, sentiment=1.0,
                          stock_archetype=1.0, primary_type=1.0)
        text = generate_reasoning(
            f, formula_variant="turtle_breakout_20",
            today_technical_stage="2", fundamental_stage="温和验证",
            survey_bin="狂", sharpe=0.85, win_rate=0.85, is_best_bucket=True, n_traded=20,
            stock_archetype="周期/事件驱动型", primary_type="业绩驱动",
        )
        assert "触发" in text
        assert "Sharpe" in text
        assert "调研狂热" in text

    def test_stage_4_warning_in_text(self):
        from services.buy_signal.reasoning import generate_reasoning
        from services.buy_signal.factor_aggregator import FactorScores
        f = FactorScores(trigger=1.0, bucket_match=0.0, historical_alpha=0.0,
                          stage_fitness=0.0, fundamental_stage=0.0, sentiment=0.2,
                          stock_archetype=0.5, primary_type=0.4)
        text = generate_reasoning(
            f, formula_variant="turtle_breakout_20",
            today_technical_stage="4", fundamental_stage="已充分演绎",
        )
        assert "⚠" in text
