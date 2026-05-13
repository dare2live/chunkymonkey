"""单测: execution_model.py + registry.py"""
from __future__ import annotations

import pytest


class TestGlobalRegistry:
    def test_singleton_exists(self):
        from services.trading_config import EXECUTION_MODEL
        assert EXECUTION_MODEL is not None
        assert EXECUTION_MODEL.version

    def test_version_format(self):
        from services.trading_config import EXECUTION_MODEL
        # 期望: "ε.X-YYYY-MM-DD"
        assert "-" in EXECUTION_MODEL.version

    def test_default_buy_is_vwap(self):
        from services.trading_config import EXECUTION_MODEL
        assert EXECUTION_MODEL.buy_pricing.mode == "vwap"

    def test_default_horizon_trading_days(self):
        from services.trading_config import EXECUTION_MODEL
        from services.trading_config.horizon import HorizonUnit
        assert EXECUTION_MODEL.horizon_unit == HorizonUnit.TRADING_DAYS

    def test_summary_serializable(self):
        from services.trading_config import EXECUTION_MODEL
        s = EXECUTION_MODEL.summary()
        assert isinstance(s, dict)
        assert "buy_mode" in s
        assert "round_trip_cost_pct" in s
        # 验证 round_trip_cost 合理 (A 股 ~25 bps)
        assert 0.001 < s["round_trip_cost_pct"] < 0.01

    def test_frozen(self):
        from services.trading_config import EXECUTION_MODEL
        with pytest.raises(Exception):
            EXECUTION_MODEL.version = "hacked"

    def test_get_execution_model_returns_same(self):
        from services.trading_config import EXECUTION_MODEL, get_execution_model
        assert get_execution_model() is EXECUTION_MODEL
