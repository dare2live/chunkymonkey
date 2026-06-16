"""build_segment_panel 纯函数单测 (_ema / _macd / _range_pos / _board)。

形态面板的数学/分类原语, 正常+边界都测; 防回退。
"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_segment_panel.py"
_spec = importlib.util.spec_from_file_location("build_segment_panel", str(_SCRIPT))
bsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsp)


def test_ema_matches_recursive_formula():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = bsp._ema(x, span=3)  # alpha = 2/4 = 0.5
    # out[0]=1; out[i]=0.5*x[i]+0.5*out[i-1]
    assert out[0] == pytest.approx(1.0)
    assert out[1] == pytest.approx(0.5 * 2 + 0.5 * 1.0)  # 1.5
    assert out[2] == pytest.approx(0.5 * 3 + 0.5 * 1.5)  # 2.25
    assert len(out) == len(x)


def test_macd_dif_dea_hist_relation():
    closes = np.linspace(10.0, 20.0, 60)  # 单调上行 → dif 应为正
    dif, dea, hist = bsp._macd(closes, fast=12, slow=26, signal=9)
    assert len(dif) == len(dea) == len(hist) == 60
    np.testing.assert_allclose(hist, dif - dea, rtol=1e-9)
    # 单调上涨末端 fast EMA 高于 slow EMA → dif>0
    assert dif[-1] > 0


def test_range_pos_definition_and_window():
    # 前 300 日固定区间 [10,20], 第 300 位收盘价测位置
    closes = np.concatenate([np.tile([10.0, 20.0], 150), np.array([15.0])])  # len=301
    rp = bsp._range_pos(closes, lookback=300)
    # i<300 全 NaN (预热不足)
    assert np.isnan(rp[:300]).all()
    # i=300: window=closes[0:300]=10/20 交替 → lo=10,hi=20; close=15 → (15-10)/(20-10)=0.5
    assert rp[300] == pytest.approx(0.5)


def test_range_pos_flat_window_is_nan():
    closes = np.concatenate([np.full(300, 12.0), np.array([12.0])])  # 全平 hi==lo
    rp = bsp._range_pos(closes, lookback=300)
    assert np.isnan(rp[300])  # hi==lo → 不可定义


@pytest.mark.parametrize("code,board", [
    ("688001", "科创板"),
    ("300750", "创业板"),
    ("600519", "沪主板"),
    ("000001", "深主板"),
    ("002594", "深主板"),
    ("830799", "北交所"),
    ("123456", "其他"),
])
def test_board_classification(code, board):
    assert bsp._board(code) == board
