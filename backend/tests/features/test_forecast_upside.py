"""Tests for forecast_upside module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.features.forecast_upside import (
    compute_target_pe_self_median,
    compute_target_pe_industry_median,
    compute_target_pe_blend,
    compute_upside,
    build_forecast_upside_features,
    feature_names,
)


class TestComputeUpside:
    def test_basic_upside_positive(self):
        eps = pd.Series([1.0, 2.0, 0.5])
        pe = pd.Series([15.0, 15.0, 15.0])
        price = pd.Series([10.0, 20.0, 10.0])
        upside = compute_upside(eps, pe, price)
        # row 0: 1.0 * 15 / 10 - 1 = 0.5
        assert abs(upside.iloc[0] - 0.5) < 1e-4
        # row 1: 2.0 * 15 / 20 - 1 = 0.5
        assert abs(upside.iloc[1] - 0.5) < 1e-4
        # row 2: 0.5 * 15 / 10 - 1 = -0.25
        assert abs(upside.iloc[2] - (-0.25)) < 1e-4

    def test_negative_eps_returns_nan(self):
        eps = pd.Series([1.0, -0.5, 0.0])  # negative + zero EPS
        pe = pd.Series([15.0, 15.0, 15.0])
        price = pd.Series([10.0, 10.0, 10.0])
        upside = compute_upside(eps, pe, price, eps_floor=0.0)
        assert not pd.isna(upside.iloc[0])
        assert pd.isna(upside.iloc[1])
        assert pd.isna(upside.iloc[2])  # zero is not > floor

    def test_zero_price_returns_nan(self):
        eps = pd.Series([1.0])
        pe = pd.Series([15.0])
        price = pd.Series([0.0])
        upside = compute_upside(eps, pe, price)
        assert pd.isna(upside.iloc[0])

    def test_clip_extreme(self):
        """Upside should be bounded."""
        eps = pd.Series([100.0])
        pe = pd.Series([100.0])
        price = pd.Series([0.001])  # tiny price → huge upside
        upside = compute_upside(eps, pe, price, upside_clip=(-0.9, 5.0))
        assert upside.iloc[0] == 5.0


class TestTargetPeSelfMedian:
    def test_rolling_median(self):
        # 90 days of PE 10-50, median = 30
        pe = pd.Series(np.linspace(10, 50, 90))
        result = compute_target_pe_self_median(pe, window_days=60, min_periods=10)
        assert not result.iloc[-1] != result.iloc[-1]  # not NaN
        # Last 60 days roughly 24-50, median ~37
        assert 25 <= result.iloc[-1] <= 50

    def test_negative_pe_handled(self):
        pe = pd.Series([10, -5, 20, 30, 40] * 20)  # 100 rows
        result = compute_target_pe_self_median(pe, window_days=10, min_periods=3)
        # Negative values → NaN, median should compute from non-NaN
        assert result.notna().sum() > 0
        assert (result.dropna() > 0).all()


class TestTargetPeIndustryMedian:
    def test_per_date_per_industry_median(self):
        panel = pd.DataFrame({
            "signal_date": ["2024-01-01"] * 4 + ["2024-01-02"] * 4,
            "industry": ["金融", "金融", "科技", "科技"] * 2,
            "pe_ttm": [10, 20, 30, 40, 12, 22, 32, 42],
        })
        result = compute_target_pe_industry_median(panel)
        # 2024-01-01 金融: median(10, 20) = 15
        assert result.iloc[0] == 15
        assert result.iloc[1] == 15
        # 2024-01-01 科技: median(30, 40) = 35
        assert result.iloc[2] == 35
        # 2024-01-02 金融: median(12, 22) = 17
        assert result.iloc[4] == 17


class TestTargetPeBlend:
    def test_blend_weights(self):
        self_m = pd.Series([10.0, 20.0])
        ind_m = pd.Series([30.0, 40.0])
        # blend_self_weight=0.5 → average
        blend = compute_target_pe_blend(self_m, ind_m, blend_self_weight=0.5)
        assert abs(blend.iloc[0] - 20.0) < 1e-4
        assert abs(blend.iloc[1] - 30.0) < 1e-4

    def test_clip_bounds(self):
        self_m = pd.Series([1.0, 200.0])
        ind_m = pd.Series([1.0, 200.0])
        blend = compute_target_pe_blend(self_m, ind_m, floor=5, cap=80)
        # 1 → clipped to 5
        assert blend.iloc[0] == 5
        assert blend.iloc[1] == 80

    def test_nan_fallback(self):
        self_m = pd.Series([np.nan, 20.0])
        ind_m = pd.Series([30.0, np.nan])
        blend = compute_target_pe_blend(self_m, ind_m, blend_self_weight=0.6)
        # 0.6*nan + 0.4*30 = nan, fallback to self (nan), then industry (30)
        assert abs(blend.iloc[0] - 30.0) < 1e-4
        # 0.6*20 + 0.4*nan = nan, fallback to self (20)
        assert abs(blend.iloc[1] - 20.0) < 1e-4


class TestBuildForecastUpsideFeatures:
    def test_end_to_end(self):
        # 2 stocks × 100 days
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        rows = []
        for stock in ["600000", "600036"]:
            for i, d in enumerate(dates):
                rows.append({
                    "stock_code": stock,
                    "signal_date": d,
                    "industry": "金融",
                    "pe_ttm": 15 + i * 0.1,
                    "fy1_eps_consensus": 1.5,
                    "close": 20.0,
                })
        panel = pd.DataFrame(rows)
        out = build_forecast_upside_features(panel)
        for col in feature_names():
            assert col in out.columns, f"missing {col}"
        # Late dates: upside_self should be finite
        late = out[out["signal_date"] >= "2024-03-01"]
        assert late["upside_self"].notna().sum() > 0

    def test_feature_names_count(self):
        names = feature_names()
        assert len(names) == 6
        for n in ["target_pe_self", "upside_blend"]:
            assert n in names
