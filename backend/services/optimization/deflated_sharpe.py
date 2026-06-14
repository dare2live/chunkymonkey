"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014) — 多重比较去过拟合治理。

地基-reset 后忠实恢复 (原 services/optimization/deflated_sharpe.py), 唯一改动: scipy.stats.norm
换 stdlib (math.erf 算 cdf + Acklam 逆正态算 ppf), 免 scipy 依赖 (CI 无 scipy; 同 oos_ic spearman 做法)。

用途 (L0 寻参防过拟合, 用户 #1 约束): 跑 N 个参数组合后, "最佳组合的 IC_IR" 本身有 multiple-testing
selection bias。本模块给"在 H0 (no skill, N trials) 下 observed 真显著性 p-value", p>0.95 才算真 alpha
非选择噪音。IC_IR = 日度 IC 序列的 information ratio (Sharpe 类), 故 DSR 对它适用。

参考: Bailey, D.H., López de Prado, M.M. (2014). "The Deflated Sharpe Ratio". JPM 40(5): 94-107.
"""
from __future__ import annotations

import math

EULER_MASCHERONI = 0.5772156649015329  # evidence: 数学常数 (Euler-Mascheroni γ)


def _norm_cdf(x: float) -> float:
    """标准正态 CDF via erf (stdlib): Φ(x)=0.5(1+erf(x/√2))。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam 逆正态 CDF 系数 (数学常数, accurate ~1.15e-9). evidence: Peter Acklam algorithm
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
_P_LOW = 0.02425  # evidence: Acklam 分段边界


def _norm_ppf(p: float) -> float:
    """标准正态逆 CDF (Acklam): Φ⁻¹(p)。p∈(0,1)。"""
    if not 0.0 < p < 1.0:
        return float("nan")
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    if p <= 1.0 - _P_LOW:
        q = p - 0.5
        r = q * q
        return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
               (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
           ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)


def expected_max_sharpe(n_trials: int, sharpe_variance: float = 1.0) -> float:
    """E[max sharpe over N independent trials] under H0 (Bailey-LdP eq.12)。N 越大随机也越高。"""
    if n_trials < 2:
        return 0.0
    sigma = math.sqrt(sharpe_variance)
    a = (1.0 - EULER_MASCHERONI) * _norm_ppf(1.0 - 1.0 / n_trials)
    b = EULER_MASCHERONI * _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(sigma * (a + b))


def deflated_sharpe_ratio(
    observed_sharpe: float, n_trials: int, n_observations: int,
    sharpe_variance: float = 1.0, skewness: float = 0.0, kurtosis: float = 3.0,
) -> float:
    """DSR p-value = P(true SR>0 | observed, N trials, T obs)。p>0.95 才算真 alpha 非试错噪音。

    Bailey-LdP 2014 eq.9: Var(SR)=[1-γ3·SR+(γ4-1)/4·SR²]/(T-1)。NaN 当输入不足/数值病态。
    """
    if n_trials < 2 or n_observations < 2:
        return float("nan")
    expected_max = expected_max_sharpe(n_trials, sharpe_variance)
    numerator = (observed_sharpe - expected_max) * math.sqrt(n_observations - 1)
    denom_sq = 1.0 - skewness * observed_sharpe + (kurtosis - 1.0) / 4.0 * observed_sharpe ** 2
    if denom_sq <= 0:
        return float("nan")
    return float(_norm_cdf(numerator / math.sqrt(denom_sq)))


def min_sharpe_for_significance(
    n_trials: int, n_observations: int, target_p: float = 0.95, sharpe_variance: float = 1.0,
) -> float:
    """反查: N trials 累积下 deflated p>=target_p 至少需多大 observed SR (粗近似 skew=0/kurt=3)。"""
    if n_trials < 2 or n_observations < 2:
        return float("nan")
    return float(expected_max_sharpe(n_trials, sharpe_variance)
                 + _norm_ppf(target_p) / math.sqrt(n_observations - 1))
