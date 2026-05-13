"""单测: candle_pattern/features.py + evaluator.py"""
from __future__ import annotations

import pytest


class TestComputeFeatures:
    def test_marubozu_bull(self):
        """大阳实体: open=low=10, close=high=11 → body_ratio=1.0, close_pos=1.0, no shadow."""
        from services.candle_pattern.features import compute_features_for_signal
        f = compute_features_for_signal(open_p=10, high=11, low=10, close=11,
                                         volume=1e6, vol_ma20=5e5, close_max_20=10.5)
        assert f.body_ratio == pytest.approx(1.0)
        assert f.close_position == pytest.approx(1.0)
        assert f.upper_shadow_ratio == 0.0
        assert f.lower_shadow_ratio == 0.0
        assert f.is_bullish
        assert f.is_marubozu
        assert f.is_high_volume     # 2.0× ma20

    def test_hammer_pattern(self):
        """锤子线: long lower shadow + small body at top."""
        from services.candle_pattern.features import compute_features_for_signal
        # low=8, high=10, open=9.5, close=9.8 → lower=1.5, body=0.3, upper=0.2
        f = compute_features_for_signal(open_p=9.5, high=10, low=8, close=9.8,
                                         volume=1e6, vol_ma20=1e6, close_max_20=9.5)
        # full = 2, body = 0.3, lower = 1.5, upper = 0.2
        assert f.body_ratio == pytest.approx(0.15, abs=0.01)
        assert f.lower_shadow_ratio == pytest.approx(0.75, abs=0.01)
        assert f.upper_shadow_ratio == pytest.approx(0.10, abs=0.01)
        assert f.is_long_lower_shadow   # > 0.6
        assert not f.is_doji            # body 0.15 < 0.10? no, 0.15 >= 0.10

    def test_doji(self):
        """十字星: tiny body."""
        from services.candle_pattern.features import compute_features_for_signal
        # body 0.05, full 1.0
        f = compute_features_for_signal(open_p=9.5, high=10, low=9, close=9.55,
                                         volume=1e6, vol_ma20=1e6, close_max_20=9.5)
        assert f.body_ratio < 0.10
        assert f.is_doji

    def test_one_word_returns_none(self):
        """一字板 (OHLC 相同) → 无形态."""
        from services.candle_pattern.features import compute_features_for_signal
        f = compute_features_for_signal(open_p=11, high=11, low=11, close=11,
                                         volume=1e6, vol_ma20=5e5, close_max_20=10)
        assert f is None

    def test_breakout_strength(self):
        """突破强度 = (close - max[-20]) / max."""
        from services.candle_pattern.features import compute_features_for_signal
        # close=11, max_20=10 → breakout = 0.1
        f = compute_features_for_signal(open_p=10, high=11.2, low=10, close=11,
                                         volume=1e6, vol_ma20=5e5, close_max_20=10)
        assert f.breakout_strength_20 == pytest.approx(0.10, abs=0.01)


class TestScorePatternMatch:
    def test_all_filters_pass(self):
        from services.candle_pattern.features import compute_features_for_signal
        from services.candle_pattern.evaluator import score_pattern_match
        f = compute_features_for_signal(10, 11, 10, 11, 1e6, 5e5, 10.5)
        # 全部超阈值
        assert score_pattern_match(f, body_ratio_min=0.5, close_position_min=0.8,
                                    volume_relative_min=1.5) == 1.0

    def test_body_too_small_reject(self):
        from services.candle_pattern.features import compute_features_for_signal
        from services.candle_pattern.evaluator import score_pattern_match
        # doji
        f = compute_features_for_signal(10, 11, 9, 10.1, 1e6, 1e6, 10)
        assert score_pattern_match(f, body_ratio_min=0.5) == 0.0

    def test_none_features_neutral(self):
        from services.candle_pattern.evaluator import score_pattern_match
        assert score_pattern_match(None) == 0.3
