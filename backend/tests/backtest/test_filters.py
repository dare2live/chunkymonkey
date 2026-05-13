"""单测: backtest/filters.py — 指数代码过滤 + net_ret cap"""
from __future__ import annotations

import pytest


class TestIsIndexCode:
    def test_csi300(self):
        from services.backtest.filters import is_index_code
        assert is_index_code("000300") is True

    def test_chinext_index(self):
        from services.backtest.filters import is_index_code
        assert is_index_code("399006") is True

    def test_normal_stock(self):
        from services.backtest.filters import is_index_code
        assert is_index_code("000001") is False   # 平安银行
        assert is_index_code("600519") is False   # 茅台
        assert is_index_code("300033") is False

    def test_none_safe(self):
        from services.backtest.filters import is_index_code
        assert is_index_code(None) is False
        assert is_index_code("") is False


class TestCapNetRet:
    def test_normal_pass_through(self):
        from services.backtest.filters import cap_net_ret
        assert cap_net_ret(0.05) == 0.05
        assert cap_net_ret(-0.10) == -0.10
        assert cap_net_ret(0.0) == 0.0

    def test_extreme_high_capped(self):
        """复权 spike: +2000% 应被 cap 到 +500%."""
        from services.backtest.filters import cap_net_ret, NET_RET_MAX
        assert cap_net_ret(20.0) == NET_RET_MAX

    def test_extreme_low_capped(self):
        """理论极限: -150% 应被 cap 到 -100%."""
        from services.backtest.filters import cap_net_ret, NET_RET_MIN
        assert cap_net_ret(-1.5) == NET_RET_MIN

    def test_boundary_max(self):
        from services.backtest.filters import cap_net_ret, NET_RET_MAX
        assert cap_net_ret(NET_RET_MAX) == NET_RET_MAX
        assert cap_net_ret(NET_RET_MAX + 0.01) == NET_RET_MAX
