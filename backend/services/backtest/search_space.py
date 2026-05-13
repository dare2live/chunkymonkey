"""Phase ζ — Optuna 超参搜索空间 (单一职责).

⚠ 改搜索范围 / 加新超参 → 只改这一处 (configs 风格).
⚠ 任何 optimize_*.py 不允许内联 trial.suggest_xxx — 必须用此处的 sample_*.

设计原则:
  - 范围基于业界经验 (止损 3%-15%, 止盈 5%-30%, trailing 1%-8%)
  - hp 选项与 profile 的 holding_days 集合保持一致 (5/10/15/20/30/60/90)
  - 后续可加新维度: kelly_fraction, min_wilson_win etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.backtest.strategy_defaults import StrategyDefaults


@dataclass(frozen=True)
class SearchSpace:
    """Optuna 5 维搜索空间 (Phase ζ.full+: 加 buy_offset 维)."""
    hp_choices: tuple[int, ...] = (5, 10, 15, 20, 30, 60, 90)
    stop_pct_lo: float = -0.15      # 最大 15% 止损
    stop_pct_hi: float = -0.03      # 最小 3% 止损 (再小没意义)
    target_pct_lo: float = 0.05     # 最低 5% 止盈
    target_pct_hi: float = 0.30     # 最高 30% 止盈
    trailing_pct_lo: float = 0.01   # 1% trailing
    trailing_pct_hi: float = 0.08   # 8% trailing
    # Phase ζ.full+: 买入点延迟 (T+1 立即 / T+2 / T+3 / T+5)
    buy_offset_choices: tuple[int, ...] = (1, 2, 3, 5)

    def sample(self, trial) -> tuple:
        """供 Optuna trial 调用 — 返回 (StrategyDefaults, hp, buy_offset)."""
        hp = trial.suggest_categorical("hp", list(self.hp_choices))
        stop_pct = trial.suggest_float("stop_pct", self.stop_pct_lo, self.stop_pct_hi)
        target_pct = trial.suggest_float("target_pct", self.target_pct_lo, self.target_pct_hi)
        trailing_pct = trial.suggest_float("trailing_pct", self.trailing_pct_lo, self.trailing_pct_hi)
        buy_offset = trial.suggest_categorical("buy_offset", list(self.buy_offset_choices))
        return StrategyDefaults(
            stop_pct=stop_pct,
            target_pct=target_pct,
            trailing_pct=trailing_pct,
            label=f"hp{hp}_offset{buy_offset}_s{stop_pct*100:.1f}_t{target_pct*100:.1f}_tr{trailing_pct*100:.1f}",
        ), hp, buy_offset


DEFAULT_SEARCH_SPACE = SearchSpace()
