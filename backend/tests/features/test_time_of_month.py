"""Tests for time-of-month features."""
from __future__ import annotations

import pandas as pd
import pytest

from services.features.time_of_month import build_time_of_month_features, feature_names


class TestTimeOfMonth:
    def test_day_of_month(self):
        df = pd.DataFrame({"signal_date": ["2024-01-15", "2024-02-28", "2024-03-01"]})
        out = build_time_of_month_features(df)
        assert list(out["tom_day_of_month"]) == [15, 28, 1]

    def test_month_phase(self):
        df = pd.DataFrame({"signal_date": ["2024-01-03", "2024-01-15", "2024-01-25"]})
        out = build_time_of_month_features(df)
        # early (1-7) / mid (8-22) / late (23+)
        assert list(out["tom_month_phase"]) == [0, 1, 2]

    def test_is_first_last_week(self):
        df = pd.DataFrame({"signal_date": ["2024-01-02", "2024-01-15", "2024-01-30"]})
        out = build_time_of_month_features(df)
        assert list(out["tom_is_first_week"]) == [1, 0, 0]
        assert list(out["tom_is_last_week"]) == [0, 0, 1]

    def test_days_to_month_end(self):
        df = pd.DataFrame({"signal_date": ["2024-01-31", "2024-02-28"]})
        out = build_time_of_month_features(df)
        # Jan 31 → 0 days to month end, Feb 28 → 1 day to Feb 29 (leap year 2024)
        assert out.iloc[0]["tom_days_to_month_end"] == 0
        assert out.iloc[1]["tom_days_to_month_end"] == 1

    def test_feature_names(self):
        names = feature_names()
        assert len(names) == 7
        assert "tom_day_of_month" in names

    def test_dtypes(self):
        df = pd.DataFrame({"signal_date": ["2024-01-15"]})
        out = build_time_of_month_features(df)
        for col in feature_names():
            assert out[col].dtype.kind == "i", f"{col} dtype not int: {out[col].dtype}"
