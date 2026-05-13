"""Paper Sim v2 — 退出规则.

对一只 open position + 当日行情, 判断是否触发清仓 + 触发原因.

5 个触发 (优先级从高到低):
  1. stop hit     close ≤ entry × (1 + optimal_stop_pct), stop_pct 为负数
  2. target hit   close ≥ entry × (1 + optimal_target_pct)
  3. trailing hit close ≤ peak_since_entry × (1 + optimal_trailing_pct)
  4. hp expired   days_held ≥ optimal_hp
  5. stage_det    open stage ≤ 2, 今日 stage = 4, 且 current_close < entry

注: stop / target / trailing 必须用每股 Optuna 寻优出的真实参数 (不全局硬编码).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExitInputs:
    """所有评估退出需要的输入. 单一字段表, 便于单测注入."""
    stock_code: str
    entry_price: float
    entry_stage: Optional[str]      # 买入时 technical_stage (字符串 e.g. '2', '4')
    optimal_hp: int
    optimal_stop_pct: Optional[float]      # 例 -0.05 (负数), None 则不用 stop
    optimal_target_pct: Optional[float]    # 例 +0.20
    optimal_trailing_pct: Optional[float]  # 例 -0.10
    days_held: int
    current_close: float
    peak_since_entry: float          # max(close) 自买入以来; 用于 trailing
    today_stage: Optional[str]       # 今日 technical_stage (取自 fact_signal_context)


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    exit_price: float = 0.0


def evaluate_exit(inp: ExitInputs) -> ExitDecision:
    """按优先级判断 5 个触发. 第一个命中就返回, 短路."""
    if inp.entry_price <= 0 or inp.current_close <= 0:
        return ExitDecision(False, reason="invalid_price")

    # 1. stop loss — 最优先 (止损永远优先于任何其它出场逻辑)
    if inp.optimal_stop_pct is not None:
        stop_level = inp.entry_price * (1 + inp.optimal_stop_pct)
        if inp.current_close <= stop_level:
            return ExitDecision(True, reason="stop_hit", exit_price=inp.current_close)

    # 2. target — 收益达标
    if inp.optimal_target_pct is not None:
        target_level = inp.entry_price * (1 + inp.optimal_target_pct)
        if inp.current_close >= target_level:
            return ExitDecision(True, reason="target_hit", exit_price=inp.current_close)

    # 3. trailing — 从历史高点回撤
    if inp.optimal_trailing_pct is not None and inp.peak_since_entry > 0:
        trail_level = inp.peak_since_entry * (1 + inp.optimal_trailing_pct)
        if inp.current_close <= trail_level:
            return ExitDecision(True, reason="trailing_hit", exit_price=inp.current_close)

    # 4. hp 到期
    if inp.optimal_hp > 0 and inp.days_held >= inp.optimal_hp:
        return ExitDecision(True, reason="hp_expired", exit_price=inp.current_close)

    # 5. stage 恶化 (买入时 ≤2 强信号, 今天 =4 + 当前亏损)
    if (inp.entry_stage in ("1", "1.5", "2")
            and inp.today_stage == "4"
            and inp.current_close < inp.entry_price):
        return ExitDecision(True, reason="stage_deterioration", exit_price=inp.current_close)

    return ExitDecision(False)
