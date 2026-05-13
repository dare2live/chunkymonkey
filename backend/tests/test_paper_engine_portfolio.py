"""Phase δ D1 — portfolio.py 单测。"""
from __future__ import annotations

import pytest

from services.paper_engine.portfolio import (
    compute_drawdown,
    compute_kpis,
    compute_nav,
    compute_top_industry,
)


class TestComputeNAV:
    def test_basic_nav(self):
        positions = {
            "600519": {"shares": 100, "open_price": 1800.0},
            "000001": {"shares": 1000, "open_price": 12.0},
        }
        prices = {"600519": 1850.0, "000001": 12.5}
        out = compute_nav(cash=100000, positions=positions, today_prices=prices)
        # 1850×100 + 12.5×1000 = 185000 + 12500 = 197500
        # + cash 100000 = 297500
        assert out["nav_value"] == 297500.0
        assert out["position_value"] == 197500.0
        assert out["cash"] == 100000
        assert out["position_count"] == 2
        assert abs(out["cash_pct"] - 100000/297500) < 1e-6

    def test_halted_uses_open_price(self):
        # 没今日价格 → 用 open_price 当作估值
        positions = {"600519": {"shares": 100, "open_price": 1800.0}}
        prices = {}  # 缺数据
        out = compute_nav(cash=100000, positions=positions, today_prices=prices)
        assert out["position_value"] == 180000.0  # 100 × 1800

    def test_empty_portfolio(self):
        out = compute_nav(cash=1_000_000, positions={}, today_prices={})
        assert out["nav_value"] == 1_000_000
        assert out["position_value"] == 0.0
        assert out["cash_pct"] == 1.0
        assert out["position_count"] == 0


class TestTopIndustry:
    def test_single_industry(self):
        mtm = {"600519": 50000, "000858": 30000}  # 白酒
        ind = {"600519": "白酒", "000858": "白酒"}
        top, pct = compute_top_industry(mtm, ind)
        assert top == "白酒"
        assert pct == 1.0

    def test_multiple_industries(self):
        mtm = {"600519": 100000, "000001": 50000, "000858": 50000}
        ind = {"600519": "白酒", "000001": "银行", "000858": "白酒"}
        top, pct = compute_top_industry(mtm, ind)
        assert top == "白酒"
        assert abs(pct - 150000/200000) < 1e-6

    def test_empty_portfolio(self):
        top, pct = compute_top_industry({}, {})
        assert top is None
        assert pct == 0.0


class TestDrawdown:
    def test_new_peak(self):
        # nav 升 → 新 peak, dd=0
        dd, peak = compute_drawdown(nav_value=1100, peak_nav=1000)
        assert dd == 0.0
        assert peak == 1100

    def test_drawdown(self):
        # 从 1200 跌到 1050 → dd = -150/1200 = -0.125
        dd, peak = compute_drawdown(nav_value=1050, peak_nav=1200)
        assert peak == 1200
        assert abs(dd - (-0.125)) < 1e-6


class TestComputeKPIs:
    def test_basic_series(self):
        # 5 日 NAV 简单上涨
        series = [
            {"snapshot_date": f"2026-05-0{i+1}", "nav_value": 1_000_000 * (1 + 0.01*(i+1)),
             "hs300_cum_ret": 0.005*(i+1)}
            for i in range(5)
        ]
        kpis = compute_kpis(series, starting_nav=1_000_000)
        assert kpis["n_days"] == 5
        assert abs(kpis["nav_chg_pct"] - 0.05) < 1e-3
        # excess = 0.05 - 0.025 = 0.025
        assert abs(kpis["excess_pct"] - 0.025) < 1e-3
        # 5 日全涨 → monthly_win = 1.0
        assert kpis["monthly_win"] == 1.0

    def test_with_drawdown(self):
        # NAV: 1.0 → 1.1 → 0.9 (max_dd = -0.18)
        series = [
            {"snapshot_date": "D1", "nav_value": 1_000_000, "hs300_cum_ret": 0.0},
            {"snapshot_date": "D2", "nav_value": 1_100_000, "hs300_cum_ret": 0.02},
            {"snapshot_date": "D3", "nav_value": 900_000,   "hs300_cum_ret": 0.03},
        ]
        kpis = compute_kpis(series, starting_nav=1_000_000)
        # peak=1.1M, low=0.9M → dd = -0.1818
        assert abs(kpis["max_dd_pct"] - (-0.1818)) < 0.01
        # 最后 cum_ret = -0.1
        assert abs(kpis["nav_chg_pct"] - (-0.1)) < 1e-3

    def test_empty_returns_empty(self):
        assert compute_kpis([], 1_000_000) == {}
