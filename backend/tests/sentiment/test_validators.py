"""单测: validators.py — 数据契约."""
from __future__ import annotations

import pytest


def _make(stock="A", date="2026-05-01", c30=1, c60=2, i30=3, i60=5):
    from services.sentiment.window_calculator import SurveyWindowResult
    return SurveyWindowResult(
        stock_code=stock, as_of_date=date,
        count_30d=c30, count_60d=c60, inst_30d=i30, inst_60d=i60,
    )


class TestValidateSurveyWindow:
    def test_valid_passes(self):
        from services.sentiment.validators import validate_survey_window
        validate_survey_window(_make())  # 不 raise

    def test_empty_stock_raises(self):
        from services.sentiment.validators import validate_survey_window, SentimentValidationError
        with pytest.raises(SentimentValidationError):
            validate_survey_window(_make(stock=""))

    def test_bad_date_raises(self):
        from services.sentiment.validators import validate_survey_window, SentimentValidationError
        with pytest.raises(SentimentValidationError):
            validate_survey_window(_make(date="20260501"))

    def test_negative_count_raises(self):
        from services.sentiment.validators import validate_survey_window, SentimentValidationError
        with pytest.raises(SentimentValidationError):
            validate_survey_window(_make(c30=-1))

    def test_30_gt_60_raises(self):
        from services.sentiment.validators import validate_survey_window, SentimentValidationError
        with pytest.raises(SentimentValidationError, match="count_30d"):
            validate_survey_window(_make(c30=5, c60=2))


class TestValidateBinDistribution:
    def test_normal_passes(self):
        from services.sentiment.validators import validate_bin_distribution
        validate_bin_distribution({"冷": 100, "温": 50, "热": 20, "狂": 5})

    def test_empty_raises(self):
        from services.sentiment.validators import validate_bin_distribution, SentimentValidationError
        with pytest.raises(SentimentValidationError):
            validate_bin_distribution({})

    def test_single_bin_raises(self):
        from services.sentiment.validators import validate_bin_distribution, SentimentValidationError
        with pytest.raises(SentimentValidationError, match="过窄"):
            validate_bin_distribution({"冷": 100})

    def test_extreme_majority_raises(self):
        """如果 "狂" 桶占比 > 50%, 说明阈值错了."""
        from services.sentiment.validators import validate_bin_distribution, SentimentValidationError
        with pytest.raises(SentimentValidationError, match="占比异常"):
            validate_bin_distribution({"冷": 10, "温": 10, "热": 10, "狂": 100})


class TestCollectSurveyIssues:
    def test_collects_without_raise(self):
        from services.sentiment.validators import collect_survey_issues
        bad = _make(c30=10, c60=5)  # 不合规
        issues = collect_survey_issues([bad])
        assert len(issues) == 1
        assert "count_30d" in issues[0].issue
