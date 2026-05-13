"""Phase π — NAV → 性能指标 (单一职责)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class BacktestMetrics:
    total_return:   float    # 总收益率 (e.g. +0.45 = +45%)
    annual_return:  float    # 年化
    max_drawdown:   float    # 最大回撤 (e.g. -0.20 = -20%)
    calmar:         float    # annual_return / abs(max_drawdown)
    sharpe:         float    # 年化 sharpe
    monthly_win_rate: float  # 月度胜率
    n_days:         int
    avg_daily_ret:  float
    std_daily_ret:  float


def compute_metrics(nav_series: list[float], trading_days_per_year: int = 252) -> BacktestMetrics:
    """NAV 序列 (从 1.0 起) → metrics."""
    n = len(nav_series)
    if n < 2:
        return BacktestMetrics(0, 0, 0, 0, 0, 0, n, 0, 0)

    nav = np.array(nav_series)
    daily_ret = np.diff(nav) / nav[:-1]

    total_ret = nav[-1] / nav[0] - 1
    years = n / trading_days_per_year
    annual_ret = (1 + total_ret) ** (1 / max(years, 0.01)) - 1 if total_ret > -1 else -1

    # Max drawdown
    running_max = np.maximum.accumulate(nav)
    drawdowns = (nav - running_max) / running_max
    max_dd = float(drawdowns.min())

    calmar = annual_ret / abs(max_dd) if abs(max_dd) > 0.001 else 0

    avg_d = float(daily_ret.mean())
    std_d = float(daily_ret.std())
    sharpe = (avg_d * trading_days_per_year) / (std_d * np.sqrt(trading_days_per_year)) if std_d > 0 else 0

    # Monthly win rate (每 21 个交易日为一月)
    months = []
    for i in range(0, n - 21, 21):
        months.append(nav[i + 21] / nav[i] - 1)
    if not months:
        months = [total_ret]
    monthly_win = float((np.array(months) > 0).mean())

    return BacktestMetrics(
        total_return=float(total_ret),
        annual_return=float(annual_ret),
        max_drawdown=max_dd,
        calmar=float(calmar),
        sharpe=float(sharpe),
        monthly_win_rate=monthly_win,
        n_days=n,
        avg_daily_ret=avg_d,
        std_daily_ret=std_d,
    )


def compute_excess_alpha(strategy_nav: list[float], benchmark_nav: list[float]) -> dict:
    """对比策略 vs 基准 (HS300) 的超额收益."""
    if len(strategy_nav) != len(benchmark_nav) or len(strategy_nav) < 2:
        return {}
    s = np.array(strategy_nav)
    b = np.array(benchmark_nav)
    excess_total = (s[-1] / s[0]) - (b[-1] / b[0])
    s_ret = np.diff(s) / s[:-1]
    b_ret = np.diff(b) / b[:-1]
    excess_daily = s_ret - b_ret
    # Information Ratio = mean(excess_daily) / std(excess_daily) × sqrt(252)
    if excess_daily.std() > 0:
        ir = float(excess_daily.mean() / excess_daily.std() * np.sqrt(252))
    else:
        ir = 0.0
    return {
        "excess_total_return":   float(excess_total),
        "excess_daily_mean":     float(excess_daily.mean()),
        "information_ratio":     ir,
    }
