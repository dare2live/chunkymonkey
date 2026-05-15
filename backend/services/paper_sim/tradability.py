"""Paper Sim — A 股 tradability mask (Codex aaedbc9d C-C, 2026-05-15).

T+1 (implicit by day-cycle) + 涨跌停 + 停牌 + segment-aware price limits.

A 股 price limit rules (dim_price_limit_rules 2020+):
  - main (60xx/00xx 主板)         normal ±10% / ST ±5%
  - chinext (300xx 创业板)         normal ±20% / ST 同 ±20%
  - star (688xx 科创板)            normal ±20% / 新股 5 日 ±100%
  - bj (8xx/4xx 北交所)            normal ±30% / 新股 5 日 ±100%
  - 新股 5 日不设限 (用 days_after_ipo < 5 判)

简化 (MVP): paper_sim 不查 is_st / days_after_ipo, 用 segment 静态 limit. 历史回测 90% case OK.
完整版 (Phase 4+): 接 dim_price_limit_rules + dim_listing_status.is_st + first_seen_date.

API:
    `get_segment_limit_pct(stock_code) -> (up_pct, down_pct)` — segment 推断
    `is_suspended(kline_row) -> bool` — volume/amount <= 0 视为停牌
    `is_limit_up_today(kline_row, pre_close, up_pct) -> bool` — close >= pre_close × (1 + up_pct - 1bp)
    `is_limit_down_today(kline_row, pre_close, down_pct) -> bool` — close <= pre_close × (1 + down_pct + 1bp)
    `can_buy(kline_row, pre_close, stock_code) -> bool` — 综合 (停牌 + 涨停 mask)
    `can_sell(kline_row, pre_close, stock_code) -> bool` — 综合 (停牌 + 跌停 mask)

注: paper_sim entry/exit price 用当日 VWAP. 涨停 mask 触发 = signal 当日无法 buy in (即使 VWAP 算出来),
    实盘成交概率极低, 保守 skip 信号. 跌停 mask 触发 = exit 路径无法 sell, 保守 hold 一天等待.
"""
from __future__ import annotations

# Bp = 1 basis point. 因 close 浮点 vs pre_close × limit_pct 可能差几 bp, 留容差.
_BP_TOLERANCE = 0.0001  # 1 bp = 0.01%


def get_segment_limit_pct(stock_code: str) -> tuple[float, float]:
    """根据股票代码推断 segment + 返回 (limit_up_pct, limit_down_pct).

    简化: 不查 is_st / 新股, 用 segment 静态 ±%. 历史回测 90% case 准确.

    Returns:
        (up_pct, down_pct) — e.g. (0.10, -0.10) for 主板, (0.20, -0.20) for 创业板/科创板.
    """
    if not stock_code:
        return (0.10, -0.10)
    if stock_code.startswith(("60", "00")):
        return (0.10, -0.10)              # 主板 ±10%
    if stock_code.startswith("30"):
        return (0.20, -0.20)              # 创业板 ±20%
    if stock_code.startswith("688") or stock_code.startswith("689"):
        return (0.20, -0.20)              # 科创板 ±20%
    if stock_code.startswith(("4", "8")):
        return (0.30, -0.30)              # 北交所 ±30%
    return (0.10, -0.10)                  # fallback 保守


def is_suspended(k: dict | None) -> bool:
    """K线 row 是否 hit 停牌. volume/amount <= 0 或 None 视为停牌."""
    if not k:
        return True
    vol = k.get("volume") or 0
    amt = k.get("amount") or 0
    close = k.get("close") or 0
    return vol <= 0 or amt <= 0 or close <= 0


def is_limit_up_today(k: dict | None, pre_close: float | None, up_pct: float) -> bool:
    """当日 close >= pre_close × (1 + up_pct - 1bp 容差) → 涨停板.

    买入限制: 涨停板当日 buy order 几乎无法成交, paper_sim skip 信号.
    """
    if not k or not pre_close or pre_close <= 0:
        return False
    close = k.get("close") or 0
    if close <= 0:
        return False
    threshold = pre_close * (1 + up_pct - _BP_TOLERANCE)
    return close >= threshold


def is_limit_down_today(k: dict | None, pre_close: float | None, down_pct: float) -> bool:
    """当日 close <= pre_close × (1 + down_pct + 1bp 容差) → 跌停板.

    卖出限制: 跌停板当日 sell order 排在长队, paper_sim 保守不卖 (hold 一天).
    """
    if not k or not pre_close or pre_close <= 0:
        return False
    close = k.get("close") or 0
    if close <= 0:
        return False
    threshold = pre_close * (1 + down_pct + _BP_TOLERANCE)
    return close <= threshold


def can_buy(k: dict | None, pre_close: float | None, stock_code: str) -> bool:
    """买入综合 mask: 不停牌 AND 不涨停板."""
    if is_suspended(k):
        return False
    up_pct, _ = get_segment_limit_pct(stock_code)
    if is_limit_up_today(k, pre_close, up_pct):
        return False
    return True


def can_sell(k: dict | None, pre_close: float | None, stock_code: str) -> bool:
    """卖出综合 mask: 不停牌 AND 不跌停板."""
    if is_suspended(k):
        return False
    _, down_pct = get_segment_limit_pct(stock_code)
    if is_limit_down_today(k, pre_close, down_pct):
        return False
    return True
