"""Phase ψ.γ.discipline — Deflated Sharpe Ratio 单测.

公式 by Bailey & López de Prado (2014). 防回退 + 数值正确性 + 边界.
"""
from __future__ import annotations

import math

from services.optimization.deflated_sharpe import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_sharpe_for_significance,
)


# ━━━━━ expected_max_sharpe ━━━━━

def test_expected_max_zero_when_one_trial():
    assert expected_max_sharpe(1) == 0.0


def test_expected_max_grows_with_n_trials():
    """跑得越多, max sharpe 期望越大 (随机也能跑出更高 SR)."""
    assert expected_max_sharpe(10) < expected_max_sharpe(100)
    assert expected_max_sharpe(100) < expected_max_sharpe(1000)


def test_expected_max_scales_with_variance_sqrt():
    """E[max] = √V × (Gumbel 常数). σ=2 vs σ=1 → 应该是 2 倍."""
    e_v1 = expected_max_sharpe(100, sharpe_variance=1.0)
    e_v4 = expected_max_sharpe(100, sharpe_variance=4.0)
    assert abs(e_v4 / e_v1 - 2.0) < 1e-6


def test_expected_max_against_known_range():
    """50 trials standard normal → E[max] ≈ 2.0-2.4 (Gumbel mean)."""
    e = expected_max_sharpe(50, sharpe_variance=1.0)
    assert 2.0 < e < 2.4


# ━━━━━ deflated_sharpe_ratio ━━━━━

def test_dsr_nan_when_too_few_trials():
    assert math.isnan(deflated_sharpe_ratio(1.0, n_trials=1, n_observations=100))


def test_dsr_nan_when_too_few_obs():
    assert math.isnan(deflated_sharpe_ratio(1.0, n_trials=10, n_observations=1))


def test_dsr_high_p_when_sr_above_expected_max():
    """SR > E[max(N)] 时 + T 大, p 应高 (显著)."""
    # E_max(N=3, V=1) ≈ 0.85, SR=2.0 >> 0.85 → 显著
    p = deflated_sharpe_ratio(2.0, n_trials=3, n_observations=252)
    assert p > 0.95


def test_dsr_low_p_when_sr_below_expected_max():
    """SR < E[max(N)] 时 + T 大, p 应低 (不显著, 跨 study 噪音)."""
    # E_max(N=100, V=1) ≈ 2.50, SR=1.0 << 2.50 → 远低于随机 best
    p = deflated_sharpe_ratio(1.0, n_trials=100, n_observations=252)
    assert p < 0.05


def test_dsr_decreases_with_more_trials_for_fixed_sr():
    """同 SR + 同 T, trials 越多, E[max] 越大, p 越低."""
    # SR=1.5, T=200 normal
    # N=10 → E_max≈1.54, z≈small negative → p≈0.46
    # N=100 → E_max≈2.50, z≈large negative → p≈极小
    p_10 = deflated_sharpe_ratio(1.5, n_trials=10, n_observations=200)
    p_100 = deflated_sharpe_ratio(1.5, n_trials=100, n_observations=200)
    assert p_100 < p_10


def test_dsr_increases_with_more_obs_when_significant():
    """SR > E[max] 时, T 越大 evidence 越强, p 越接近 1."""
    # E_max(N=20, V=1) ≈ 1.86, SR=2.5 > 1.86 → 显著方向
    p_50 = deflated_sharpe_ratio(2.5, n_trials=20, n_observations=50)
    p_500 = deflated_sharpe_ratio(2.5, n_trials=20, n_observations=500)
    assert p_500 > p_50
    assert p_500 > 0.95   # 大 T 强化显著


def test_dsr_decreases_with_more_obs_when_insignificant():
    """SR < E[max] 时, T 越大 evidence 越强 (反向), p 越接近 0."""
    # SR=1.0 < E_max(N=20, V=1)=1.86 → 不显著方向
    p_50 = deflated_sharpe_ratio(1.0, n_trials=20, n_observations=50)
    p_500 = deflated_sharpe_ratio(1.0, n_trials=20, n_observations=500)
    assert p_500 < p_50


def test_dsr_negative_skew_lowers_p_when_significant():
    """显著 case (p > 0.5) 下, 负 skew → denominator 变大 → |z| 变小 → p 朝 0.5 靠拢."""
    # SR=2.5 > E_max(N=20)=1.86 → baseline p > 0.5
    p_normal = deflated_sharpe_ratio(
        2.5, n_trials=20, n_observations=200, skewness=0.0, kurtosis=3.0
    )
    p_skewed = deflated_sharpe_ratio(
        2.5, n_trials=20, n_observations=200, skewness=-1.0, kurtosis=3.0
    )
    assert p_normal > 0.5
    assert p_skewed < p_normal


def test_dsr_high_kurtosis_lowers_p_when_significant():
    """显著 case 下, 高 kurtosis (fat tail) → denominator 变大 → p 朝 0.5 靠拢."""
    p_normal = deflated_sharpe_ratio(
        2.5, n_trials=20, n_observations=200, kurtosis=3.0
    )
    p_fat = deflated_sharpe_ratio(
        2.5, n_trials=20, n_observations=200, kurtosis=8.0
    )
    assert p_normal > 0.5
    assert p_fat < p_normal


def test_dsr_zero_sharpe_below_expected_max():
    """observed SR=0 + 任何 N>1 → 远低于 E[max(N)] → p ≈ 0."""
    p = deflated_sharpe_ratio(0.0, n_trials=10, n_observations=500)
    assert p < 0.05


# ━━━━━ min_sharpe_for_significance ━━━━━

def test_min_sr_grows_with_n_trials():
    sr_10 = min_sharpe_for_significance(n_trials=10, n_observations=252)
    sr_100 = min_sharpe_for_significance(n_trials=100, n_observations=252)
    assert sr_100 > sr_10


def test_min_sr_shrinks_with_n_obs():
    sr_50 = min_sharpe_for_significance(n_trials=20, n_observations=50)
    sr_500 = min_sharpe_for_significance(n_trials=20, n_observations=500)
    assert sr_500 < sr_50


def test_min_sr_nan_for_invalid():
    assert math.isnan(min_sharpe_for_significance(n_trials=1, n_observations=100))
    assert math.isnan(min_sharpe_for_significance(n_trials=100, n_observations=1))


# ━━━━━ Euler-Mascheroni 常数正确性 ━━━━━

def test_euler_constant_value():
    assert abs(EULER_MASCHERONI - 0.5772156649) < 1e-10
