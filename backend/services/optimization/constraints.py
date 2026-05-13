"""Phase η++++++ + ψ — Optuna 硬约束 (Config-driven, 单一职责).

⚠ Optuna 目标函数返回前先过此关. 任何违反 → 该 trial 给 -INF 直接弃.
⚠ 改约束 → 改 backend/config/optuna_config.yaml.constraints. Rule 7: 不许 hardcode.

业界投资学约定 (默认值, 可调):
  - max_drawdown > -25% 必须 reject (用户心脏受不了)
  - 最差单笔 > -30% reject (单股可能退市)
  - 最长连续亏损 streak ≤ 5 (心理承受极限)
  - 至少开仓 5 次 (样本量保底)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from services.backtest.result import TradeResult
from services.optimization.config import OptunaConfig, get_optuna_config


@dataclass(frozen=True)
class HardConstraints:
    """投资者风险底线 (任何 trial 违反这些都被弃)."""
    max_acceptable_drawdown:  float
    worst_single_loss:        float
    max_loss_streak:          int
    min_traded:               int

    @classmethod
    def from_config(cls, cfg: Optional[OptunaConfig] = None) -> "HardConstraints":
        cfg = cfg or get_optuna_config()
        c = cfg.constraints
        return cls(
            max_acceptable_drawdown=c.max_acceptable_drawdown,
            worst_single_loss=c.worst_single_loss,
            max_loss_streak=c.max_loss_streak,
            min_traded=c.min_traded,
        )


# 模块级单例 (从 yaml 读)
DEFAULT_CONSTRAINTS = HardConstraints.from_config()


def _max_loss_streak(rets: np.ndarray) -> int:
    """最长连续亏损 streak."""
    if len(rets) == 0:
        return 0
    losses = rets < 0
    max_streak = cur = 0
    for is_loss in losses:
        if is_loss:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0
    return max_streak


def passes_hard_constraints(
    trades: list[TradeResult],
    constraints: Optional[HardConstraints] = None,
) -> tuple[bool, Optional[str]]:
    """检查 trades 是否通过所有硬约束.

    Returns:
        (passes: bool, fail_reason: str or None)
    """
    c = constraints or DEFAULT_CONSTRAINTS

    if not trades:
        return False, "no_trades"
    traded = [t for t in trades if t.exit_reason != "one_word_blocked"]
    if len(traded) < c.min_traded:
        return False, f"min_traded {len(traded)} < {c.min_traded}"

    rets = np.array([t.net_ret for t in traded])
    dds  = np.array([t.max_drawdown for t in traded])

    # 1. 平均 max_dd 不能超
    avg_dd = float(dds.mean())
    if avg_dd < c.max_acceptable_drawdown:
        return False, f"avg_dd {avg_dd:.2%} < {c.max_acceptable_drawdown:.2%}"

    # 2. 单笔最差不能超
    worst_loss = float(rets.min())
    if worst_loss < c.worst_single_loss:
        return False, f"worst_loss {worst_loss:.2%} < {c.worst_single_loss:.2%}"

    # 3. 最长连亏不能超
    streak = _max_loss_streak(rets)
    if streak > c.max_loss_streak:
        return False, f"loss_streak {streak} > {c.max_loss_streak}"

    return True, None
