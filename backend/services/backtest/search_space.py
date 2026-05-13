"""Phase ζ + ψ — Optuna 超参搜索空间 (Config-driven, 单一职责).

⚠ 改搜索范围 → 改 backend/config/optuna_config.yaml.search_space.strategy
⚠ 任何 optimize_*.py 不允许内联 trial.suggest_xxx — 必须用此处的 sample_*.
⚠ Rule 7 (CLAUDE.md): hp_choices / 区间不许 hardcode, 全走 config.

设计原则:
  - 范围基于业界经验, 但写在 yaml 里方便 sensitivity sweep
  - hp 选项与 profile 的 holding_days 集合保持一致
  - DEFAULT_SEARCH_SPACE 模块级单例 — 业务代码用这个, 不要每次重 load
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.backtest.strategy_defaults import StrategyDefaults
from services.optimization.config import OptunaConfig, get_optuna_config


@dataclass(frozen=True)
class SearchSpace:
    """5 维 strategy 搜索空间. 实例化从 config 读, 不 hardcode."""
    hp_choices: tuple
    stop_pct_lo: float
    stop_pct_hi: float
    target_pct_lo: float
    target_pct_hi: float
    trailing_pct_lo: float
    trailing_pct_hi: float
    buy_offset_choices: tuple

    @classmethod
    def from_config(cls, cfg: Optional[OptunaConfig] = None) -> "SearchSpace":
        """从 yaml config 构造."""
        cfg = cfg or get_optuna_config()
        s = cfg.search_space.strategy
        return cls(
            hp_choices=s.hp_choices,
            stop_pct_lo=s.stop_pct.lo,
            stop_pct_hi=s.stop_pct.hi,
            target_pct_lo=s.target_pct.lo,
            target_pct_hi=s.target_pct.hi,
            trailing_pct_lo=s.trailing_pct.lo,
            trailing_pct_hi=s.trailing_pct.hi,
            buy_offset_choices=s.buy_offset_choices,
        )

    def sample(self, trial) -> tuple:
        """供 Optuna trial 调用 — 返回 (StrategyDefaults, hp, buy_offset)."""
        hp = trial.suggest_categorical("hp", list(self.hp_choices))
        stop_pct = trial.suggest_float("stop_pct", self.stop_pct_lo, self.stop_pct_hi)
        target_pct = trial.suggest_float("target_pct", self.target_pct_lo, self.target_pct_hi)
        trailing_pct = trial.suggest_float("trailing_pct",
                                           self.trailing_pct_lo, self.trailing_pct_hi)
        buy_offset = trial.suggest_categorical("buy_offset", list(self.buy_offset_choices))
        return StrategyDefaults(
            stop_pct=stop_pct,
            target_pct=target_pct,
            trailing_pct=trailing_pct,
            label=f"hp{hp}_offset{buy_offset}_s{stop_pct*100:.1f}_"
                  f"t{target_pct*100:.1f}_tr{trailing_pct*100:.1f}",
        ), hp, buy_offset


# 模块级单例 — 业务代码 import 这个, 改参数走 yaml
DEFAULT_SEARCH_SPACE = SearchSpace.from_config()
