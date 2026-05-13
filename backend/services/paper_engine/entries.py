"""Phase δ — 入场规则 (纯函数)。

每日对每个 BUY 候选评估:
  - 一字涨停 (open=high≥9.7%) → reject (无法买入)
  - 停牌 (无 K 线) → skip (next day retry)
  - entry_target_price 落在今日 [low, high] 区间 → fill = entry_target
  - 跳空高开超 entry_max_price → reject
  - 否则 → fill = today_open

仓位大小: target_weight × 可用资金 / fill_price, 100 股整数 (A 股最小单位)
"""
from __future__ import annotations

import math
from typing import Any

from services.paper_engine.exits import LIMIT_THRESHOLD, is_limit_up_day


def evaluate_entry(
    *,
    entry_target_price: float | None,
    entry_aggressive_price: float | None = None,
    entry_max_price: float | None = None,
    today_open: float | None,
    today_high: float | None,
    today_low: float | None,
    today_close: float | None,
    prev_close: float | None = None,
) -> dict[str, Any]:
    """评估单股入场。

    Returns:
        {
          "action": "enter" | "reject" | "skip",
          "reason": str,
          "fill_price": float | None,
        }
    """
    if today_open is None:
        return {"action": "skip", "reason": "halted", "fill_price": None}

    # 一字涨停拒入
    if is_limit_up_day(today_open, prev_close) and today_high is not None and today_low is not None:
        if today_high == today_low:
            return {"action": "reject", "reason": "limit_up_one_word", "fill_price": None}

    # entry_target 缺失 fallback: 用 today_open
    if entry_target_price is None:
        if entry_max_price and today_open > entry_max_price:
            return {"action": "reject", "reason": "open_above_max", "fill_price": None}
        return {"action": "enter", "reason": "open_fallback", "fill_price": today_open}

    # entry_target 在 [low, high] 内 → 用 entry_target 作 fill (假设盘中触发限价单)
    if today_high is not None and today_low is not None:
        if today_low <= entry_target_price <= today_high:
            return {"action": "enter", "reason": "limit_filled", "fill_price": entry_target_price}

    # 跳空高开
    if entry_max_price is not None and today_open > entry_max_price:
        return {"action": "reject", "reason": "gap_above_max", "fill_price": None}

    # 否则用 today_open
    return {"action": "enter", "reason": "market_open", "fill_price": today_open}


def compute_shares(
    *,
    cash_available: float,
    target_weight: float,
    fill_price: float,
    lot_size: int = 100,
) -> int:
    """A 股 100 股整数仓位。

    Args:
        cash_available: 当前可用现金
        target_weight: 目标占比 (0-1)
        fill_price: 入场价
        lot_size: 一手股数, A 股 = 100

    Returns:
        股数 (lot_size 整数倍, 至少 0)
    """
    if cash_available <= 0 or fill_price <= 0 or target_weight <= 0:
        return 0
    target_notional = cash_available * target_weight
    raw_shares = target_notional / fill_price
    lots = math.floor(raw_shares / lot_size)
    return max(0, lots * lot_size)
