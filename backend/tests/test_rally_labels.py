"""主升浪 GT 标签拆守门单测 (2026-06-19, A0 地基止血 #c).

证: (1) rally_labels 契约把 outcome 列 (gain/peak/dd/bull_aligned) 判为禁做 X, base_days 为 PIT 特征;
    assert_no_outcome_leakage 真拦 outcome (leakage 死闸非空壳)。
    (2) build_rally_entry_pit._compute fwd_complete: pre-calendar=True / 边缘内=True / 近边缘右删失=False。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services import rally_labels  # noqa: E402
from scripts.build_rally_entry_pit import _compute  # noqa: E402


def test_contract_roles():
    assert rally_labels.entry_anchor() == "bottom_date"
    assert rally_labels.label_column() == "is_true_rally"
    assert "base_days" in rally_labels.pit_feature_columns()
    oc = rally_labels.outcome_columns()
    # bull_aligned 是隐蔽陷阱: 名字像入场态, 实为拉升期测 -> 必在 outcome
    for col in ("gain_to_peak_pct", "peak_date", "peak_offset_days", "bull_aligned", "path_max_dd_pct"):
        assert col in oc, f"{col} 应判为 outcome"
    # base_days 绝不能在 outcome
    assert "base_days" not in oc


def test_assert_no_outcome_leakage_blocks():
    # 含 outcome -> raise (leakage 死闸真触发)
    with pytest.raises(ValueError):
        rally_labels.assert_no_outcome_leakage(["base_days", "mom_60", "bull_aligned"])
    with pytest.raises(ValueError):
        rally_labels.assert_no_outcome_leakage(["gain_to_peak_pct"])
    # 纯 PIT 特征 -> 放行
    rally_labels.assert_no_outcome_leakage(["base_days", "mom_60", "reversal_20"])


def test_fwd_complete_compute():
    # 交易日历 2023-01-03 .. 2024-12-31 (简化), 数据边缘 2024-06-28
    cal = [f"2023-{m:02d}-{d:02d}" for m in range(1, 13) for d in (3, 17)]   # 24 个交易日
    cal += [f"2024-{m:02d}-{d:02d}" for m in range(1, 13) for d in (3, 17)]  # +24 = 48
    cal.sort()
    last_data = "2024-06-28"   # 边缘在 2024 年中
    # episode: (code, bottom, base_days, fwd_window_len, is_true_rally)
    eps = [
        ("000001", "2019-05-10", 80, 5, True),   # pre-calendar -> fwd_complete True
        ("000002", "2023-01-17", 60, 3, True),   # bottom 后第3交易日 <= 边缘 -> True
        ("000003", "2024-06-17", 50, 10, True),  # bottom 后第10交易日 > 边缘 2024-06-28 -> False (右删失)
    ]
    out = _compute(eps, cal, last_data)
    fc = {r[0]: r[3] for r in out}
    assert fc["000001"] is True    # pre-calendar
    assert fc["000002"] is True    # forward 窗完整观测
    assert fc["000003"] is False   # 近边缘右删失
    # 返回行形态: (code, bottom, base_days, fwd_complete, is_true_rally, fwd_len)
    assert out[0] == ("000001", "2019-05-10", 80, True, True, 5)
