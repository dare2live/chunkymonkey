"""单测: horizon.py"""
from __future__ import annotations

import pytest


class TestAddHoldingPeriod:
    def test_calendar_days(self):
        from services.trading_config.horizon import HorizonUnit, add_holding_period
        # 2026-05-01 → +5 自然日 = 2026-05-06
        r = add_holding_period("2026-05-01", 5, HorizonUnit.CALENDAR_DAYS)
        assert r == "2026-05-06"

    def test_trading_days(self):
        from services.trading_config.horizon import HorizonUnit, add_holding_period
        # 模拟交易日 (跳周末)
        tds = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
               "2026-05-11", "2026-05-12"]
        # 2026-05-04 + 5 交易日 = 2026-05-11
        r = add_holding_period("2026-05-04", 5, HorizonUnit.TRADING_DAYS, trading_dates=tds)
        assert r == "2026-05-11"

    def test_trading_days_out_of_range_returns_none(self):
        from services.trading_config.horizon import HorizonUnit, add_holding_period
        tds = ["2026-05-04", "2026-05-05"]
        # 5 个交易日越界
        r = add_holding_period("2026-05-04", 5, HorizonUnit.TRADING_DAYS, trading_dates=tds)
        assert r is None

    def test_trading_days_requires_dates(self):
        from services.trading_config.horizon import HorizonUnit, add_holding_period
        with pytest.raises(ValueError):
            add_holding_period("2026-05-04", 5, HorizonUnit.TRADING_DAYS)

    def test_unknown_unit(self):
        from services.trading_config.horizon import add_holding_period
        with pytest.raises(ValueError):
            add_holding_period("2026-05-04", 5, "invalid_unit")  # type: ignore


class TestCountHoldingPeriod:
    def test_calendar(self):
        from services.trading_config.horizon import HorizonUnit, count_holding_period
        r = count_holding_period("2026-05-01", "2026-05-06", HorizonUnit.CALENDAR_DAYS)
        assert r == 5

    def test_trading(self):
        from services.trading_config.horizon import HorizonUnit, count_holding_period
        tds = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
               "2026-05-11", "2026-05-12"]
        r = count_holding_period("2026-05-04", "2026-05-11",
                                  HorizonUnit.TRADING_DAYS, trading_dates=tds)
        assert r == 5

    def test_unit_consistency_solves_bug4(self):
        """audit Bug #4: 5 交易日 ≠ 5 自然日 (有周末)."""
        from services.trading_config.horizon import HorizonUnit, count_holding_period
        tds = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
               "2026-05-11", "2026-05-12"]
        # 2026-05-04 (周一) → 2026-05-11 (下周一): 5 交易日 = 7 自然日
        td_count = count_holding_period("2026-05-04", "2026-05-11",
                                         HorizonUnit.TRADING_DAYS, trading_dates=tds)
        cd_count = count_holding_period("2026-05-04", "2026-05-11", HorizonUnit.CALENDAR_DAYS)
        assert td_count == 5
        assert cd_count == 7
        assert td_count != cd_count   # 这就是 audit Bug #4 的根源
