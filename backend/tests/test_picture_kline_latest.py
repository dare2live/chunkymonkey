"""Phase γ D2 — kline_latest 单测。"""
from __future__ import annotations

import pytest

from services.picture.kline_latest import compute_chg_pct, derive_kline_latest


class TestComputeChgPct:
    def test_basic_up(self):
        # 10 → 11, 涨 10%
        assert abs(compute_chg_pct(11.0, 10.0) - 0.10) < 1e-6

    def test_basic_down(self):
        assert abs(compute_chg_pct(9.0, 10.0) - (-0.10)) < 1e-6

    def test_flat(self):
        assert compute_chg_pct(10.0, 10.0) == 0.0

    def test_none_today_returns_none(self):
        assert compute_chg_pct(None, 10.0) is None

    def test_none_prev_returns_none(self):
        assert compute_chg_pct(10.0, None) is None

    def test_zero_prev_returns_none(self):
        # 防 div by zero
        assert compute_chg_pct(10.0, 0.0) is None


class TestDeriveKlineLatest:
    def test_full(self):
        out = derive_kline_latest(today_close=10.50, prev_close=10.00)
        assert out["latest_close"] == 10.50
        assert abs(out["chg_pct"] - 0.05) < 1e-6

    def test_missing_prev(self):
        out = derive_kline_latest(today_close=10.50, prev_close=None)
        assert out["latest_close"] == 10.50
        assert out["chg_pct"] is None

    def test_missing_today(self):
        out = derive_kline_latest(today_close=None, prev_close=10.0)
        assert out["latest_close"] is None
        assert out["chg_pct"] is None
