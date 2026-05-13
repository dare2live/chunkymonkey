"""Phase δ — 退出规则 (纯函数)。

每个持仓每日检查 3 条退出规则 (按优先级):
  1. stop_hit:    今日低点 ≤ exit_stop_price → 触发止损
  2. target_hit:  今日高点 ≥ exit_target_1_price → 触发止盈
  3. horizon:     holding_days ≥ expected_horizon → 到期出场

涨跌停 gate: 一字跌停时 stop 不可达, 用 close 作 fill (gap 损失接受)
            一字涨停时 target 不可达, blocked_limit 留 D+1
"""
from __future__ import annotations

from typing import Any

LIMIT_THRESHOLD = 0.097  # 9.7% 视为涨跌停


def is_limit_up_day(open_p: float, prev_close: float | None) -> bool:
    if prev_close is None or prev_close <= 0:
        return False
    return (open_p - prev_close) / prev_close >= LIMIT_THRESHOLD


def is_limit_down_day(open_p: float, prev_close: float | None) -> bool:
    if prev_close is None or prev_close <= 0:
        return False
    return (open_p - prev_close) / prev_close <= -LIMIT_THRESHOLD


def evaluate_exit(
    *,
    holding_days: int,
    expected_horizon: int | None,
    exit_stop_price: float | None,
    exit_target_1_price: float | None,
    today_open: float | None,
    today_high: float | None,
    today_low: float | None,
    today_close: float | None,
    prev_close: float | None = None,
) -> dict[str, Any]:
    """评估单仓退出。

    Returns:
        {
          "action": "hold" | "exit",
          "reason": "stop" | "target_1" | "horizon" | "blocked_limit" | None,
          "fill_price": float | None,
        }
    """
    if today_close is None or today_open is None:
        # 停牌, 持仓不动
        return {"action": "hold", "reason": "halted", "fill_price": None}

    # 优先级 1: 止损 (今日低点穿透 stop)
    if exit_stop_price is not None and today_low is not None and today_low <= exit_stop_price:
        # 一字跌停 stop 不可达, 用 close fill (吃 gap 损失)
        if is_limit_down_day(today_open, prev_close):
            return {"action": "exit", "reason": "stop", "fill_price": today_close}
        # 正常: 用 stop_price (假设 stop limit order 已挂)
        return {"action": "exit", "reason": "stop", "fill_price": exit_stop_price}

    # 优先级 2: 止盈 (今日高点触达 target_1)
    if exit_target_1_price is not None and today_high is not None and today_high >= exit_target_1_price:
        # 一字涨停 target 不可达, blocked
        if is_limit_up_day(today_open, prev_close):
            return {"action": "hold", "reason": "blocked_limit", "fill_price": None}
        return {"action": "exit", "reason": "target_1", "fill_price": exit_target_1_price}

    # 优先级 3: horizon 到期 (T+expected_horizon 收盘出)
    if expected_horizon is not None and holding_days >= expected_horizon:
        if is_limit_down_day(today_open, prev_close):
            return {"action": "hold", "reason": "blocked_limit", "fill_price": None}
        return {"action": "exit", "reason": "horizon", "fill_price": today_close}

    return {"action": "hold", "reason": None, "fill_price": None}
