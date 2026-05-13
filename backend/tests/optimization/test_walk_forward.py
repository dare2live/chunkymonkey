"""Phase ψ — walk_forward.py 时序切分单测.

防回退场景:
- holdout: train.signal_date 都 < test.signal_date
- expanding: 每个窗 train.date 都 < test.date
- 小样本 / 边界 / 异常入参 raise / None
"""
from __future__ import annotations

import pytest

from services.optimization.walk_forward import (
    WalkForwardSplit, assert_no_temporal_leak,
    split_dispatch, split_expanding, split_holdout,
)


def _make_signals(n: int, start_year: int = 2023) -> list[dict]:
    """生成 n 笔 signals, signal_date 按月递增, 按时序排好."""
    return [
        {"stock_code": "600000",
         "signal_date": f"{start_year + (i // 12)}-{(i % 12) + 1:02d}-15"}
        for i in range(n)
    ]


# ━━━━━ holdout ━━━━━

def test_holdout_basic_70_30():
    sigs = _make_signals(100)
    s = split_holdout(sigs, train_ratio=0.7)
    assert s is not None
    assert s.n_train == 70
    assert s.n_test == 30
    assert s.train[-1]["signal_date"] < s.test[0]["signal_date"]
    assert s.mode == "holdout"


def test_holdout_no_temporal_overlap():
    """关键防 leakage 检查: train 最后一笔严格早于 test 第一笔."""
    sigs = _make_signals(50)
    s = split_holdout(sigs, train_ratio=0.7)
    assert s is not None
    last_train_date = max(t["signal_date"] for t in s.train)
    first_test_date = min(t["signal_date"] for t in s.test)
    assert last_train_date < first_test_date


def test_holdout_returns_none_when_sample_too_small():
    sigs = _make_signals(5)
    s = split_holdout(sigs, min_train=5, min_test=3)
    assert s is None    # 5 < 5+3


def test_holdout_invalid_ratio_raises():
    sigs = _make_signals(20)
    with pytest.raises(ValueError):
        split_holdout(sigs, train_ratio=1.5)
    with pytest.raises(ValueError):
        split_holdout(sigs, train_ratio=0.0)


def test_holdout_preserves_temporal_order_after_unsorted_input():
    """乱序输入也应按 signal_date 切."""
    sigs = _make_signals(30)
    import random
    random.seed(123)
    random.shuffle(sigs)
    s = split_holdout(sigs, train_ratio=0.7)
    assert s is not None
    # 即使输入乱序, train 集所有 date 都 ≤ test 集任何 date
    last_train = max(t["signal_date"] for t in s.train)
    first_test = min(t["signal_date"] for t in s.test)
    assert last_train < first_test


# ━━━━━ expanding ━━━━━

def test_expanding_4_windows():
    sigs = _make_signals(100)
    splits = split_expanding(sigs, n_windows=4, min_train=10, min_test=3)
    assert len(splits) == 4
    # 每窗的 train.date 都 < test.date
    for s in splits:
        last_train = max(t["signal_date"] for t in s.train)
        first_test = min(t["signal_date"] for t in s.test)
        assert last_train < first_test
    # train 集随窗扩大
    sizes = [s.n_train for s in splits]
    assert sizes == sorted(sizes)
    # window 4 的 train 应到 80, test 到 100
    assert splits[-1].n_train >= 60


def test_expanding_small_sample_returns_empty():
    sigs = _make_signals(10)
    splits = split_expanding(sigs, n_windows=4, min_train=10, min_test=3)
    assert splits == []


# ━━━━━ split_dispatch ━━━━━

def test_dispatch_holdout():
    sigs = _make_signals(50)
    splits = split_dispatch(sigs, mode="holdout")
    assert len(splits) == 1
    assert splits[0].mode == "holdout"


def test_dispatch_expanding():
    sigs = _make_signals(100)
    splits = split_dispatch(sigs, mode="expanding")
    assert len(splits) >= 2
    for s in splits:
        assert s.mode == "expanding"


def test_dispatch_none():
    """none 模式: train = all signals, test = empty (调试用, governance 会拒入业务)."""
    sigs = _make_signals(30)
    splits = split_dispatch(sigs, mode="none")
    assert len(splits) == 1
    assert splits[0].mode == "none"
    assert splits[0].n_train == 30
    assert splits[0].n_test == 0


def test_dispatch_unknown_mode_raises():
    sigs = _make_signals(30)
    with pytest.raises(ValueError):
        split_dispatch(sigs, mode="purged_cv")  # 还没实现


# ━━━━━ leak guard ━━━━━

def test_assert_no_leak_passes_holdout():
    sigs = _make_signals(50)
    s = split_holdout(sigs, train_ratio=0.7)
    assert_no_temporal_leak(s)   # 不 raise


def test_assert_no_leak_raises_on_overlap():
    """手工构造 leak (train 最后一笔 = test 第一笔), 必须 raise."""
    leaky = WalkForwardSplit(
        train=[{"stock_code": "1", "signal_date": "2024-01-01"},
               {"stock_code": "1", "signal_date": "2024-06-01"}],
        test=[{"stock_code": "1", "signal_date": "2024-06-01"},   # 同日 → leak
              {"stock_code": "1", "signal_date": "2024-12-01"}],
        train_start="2024-01-01", train_end="2024-06-01",
        test_start="2024-06-01", test_end="2024-12-01",
        mode="holdout",
    )
    with pytest.raises(AssertionError, match="Temporal leak"):
        assert_no_temporal_leak(leaky)


def test_dispatch_expanding_monthly_falls_back_to_holdout_when_too_few_months():
    """A 股 signal 稀疏 (< 12 月跨度) 时, expanding_monthly 应自动退到 holdout."""
    # 8 个月信号 (cfg.min_total_months=12 拦) — 但 ≥ holdout 阈值
    sigs = [{"stock_code": "600000", "signal_date": f"2024-{m:02d}-15"}
            for m in range(1, 9)
            for d in range(1, 6)]   # 8 月 × 5 笔/月 = 40 笔
    # 加修正: 上面写错 nesting, 重写
    sigs = []
    for m in range(1, 9):
        for d in range(1, 6):
            sigs.append({"stock_code": "600000",
                         "signal_date": f"2024-{m:02d}-{d:02d}"})
    splits = split_dispatch(sigs, mode="expanding_monthly")
    assert len(splits) == 1
    # 应该退到 holdout, 不是 expanding_monthly
    assert splits[0].mode == "holdout"


def test_assert_no_leak_skips_none_mode():
    """none 模式不检查 (因为是调试用整段)."""
    leaky = WalkForwardSplit(
        train=[{"stock_code": "1", "signal_date": "2024-01-01"}],
        test=[],
        train_start="2024-01-01", train_end="2024-01-01",
        test_start="", test_end="",
        mode="none",
    )
    assert_no_temporal_leak(leaky)   # 不 raise
