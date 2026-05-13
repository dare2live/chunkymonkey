"""单测: optimization/composite.py — 多目标聚合"""
from __future__ import annotations

import pytest

from services.optimization.objectives import ObjectiveValues


class TestCompositeWeights:
    def test_default_sums_to_one(self):
        from services.optimization.composite import DEFAULT_OBJECTIVE_WEIGHTS as W
        total = (W.calmar_w + W.sortino_w + W.sharpe_w + W.stability_w
                 + W.pain_w + W.ulcer_w + W.tail_w)
        assert abs(total - 1.0) < 0.001

    def test_invalid_weights_raise(self):
        from services.optimization.composite import CompositeWeights
        with pytest.raises(ValueError):
            CompositeWeights(calmar_w=0.5, sortino_w=0.5, sharpe_w=0.5,
                              stability_w=0.5, pain_w=0.5, ulcer_w=0.5, tail_w=0.5)


class TestCompositeScore:
    def test_high_calmar_high_score(self):
        from services.optimization.composite import composite_score
        obj = ObjectiveValues(sharpe=1.0, calmar=3.0, sortino=2.0,
                              pain_index=0.05, ulcer_index=0.07, tail_risk=-0.10,
                              stability=0.85, n_traded=20)
        s = composite_score(obj)
        assert s > 0   # 正贡献占优

    def test_deep_pain_reduces_score(self):
        from services.optimization.composite import composite_score
        good = ObjectiveValues(sharpe=1.0, calmar=2.0, sortino=1.5,
                               pain_index=0.02, ulcer_index=0.03, tail_risk=-0.05,
                               stability=0.85, n_traded=20)
        bad  = ObjectiveValues(sharpe=1.0, calmar=2.0, sortino=1.5,
                               pain_index=0.20, ulcer_index=0.25, tail_risk=-0.30,
                               stability=0.85, n_traded=20)
        assert composite_score(good) > composite_score(bad)

    def test_sample_weight_effect(self):
        """n_traded 越大, log(1+n) 越大, score 越高."""
        from services.optimization.composite import composite_score
        small = ObjectiveValues(sharpe=1.0, calmar=2.0, sortino=1.5,
                                pain_index=0.05, ulcer_index=0.07, tail_risk=-0.10,
                                stability=0.85, n_traded=5)
        large = ObjectiveValues(sharpe=1.0, calmar=2.0, sortino=1.5,
                                pain_index=0.05, ulcer_index=0.07, tail_risk=-0.10,
                                stability=0.85, n_traded=100)
        assert composite_score(large) > composite_score(small)


class TestContributions:
    def test_returns_dict(self):
        from services.optimization.composite import score_contributions
        obj = ObjectiveValues(sharpe=1.0, calmar=2.0, sortino=1.5,
                              pain_index=0.05, ulcer_index=0.07, tail_risk=-0.10,
                              stability=0.85, n_traded=20)
        c = score_contributions(obj)
        assert "calmar" in c and "sortino" in c
        assert c["pain_penalty"] < 0
