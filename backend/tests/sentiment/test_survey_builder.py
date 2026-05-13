"""单测: survey_builder.py — orchestrator."""
from __future__ import annotations

import pytest


class TestBuildSurveyFeatures:
    def test_empty_input(self):
        from services.sentiment.survey_builder import build_survey_features
        out = build_survey_features({}, "2026-05-01", "2026-05-10")
        assert out == []

    def test_single_stock_single_event(self):
        from services.sentiment.survey_builder import build_survey_features
        # 关闭 validate (单股 + 1 事件 → 桶分布过窄会触发 ValidationError)
        out = build_survey_features(
            {"A": [("2026-05-01", 3)]},
            grid_start="2026-05-01", grid_end="2026-05-05",
            validate=False,
        )
        # 5 天都应有快照 (windows 都不为空)
        assert len(out) == 5
        for r in out:
            assert r.stock_code == "A"
            assert r.survey_count_60d == 1
            assert r.survey_bin in ("冷", "温", "热", "狂")
            # 1 在 [1,3) → 温
            assert r.survey_bin == "温"

    def test_bin_distribution_with_diverse_events(self):
        """构造分散事件, 桶分布应通过 validate."""
        from services.sentiment.survey_builder import build_survey_features
        events = {
            "A": [("2026-05-01", 3)],                                              # 1 次 → 温
            "B": [("2026-04-30", 2), ("2026-05-01", 2)],                           # 2 次 → 温
            "C": [("2026-04-28", 1), ("2026-04-30", 1), ("2026-05-01", 1)],        # 3 次 → 热
            "D": [("2026-04-27", 1), ("2026-04-28", 1), ("2026-04-29", 1),
                  ("2026-04-30", 1), ("2026-05-01", 1), ("2026-05-02", 1),
                  ("2026-05-03", 1), ("2026-05-04", 1)],                           # 6+ 次 → 狂
            "E": [("2026-05-04", 1)],                                              # 1 次 → 温
        }
        out = build_survey_features(events,
            grid_start="2026-05-04", grid_end="2026-05-04",
            validate=True)
        # 5 股 × 1 日 = 5 行, 但 D 在 5-04 应是狂 (6+ events 已积累)
        bins = [r.survey_bin for r in out]
        assert "温" in bins
        assert "热" in bins or "狂" in bins  # 至少有更高桶

    def test_bin_assignment_correctness(self):
        from services.sentiment.survey_builder import build_survey_features
        out = build_survey_features(
            {"X": [(f"2026-04-{d:02d}", 1) for d in range(20, 30)]},  # 10 次 → 狂
            grid_start="2026-04-29", grid_end="2026-04-29",
            validate=False,
        )
        assert out[0].survey_bin == "狂"


class TestBinDistribution:
    def test_counts_bins(self):
        from services.sentiment.survey_builder import bin_distribution, SurveyFeatureRow
        rows = [
            SurveyFeatureRow("A", "2026-05-01", 1, 1, 1, 1, "冷"),
            SurveyFeatureRow("B", "2026-05-01", 2, 2, 2, 2, "温"),
            SurveyFeatureRow("C", "2026-05-01", 3, 3, 3, 3, "温"),
        ]
        dist = bin_distribution(rows)
        assert dist == {"冷": 1, "温": 2}
