"""主升浪候选检测原语单测 (2026-06-19, A0 地基止血 #d).

证: pivot底/前瞻涨幅/长底/forward完整 原语正确 (负样本 generator 与 GT 共用, 口径必须对)。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.rally_detect import (  # noqa: E402
    base_days_count,
    forward_complete,
    forward_max_gain,
    is_pivot_low,
)


def test_is_pivot_low():
    lows = [10.0, 9.0, 8.0, 5.0, 8.0, 9.0, 10.0]   # i=3 是区间最低
    assert is_pivot_low(lows, 3, win=3)
    assert not is_pivot_low(lows, 0, win=3)        # 边缘非最低
    assert not is_pivot_low(lows, 2, win=3)        # 8.0 > 5.0
    assert not is_pivot_low([10.0, 0.0, 8.0], 1, win=1)   # 0 价非法


def test_forward_max_gain():
    highs = [10.0, 11.0, 16.0, 12.0]   # i=0 底, 前瞻 max high=16
    lows = [8.0, 9.0, 10.0, 11.0]
    g = forward_max_gain(highs, lows, 0, maxfwd=3)
    assert abs(g - (16.0 / 8.0 - 1.0)) < 1e-9      # 16/8-1 = 1.0 (涨100%)
    assert forward_max_gain(highs, lows, 3, maxfwd=3) is None   # 末位无前瞻


def test_base_days_count():
    # ref_low=10 -> 贴底带 [8.5, 12.5]; closes[i-lookback:i] 不含 i
    closes = [11.0, 9.0, 30.0, 12.0, 999.0]
    # i=4, lookback=4 -> 看 closes[0:4]=[11,9,30,12]; 在 [8.5,12.5] 的: 11,9,12 = 3 (30 出界)
    assert base_days_count(closes, 4, ref_low=10.0, lookback=4) == 3


def test_forward_complete():
    cal = [f"2024-{m:02d}-{d:02d}" for m in range(1, 13) for d in (3, 17)]   # 24 交易日
    cal.sort()
    last = "2024-06-28"
    assert forward_complete("2019-01-01", cal, last, 5) is True    # pre-calendar
    assert forward_complete("2024-01-03", cal, last, 3) is True    # 第3交易日 <= 边缘
    assert forward_complete("2024-06-17", cal, last, 10) is False  # 第10交易日 > 边缘 (右删失)
