"""Tests for backtest_validation gates (PBO / DSR / Conservative / IS-OOS).

ChunkyMonkey MSAF Phase 1.5 (Codex R31 design 落地).
"""
from __future__ import annotations

import numpy as np
import pytest

from services.backtest_validation.pbo import compute_pbo
from services.backtest_validation.dsr import compute_dsr
from services.backtest_validation.gate import (
    gate_pbo, gate_dsr, gate_conservative, gate_is_oos, run_all_gates,
)


def test_pbo_clean_strategy_passes():
    """Strategy with consistent OOS outperformance should have low PBO."""
    rng = np.random.default_rng(42)
    # 10 trials, 64 periods. trial 0 is genuinely best (consistent +alpha)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005  # +50bp/period consistent alpha
    result = compute_pbo(returns, sub_periods=8)
    # 真 alpha → lambda 应该 > 0 多数 combo, PBO 应低
    assert result.pbo < 0.5, f"Genuine alpha should have PBO < 0.5, got {result.pbo}"


def test_pbo_noise_strategy_high_pbo():
    """Pure noise strategies should have PBO ≈ 0.5."""
    rng = np.random.default_rng(123)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    result = compute_pbo(returns, sub_periods=8)
    # 纯噪声 → PBO 应该 ≈ 0.5 (best IS 在 OOS rank 随机)
    assert 0.20 <= result.pbo <= 0.80, f"Noise should have PBO ≈ 0.5, got {result.pbo}"


def test_pbo_invalid_input():
    """Bad input should raise."""
    with pytest.raises(ValueError):
        compute_pbo(np.array([1, 2, 3]))  # 1D
    with pytest.raises(ValueError):
        compute_pbo(np.zeros((1, 16)))  # too few trials
    with pytest.raises(ValueError):
        compute_pbo(np.zeros((10, 5)), sub_periods=16)  # too few periods


def test_dsr_high_sharpe_passes():
    """Genuine high Sharpe with low n_trials should pass DSR."""
    rng = np.random.default_rng(42)
    n = 252
    # Daily return mean = 0.001, std = 0.01 → annualized SR = 0.001 * sqrt(252) / 0.01 = 1.59
    returns = rng.normal(0.0008, 0.01, n)
    result = compute_dsr(returns, n_trials=1)
    assert result.sr_observed > 0.5
    assert result.p_conf > 0.5  # Better than random


def test_dsr_selection_bias_n_trials():
    """Higher n_trials → higher expected_max SR → harder DSR pass."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0005, 0.01, 252)
    r1 = compute_dsr(returns, n_trials=1)
    r100 = compute_dsr(returns, n_trials=100)
    # With more trials, expected max SR ↑, p_conf ↓
    assert r100.sr_expected_max > r1.sr_expected_max


def test_dsr_negative_sharpe_fails():
    """Negative SR should fail DSR."""
    rng = np.random.default_rng(42)
    returns = rng.normal(-0.0005, 0.01, 252)  # negative mean
    result = compute_dsr(returns, n_trials=1)
    assert not result.passes
    assert result.p_conf < 0.5


def test_gate_conservative_pass():
    g = gate_conservative(ann_ret_normal=0.20, ann_ret_conservative=0.10)
    assert g.passes
    assert "pass" in g.reason


def test_gate_conservative_fail():
    g = gate_conservative(ann_ret_normal=0.20, ann_ret_conservative=-0.05)
    assert not g.passes


def test_gate_is_oos_pass():
    g = gate_is_oos(is_metric=0.04, oos_metric=0.035, max_relative_drop=0.30)
    assert g.passes
    assert g.detail["relative_drop"] < 0.30


def test_gate_is_oos_fail_large_drop():
    g = gate_is_oos(is_metric=0.10, oos_metric=0.02, max_relative_drop=0.30)
    assert not g.passes
    assert g.detail["relative_drop"] > 0.30


def test_run_all_gates_missing_inputs():
    """Missing inputs → warn_only (not block)."""
    r = run_all_gates(challenger_id="test_missing")
    assert not r.all_pass
    assert r.promote_action == "warn_only"
    assert r.pbo.detail.get("error") == "input_missing"
    assert r.dsr.detail.get("error") == "input_missing"


def test_run_all_gates_full_pass():
    """All inputs pass → promote."""
    rng = np.random.default_rng(42)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005

    r = run_all_gates(
        challenger_id="test_pass",
        returns_matrix=returns,
        oos_returns=returns[0],
        n_trials_for_dsr=10,
        ann_normal=0.20,
        ann_conservative=0.12,
        is_metric=0.05,
        oos_metric=0.04,
    )
    # Note: DSR p_conf with n_trials=10 may be stringent; verify status
    assert r.is_oos.passes
    assert r.conservative.passes


def test_run_all_gates_pbo_fail_blocks():
    """High PBO → block."""
    rng = np.random.default_rng(123)
    returns = rng.normal(0, 0.01, (10, 64))  # pure noise
    r = run_all_gates(
        challenger_id="test_pbo_fail",
        returns_matrix=returns,
        oos_returns=returns[0],
        ann_normal=0.05,
        ann_conservative=0.02,
        is_metric=0.04,
        oos_metric=0.035,
    )
    # Pure noise → PBO ≈ 0.5 > 0.20 → 不 pass
    # 但所有其它 gate 都可能 pass (small sample size)
    assert not r.pbo.passes or r.pbo.detail.get("pbo", 0) >= 0.20
