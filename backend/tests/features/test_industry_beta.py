"""Tests for industry_beta features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.features.industry_beta import build_industry_beta_features, feature_names


def _make_stock_returns(n_dates=80, n_stocks=3):
    """Synthetic stock returns with industry tag."""
    rng = np.random.default_rng(42)
    rows = []
    for s in range(n_stocks):
        ind = "tech" if s < 2 else "finance"
        for d in range(n_dates):
            rows.append({
                "stock_code": f"6000{s:02d}",
                "signal_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=d),
                "industry": ind,
                "ret_1d": rng.normal(0, 0.02),
            })
    return pd.DataFrame(rows)


def _make_industry_returns(stock_df):
    """Industry-level returns aggregated from stocks."""
    ind_ret = (
        stock_df.groupby(["industry", "signal_date"])["ret_1d"]
        .mean()
        .reset_index()
    )
    return ind_ret


class TestIndustryBeta:
    def test_basic_beta_computation(self):
        stock_df = _make_stock_returns(n_dates=80, n_stocks=3)
        ind_df = _make_industry_returns(stock_df)
        out = build_industry_beta_features(stock_df, ind_df, lookback_days=60)
        # 输出列存在
        for col in feature_names(60):
            assert col in out.columns

    def test_dtype_float32(self):
        stock_df = _make_stock_returns(n_dates=80, n_stocks=3)
        ind_df = _make_industry_returns(stock_df)
        out = build_industry_beta_features(stock_df, ind_df, lookback_days=60)
        for col in feature_names(60):
            assert out[col].dtype == np.float32

    def test_beta_finite_after_lookback(self):
        """After 60 days, beta should be defined (not NaN)."""
        stock_df = _make_stock_returns(n_dates=100, n_stocks=2)
        ind_df = _make_industry_returns(stock_df)
        out = build_industry_beta_features(stock_df, ind_df, lookback_days=60)
        # After day 60 there should be finite beta
        late = out[out["signal_date"] >= pd.Timestamp("2024-03-15")]
        n_finite = late["ind_beta_60d"].notna().sum()
        assert n_finite > 0
