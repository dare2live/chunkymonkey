"""Phase ψ — R1 expanding_monthly walk-forward 单测.

防回退:
- 每月一切, train 累积, test 当月
- 每窗 train.date 都 < test.date (anti-leak)
- 小样本 (< min_total_months) 返回 []
- 真实信号密度 (每月 N 笔) 应有 N-min_train 个窗
"""
from __future__ import annotations

import pytest

from services.optimization.walk_forward import (
    assert_no_temporal_leak, split_expanding_monthly,
)


def _make_signals_per_month(n_months: int, sigs_per_month: int = 5,
                            start_year: int = 2023) -> list[dict]:
    """生成 n_months 个月 × sigs_per_month 个信号 (按月分布)."""
    sigs = []
    y, m = start_year, 1
    for _ in range(n_months):
        for d in range(1, sigs_per_month + 1):
            sigs.append({"stock_code": "600000",
                         "signal_date": f"{y}-{m:02d}-{d:02d}"})
        m += 1
        if m > 12:
            m = 1; y += 1
    return sigs


def test_expanding_monthly_returns_expected_window_count():
    """24 个月 × 5 笔/月, min_train=6, forward=1 → 应该 24-6 = 18 个窗."""
    sigs = _make_signals_per_month(n_months=24, sigs_per_month=5)
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=1, min_test=3)
    assert len(splits) == 18


def test_expanding_monthly_train_accumulates():
    """每个窗 train 应比上一个窗大 (累积 expanding)."""
    sigs = _make_signals_per_month(n_months=18, sigs_per_month=5)
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=1, min_test=3)
    train_sizes = [s.n_train for s in splits]
    assert train_sizes == sorted(train_sizes)
    assert train_sizes[0] < train_sizes[-1]


def test_expanding_monthly_test_is_current_month():
    """每窗 test 应该是 train 之后那 1 个月."""
    sigs = _make_signals_per_month(n_months=12, sigs_per_month=5)
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=1, min_test=3)
    for s in splits:
        # train 最后一笔严格早于 test 第一笔
        last_train = max(t["signal_date"] for t in s.train)
        first_test = min(t["signal_date"] for t in s.test)
        assert last_train < first_test, f"leak: {last_train} >= {first_test}"
        # test 每笔的 year-month 应该同月
        test_months = set(t["signal_date"][:7] for t in s.test)
        assert len(test_months) == 1, f"test 跨月: {test_months}"


def test_expanding_monthly_no_temporal_leak_passes_assert():
    sigs = _make_signals_per_month(n_months=24, sigs_per_month=5)
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=1, min_test=3)
    for s in splits:
        assert_no_temporal_leak(s)


def test_expanding_monthly_skips_low_signal_months():
    """如果某个月信号 < min_test, 该月窗应被跳过."""
    # 月 1-12 正常每月 5 笔, 月 13 只有 1 笔
    sigs = _make_signals_per_month(n_months=12, sigs_per_month=5)
    sigs.append({"stock_code": "600000", "signal_date": "2024-01-15"})   # 仅 1 笔
    sigs += [{"stock_code": "600000", "signal_date": f"2024-02-{d:02d}"} for d in range(1, 6)]
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=1, min_test=3)
    # 月 13 = 2024-01 应被跳 (只 1 笔), 月 14 = 2024-02 应在
    test_months = [s.test_start[:7] for s in splits]
    assert "2024-01" not in test_months
    assert "2024-02" in test_months


def test_expanding_monthly_too_few_months_returns_empty():
    """min_total_months 默认 12, 11 月数据 → 应返回 []."""
    sigs = _make_signals_per_month(n_months=8, sigs_per_month=5)
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=1)
    # cfg.min_total_months = 12, 8 月不够 → []
    assert splits == []


def test_expanding_monthly_invalid_params_raise():
    sigs = _make_signals_per_month(n_months=12, sigs_per_month=5)
    with pytest.raises(ValueError):
        split_expanding_monthly(sigs, min_train_months=0)
    with pytest.raises(ValueError):
        split_expanding_monthly(sigs, forward_months=0)


def test_expanding_monthly_forward_2_months():
    """forward_months=2 → 每窗 OOS = 2 个月."""
    sigs = _make_signals_per_month(n_months=24, sigs_per_month=5)
    splits = split_expanding_monthly(sigs, min_train_months=6, forward_months=2, min_test=3)
    for s in splits:
        test_months = set(t["signal_date"][:7] for t in s.test)
        # 每窗 OOS 最多 2 个月 (最后一窗可能只 1 个月 if remainder)
        assert len(test_months) <= 2
