"""卖出规则: trailing stop / hp 到期 / 止损价.

每持仓每日检查 (主 paper engine 驱动):
  1. 是否触达 stop_price → 全平 (止损)
  2. 是否触达 sell_target (盈利目标) → 启动 trailing
     - trailing 后, 若回撤 ≥ trailing_pct → 全平 (锁定利润)
  3. 是否到 hp 到期 → 全平 (持仓周期满)

返回: {"action": "hold"|"sell", "reason": str, "fill_pct": float}
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PositionState:
    """持仓状态 (调用方传入)."""
    stock_code: str
    buy_date: str
    buy_price: float
    holding_days_elapsed: int
    holding_days_target: int
    sell_target_price: float
    stop_price: float
    trailing_pct: float
    high_since_buy: float = 0.0   # 持仓期最高价
    trailing_armed: bool = False  # 是否已触达 sell_target


def evaluate_sell(
    pos: PositionState,
    today_high: float,
    today_low: float,
    today_close: float,
) -> dict:
    """单日卖出判定.

    优先级:
      1. 止损 (优先于其他)
      2. trailing 已启动 + 回撤超阈值
      3. 到期
      4. 持有 (更新 trailing 状态)
    """
    if today_high is None or today_low is None or today_close is None:
        return {"action": "hold", "reason": "halted", "fill_pct": None}

    # 1. 止损 (today_low ≤ stop)
    if today_low <= pos.stop_price:
        return {"action": "sell", "reason": "stop_hit", "fill_pct": pos.stop_price / pos.buy_price - 1}

    # 2a. 首次触达 sell_target → arm trailing
    if not pos.trailing_armed and today_high >= pos.sell_target_price:
        pos.trailing_armed = True
        pos.high_since_buy = max(pos.high_since_buy, today_high)

    # 2b. trailing 已启动, 检查回撤
    if pos.trailing_armed:
        pos.high_since_buy = max(pos.high_since_buy, today_high)
        retracement_from_high = (today_close - pos.high_since_buy) / pos.high_since_buy
        if retracement_from_high <= -pos.trailing_pct:
            fill = today_close
            return {"action": "sell", "reason": "trailing_stop",
                    "fill_pct": fill / pos.buy_price - 1}

    # 3. hp 到期
    if pos.holding_days_elapsed >= pos.holding_days_target:
        return {"action": "sell", "reason": "hp_expired",
                "fill_pct": today_close / pos.buy_price - 1}

    return {"action": "hold", "reason": None, "fill_pct": today_close / pos.buy_price - 1}


def can_add_position(
    *,
    first_buy_price: float,
    first_buy_date_idx: int,
    today_idx: int,
    today_price: float,
    current_position_pct: float,
    profile_stock_cap: float,
    min_days_after_first: int = 3,
) -> dict:
    """加仓判定 (用户 Q4 规则: 距首次 ≥3 交易日 + 当前价 ≤ 首次价 + ≤ 原仓位 50%).

    返回: {can_add: bool, reason: str, add_position_pct: float}
    """
    if today_idx - first_buy_date_idx < min_days_after_first:
        return {"can_add": False, "reason": "too_soon", "add_position_pct": 0.0}
    if today_price > first_buy_price:
        return {"can_add": False, "reason": "no_pullback", "add_position_pct": 0.0}
    # 总仓位检查
    add_pct = current_position_pct * 0.5  # 加仓 = 原仓位 50%
    new_total = current_position_pct + add_pct
    if new_total > profile_stock_cap:
        # 截到 cap
        add_pct = max(0.0, profile_stock_cap - current_position_pct)
    if add_pct <= 0:
        return {"can_add": False, "reason": "cap_reached", "add_position_pct": 0.0}
    return {"can_add": True, "reason": "ok", "add_position_pct": round(add_pct, 4)}
