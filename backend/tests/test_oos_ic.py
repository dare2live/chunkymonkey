"""walk-forward OOS RankIC 核心测试 — 守 PIT 前向收益 (不回看) + 截面 IC 正确 + expanding 窗口。

leakage 守门 (死亡条款泄漏死): forward_returns[i] 只用 close[i+h], 绝不含 close[<i];
cheating feature=未来收益 -> IC~1 (验引擎能识别完美预测器), PIT 动量 feature -> 温和 IC。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.portfolio_walk_forward.oos_ic import (  # noqa: E402
    PanelRow, cross_sectional_ic, expanding_monthly_windows, forward_returns, oos_rank_ic,
)


# ---- forward_returns (PIT 标注) ----
def test_forward_returns_uses_future_not_past():
    closes = [10.0, 11.0, 12.0, 13.0]
    out = forward_returns(["d1", "d2", "d3", "d4"], closes, horizon=1)
    assert out[0] == pytest.approx(11.0 / 10.0 - 1)   # 用 close[1] 非 close[-1]
    assert out[1] == pytest.approx(12.0 / 11.0 - 1)
    assert out[2] == pytest.approx(13.0 / 12.0 - 1)
    assert out[3] is None                              # 末 horizon 行无未来


def test_forward_returns_horizon2_tail_none():
    out = forward_returns(["d1", "d2", "d3", "d4"], [10.0, 11.0, 12.0, 13.0], horizon=2)
    assert out[0] == pytest.approx(12.0 / 10.0 - 1)
    assert out[2] is None and out[3] is None           # 末 2 行 None


def test_forward_returns_no_backward_leak():
    # red->green: 若实现误用 close[i-1] 当未来, 首行会算成 nan/负值; 断言首行严格用 close[i+1]
    closes = [100.0, 50.0, 200.0]
    out = forward_returns(["d1", "d2", "d3"], closes, horizon=1)
    assert out[0] == pytest.approx(50.0 / 100.0 - 1)   # = -0.5, 用未来下跌; 回看会得 +1.0
    assert out[0] < 0


def test_forward_returns_rejects_bad_horizon():
    with pytest.raises(ValueError):
        forward_returns(["d1"], [10.0], horizon=0)


# ---- 截面 IC ----
def test_cross_sectional_ic_monotonic():
    feats = [1.0, 2.0, 3.0, 4.0, 5.0]
    labels = [0.1, 0.2, 0.3, 0.4, 0.5]               # 完全同序
    assert cross_sectional_ic(feats, labels) == pytest.approx(1.0)


def test_cross_sectional_ic_inverse():
    assert cross_sectional_ic([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_cross_sectional_ic_insufficient_or_constant():
    assert cross_sectional_ic([1.0, 2.0], [0.1, 0.2]) is None          # <3
    assert cross_sectional_ic([5.0, 5.0, 5.0, 5.0], [1.0, 2.0, 3.0, 4.0]) is None  # 恒定


# ---- expanding_monthly 窗口 ----
def test_expanding_monthly_windows_split():
    months = [f"2024{m:02d}" for m in range(1, 13)]  # 12 月
    w = expanding_monthly_windows(months, min_train_months=6, forward_months=1, min_total_months=12)
    assert len(w) == 6                                # 月 7-12 各一窗
    assert w[0][0] == tuple(months[:6]) and w[0][1] == ("202407",)
    assert w[-1][1] == ("202412",)
    # train 单调扩张
    assert len(w[1][0]) == 7


def test_expanding_monthly_windows_insufficient():
    assert expanding_monthly_windows([f"2024{m:02d}" for m in range(1, 6)],
                                     min_train_months=6, forward_months=1, min_total_months=12) == []


# ---- 端到端 OOS RankIC + PIT ----
def _panel_with_signal(strength: float, seed: int) -> list[PanelRow]:
    """合成 14 月 x 20 股面板: feature 与 fwd_ret 按 strength 相关 (1=完美, 0=噪声)。"""
    rng = np.random.default_rng(seed)
    rows: list[PanelRow] = []
    for mi in range(1, 15):
        for s in range(20):
            f = rng.normal()
            noise = rng.normal()
            lab = strength * f + (1 - strength) * noise
            rows.append(PanelRow(date=f"2024{mi:02d}15" if mi <= 12 else f"2025{mi-12:02d}15",
                                 code=f"s{s}", feature=f, fwd_ret=lab))
    return rows


def test_oos_rank_ic_detects_signal():
    res = oos_rank_ic(_panel_with_signal(strength=0.9, seed=1))
    assert res["oos_rank_ic"] is not None
    assert res["oos_rank_ic"] > 0.5                   # 强信号 -> 高 IC
    assert res["n_windows"] >= 1 and res["n_days"] >= 1


def test_oos_rank_ic_noise_near_zero():
    res = oos_rank_ic(_panel_with_signal(strength=0.0, seed=2))
    assert res["oos_rank_ic"] is not None
    assert abs(res["oos_rank_ic"]) < 0.2              # 纯噪声 -> IC ~0 (不虚高)


def test_oos_rank_ic_insufficient_months_unknown():
    # 只 6 月 < min_total 12 -> oos_rank_ic=None (标 unknown 不报假数)
    rows = [PanelRow(date=f"2024{mi:02d}15", code=f"s{s}", feature=1.0 * s, fwd_ret=0.1 * s)
            for mi in range(1, 7) for s in range(20)]
    res = oos_rank_ic(rows)
    assert res["oos_rank_ic"] is None and res["reason"] == "insufficient_months"
