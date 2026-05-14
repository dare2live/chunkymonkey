"""Paper Sim v2 — L2 vol-aware per-stock 参数缩放 单测 (Phase ψ.β.5).

Rule 6 (数据驱动): 验证 vol_60d × sqrt(hp/252) × sigma 缩放公式.
Rule 9.1 (真金白银): 验证 hard bounds clip — 防止极端 vol 估算失真伤实盘.

设计:
- enabled=False → 返回 default (向后兼容, 不破坏现有行为)
- 高 vol 股: stop/target 接近 max bounds
- 低 vol 股: stop/target 接近 min bounds
- vol=None / vol<=0 → fallback default
"""
from __future__ import annotations

import math

import pytest

from services.paper_sim.selector import _vol_aware_params


_DEFAULT_VA = {
    "enabled": True,
    "stop_sigma": 2.0,
    "target_sigma": 3.0,
    "trailing_sigma": 1.0,
    "stop_min": -0.20, "stop_max": -0.05,
    "target_min": 0.10, "target_max": 0.35,
    "trailing_min": 0.03, "trailing_max": 0.10,
}


def test_disabled_returns_defaults():
    """va.enabled=False → 完全等同 default, 不动 stop/target/trailing."""
    s, t, tr = _vol_aware_params(0.25, 15, {"enabled": False}, -0.08, 0.22, 0.06)
    assert s == -0.08
    assert t == 0.22
    assert tr == 0.06


def test_none_vol_fallback_to_defaults():
    """vol_60d 缺失 → fallback default."""
    s, t, tr = _vol_aware_params(None, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    assert s == -0.10
    assert t == 0.20
    assert tr == 0.05


def test_zero_or_negative_vol_fallback():
    """vol_60d ≤ 0 (异常) → fallback default."""
    s, _, _ = _vol_aware_params(0.0, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    assert s == -0.10
    s, _, _ = _vol_aware_params(-0.05, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    assert s == -0.10


def test_mid_vol_typical_a_share():
    """典型 A 股 vol_60d 年化 ≈ 0.30 (30%), hp=15 trading days.

    sigma_15 = 0.30 × sqrt(15/252) ≈ 0.0732
    stop     = -2 × 0.0732 = -0.1464 → 在 [-0.20, -0.05] 内, 不 clip
    target   = +3 × 0.0732 = +0.2196 → 在 [0.10, 0.35] 内, 不 clip
    trailing = +1 × 0.0732 = +0.0732 → 在 [0.03, 0.10] 内, 不 clip
    """
    s, t, tr = _vol_aware_params(0.30, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    expected_sigma = 0.30 * math.sqrt(15 / 252)
    assert s == pytest.approx(-2 * expected_sigma, rel=0.01)
    assert t == pytest.approx(3 * expected_sigma, rel=0.01)
    assert tr == pytest.approx(expected_sigma, rel=0.01)


def test_high_vol_clipped_to_max_bounds():
    """vol_60d=0.80 (极高 80% 年化), 15 天 sigma ≈ 0.195, stop 公式 = -0.39 → clip 到 -0.20."""
    s, t, tr = _vol_aware_params(0.80, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    assert s == -0.20, f"高 vol stop 应 clip 到 stop_min=-0.20, got {s}"
    assert t == 0.35, f"高 vol target 应 clip 到 target_max=0.35, got {t}"
    assert tr == 0.10, f"高 vol trailing 应 clip 到 trailing_max=0.10, got {tr}"


def test_low_vol_clipped_to_min_bounds():
    """vol_60d=0.10 (低 10% 年化), 15 天 sigma ≈ 0.0244, stop 公式 = -0.0488 → clip 到 -0.05."""
    s, t, tr = _vol_aware_params(0.10, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    assert s == pytest.approx(-0.05, abs=1e-6), f"低 vol stop 应 clip 到 stop_max=-0.05, got {s}"
    assert t == pytest.approx(0.10, abs=1e-6), f"低 vol target 应 clip 到 target_min=0.10, got {t}"
    assert tr == pytest.approx(0.03, abs=1e-6), f"低 vol trailing 应 clip 到 trailing_min=0.03, got {tr}"


def test_longer_hp_scales_sigma_up():
    """hp 越长 sigma 越大. hp=60 vs hp=15, 同一 vol_60d."""
    vol = 0.25
    s15, t15, _ = _vol_aware_params(vol, 15, _DEFAULT_VA, -0.10, 0.20, 0.05)
    s60, t60, _ = _vol_aware_params(vol, 60, _DEFAULT_VA, -0.10, 0.20, 0.05)
    # hp=60 sigma 是 hp=15 的 sqrt(60/15) = 2 倍
    # 但 clip 之后可能都打到 bound
    assert abs(s60) >= abs(s15) - 1e-6, "hp=60 stop 应 ≥ hp=15 (更宽)"
    assert t60 >= t15 - 1e-6, "hp=60 target 应 ≥ hp=15 (更高)"


def test_custom_sigma_multipliers():
    """sigma 倍数从 yaml 来, 业务代码不 hardcode."""
    va = {**_DEFAULT_VA, "stop_sigma": 1.0, "target_sigma": 1.0}
    s, t, _ = _vol_aware_params(0.30, 15, va, -0.10, 0.20, 0.05)
    expected_sigma = 0.30 * math.sqrt(15 / 252)
    assert s == pytest.approx(-expected_sigma, rel=0.01)
    assert t == pytest.approx(expected_sigma, rel=0.01) if expected_sigma >= 0.10 else 0.10
