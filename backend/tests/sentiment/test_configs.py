"""单测: configs.py — 参数源."""
from __future__ import annotations

import pytest
from dataclasses import replace


class TestSurveyBinThresholds:
    def test_default_monotonic(self):
        from services.sentiment.configs import SURVEY_BIN
        # 边界值必须严格递增
        assert SURVEY_BIN.cold_max < SURVEY_BIN.warm_max < SURVEY_BIN.hot_max

    def test_labels_exactly_4(self):
        from services.sentiment.configs import SURVEY_BIN
        assert len(SURVEY_BIN.LABELS) == 4
        assert SURVEY_BIN.LABELS == ("冷", "温", "热", "狂")

    def test_frozen_cannot_mutate(self):
        from services.sentiment.configs import SURVEY_BIN
        with pytest.raises(Exception):
            SURVEY_BIN.cold_max = 99


class TestProfileFactorPolicy:
    def test_long_includes_survey(self):
        from services.sentiment.configs import PROFILE_POLICY
        assert "survey_count_60d" in PROFILE_POLICY.long_factors

    def test_short_no_sentiment(self):
        """短期 profile 不应启用任何 sentiment (IC=-0.001 实测)."""
        from services.sentiment.configs import PROFILE_POLICY
        assert PROFILE_POLICY.short_factors == ()

    def test_get_eligible_unknown_raises(self):
        from services.sentiment.configs import PROFILE_POLICY
        with pytest.raises(ValueError):
            PROFILE_POLICY.get_eligible("crazy_profile")

    def test_get_eligible_all_three(self):
        from services.sentiment.configs import PROFILE_POLICY
        for pid in ["short", "mid", "long"]:
            r = PROFILE_POLICY.get_eligible(pid)
            assert isinstance(r, tuple)


class TestFactorICThresholds:
    def test_strong_higher_than_weak(self):
        from services.sentiment.configs import IC_GATE
        assert IC_GATE.strong_threshold > IC_GATE.weak_threshold

    def test_pos_pct_above_50(self):
        from services.sentiment.configs import IC_GATE
        assert IC_GATE.pos_pct_threshold > 0.5
