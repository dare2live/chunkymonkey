"""Phase δ D1 — weights.py 单测。"""
from __future__ import annotations

import pytest

from services.paper_engine.weights import (
    derive_target_weights,
    equal_weight,
    rank_decay,
    score_weighted,
)


class TestEqualWeight:
    def test_basic_5_picks(self):
        picks = [{"stock_code": f"S{i}"} for i in range(5)]
        out = equal_weight(picks, cash_reserve=0.10)
        assert len(out) == 5
        # (1 - 0.10) / 5 = 0.18
        for w in out:
            assert abs(w["target_weight"] - 0.18) < 1e-6
        # sum = 0.9, cash_reserve 0.1
        assert abs(sum(w["target_weight"] for w in out) - 0.9) < 1e-6

    def test_empty(self):
        assert equal_weight([], 0.1) == []


class TestScoreWeighted:
    def test_higher_score_more_weight(self):
        picks = [
            {"stock_code": "A", "pred_score": 0.9},
            {"stock_code": "B", "pred_score": 0.5},
            {"stock_code": "C", "pred_score": 0.1},
        ]
        out = score_weighted(picks, cash_reserve=0.0)
        # 总 score = 1.5, 三人分别 0.6 / 0.333 / 0.067
        assert abs(out[0]["target_weight"] - 0.6) < 1e-3
        assert abs(out[1]["target_weight"] - (0.5/1.5)) < 1e-3
        # 总和 = 1.0
        assert abs(sum(w["target_weight"] for w in out) - 1.0) < 1e-3

    def test_zero_scores_fallback_to_equal(self):
        # 全 0 分 → 退回等权
        picks = [{"stock_code": f"S{i}", "pred_score": 0} for i in range(4)]
        out = score_weighted(picks, cash_reserve=0.20)
        # (1-0.2)/4 = 0.20
        for w in out:
            assert abs(w["target_weight"] - 0.20) < 1e-6


class TestRankDecay:
    def test_rank_1_highest_weight(self):
        picks = [
            {"stock_code": f"S{i}", "rank_in_date": i + 1}
            for i in range(5)
        ]
        out = rank_decay(picks, cash_reserve=0.0, halflife=10)
        # rank=1 应权重最大
        assert out[0]["target_weight"] > out[4]["target_weight"]
        # 总和 = 1.0
        assert abs(sum(w["target_weight"] for w in out) - 1.0) < 1e-3


class TestDeriveTargetWeights:
    def test_default_method_is_equal_weight(self):
        picks = [{"stock_code": "A"}, {"stock_code": "B"}]
        out = derive_target_weights(picks, method="equal_weight", cash_reserve=0.0)
        for w in out:
            assert w["target_weight"] == 0.5

    def test_score_weighted_method(self):
        picks = [
            {"stock_code": "A", "pred_score": 0.8},
            {"stock_code": "B", "pred_score": 0.2},
        ]
        out = derive_target_weights(picks, method="score_weighted", cash_reserve=0.0)
        assert out[0]["target_weight"] > out[1]["target_weight"]

    def test_unknown_method_falls_back_to_equal(self):
        picks = [{"stock_code": "A"}, {"stock_code": "B"}]
        out = derive_target_weights(picks, method="nonexistent_strategy", cash_reserve=0.0)
        for w in out:
            assert w["target_weight"] == 0.5
