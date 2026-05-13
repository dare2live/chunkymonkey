"""Paper Sim v2 — _vwap helper 单测 (Rule 6 反例修正后的回归测试).

关键约束: A 股 volume 单位是"手"(100 股) 不是"股", VWAP 必须 / 100 才落在 [low, high].
旧公式 amount / volume 错 100 倍, 该单测防回退.
"""
from __future__ import annotations

import pytest

from services.paper_sim.driver import _vwap


def test_vwap_uses_hundred_share_lot_conversion():
    """实际 A 股 K 线样本: 600000 2025-01-02 ohlc=[10.30,10.42,10.05,10.13]"""
    k = {
        "open": 10.30, "high": 10.42, "low": 10.05, "close": 10.13,
        "volume": 1_000_000,         # 100 万手
        "amount": 1_019_000_000,     # 10.19 亿元 (vwap ≈ 10.19)
    }
    v = _vwap(k)
    assert v == pytest.approx(10.19)
    # 必须落在 [low, high]
    assert k["low"] <= v <= k["high"]


def test_vwap_falls_back_to_close_when_no_volume():
    """停牌当日 volume=0 → fallback close."""
    k = {"close": 12.5, "volume": 0, "amount": 0}
    assert _vwap(k) == 12.5


def test_vwap_falls_back_to_close_when_no_amount():
    k = {"close": 12.5, "volume": 1000, "amount": 0}
    assert _vwap(k) == 12.5


def test_vwap_does_not_use_raw_division():
    """防回退: 旧 amount/volume 公式会大 100 倍, 必须 / (volume × 100)."""
    k = {
        "open": 10, "high": 11, "low": 9.5, "close": 10.5,
        "volume": 500_000, "amount": 525_000_000,
    }
    v = _vwap(k)
    # 正确: 525e6 / (500e3 × 100) = 10.5
    assert v == pytest.approx(10.5)
    # 错误: 525e6 / 500e3 = 1050  ← 旧 bug 值
    assert v != 1050


def test_vwap_handles_missing_keys():
    """K 线 dict 字段缺失 → fallback close 或 0."""
    assert _vwap({"close": 5.0}) == 5.0
    assert _vwap({}) == 0
