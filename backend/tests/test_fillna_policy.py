"""Tests for PreparedPanel feature missing-value policy."""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.perf.prepared_panel import apply_feature_fillna_policy, build_panel_from_df


def test_count_feature_nan_fills_zero():
    df = pd.DataFrame({"lhb_count_30d": [np.nan, 2.0]})

    out = apply_feature_fillna_policy(df, ["lhb_count_30d"], audit=False)

    assert out["lhb_count_30d"].tolist() == [0.0, 2.0]


def test_numeric_feature_nan_stays_nan():
    df = pd.DataFrame({"sm_ret_60d": [np.nan, 0.12]})

    out = apply_feature_fillna_policy(df, ["sm_ret_60d"], audit=False)

    assert pd.isna(out.loc[0, "sm_ret_60d"])
    assert out.loc[1, "sm_ret_60d"] == 0.12


def test_categorical_feature_nan_fills_missing():
    df = pd.DataFrame({"industry_pit_confidence": [None, "observed_snapshot"]})

    out = apply_feature_fillna_policy(df, ["industry_pit_confidence"], audit=False)

    assert out["industry_pit_confidence"].tolist() == ["MISSING", "observed_snapshot"]


def test_build_panel_preserves_numeric_nan_but_fills_count_zero():
    df = pd.DataFrame({
        "stock_code": ["600000", "600001"],
        "signal_date": ["2024-01-02", "2024-01-02"],
        "lhb_count_30d": [np.nan, 3.0],
        "sm_ret_60d": [np.nan, 0.2],
        "fwd_cost_after_20d": [0.01, 0.02],
    })

    panel = build_panel_from_df(
        df,
        label_col="fwd_cost_after_20d",
        feature_cols=["lhb_count_30d", "sm_ret_60d"],
    )

    assert panel.X[0, 0] == 0.0
    assert np.isnan(panel.X[0, 1])
