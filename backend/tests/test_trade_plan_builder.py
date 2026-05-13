"""Phase γ D4 — trade_plan builder 单测。"""
from __future__ import annotations

import pytest

from services.trade_plan.builder import build_trade_plan


class TestBuildTradePlan:
    def test_happy_path_returns_all_fields(self):
        out = build_trade_plan(
            close=100.0,
            atr_14=3.0,
            atr_14_pct=0.03,
            entry_level_20=102.0,
            entry_level_55=108.0,
            stop_level_20_2n=94.0,
            expected_horizon_days=20,
        )
        # entry_target = 100 × (1 + 0.03×0.3) = 100.9
        assert abs(out["entry_target_price"] - 100.9) < 0.01
        # entry_aggressive = max(102, 100×1.02) = 102
        assert out["entry_aggressive_price"] == 102.0
        # entry_max = entry_level_55 = 108
        assert out["entry_max_price"] == 108.0
        # exit_target_1 = 100.9 × (1 + 2×0.03) = 100.9 × 1.06 = 106.954
        assert abs(out["exit_target_1_price"] - 106.954) < 0.01
        # exit_target_2 = 100.9 × (1 + 4×0.03) = 100.9 × 1.12 = 113.008
        assert abs(out["exit_target_2_price"] - 113.008) < 0.01
        # exit_stop = stop_level_20_2n = 94
        assert out["exit_stop_price"] == 94.0
        # R/R = (106.954 - 100.9) / (100.9 - 94) = 6.054 / 6.9 ≈ 0.877
        assert abs(out["risk_reward_ratio"] - 0.877) < 0.01
        assert out["expected_horizon_days"] == 20

    def test_atr_pct_auto_computed_when_missing(self):
        out = build_trade_plan(close=100.0, atr_14=3.0)
        # atr_pct = 3/100 = 0.03 → entry_target = 100.9 (same as above)
        assert abs(out["entry_target_price"] - 100.9) < 0.01

    def test_missing_close_returns_empty(self):
        out = build_trade_plan(close=None, atr_14=3.0)
        assert out["entry_target_price"] is None
        assert "close_missing" in out["reason_codes_json"]

    def test_missing_atr_returns_empty(self):
        out = build_trade_plan(close=100.0, atr_14=None)
        assert out["entry_target_price"] is None
        assert "atr_14_missing" in out["reason_codes_json"]

    def test_negative_atr_returns_empty(self):
        out = build_trade_plan(close=100.0, atr_14=-1.0)
        assert out["entry_target_price"] is None

    def test_fallback_entry_max_when_no_55_level(self):
        # entry_level_55 missing → entry_max = entry_target × 1.05
        out = build_trade_plan(
            close=100.0, atr_14=3.0,
            entry_level_20=102.0, entry_level_55=None,
            stop_level_20_2n=94.0,
        )
        # entry_target=100.9, entry_max=100.9×1.05=105.945
        assert abs(out["entry_max_price"] - 105.945) < 0.01

    def test_fallback_stop_when_missing(self):
        # stop_level_20_2n missing → exit_stop = close - 2×ATR = 100 - 6 = 94
        out = build_trade_plan(close=100.0, atr_14=3.0, stop_level_20_2n=None)
        assert out["exit_stop_price"] == 94.0

    def test_expected_horizon_defaults_to_20(self):
        out = build_trade_plan(close=100.0, atr_14=3.0, expected_horizon_days=None)
        assert out["expected_horizon_days"] == 20

    def test_reason_codes_includes_atr_and_rr(self):
        out = build_trade_plan(close=100.0, atr_14=3.0)
        reasons = out["reason_codes_json"]
        assert any("atr_14" in r for r in reasons)
        assert any("R/R" in r for r in reasons)

    def test_high_volatility_stock(self):
        # ATR 10% 的高波动股
        out = build_trade_plan(close=50.0, atr_14=5.0, stop_level_20_2n=40.0)
        # entry_target = 50 × (1 + 0.1×0.3) = 50 × 1.03 = 51.5
        assert abs(out["entry_target_price"] - 51.5) < 0.01
        # exit_target_1 = 51.5 × 1.2 = 61.8
        assert abs(out["exit_target_1_price"] - 61.8) < 0.01
        # R/R = (61.8 - 51.5) / (51.5 - 40) = 10.3 / 11.5 ≈ 0.896
        assert out["risk_reward_ratio"] is not None
