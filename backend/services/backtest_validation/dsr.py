"""DSR (Deflated Sharpe Ratio) — Bailey & Lopez de Prado 2014.

DSR p_conf = Phi((SR_hat - SR_expected_max) * sqrt(n-1) / sqrt(1 - skew*SR + (k-1)/4*SR^2))

where:
- SR_hat: observed Sharpe ratio (annualized)
- SR_expected_max: expected max SR under null hypothesis (depends on n_trials)
- n: number of return observations
- skew: skewness of returns
- k: kurtosis of returns

ChunkyMonkey 实测红线: DSR p_conf < 0.95 → block promote.

Reference:
- Bailey & Lopez de Prado (2014) "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting, and Non-Normality"
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class DSRResult:
    sr_observed: float
    sr_expected_max: float
    dsr: float  # Deflated Sharpe (z-statistic)
    p_conf: float  # Phi(dsr), should be >= 0.95
    p_value: float  # 1 - p_conf
    n_obs: int
    n_trials: int
    skew: float
    kurtosis: float
    passes: bool  # p_conf >= 0.95


def compute_dsr(
    returns: np.ndarray,
    *,
    n_trials: int = 1,
    threshold_p_conf: float = 0.95,
    periods_per_year: int = 252,
) -> DSRResult:
    """Compute Deflated Sharpe Ratio.

    Args:
        returns: 1D array of period returns (e.g. daily returns)
        n_trials: number of strategies tested (for selection bias correction).
            纯量化 Optuna 50 trials = 50; sniper 单策略 = 1; ensemble = 3 类.
        threshold_p_conf: p_conf >= threshold passes (default 0.95)
        periods_per_year: 252 daily / 12 monthly

    Returns:
        DSRResult
    """
    returns = np.asarray(returns, dtype=np.float64).flatten()
    n = len(returns)
    if n < 30:
        raise ValueError(f"Need at least 30 observations, got {n}")
    if n_trials < 1:
        raise ValueError(f"n_trials >= 1, got {n_trials}")

    mean = returns.mean()
    std = returns.std(ddof=1)
    if std < 1e-12:
        raise ValueError("returns std too small for SR")

    sr_period = mean / std
    sr_annualized = sr_period * math.sqrt(periods_per_year)

    skew = float(stats.skew(returns))
    excess_kurt = float(stats.kurtosis(returns))  # excess kurtosis (Normal = 0)

    # Expected max SR under null hypothesis (Bailey & Lopez de Prado 2014 Eq. 6)
    if n_trials > 1:
        # Use Euler-Mascheroni constant approximation
        euler = 0.5772156649015329
        sr_expected_max_period = (
            (1 - euler) * stats.norm.ppf(1 - 1 / n_trials)
            + euler * stats.norm.ppf(1 - 1 / (n_trials * math.e))
        )
        sr_expected_max = sr_expected_max_period * math.sqrt(periods_per_year)
    else:
        sr_expected_max = 0.0

    # DSR = z-statistic that observed SR > expected max SR
    # Adjusted SR variance (Eq. 2 in DSR paper)
    sr_period_for_var = sr_period
    denom = math.sqrt(
        (1 - skew * sr_period_for_var + (excess_kurt) / 4.0 * sr_period_for_var ** 2)
        / (n - 1)
    )
    if denom < 1e-12:
        denom = 1e-12
    dsr_z = (sr_annualized - sr_expected_max) / (denom * math.sqrt(periods_per_year))

    p_conf = float(stats.norm.cdf(dsr_z))
    p_value = 1.0 - p_conf

    return DSRResult(
        sr_observed=sr_annualized,
        sr_expected_max=sr_expected_max,
        dsr=dsr_z,
        p_conf=p_conf,
        p_value=p_value,
        n_obs=n,
        n_trials=n_trials,
        skew=skew,
        kurtosis=excess_kurt,
        passes=p_conf >= threshold_p_conf,
    )
