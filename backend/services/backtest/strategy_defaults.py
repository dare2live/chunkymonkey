"""Phase ε.4 — 策略默认参数 (stop / target / trailing).

⚠ 这些是策略参数 (区别于 execution_model 的执行参数).
⚠ 改这里 → 重建 mart_stock_formula_optuna_v2 全表都受影响.
⚠ D 路线 (ζ) 会按桶寻优替代这些默认值.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyDefaults:
    """单一策略的 stop/target/trailing 三连参数."""
    stop_pct: float        # 例 -0.06: stop_price = buy × 0.94
    target_pct: float      # 例 +0.10: target = buy × 1.10 (达到 arm trailing)
    trailing_pct: float    # 例 0.025: 从 high_since_buy 回撤超 2.5% → 卖
    label: str = ""        # 调试用名


# Phase ε 默认: 偏保守 (止损 6% / 止盈 10% / 移动 2.5%)
# 后续 D 路线会按 (stock × formula × bucket × hp) 寻优
DEFAULT_STRATEGY = StrategyDefaults(
    stop_pct=-0.06,
    target_pct=+0.10,
    trailing_pct=0.025,
    label="default_conservative",
)
