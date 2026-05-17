"""Tests for market_cap_decile features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.features.market_cap_decile import build_market_cap_features, feature_names


class TestMarketCapDecile:
    def test_basic_decile_ranking(self):
        """100 stocks 1 date, market cap 1-100 → decile 1-10."""
        df = pd.DataFrame({
            "stock_code": [f"60000{i:02d}" for i in range(100)],
            "signal_date": ["2024-01-15"] * 100,
            "market_cap_total": list(range(1, 101)),
        })
        out = build_market_cap_features(df)
        # Smallest cap → decile 1
        assert out.iloc[0]["mc_decile"] == 1
        # Largest cap → decile 10
        assert out.iloc[-1]["mc_decile"] == 10
        # is_small / is_large
        assert out.iloc[0]["mc_is_small"] == 1
        assert out.iloc[-1]["mc_is_large"] == 1

    def test_rank_normalized_range(self):
        df = pd.DataFrame({
            "stock_code": [f"6000{i:02d}" for i in range(20)],
            "signal_date": ["2024-01-15"] * 20,
            "market_cap_total": list(range(1, 21)),
        })
        out = build_market_cap_features(df)
        assert out["mc_rank_normalized"].min() >= 0.0
        assert out["mc_rank_normalized"].max() <= 1.0

    def test_per_date_ranking(self):
        """跨 date 各自 ranking, 不跨期混."""
        df = pd.DataFrame({
            "stock_code": ["a", "b", "a", "b"],
            "signal_date": ["2024-01-15", "2024-01-15", "2024-02-15", "2024-02-15"],
            "market_cap_total": [100, 200, 50, 150],
        })
        out = build_market_cap_features(df)
        # date 1: a=100 (smaller, smaller rank), b=200 (larger)
        # date 2: a=50 (smaller), b=150 (larger)
        d1 = out[out["signal_date"] == "2024-01-15"]
        d2 = out[out["signal_date"] == "2024-02-15"]
        assert d1.iloc[0]["mc_rank_normalized"] < d1.iloc[1]["mc_rank_normalized"]
        assert d2.iloc[0]["mc_rank_normalized"] < d2.iloc[1]["mc_rank_normalized"]

    def test_log_cap(self):
        df = pd.DataFrame({
            "stock_code": ["a", "b"],
            "signal_date": ["2024-01-15"] * 2,
            "market_cap_total": [1_000_000, 100_000_000],  # 1M vs 100M
        })
        out = build_market_cap_features(df)
        # log10 1M = 6, log10 100M = 8
        assert abs(out.iloc[0]["mc_log_cap"] - 6.0) < 0.01
        assert abs(out.iloc[1]["mc_log_cap"] - 8.0) < 0.01

    def test_handle_missing_cap(self):
        df = pd.DataFrame({
            "stock_code": ["a", "b", "c"],
            "signal_date": ["2024-01-15"] * 3,
            "market_cap_total": [100, np.nan, 0],
        })
        out = build_market_cap_features(df)
        # NaN cap → mc_log_cap NaN
        assert pd.isna(out.iloc[1]["mc_log_cap"])

    def test_feature_names(self):
        names = feature_names()
        assert len(names) == 6
        assert "mc_decile" in names
