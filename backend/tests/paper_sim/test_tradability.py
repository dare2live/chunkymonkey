"""Paper Sim — tradability mask 单测 (Codex C-C 2026-05-15).

T+1 (隐式) + 停牌 + 涨跌停 + segment-aware ±%.
"""
from __future__ import annotations

import pytest

from services.paper_sim.tradability import (
    get_segment_limit_pct,
    is_suspended,
    is_limit_up_today,
    is_limit_down_today,
    can_buy,
    can_sell,
)


# ============ segment 推断 ============


@pytest.mark.parametrize("code, expected_up", [
    ("600519", 0.10),   # 主板
    ("000001", 0.10),
    ("300750", 0.20),   # 创业板
    ("688981", 0.20),   # 科创板
    ("689009", 0.20),   # 科创板
    ("830974", 0.30),   # 北交所
    ("430489", 0.30),   # 北交所
    ("", 0.10),         # fallback
])
def test_segment_limit_pct(code, expected_up):
    up, down = get_segment_limit_pct(code)
    assert up == expected_up
    assert down == -expected_up


# ============ 停牌 ============


def test_suspended_volume_zero():
    assert is_suspended({"volume": 0, "amount": 1000, "close": 10})


def test_suspended_amount_zero():
    assert is_suspended({"volume": 100, "amount": 0, "close": 10})


def test_suspended_close_zero():
    assert is_suspended({"volume": 100, "amount": 1000, "close": 0})


def test_suspended_none_row():
    assert is_suspended(None)


def test_not_suspended_normal():
    assert not is_suspended({"volume": 100, "amount": 1000, "close": 10})


# ============ 涨停 ============


def test_limit_up_exact_threshold():
    """close = pre_close × 1.10 → 主板涨停."""
    k = {"close": 11.0}
    assert is_limit_up_today(k, pre_close=10.0, up_pct=0.10)


def test_limit_up_just_below():
    """close = pre_close × 1.099 → 未涨停 (低于 1bp 容差)."""
    k = {"close": 10.99}
    # 容差 = 1 bp, threshold = 10 * (1 + 0.10 - 0.0001) = 10.999
    # close 10.99 < 10.999 → 未触发
    assert not is_limit_up_today(k, pre_close=10.0, up_pct=0.10)


def test_limit_up_chinext_20pct():
    """创业板 ±20%."""
    k = {"close": 12.0}
    assert is_limit_up_today(k, pre_close=10.0, up_pct=0.20)


def test_limit_up_pre_close_missing():
    """缺 pre_close → 不触发 (保守不 mask, 允许 buy)."""
    assert not is_limit_up_today({"close": 100}, pre_close=None, up_pct=0.10)
    assert not is_limit_up_today({"close": 100}, pre_close=0, up_pct=0.10)


# ============ 跌停 ============


def test_limit_down_exact_threshold():
    """close = pre_close × 0.90 → 主板跌停."""
    k = {"close": 9.0}
    assert is_limit_down_today(k, pre_close=10.0, down_pct=-0.10)


def test_limit_down_just_above():
    """close = pre_close × 0.901 → 未跌停."""
    k = {"close": 9.01}
    assert not is_limit_down_today(k, pre_close=10.0, down_pct=-0.10)


# ============ 综合 can_buy / can_sell ============


def test_can_buy_normal():
    k = {"volume": 100, "amount": 1000, "close": 10.5}
    assert can_buy(k, pre_close=10.0, stock_code="600519")


def test_can_buy_suspended():
    k = {"volume": 0, "amount": 0, "close": 0}
    assert not can_buy(k, pre_close=10.0, stock_code="600519")


def test_can_buy_limit_up():
    k = {"volume": 100, "amount": 1000, "close": 11.0}
    assert not can_buy(k, pre_close=10.0, stock_code="600519")    # 涨停


def test_can_sell_limit_down():
    k = {"volume": 100, "amount": 1000, "close": 9.0}
    assert not can_sell(k, pre_close=10.0, stock_code="600519")   # 跌停


def test_can_sell_normal():
    k = {"volume": 100, "amount": 1000, "close": 9.5}
    assert can_sell(k, pre_close=10.0, stock_code="600519")


def test_can_buy_no_pre_close_passes():
    """缺 pre_close → 不 mask (保守允许), 历史数据缺失场景."""
    k = {"volume": 100, "amount": 1000, "close": 100.0}
    assert can_buy(k, pre_close=None, stock_code="600519")
