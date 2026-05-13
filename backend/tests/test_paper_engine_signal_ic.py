"""Phase δ D1 — signal_ic.py 单测。"""
from __future__ import annotations

import pytest

from services.paper_engine.signal_ic import _pearson, _rank, spearman_ic


class TestRank:
    def test_basic(self):
        # [10, 20, 15, 5] → ranks [2, 4, 3, 1]
        ranks = _rank([10, 20, 15, 5])
        assert ranks == [2.0, 4.0, 3.0, 1.0]

    def test_ties_use_average(self):
        # [10, 10, 20] → ranks [1.5, 1.5, 3]
        ranks = _rank([10, 10, 20])
        assert ranks == [1.5, 1.5, 3.0]


class TestPearson:
    def test_perfect_positive(self):
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        assert abs(_pearson(x, y) - 1.0) < 1e-6

    def test_perfect_negative(self):
        x = [1, 2, 3, 4, 5]
        y = [5, 4, 3, 2, 1]
        assert abs(_pearson(x, y) - (-1.0)) < 1e-6

    def test_zero_variance_returns_none(self):
        assert _pearson([1, 1, 1], [2, 3, 4]) is None


class TestSpearmanIC:
    def test_perfect_rank_correlation(self):
        # 分数 = 收益 完全正相关 → IC=1
        scores = [0.9, 0.5, 0.1, 0.3, 0.7]
        rets   = [0.10, 0.05, 0.01, 0.03, 0.07]  # 同 rank
        ic = spearman_ic(scores, rets)
        assert abs(ic - 1.0) < 1e-6

    def test_perfect_anti_correlation(self):
        scores = [0.9, 0.5, 0.1, 0.3, 0.7]
        rets   = [-0.10, -0.05, -0.01, -0.03, -0.07]  # 反 rank
        ic = spearman_ic(scores, rets)
        assert abs(ic - (-1.0)) < 1e-6

    def test_handles_none_pairs(self):
        scores = [0.9, 0.5, None, 0.3]
        rets   = [0.10, None, 0.05, 0.03]
        # 只剩 (0.9, 0.10), (0.3, 0.03) — n=2 < 3 → None
        assert spearman_ic(scores, rets) is None

    def test_n_lt_3_returns_none(self):
        assert spearman_ic([0.5], [0.05]) is None
        assert spearman_ic([0.5, 0.7], [0.05, 0.07]) is None

    def test_empty_returns_none(self):
        assert spearman_ic([], []) is None
