"""Phase ψ.γ.discipline — Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

⚠ Rule 7 / Rule 8 在跨 study 多重测试场景的补充:
    Rule 7 防单 study 内 in-sample / OOS leakage.
    Rule 8 强制 walk-forward OOS 入业务表.
    **但** 累积跑过 N 个 study 变体 (ψ.γ.1 ensemble 20 维 + ψ.δ.1 板块 +
    per_stock_stage 9 维 + ψ.β reversal hp 多档 ablation + ...),
    "最好那一个 study 的 OOS sharpe" 本身有 multiple testing selection bias.

    Bailey-López de Prado 公式给出: 在 H0 (no skill, N trials random) 下,
    observed_sharpe 的"真实显著性" p-value. p > 0.95 才算 alpha 真存在,
    否则可能是跨 study 试错噪音.

⚠ 公式假设: returns ~ N(0, σ²) (skew=0, kurtosis=3) 是简化情况.
    真实策略 returns 通常有负 skew + 高 kurtosis (fat tail),
    实际 deflated SR 会更严 (denominator 更大 → p 更低).
    作为治理 prior 已足够, 用户传 skewness/kurtosis 可精确化.

参考: Bailey, D.H., López de Prado, M.M. (2014). "The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting, and Non-Normality".
    Journal of Portfolio Management 40 (5): 94-107.
"""
from __future__ import annotations

import math

from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


def expected_max_sharpe(
    n_trials: int,
    sharpe_variance: float = 1.0,
) -> float:
    """E[max sharpe over N independent trials] under H0 (no skill).

    Bailey & López de Prado (2014) eq. 12. 假设 trials sharpe ~ N(0, V[SR]).
    N 越大, max 期望越大 (随机也能跑出更高 SR).

    Args:
        n_trials: 跨 study 累积尝试的策略变体数. 治理粒度选 study 级 (best per study)
                  还是 trial 级取决于你信哪个; 常见做法 = trial 级,
                  n_trials = sum of all Optuna trials run across all studies.
        sharpe_variance: 不同 trials sharpe 估计的方差. 没有 prior 时用 1.0 (粗近似);
                  有历史 study 数据时取实测 V[SR].
    Returns:
        E[max SR] — N 个 random trial sharpe 的最大值期望.
    """
    if n_trials < 2:
        return 0.0
    sigma = math.sqrt(sharpe_variance)
    a = (1.0 - EULER_MASCHERONI) * norm.ppf(1.0 - 1.0 / n_trials)
    b = EULER_MASCHERONI * norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(sigma * (a + b))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    sharpe_variance: float = 1.0,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio p-value (Bailey & López de Prado, 2014).

    返回 P(true SR > 0 | observed = observed_sharpe, N trials, T obs).
    阈值惯例: p > 0.95 才算 "observed SR 真有 alpha",
    否则可能是跨 study 试错噪音.

    Args:
        observed_sharpe: 实测 OOS Sharpe (周期同 n_observations 单位).
        n_trials: 跨 study 累积尝试变体总数.
        n_observations: OOS 样本数 (e.g. 日度=交易日 / 月度=月数 / trade 级=trade 数).
        sharpe_variance: trials 间 SR 方差 prior (默认 1.0).
        skewness:  returns 偏度. 默认 0 (正态). 策略 returns 常负 skew.
        kurtosis:  returns 峰度. 默认 3 (正态). 策略 returns 常 > 3.

    Returns:
        Deflated SR p-value ∈ [0, 1]. NaN 当输入不足 (n_trials<2 / T<2) 或数值病态.
    """
    if n_trials < 2 or n_observations < 2:
        return float("nan")

    expected_max = expected_max_sharpe(n_trials, sharpe_variance)
    t_obs = n_observations

    # Bailey-LdP 2014 eq. 9: Var(SR_hat) = [1 - γ_3·SR + (γ_4-1)/4·SR^2] / (T-1)
    # γ_3 = skewness, γ_4 = (full) kurtosis (NOT excess). Normal: γ_3=0, γ_4=3.
    # numerator 乘 sqrt(T-1), denominator 不再除 T.
    numerator = (observed_sharpe - expected_max) * math.sqrt(t_obs - 1)
    denom_sq = (
        1.0
        - skewness * observed_sharpe
        + (kurtosis - 1.0) / 4.0 * observed_sharpe ** 2
    )
    if denom_sq <= 0:
        return float("nan")
    denominator = math.sqrt(denom_sq)
    if denominator == 0:
        return float("nan")

    return float(norm.cdf(numerator / denominator))


def min_sharpe_for_significance(
    n_trials: int,
    n_observations: int,
    target_p: float = 0.95,
    sharpe_variance: float = 1.0,
) -> float:
    """反查: 跨 N trials 累积下, 要 deflated p >= target_p 至少需要多大 observed SR.

    用途: "跑了 50 个变体, 我的 SR 至少要多少才算可信?" — 给数字答案.
    简化: skew=0 kurtosis=3, 反解 dsr=target_p, 忽略 denominator 微调 (粗近似).

    Args:
        n_trials: 跨 study 累积变体数.
        n_observations: OOS 样本数.
        target_p: 显著性目标 (默认 0.95).
        sharpe_variance: trials 间 SR 方差 prior.

    Returns:
        Minimum observed Sharpe 阈值.
    """
    if n_trials < 2 or n_observations < 2:
        return float("nan")
    z = norm.ppf(target_p)
    expected_max = expected_max_sharpe(n_trials, sharpe_variance)
    return float(expected_max + z / math.sqrt(n_observations - 1))
