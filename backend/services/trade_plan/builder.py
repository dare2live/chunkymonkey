"""Phase γ D4 — trade_plan 8 字段算式。

输入: 单股 turtle features + latest close + (可选) 推荐持仓天数
输出: dict 含 8 个 plan 字段 + reason_codes

算法 (开发手册 §6.4.1):
  entry_target_price      = close × (1 + ATR_pct × 0.3)
  entry_aggressive_price  = max(entry_level_20, close × 1.02)
  entry_max_price         = entry_level_55 or entry_target × 1.05
  exit_stop_price         = stop_level_20_2n (= close - 2 × ATR_14)
  exit_target_1_price     = entry_target × (1 + 2 × ATR_pct)
  exit_target_2_price     = entry_target × (1 + 4 × ATR_pct)
  risk_reward_ratio       = (exit_target_1 - entry_target) / (entry_target - exit_stop)
  expected_horizon_days   = 上游传入 (来自 mart_stage_formula_fitness 最佳行 holding_days)

设计:
  - 纯函数, 不读 DB (DB I/O 在 scripts/build_stock_trade_plan.py)
  - 任意必要输入缺失 → 返回 None 字段 (不阻断 build)
  - reason_codes 是 list[str], 用于审计 + UI hover 解释
"""
from __future__ import annotations

from typing import Any


def build_trade_plan(
    *,
    close: float | None,
    atr_14: float | None,
    atr_14_pct: float | None = None,
    entry_level_20: float | None = None,
    entry_level_55: float | None = None,
    stop_level_20_2n: float | None = None,
    expected_horizon_days: int | None = None,
    entry_basis: str = "turtle_20",
) -> dict[str, Any]:
    """根据 turtle features + close 生成 8 字段 trade plan。

    Returns:
        {entry_target_price, entry_aggressive_price, entry_max_price,
         exit_target_1_price, exit_target_2_price, exit_stop_price,
         risk_reward_ratio, expected_horizon_days, atr_14, entry_basis, reason_codes_json}
    """
    reasons: list[str] = []

    # 必要原料: close + ATR
    if close is None or close <= 0:
        reasons.append("close_missing")
        return _empty_plan(reasons, atr_14, entry_basis)
    if atr_14 is None or atr_14 <= 0:
        reasons.append("atr_14_missing")
        return _empty_plan(reasons, atr_14, entry_basis)

    # ATR 百分比 (若未传, 自己算)
    atr_pct = atr_14_pct
    if atr_pct is None or atr_pct <= 0:
        atr_pct = atr_14 / close
    reasons.append(f"atr_14:{atr_14:.3f}/{atr_pct*100:.2f}%")

    # 入场 3 价
    entry_target = close * (1.0 + atr_pct * 0.3)
    entry_aggressive = max(entry_level_20 or 0.0, close * 1.02) or None
    entry_max = entry_level_55 if (entry_level_55 and entry_level_55 > 0) else entry_target * 1.05

    # 出场 3 价
    exit_stop = stop_level_20_2n if (stop_level_20_2n and stop_level_20_2n > 0) else (close - 2 * atr_14)
    exit_target_1 = entry_target * (1.0 + 2 * atr_pct)
    exit_target_2 = entry_target * (1.0 + 4 * atr_pct)

    # 风险报酬比 = (盈 / 亏); 亏 = entry - stop, 盈 = target1 - entry
    risk = entry_target - exit_stop
    reward = exit_target_1 - entry_target
    rr = (reward / risk) if risk > 0 else None
    if rr is not None:
        reasons.append(f"R/R:{rr:.2f}")

    if entry_level_55:
        reasons.append(f"entry_55:{entry_level_55:.2f}")

    return {
        "entry_target_price":     round(entry_target, 4),
        "entry_aggressive_price": round(entry_aggressive, 4) if entry_aggressive else None,
        "entry_max_price":        round(entry_max, 4),
        "exit_target_1_price":    round(exit_target_1, 4),
        "exit_target_2_price":    round(exit_target_2, 4),
        "exit_stop_price":        round(exit_stop, 4),
        "risk_reward_ratio":      round(rr, 3) if rr is not None else None,
        "expected_horizon_days":  expected_horizon_days or 20,  # fallback 20 (Phase γ Plan agent 决策)
        "atr_14":                 atr_14,
        "entry_basis":            entry_basis,
        "reason_codes_json":      reasons,
    }


def _empty_plan(reasons: list[str], atr_14: float | None, entry_basis: str) -> dict[str, Any]:
    """缺料返回全 None plan, 但保留 reason_codes 解释为何。"""
    return {
        "entry_target_price": None,
        "entry_aggressive_price": None,
        "entry_max_price": None,
        "exit_target_1_price": None,
        "exit_target_2_price": None,
        "exit_stop_price": None,
        "risk_reward_ratio": None,
        "expected_horizon_days": None,
        "atr_14": atr_14,
        "entry_basis": entry_basis,
        "reason_codes_json": reasons,
    }
