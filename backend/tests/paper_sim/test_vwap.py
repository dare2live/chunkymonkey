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


# ━━━━━ Phase ψ.β.5 sanity check (akshare_sina vs tdxhub volume 单位不一致) ━━━━━


def test_vwap_handles_akshare_sina_volume_unit():
    """实测 bug: akshare_sina source volume 单位 = 股, tdxhub source = 手.
    旧 _vwap 写死 /(volume×100) → akshare 数据算出 VWAP / 100 (= 0.11 元),
    触发 stop_hit 假信号, NAV 暴跌. 修后 sanity check 选合理的 vwap.
    """
    # 601098 2026-05-07 真实数据 (akshare_sina, volume=股)
    # close=11.44, volume=11_954_500 股
    k = {
        "open": 11.55, "high": 11.7, "low": 11.0, "close": 11.44,
        "volume": 11_954_500,
        "amount": 11_954_500 * 11.4,    # 约 1.36 亿元 (volume × avg_price)
    }
    v = _vwap(k)
    # 应该选 amount/volume = 11.4 (落在 [low, high])
    assert v == pytest.approx(11.4, rel=0.01), \
        f"akshare volume 应识别为'股', vwap ≈ 11.4 not {v}"
    assert k["low"] * 0.95 <= v <= k["high"] * 1.05


def test_vwap_handles_tdxhub_volume_unit_still():
    """tdxhub source volume = 手 — 老逻辑必须仍 work."""
    # 601098 2026-05-06 真实 (tdxhub, volume=手)
    k = {
        "open": 11.42, "high": 11.6, "low": 11.3, "close": 11.53,
        "volume": 269_295,                       # 26.9 万手
        "amount": 269_295 * 11.5 * 100,          # 3.1 亿元 (手 × 价 × 100)
    }
    v = _vwap(k)
    assert v == pytest.approx(11.5, rel=0.01), \
        f"tdxhub volume='手', vwap = amt/(vol×100) ≈ 11.5 not {v}"


def test_vwap_rejects_extreme_ratio_uses_close():
    """volume / amount 极端不匹配 (e.g. amount × 100 错位) — 用 close fallback."""
    # 两个候选 vwap 都不落在 [low, high] 范围 → close fallback
    k = {
        "open": 10, "high": 11, "low": 9.5, "close": 10.5,
        "volume": 500_000,
        "amount": 5_000,    # 严重错位, 算出 vwap=0.01 或 0.0001 都不合理
    }
    v = _vwap(k)
    assert v == 10.5, f"极端不合理 vwap → close fallback, expect 10.5 not {v}"
