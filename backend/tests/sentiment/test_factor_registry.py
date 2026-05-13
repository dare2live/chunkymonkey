"""单测: factor_registry.py — 中央注册表."""
from __future__ import annotations

import pytest


class TestFactorRegistry:
    def test_survey_count_60d_registered(self):
        from services.sentiment.factor_registry import get_factor
        f = get_factor("survey_count_60d")
        assert f.kind == "sentiment"
        assert f.bin_column == "survey_bin"
        assert f.bin_count == 4

    def test_lhb_score_negative_direction(self):
        """龙虎榜 IC 全部负, direction 应为 negative."""
        from services.sentiment.factor_registry import get_factor
        f = get_factor("lhb_score")
        assert f.ic_direction() == "negative"

    def test_survey_positive_direction(self):
        from services.sentiment.factor_registry import get_factor
        f = get_factor("survey_count_60d")
        assert f.ic_direction() == "positive"

    def test_lhb_not_eligible_anywhere(self):
        """龙虎榜不应在任何 profile 启用."""
        from services.sentiment.factor_registry import is_factor_eligible
        for pid in ["short", "mid", "long"]:
            assert is_factor_eligible("lhb_score", pid) is False

    def test_survey_only_long(self):
        from services.sentiment.factor_registry import is_factor_eligible
        assert is_factor_eligible("survey_count_60d", "short") is False
        assert is_factor_eligible("survey_count_60d", "mid") is False
        assert is_factor_eligible("survey_count_60d", "long") is True

    def test_unknown_factor_raises(self):
        from services.sentiment.factor_registry import get_factor
        with pytest.raises(KeyError):
            get_factor("non_existent")


class TestBucketDims:
    def test_long_has_survey_bin(self):
        from services.sentiment.factor_registry import get_bucket_dims
        dims = get_bucket_dims("long")
        assert "survey_bin" in dims

    def test_short_no_extra_dims(self):
        from services.sentiment.factor_registry import get_bucket_dims
        assert get_bucket_dims("short") == []

    def test_mid_no_extra_dims(self):
        from services.sentiment.factor_registry import get_bucket_dims
        assert get_bucket_dims("mid") == []


class TestFactorSummary:
    def test_summary_returns_all(self):
        from services.sentiment.factor_registry import factor_summary, FACTOR_REGISTRY
        out = factor_summary()
        assert len(out) == len(FACTOR_REGISTRY)
        # 每条都有 direction
        for r in out:
            assert r["direction"] in ("positive", "negative", "neutral")

    def test_best_horizon_for_survey(self):
        """调研因子最佳 horizon 应该是 60d (IC=0.086)."""
        from services.sentiment.factor_registry import get_factor
        f = get_factor("survey_count_60d")
        best = f.best_horizon()
        assert best.horizon_days == 60
        assert best.ic_mean > 0.08
