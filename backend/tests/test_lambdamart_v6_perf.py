"""Retrain stall Fix 1 (Claude general aacdbf94, 2026-05-19) — perf 回退 + int64 path 单测.

测试 build_walk_forward_windows + assert_pit_strict int64 fast-path 等价性 + perf.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from scripts.run_p0b_lambdamart_v6 import (  # type: ignore
    RankPanel,
    assert_pit_strict,
    build_walk_forward_windows,
)


def _make_synthetic_panel(n_dates: int = 500, n_stocks_per_date: int = 100) -> RankPanel:
    """Synthetic panel for perf testing.

    n_dates=500 (跨 ~2 年, 满足 min_train_months=6 + embargo + 多 OOS month) × n_stocks_per_date=100
    = 50K rows, fast for unit test. Real retrain has 700 dates × 5600 stocks = 3.93M rows.
    """
    start = pd.Timestamp("2023-01-02")
    dates = pd.bdate_range(start=start, periods=n_dates).strftime("%Y-%m-%d").to_numpy()
    signal_dates = np.repeat(dates, n_stocks_per_date)
    stock_codes = np.tile(
        np.array([f"S{i:04d}" for i in range(n_stocks_per_date)], dtype="<U6"),
        n_dates,
    )
    n = len(signal_dates)
    X = np.random.RandomState(42).rand(n, 5).astype(np.float32)
    y_relevance = np.random.RandomState(43).randint(0, 5, size=n).astype(np.int32)
    y_raw = np.random.RandomState(44).randn(n).astype(np.float32)
    feature_columns = [f"f{i}" for i in range(5)]
    return RankPanel(
        X=X,
        y_raw=y_raw,
        y_relevance=y_relevance,
        signal_dates=signal_dates.astype("<U10"),
        stock_codes=stock_codes,
        feature_columns=feature_columns,
    )


def test_assert_pit_strict_int64_fast_path_equivalent():
    """Fix 1: int64 fast-path 跟 string legacy path 结果一致 (PASS case)."""
    train_str = np.array(["2023-01-02", "2023-01-15", "2023-02-01"], dtype="<U10")
    test_str = np.array(["2023-02-15", "2023-03-01"], dtype="<U10")
    train_int = pd.to_datetime(pd.Series(train_str)).values.astype("datetime64[D]").astype("int64")
    test_int = pd.to_datetime(pd.Series(test_str)).values.astype("datetime64[D]").astype("int64")
    # Both paths should PASS (no PIT leak)
    assert_pit_strict(train_str, test_str)
    assert_pit_strict(train_int, test_int)


def test_assert_pit_strict_int64_leak_detection():
    """Fix 1: int64 fast-path 能 raise on PIT leak (last_train == first_test)."""
    train_int = np.array([19000, 19001], dtype=np.int64)
    test_int = np.array([19001, 19002], dtype=np.int64)
    with pytest.raises(AssertionError, match="PIT leak"):
        assert_pit_strict(train_int, test_int)


def test_assert_pit_strict_int64_legit_boundary():
    """Fix 1: int64 边界 (last_train + 1 == first_test) 应 PASS."""
    train_int = np.array([19000, 19001], dtype=np.int64)
    test_int = np.array([19002, 19003], dtype=np.int64)
    assert_pit_strict(train_int, test_int)


@pytest.mark.perf
def test_build_walk_forward_windows_perf_regression():
    """Fix 1 perf 回退测试: synthetic 200 dates × 100 stocks panel, build < 5 sec.

    Baseline (Mac 8C): old impl on 3.93M rows took 15 min. Synthetic 20K rows 应 < 5 sec.
    """
    panel = _make_synthetic_panel(n_dates=500, n_stocks_per_date=100)
    start = time.time()
    windows = build_walk_forward_windows(panel, min_train_months=6, forward_months=1)
    elapsed = time.time() - start
    assert elapsed < 5.0, f"build took {elapsed:.2f}s, expected < 5s (Fix 1 regression?)"
    assert len(windows) >= 1, f"should produce >=1 window for {len(panel.signal_dates)//100}-date panel, got {len(windows)}"
    # PIT invariant: train_end < test_start for each window
    for w in windows:
        assert w.train_end < w.test_start, f"PIT leak in window: {w.train_end} >= {w.test_start}"
