"""单测: bin_assigner.py — 桶分配."""
from __future__ import annotations

import pytest
import math


class TestAssignSurveyBin:
    def test_zero_is_cold(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        assert assign_survey_bin(0) == "冷"

    def test_none_is_cold(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        assert assign_survey_bin(None) == "冷"

    def test_nan_is_cold(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        assert assign_survey_bin(float("nan")) == "冷"

    def test_boundary_cold_to_warm(self):
        """count=1 → "温" (默认 cold_max=1, [0,1) 是冷)."""
        from services.sentiment.bin_assigner import assign_survey_bin
        from services.sentiment.configs import SURVEY_BIN
        assert assign_survey_bin(SURVEY_BIN.cold_max) == "温"

    def test_boundary_warm_to_hot(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        from services.sentiment.configs import SURVEY_BIN
        assert assign_survey_bin(SURVEY_BIN.warm_max) == "热"

    def test_boundary_hot_to_extreme(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        from services.sentiment.configs import SURVEY_BIN
        assert assign_survey_bin(SURVEY_BIN.hot_max) == "狂"

    def test_large_is_extreme(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        assert assign_survey_bin(1000) == "狂"

    def test_negative_raises(self):
        from services.sentiment.bin_assigner import assign_survey_bin
        with pytest.raises(ValueError):
            assign_survey_bin(-1)

    def test_custom_thresholds(self):
        """注入派生 config: 验证不硬编码."""
        from services.sentiment.bin_assigner import assign_survey_bin
        from services.sentiment.configs import SurveyBinThresholds
        custom = SurveyBinThresholds(cold_max=10, warm_max=20, hot_max=30)
        assert assign_survey_bin(5,  custom) == "冷"
        assert assign_survey_bin(15, custom) == "温"
        assert assign_survey_bin(25, custom) == "热"
        assert assign_survey_bin(99, custom) == "狂"


class TestBinEdges:
    def test_edges_cover_all_ranges(self):
        from services.sentiment.bin_assigner import bin_edges, assign_survey_bin
        edges = bin_edges()
        assert len(edges) == 4
        # 验证 edges 与 assign 结果一致
        for lo, hi, label in edges:
            if hi == float("inf"):
                v = lo + 100
            else:
                v = (lo + hi) / 2
            assert assign_survey_bin(v) == label

    def test_all_labels_returned(self):
        from services.sentiment.bin_assigner import all_bin_labels
        labels = all_bin_labels()
        assert labels == ("冷", "温", "热", "狂")
