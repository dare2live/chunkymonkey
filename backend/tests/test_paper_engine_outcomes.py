"""Phase δ D2 — outcomes.py 单测。"""
from __future__ import annotations

import pytest

from services.paper_engine.outcomes import (
    LOSS_THRESHOLD,
    WIN_THRESHOLD,
    build_decision_outcome,
    classify_outcome,
    compute_forward_returns,
)


class TestClassifyOutcome:
    def test_win(self):
        assert classify_outcome(0.05) == "win"
        assert classify_outcome(0.02) == "win"

    def test_loss(self):
        assert classify_outcome(-0.05) == "loss"
        assert classify_outcome(-0.02) == "loss"

    def test_flat(self):
        assert classify_outcome(0.01) == "flat"
        assert classify_outcome(-0.01) == "flat"

    def test_active(self):
        assert classify_outcome(None) == "active"


class TestComputeForwardReturns:
    def test_basic_progression(self):
        # entry=100, closes: D+1..D+30 (升序)
        future = [102, 103, 104, 105, 106, 107, 108, 109, 110, 111] + [115] * 20
        out = compute_forward_returns(100.0, future)
        # D+5 close=106, ret = 0.06
        assert abs(out["fwd_ret_5d"] - 0.06) < 1e-6
        # D+10 close=111, ret = 0.11
        assert abs(out["fwd_ret_10d"] - 0.11) < 1e-6
        # D+30 close=115, ret = 0.15
        assert abs(out["fwd_ret_30d"] - 0.15) < 1e-6

    def test_short_series_returns_none(self):
        # 只有 3 日数据
        out = compute_forward_returns(100.0, [101, 102, 103])
        assert out["fwd_ret_5d"] is None
        assert out["fwd_ret_10d"] is None

    def test_handles_halted(self):
        # 中间有 None (停牌)
        out = compute_forward_returns(100.0, [102, None, 104, None, 106])
        # D+5 = 106 仍可用
        assert abs(out["fwd_ret_5d"] - 0.06) < 1e-6

    def test_max_dd_within_30d(self):
        # 涨到 110 后跌到 90
        future = [105, 110, 100, 95, 90] + [None] * 25
        out = compute_forward_returns(100.0, future)
        # peak=110, low=90 → dd = -20/110 ≈ -0.1818
        assert abs(out["fwd_max_dd_30d"] - (-0.1818)) < 0.01

    def test_zero_entry_returns_none(self):
        out = compute_forward_returns(0.0, [105, 106])
        assert out["fwd_ret_5d"] is None


class TestBuildDecisionOutcome:
    def test_full_outcome(self):
        future = [102, 103, 104, 105, 108]  # D+5 = 108 → ret = 0.08 = win
        out = build_decision_outcome(
            decision_date="2026-05-01", stock_code="600519",
            entry_price=100.0,
            rank_in_date=1, pred_score=0.85,
            primary_formula_id="macd_golden_cross", industry_l1="白酒",
            future_closes=future,
        )
        assert out["decision_type"] == "BUY"
        assert abs(out["fwd_ret_5d"] - 0.08) < 1e-6
        assert out["outcome_5d"] == "win"
        # 没 D+10 / D+30 → active
        assert out["outcome_10d"] == "active"
        assert out["outcome_30d"] == "active"
