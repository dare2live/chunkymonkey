"""Phase π.2 — 市场环境识别 (regime classification).

⚠ 单一职责: HS300 滚动 60d 收益率 → 牛/熊/震荡.
⚠ 改阈值: 改这一处.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

RegimeLabel = Literal["bull", "bear", "sideways"]
REGIME_LABELS: tuple[RegimeLabel, ...] = ("bull", "bear", "sideways")


@dataclass(frozen=True)
class RegimeConfig:
    """regime 阈值."""
    bull_threshold:  float = 0.10   # 60d ≥ +10% = 牛
    bear_threshold:  float = -0.10  # 60d ≤ -10% = 熊
    window_days:     int = 60       # 滚动窗口


def classify_regime(hs300_60d_return: float, config: RegimeConfig = RegimeConfig()) -> RegimeLabel:
    """HS300 60d 收益 → regime label."""
    if hs300_60d_return >= config.bull_threshold:
        return "bull"
    if hs300_60d_return <= config.bear_threshold:
        return "bear"
    return "sideways"


def summarize_regime_segments(
    strategy_nav: Sequence[float],
    benchmark_nav: Sequence[float],
    config: RegimeConfig = RegimeConfig(),
) -> list[dict[str, object]]:
    """按 benchmark regime 汇总策略日收益.

    与 v3 portfolio backtest API 原有口径一致:
    - regime 由 benchmark 的 rolling window return 决定;
    - 策略收益使用同一天 vs 前一日的日收益;
    - 输出顺序固定为 bull / bear / sideways.
    """
    stats = {
        label: {"n_days": 0, "sum_daily_ret": 0.0, "compound": 1.0}
        for label in REGIME_LABELS
    }
    limit = min(len(strategy_nav), len(benchmark_nav))
    for index in range(config.window_days, limit):
        r_window = benchmark_nav[index] / benchmark_nav[index - config.window_days] - 1
        daily_ret = strategy_nav[index] / strategy_nav[index - 1] - 1
        bucket = stats[classify_regime(r_window, config)]
        bucket["n_days"] += 1
        bucket["sum_daily_ret"] += daily_ret
        bucket["compound"] *= 1 + daily_ret

    segments: list[dict[str, object]] = []
    for label in REGIME_LABELS:
        bucket = stats[label]
        n_days = int(bucket["n_days"])
        if n_days:
            segments.append({
                "regime": label,
                "n_days": n_days,
                "avg_daily_ret": bucket["sum_daily_ret"] / n_days,
                "cumulative_return": bucket["compound"] - 1,
            })
        else:
            segments.append({
                "regime": label,
                "n_days": 0,
                "avg_daily_ret": 0,
                "cumulative_return": 0,
            })
    return segments
