"""RankIC 单测.

正常 / 全相同 / 太少 stocks / NaN 路径.
"""
from __future__ import annotations

import math

from services.ml_ranking.rank_ic import compute_cross_section_ic, compute_rank_ic


def test_perfect_correlation():
    scores = [1.0, 2.0, 3.0, 4.0, 5.0]
    rets = [0.01, 0.02, 0.03, 0.04, 0.05]
    ic = compute_cross_section_ic(scores, rets)
    assert ic is not None and abs(ic - 1.0) < 1e-9


def test_perfect_anti_correlation():
    scores = [1.0, 2.0, 3.0, 4.0, 5.0]
    rets = [0.05, 0.04, 0.03, 0.02, 0.01]
    ic = compute_cross_section_ic(scores, rets)
    assert ic is not None and abs(ic - (-1.0)) < 1e-9


def test_no_signal_zero_correlation():
    """随机配对 IC 应近 0."""
    import random
    random.seed(42)
    scores = list(range(20))
    rets = list(range(20))
    random.shuffle(rets)
    ic = compute_cross_section_ic(scores, [float(r) for r in rets])
    assert ic is not None and abs(ic) < 0.5  # 随机, 不强求 0


def test_too_few_pairs_returns_none():
    assert compute_cross_section_ic([1.0], [0.5]) is None
    assert compute_cross_section_ic([], []) is None


def test_nan_filtered_out():
    scores = [1.0, float("nan"), 3.0, 4.0]
    rets = [0.01, 0.02, 0.03, 0.04]
    ic = compute_cross_section_ic(scores, rets)
    # 3 valid pairs → 仍可算
    assert ic is not None


def test_compute_rank_ic_stitched_basic():
    """两个 signal_date 各 3 stocks, 完美正相关 → mean_rank_ic ≈ 1.0."""
    rows = [
        {"signal_date": "2024-01-15", "score": 1, "fwd_cost_after_10d": 0.01},
        {"signal_date": "2024-01-15", "score": 2, "fwd_cost_after_10d": 0.02},
        {"signal_date": "2024-01-15", "score": 3, "fwd_cost_after_10d": 0.03},
        {"signal_date": "2024-02-15", "score": 1, "fwd_cost_after_10d": 0.05},
        {"signal_date": "2024-02-15", "score": 2, "fwd_cost_after_10d": 0.10},
        {"signal_date": "2024-02-15", "score": 3, "fwd_cost_after_10d": 0.15},
    ]
    result = compute_rank_ic(rows)
    assert result.n_dates == 2
    assert abs(result.mean_rank_ic - 1.0) < 1e-9


def test_compute_rank_ic_skips_single_stock_dates():
    rows = [
        {"signal_date": "2024-01-15", "score": 1, "fwd_cost_after_10d": 0.01},  # only 1 stock
        {"signal_date": "2024-02-15", "score": 1, "fwd_cost_after_10d": 0.05},
        {"signal_date": "2024-02-15", "score": 2, "fwd_cost_after_10d": 0.10},
    ]
    result = compute_rank_ic(rows)
    assert result.n_dates == 1  # only 02-15
    assert result.n_dates_skipped == 1  # 01-15 single


def test_compute_rank_ic_handles_missing_label():
    rows = [
        {"signal_date": "2024-01-15", "score": 1, "fwd_cost_after_10d": None},
        {"signal_date": "2024-01-15", "score": 2, "fwd_cost_after_10d": 0.02},
        {"signal_date": "2024-01-15", "score": 3, "fwd_cost_after_10d": 0.03},
    ]
    result = compute_rank_ic(rows)
    # 2 valid pairs (score 2 & 3)
    assert result.n_dates == 1


def test_compute_rank_ic_empty():
    result = compute_rank_ic([])
    assert result.n_dates == 0
    assert math.isnan(result.mean_rank_ic)
