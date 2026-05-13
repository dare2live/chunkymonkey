"""Phase γ D2 — valuation 派生单测。"""
from __future__ import annotations

import pytest

from services.picture.valuation import (
    compute_pe_percentile,
    compute_upside_pct,
    derive_valuation,
)


class TestComputePePercentile:
    def test_below_p30_is_low(self):
        assert compute_pe_percentile(10.0, p30=15.0, p50=25.0, p70=40.0) == 0.15

    def test_between_p30_and_p50_mid(self):
        assert compute_pe_percentile(20.0, p30=15.0, p50=25.0, p70=40.0) == 0.40

    def test_between_p50_and_p70(self):
        assert compute_pe_percentile(30.0, p30=15.0, p50=25.0, p70=40.0) == 0.60

    def test_above_p70_is_high(self):
        assert compute_pe_percentile(50.0, p30=15.0, p50=25.0, p70=40.0) == 0.85

    def test_none_pe_returns_none(self):
        assert compute_pe_percentile(None, 15, 25, 40) is None

    def test_negative_pe_returns_none(self):
        # 亏损股 PE 为负, 不算估值
        assert compute_pe_percentile(-5.0, 15, 25, 40) is None

    def test_missing_threshold_returns_none(self):
        assert compute_pe_percentile(20.0, p30=None, p50=25, p70=40) is None


class TestComputeUpsidePct:
    def test_basic_positive_upside(self):
        # peer_target = 20 × 5 = 100, close 80, upside = 25%
        out = compute_upside_pct(close=80.0, peer_pe_median=20.0, eps_ttm=5.0)
        assert abs(out - 25.0) < 1e-6

    def test_negative_upside_clamped_to_zero(self):
        # peer_target = 10 × 3 = 30, close 50, upside = -40% → clamp 0
        out = compute_upside_pct(close=50.0, peer_pe_median=10.0, eps_ttm=3.0)
        assert out == 0.0

    def test_huge_upside_capped(self):
        # peer_target = 100 × 10 = 1000, close 10, upside = 9900% → cap 80%
        out = compute_upside_pct(close=10.0, peer_pe_median=100.0, eps_ttm=10.0)
        assert out == 80.0

    def test_zero_close_returns_none(self):
        assert compute_upside_pct(close=0.0, peer_pe_median=20.0, eps_ttm=5.0) is None

    def test_none_eps_returns_none(self):
        assert compute_upside_pct(close=80.0, peer_pe_median=20.0, eps_ttm=None) is None

    def test_negative_eps_returns_none(self):
        # 亏损股 EPS 负, 不算 upside
        assert compute_upside_pct(close=80.0, peer_pe_median=20.0, eps_ttm=-2.0) is None


class TestDeriveValuation:
    def test_full_happy_path(self):
        out = derive_valuation(
            pe_ttm=20.0, pe_p30=15.0, pe_p50=25.0, pe_p70=40.0,
            close=80.0, peer_pe_median=22.0, eps_ttm=4.5,
        )
        assert out["valuation_pe"] == 20.0
        assert out["valuation_pe_pctile"] == 0.40
        # peer_target = 22 × 4.5 = 99, close 80, upside = 23.75%
        assert abs(out["valuation_upside_pct"] - 23.75) < 1e-6

    def test_only_pe_present_returns_partial(self):
        out = derive_valuation(pe_ttm=25.0)
        assert out["valuation_pe"] == 25.0
        assert out["valuation_pe_pctile"] is None
        assert out["valuation_upside_pct"] is None

    def test_negative_pe_returns_none_for_pe(self):
        out = derive_valuation(pe_ttm=-3.0)
        assert out["valuation_pe"] is None
