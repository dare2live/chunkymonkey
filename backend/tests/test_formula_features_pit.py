"""裸K线特征提取器 + PIT 行为门测试 — 守泄漏死红线。

核心 red->green: pit_guard.assert_pit_clean 必须**抓到**植入的前瞻泄漏 (用未来 bar 的 feature -> clean=False);
真实 4 active 提取器全过 PIT 门 (clean=True)。这是 L0 防泄露固化的自动核证 (非开发者自觉)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.formula_engine import features as F  # noqa: E402
from services.portfolio_walk_forward.pit_guard import assert_pit_clean  # noqa: E402


def _bars(n: int = 180, seed: int = 7) -> dict:  # >145 让 ma_base(MA145) warmup 完成
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 1.0)
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    return {"close": close.tolist(), "high": high.tolist(), "low": low.tolist()}


# ---- EMA PIT ----
def test_ema_warmup_none_and_recursive():
    out = F.ema([1.0, 2.0, 3.0, 4.0, 5.0], period=3)
    assert out[0] is None and out[1] is None      # warmup
    assert out[2] is not None
    assert F.ema([1.0], period=1)[0] == 1.0


# ---- 各提取器产值 + warmup ----
@pytest.mark.parametrize("fid", F.ACTIVE_FORMULAS)
def test_extractor_produces_values(fid):
    feat = F.extract_feature(fid, _bars())
    assert len(feat) == 180
    assert any(v is not None for v in feat)        # 有有效值
    assert feat[0] is None                          # 首行必 warmup (无足够历史)


def test_extract_unknown_formula_raises():
    with pytest.raises(ValueError, match="未知 formula"):
        F.extract_feature("not_a_formula", _bars())


# ---- PIT 行为门: 真实提取器全过 ----
@pytest.mark.parametrize("fid", F.ACTIVE_FORMULAS)
def test_real_extractors_are_pit_clean(fid):
    rep = assert_pit_clean(lambda b: F.extract_feature(fid, b), _bars())
    assert rep["clean"], f"{fid} PIT 泄漏: {rep['violations'][:2]}"
    assert rep["n_checked"] > 0


# ---- red->green: PIT 门必须抓到植入的前瞻泄漏 ----
def test_pit_guard_catches_lookahead_leak():
    def leaky(b):  # 故意用未来: feature[i] = close[i+1] (lookahead)
        c = b["close"]
        return [c[i + 1] if i + 1 < len(c) else None for i in range(len(c))]
    rep = assert_pit_clean(leaky, _bars())
    assert not rep["clean"], "PIT 门漏检前瞻泄漏 = 守门失效 (泄漏死红线破)"
    assert len(rep["violations"]) >= 1


def test_pit_guard_catches_centered_window_leak():
    def centered(b):  # 居中窗 (含未来): mean(close[i-2:i+3])
        c = b["close"]
        out = []
        for i in range(len(c)):
            if i < 2 or i + 2 >= len(c):
                out.append(None)
            else:
                out.append(float(np.mean(c[i - 2:i + 3])))
        return out
    rep = assert_pit_clean(centered, _bars())
    assert not rep["clean"], "居中窗用未来, PIT 门必须抓到"
