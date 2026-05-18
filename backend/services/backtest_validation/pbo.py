"""PBO (Probability of Backtest Overfitting) — Lopez de Prado CSCV.

Combinatorially Symmetric Cross-Validation:
对 n_trials × n_periods 收益 matrix, 平均拆 S=16 个 sub-period, 每次选 S/2 做
IS, 另 S/2 做 OOS. 对 IS 选 best trial → 检查 OOS rank percentile.

PBO = Pr(λ < 0), where λ = logit(omega_bar / n) and omega_bar = avg OOS rank.

Reference:
- Bailey, Borwein, Lopez de Prado, Zhu (2014)
  "The Probability of Backtest Overfitting"
- Lopez de Prado (2014) "The False Strategy Theorem"

ChunkyMonkey 实测红线: PBO > 0.20 → block promote.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    lambda_mean: float
    lambda_std: float
    n_combos: int
    sub_periods: int
    passes: bool  # PBO <= 0.20


def compute_pbo(
    returns_matrix: np.ndarray,
    *,
    sub_periods: int = 16,
    threshold: float = 0.20,
) -> PBOResult:
    """Compute PBO via CSCV.

    Args:
        returns_matrix: shape (n_trials, n_periods) — 每行一个 trial OOS returns
            注: 输入必须是 OOS walk-forward returns (不是 IS)
        sub_periods: S in CSCV, 默认 16 (Lopez de Prado 推荐 even number)
        threshold: PBO ≤ threshold 视为 pass (默认 0.20)

    Returns:
        PBOResult(pbo, lambda_mean, lambda_std, n_combos, sub_periods, passes)
    """
    if returns_matrix.ndim != 2:
        raise ValueError(f"returns_matrix must be 2D, got {returns_matrix.shape}")
    n_trials, n_periods = returns_matrix.shape
    if n_trials < 2:
        raise ValueError(f"need at least 2 trials, got {n_trials}")
    if sub_periods % 2 != 0:
        raise ValueError(f"sub_periods must be even, got {sub_periods}")
    if n_periods < sub_periods:
        raise ValueError(f"n_periods={n_periods} < sub_periods={sub_periods}")

    # Split into S sub-periods of equal length
    base = n_periods // sub_periods
    rem = n_periods % sub_periods
    sub_indices = []
    cursor = 0
    for i in range(sub_periods):
        size = base + (1 if i < rem else 0)
        sub_indices.append(np.arange(cursor, cursor + size))
        cursor += size

    half = sub_periods // 2
    combos = list(itertools.combinations(range(sub_periods), half))
    lambda_values: list[float] = []
    for is_subs in combos:
        is_set = set(is_subs)
        is_idx = np.concatenate([sub_indices[i] for i in is_subs])
        oos_idx = np.concatenate([sub_indices[i] for i in range(sub_periods) if i not in is_set])

        # IS Sharpe per trial
        is_returns = returns_matrix[:, is_idx]
        is_mean = is_returns.mean(axis=1)
        is_std = is_returns.std(axis=1, ddof=1)
        is_sharpe = np.where(is_std > 1e-12, is_mean / is_std, 0.0)

        # Best IS trial
        best_is = int(np.argmax(is_sharpe))

        # OOS Sharpe per trial
        oos_returns = returns_matrix[:, oos_idx]
        oos_mean = oos_returns.mean(axis=1)
        oos_std = oos_returns.std(axis=1, ddof=1)
        oos_sharpe = np.where(oos_std > 1e-12, oos_mean / oos_std, 0.0)

        # Rank of best_is in OOS (1 = worst, n = best)
        ranks = np.argsort(np.argsort(oos_sharpe)) + 1
        omega = (ranks[best_is] - 0.5) / n_trials
        omega = max(min(omega, 1 - 1e-12), 1e-12)  # clip for logit
        lam = math.log(omega / (1 - omega))
        lambda_values.append(lam)

    lambda_arr = np.array(lambda_values)
    pbo = float((lambda_arr < 0).mean())
    return PBOResult(
        pbo=pbo,
        lambda_mean=float(lambda_arr.mean()),
        lambda_std=float(lambda_arr.std(ddof=1) if len(lambda_arr) > 1 else 0.0),
        n_combos=len(combos),
        sub_periods=sub_periods,
        passes=pbo <= threshold,
    )
