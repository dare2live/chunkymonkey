"""Tests for PreparedSignalSet (phase 2 performance optimization)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.perf import PreparedSignalSet, build_from_df


def _make_df(n=100):
    """Synthetic signal df."""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "stock_code": rng.choice(["600000", "000001", "300001"], n),
        "signal_date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "signal_bar_idx": rng.integers(0, 252, n),
        "stage": rng.choice(["pre_rise", "rise", "consolidation"], n),
        "body_ratio": rng.uniform(0, 1, n),
        "lower_shadow_ratio": rng.uniform(0, 1, n),
        "close_position": rng.uniform(0, 1, n),
        "volume_relative": rng.uniform(0.5, 3.0, n),
    })


class TestBuildFromDf:
    def test_build_from_df_basic(self):
        df = _make_df(50)
        pss = build_from_df(df)
        assert len(pss) == 50
        assert pss.body_ratio.dtype == np.float32
        assert pss.signal_bar_idx.dtype == np.int32
        assert pss.signal_date.dtype == "datetime64[D]"
        assert pss.stage.dtype == np.int8
        # stage_codec maps back
        assert pss.stage_codec is not None
        assert len(pss.stage_codec) == 3

    def test_build_from_df_missing_cols(self):
        df = pd.DataFrame({"stock_code": ["x"], "signal_date": ["2024-01-01"]})
        with pytest.raises(ValueError, match="missing required cols"):
            build_from_df(df)

    def test_stock_slice_contiguous(self):
        df = _make_df(60)
        pss = build_from_df(df)
        # stock_slice should be dict of (start, end) for each unique code
        for code, (start, end) in pss.stock_slice_start_end.items():
            # Verify slice contains only this code
            assert all(pss.stock_code[start:end] == code), f"slice {start}:{end} for {code} not contiguous"


class TestFilter:
    def test_filter_body_ratio_min(self):
        df = _make_df(100)
        pss = build_from_df(df)
        mask = pss.filter(body_ratio_min=0.5)
        assert mask.dtype == bool
        assert len(mask) == 100
        # All masked rows have body_ratio >= 0.5
        assert np.all(pss.body_ratio[mask] >= 0.5)

    def test_filter_multi_field(self):
        df = _make_df(100)
        pss = build_from_df(df)
        mask = pss.filter(
            body_ratio_min=0.3,
            lower_shadow_min=0.2,
            volume_relative_min=1.0,
        )
        assert np.all(pss.body_ratio[mask] >= 0.3)
        assert np.all(pss.lower_shadow_ratio[mask] >= 0.2)
        assert np.all(pss.volume_relative[mask] >= 1.0)

    def test_filter_stage_subset(self):
        df = _make_df(100)
        pss = build_from_df(df)
        # Pick first 2 stages by int code
        stage_codes = list(pss.stage_codec.keys())[:2]
        mask = pss.filter(stages=stage_codes)
        assert np.all(np.isin(pss.stage[mask], stage_codes))

    def test_filter_no_args_returns_all_true(self):
        df = _make_df(20)
        pss = build_from_df(df)
        mask = pss.filter()
        assert mask.sum() == 20  # all True
