"""Tests for PreparedPanel (phase 4 perf)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.perf.prepared_panel import (
    PreparedPanel, build_panel_from_df, compute_walk_forward_windows
)


def _make_df(n_dates=12, n_stocks=10):
    """Synthetic panel: 12 months × 10 stocks = 120 rows × 5 features + 3 labels."""
    rng = np.random.default_rng(42)
    rows = []
    for m in range(n_dates):
        signal_date = pd.Timestamp("2024-01-01") + pd.DateOffset(months=m)
        for s in range(n_stocks):
            rows.append({
                "stock_code": f"6000{s:02d}",
                "signal_date": signal_date,
                "feat_a": rng.normal(),
                "feat_b": rng.normal(),
                "feat_c": rng.normal(),
                "feat_d": rng.normal(),
                "feat_e": rng.normal(),
                "fwd_cost_after_5d": rng.normal() * 0.05,
                "fwd_cost_after_10d": rng.normal() * 0.07,
                "fwd_cost_after_20d": rng.normal() * 0.1,
            })
    return pd.DataFrame(rows)


class TestPreparedPanel:
    def test_build_from_df_basic(self):
        df = _make_df()
        panel = build_panel_from_df(df, label_col="fwd_cost_after_20d")
        assert panel.X.dtype == np.float32
        assert panel.y.dtype == np.float32
        assert panel.X.shape == (120, 5)  # 5 features
        assert panel.feature_columns == ["feat_a", "feat_b", "feat_c", "feat_d", "feat_e"]
        assert panel.date_codes is not None
        assert panel.stock_codes is not None
        # 3 alt labels populated
        assert panel.y_5d is not None
        assert panel.y_10d is not None
        assert panel.y_20d is not None

    def test_dtype_validation(self):
        with pytest.raises(ValueError, match="X must be float32"):
            PreparedPanel(
                X=np.zeros((10, 3), dtype=np.float64),
                y=np.zeros(10, dtype=np.float32),
            )
        with pytest.raises(ValueError, match="y must be float32"):
            PreparedPanel(
                X=np.zeros((10, 3), dtype=np.float32),
                y=np.zeros(10, dtype=np.float64),
            )

    def test_walk_forward_windows(self):
        df = _make_df(n_dates=12, n_stocks=10)
        panel = build_panel_from_df(df)
        panel = compute_walk_forward_windows(panel, min_train_months=6, forward_months=1)
        # 12 months, min_train=6, forward=1 → expanding windows from month 6 to 11
        assert panel.n_windows == 6
        # First window: train [0..5], test [6]
        w0 = panel.window_indices[0]
        assert len(w0["train_idx"]) == 6 * 10  # 6 months × 10 stocks
        assert len(w0["test_idx"]) == 10        # 1 month × 10 stocks

    def test_get_window(self):
        df = _make_df()
        panel = build_panel_from_df(df)
        panel = compute_walk_forward_windows(panel, min_train_months=6)
        X_train, y_train, X_test, y_test = panel.get_window(0)
        assert X_train.shape[1] == panel.n_features
        assert X_train.shape[0] + X_test.shape[0] <= len(panel)
        assert X_train.dtype == np.float32
        assert y_train.dtype == np.float32

    def test_drop_na_label(self):
        df = _make_df()
        # Inject NaN in label column
        df.loc[0:5, "fwd_cost_after_20d"] = np.nan
        panel = build_panel_from_df(df, label_col="fwd_cost_after_20d")
        assert len(panel) == 120 - 6  # 6 NaN dropped
