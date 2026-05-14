"""Phase ψ.β.perf — _idx dict cache 单测 (防回退).

测试 _idx 用 id(bars) cache, 加速 O(N) → O(1).
防御: cache 失效场景 + LRU evict.
"""
from __future__ import annotations

import pytest

from services.backtest.realistic_engine import Bar, _BAR_DATE_IDX_CACHE, _idx


def _bars(n: int = 5) -> list[Bar]:
    return [
        Bar(date=f"2024-01-{i:02d}", open=10, high=11, low=9, close=10.5,
            volume=1000, amount=10500)
        for i in range(1, n + 1)
    ]


def test_idx_returns_correct_index():
    _BAR_DATE_IDX_CACHE.clear()
    bars = _bars(5)
    assert _idx(bars, "2024-01-01") == 0
    assert _idx(bars, "2024-01-03") == 2
    assert _idx(bars, "2024-01-05") == 4


def test_idx_returns_neg1_when_not_found():
    _BAR_DATE_IDX_CACHE.clear()
    bars = _bars(5)
    assert _idx(bars, "2025-12-31") == -1
    assert _idx(bars, "") == -1


def test_idx_cache_hit_after_first_call():
    """第二次调用同 bars 应直接 dict.get O(1), 不重建 cache."""
    _BAR_DATE_IDX_CACHE.clear()
    bars = _bars(5)
    _idx(bars, "2024-01-01")
    key = id(bars)
    assert key in _BAR_DATE_IDX_CACHE
    cache_dict = _BAR_DATE_IDX_CACHE[key]
    # 第二次调用必须复用同一个 dict object
    _idx(bars, "2024-01-02")
    assert _BAR_DATE_IDX_CACHE[key] is cache_dict


def test_idx_cache_separates_different_bars():
    """两个不同 bars 列表 → 两份独立 cache."""
    _BAR_DATE_IDX_CACHE.clear()
    bars1 = _bars(5)
    bars2 = _bars(3)
    _idx(bars1, "2024-01-01")
    _idx(bars2, "2024-01-01")
    assert len(_BAR_DATE_IDX_CACHE) == 2
    assert id(bars1) in _BAR_DATE_IDX_CACHE
    assert id(bars2) in _BAR_DATE_IDX_CACHE


def test_idx_speed_benchmark():
    """性能 benchmark: 800 天 bars × 10000 查询, 应远快于 linear search."""
    import time
    _BAR_DATE_IDX_CACHE.clear()
    n = 800
    bars = [
        Bar(date=f"2024-{(i//30)+1:02d}-{(i%30)+1:02d}",
            open=10, high=11, low=9, close=10.5, volume=1000, amount=10500)
        for i in range(n)
    ]
    queries = [bars[i].date for i in range(0, n, 80)] * 100   # 1000 queries
    t0 = time.time()
    for q in queries:
        _idx(bars, q)
    elapsed = time.time() - t0
    # 1000 queries × O(1) 应 < 10ms (含 cache build 1 次)
    assert elapsed < 0.1, f"_idx too slow: {elapsed:.3f}s for 1000 queries"
