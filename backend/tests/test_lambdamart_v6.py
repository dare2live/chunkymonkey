from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.run_p0b_lambdamart_v6 import (
    RankPanel,
    assert_pit_strict,
    build_walk_forward_windows,
)
from services.perf.prepared_panel import make_lambdarank_groups


def _panel_from_df(df: pd.DataFrame) -> RankPanel:
    df_sorted = df.sort_values(["signal_date", "stock_code"]).reset_index(drop=True)
    feature_cols = ["feat_a", "feat_b"]
    X, y_rel, groups = make_lambdarank_groups(
        df_sorted,
        df_sorted["signal_date"].drop_duplicates().tolist(),
        feature_cols=feature_cols,
    )
    assert int(groups.sum()) == len(df_sorted)
    return RankPanel(
        X=X,
        y_raw=df_sorted["fwd_cost_after_20d"].to_numpy(dtype=np.float32),
        y_relevance=y_rel,
        signal_dates=pd.to_datetime(df_sorted["signal_date"]).dt.strftime("%Y-%m-%d").to_numpy(),
        stock_codes=df_sorted["stock_code"].astype(str).to_numpy(),
        feature_columns=feature_cols,
    )


def test_group_sizes_correct():
    df = pd.DataFrame([
        {"stock_code": "A", "signal_date": "2024-01-02", "feat_a": 1.0, "feat_b": np.nan, "fwd_cost_after_20d": 0.01},
        {"stock_code": "B", "signal_date": "2024-01-02", "feat_a": 2.0, "feat_b": 2.0, "fwd_cost_after_20d": 0.02},
        {"stock_code": "C", "signal_date": "2024-01-02", "feat_a": 3.0, "feat_b": 3.0, "fwd_cost_after_20d": 0.03},
        {"stock_code": "A", "signal_date": "2024-01-03", "feat_a": 4.0, "feat_b": 4.0, "fwd_cost_after_20d": -0.01},
        {"stock_code": "B", "signal_date": "2024-01-03", "feat_a": 5.0, "feat_b": 5.0, "fwd_cost_after_20d": 0.04},
    ])

    X, y_rel, groups = make_lambdarank_groups(
        df,
        ["2024-01-02", "2024-01-03"],
        feature_cols=["feat_a", "feat_b"],
    )

    assert groups.tolist() == [3, 2]
    assert int(groups.sum()) == len(df)
    assert X.shape == (5, 2)
    assert y_rel.shape == (5,)
    assert X[0, 1] == -9999.0


def test_relevance_label_encoding():
    rows = []
    for i in range(10):
        rows.append({
            "stock_code": f"S{i:02d}",
            "signal_date": "2024-02-01",
            "feat_a": float(i),
            "feat_b": float(10 - i),
            "fwd_cost_after_20d": float(i),
        })
    df = pd.DataFrame(rows)

    _, y_rel, groups = make_lambdarank_groups(
        df,
        ["2024-02-01"],
        feature_cols=["feat_a", "feat_b"],
    )

    assert groups.tolist() == [10]
    assert y_rel.min() == 0
    assert y_rel.max() == 4
    assert y_rel[0] == 0
    assert y_rel[-1] == 4


def test_pit_strict_no_leakage():
    rows = []
    for m in range(13):
        signal_date = pd.Timestamp("2024-01-15") + pd.DateOffset(months=m)
        for s in range(2):
            rows.append({
                "stock_code": f"S{s:02d}",
                "signal_date": signal_date,
                "feat_a": float(m),
                "feat_b": float(s),
                "fwd_cost_after_20d": float(m + s),
            })
    rows.append({
        "stock_code": "S99",
        "signal_date": pd.Timestamp("2026-01-15"),
        "feat_a": 99.0,
        "feat_b": 99.0,
        "fwd_cost_after_20d": 99.0,
    })
    panel = _panel_from_df(pd.DataFrame(rows))

    windows = build_walk_forward_windows(
        panel,
        min_train_months=6,
        forward_months=1,
    )

    future_date = "2026-01-15"
    assert any(future_date in set(panel.signal_dates[w.test_idx]) for w in windows)
    for window in windows:
        train_dates = panel.signal_dates[window.train_idx]
        test_dates = panel.signal_dates[window.test_idx]
        assert_pit_strict(train_dates, test_dates)
        if future_date in set(test_dates):
            assert future_date not in set(train_dates)
