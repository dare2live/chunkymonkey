"""Phase ψ.α B — split_train_end_forward 严格 walk-forward 单测.

防回退场景:
- train.signal_date 都 < train_end_date
- test.signal_date 都 ∈ [train_end_date, train_end_date + forward_days)
- 任一不足返回 None
- list_month_ends 正确列出月末
"""
from __future__ import annotations

import pytest

from services.optimization.walk_forward import (
    list_month_ends, split_train_end_forward,
)


def _sigs(n: int, start_year: int = 2024) -> list[dict]:
    """按月递增信号."""
    out = []
    y, m = start_year, 1
    for i in range(n):
        out.append({"stock_code": "600000",
                    "signal_date": f"{y}-{m:02d}-15"})
        m += 1
        if m > 12:
            m = 1; y += 1
    return out


# ━━━━━ split_train_end_forward ━━━━━


def test_split_train_end_forward_basic():
    """24 月信号, train_end=2024-12-31, forward=60d → train=2024-01~12, test=2025-01~02."""
    sigs = _sigs(24)
    s = split_train_end_forward(sigs, train_end_date="2024-12-31",
                                forward_days=60, min_train=5, min_test=2)
    assert s is not None
    assert s.mode == "train_end_forward"
    # train 全部在 2024-12-31 之前
    for t in s.train:
        assert t["signal_date"] < "2024-12-31"
    # test 全部在 [2024-12-31, 2025-03-01) (forward 60d)
    for t in s.test:
        assert "2024-12-31" <= t["signal_date"] < "2025-03-01"


def test_split_train_end_forward_no_temporal_overlap():
    """anti-leak: train 最大 date < test 最小 date."""
    sigs = _sigs(36)
    s = split_train_end_forward(sigs, train_end_date="2025-06-30", forward_days=90)
    assert s is not None
    last_train = max(t["signal_date"] for t in s.train)
    first_test = min(t["signal_date"] for t in s.test)
    assert last_train < first_test


def test_split_train_end_forward_returns_none_when_too_few():
    """train 或 test 任一 < min, 返回 None."""
    sigs = _sigs(3)
    s = split_train_end_forward(sigs, train_end_date="2024-06-30",
                                forward_days=60, min_train=5, min_test=3)
    assert s is None


def test_split_train_end_forward_test_window_respected():
    """forward_days=30 → test 只含 train_end_date 后 30 天."""
    sigs = _sigs(36)
    # forward=30 天单月 sigs 数少, 用 min_test=1 让函数不 reject
    s = split_train_end_forward(sigs, train_end_date="2024-06-30",
                                forward_days=30, min_test=1)
    assert s is not None
    for t in s.test:
        assert "2024-06-30" <= t["signal_date"] < "2024-07-30"


def test_split_train_end_forward_test_window_far_future_returns_none():
    """train_end_date 之后 forward_days 内没有信号 → None."""
    sigs = _sigs(12, start_year=2024)   # 2024-01 ~ 2024-12
    # train_end=2026-01-31, forward=60d 没信号
    s = split_train_end_forward(sigs, train_end_date="2026-01-31",
                                forward_days=60, min_train=5, min_test=2)
    assert s is None


# ━━━━━ list_month_ends ━━━━━


def test_list_month_ends_basic():
    out = list_month_ends("2024-01-15", "2024-04-12")
    # 不含 end 月 (2024-04), 应只 2024-01, 02, 03 月末
    assert out == ["2024-01-31", "2024-02-29", "2024-03-31"]


def test_list_month_ends_cross_year():
    out = list_month_ends("2023-11-01", "2024-02-12")
    assert out == ["2023-11-30", "2023-12-31", "2024-01-31"]


def test_list_month_ends_leap_year_feb():
    out = list_month_ends("2024-02-01", "2024-03-12")
    assert out == ["2024-02-29"]   # 2024 闰年


def test_list_month_ends_non_leap_year_feb():
    out = list_month_ends("2023-02-01", "2023-03-12")
    assert out == ["2023-02-28"]
