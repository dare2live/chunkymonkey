"""Phase ε.1 — 涨跌停 / 停牌 过滤器 (单一职责).

⚠ 解决 audit Bug #5: 回测不过滤一字板, 假信号变 "win".

A 股交易规则:
  - 主板涨跌停 ±10%
  - 创业板 / 科创板 / 北交所 ±20%
  - ST 股 ±5%
  - 一字板: 全日 open==high==low==close 且达到涨/跌停限度
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

BoardType = Literal["main", "chinext", "star", "bj", "st", "auto"]


@dataclass(frozen=True)
class LimitBoardConfig:
    """涨跌停过滤配置."""
    main_limit_pct:    float = 0.10   # 主板 ±10%
    chinext_limit_pct: float = 0.20   # 创业板 ±20%
    star_limit_pct:    float = 0.20   # 科创板 ±20%
    bj_limit_pct:      float = 0.30   # 北交所 ±30%
    st_limit_pct:      float = 0.05   # ST 股 ±5%

    # 实际识别阈值 (略低于标称, 避免浮点误差)
    detect_buffer_pct: float = 0.997   # 实际涨幅 ≥ 限度 × 0.997 即视为涨停

    # 行为开关
    reject_buy_on_limit_up_one_word:    bool = True   # 一字涨停拒绝买入
    allow_sell_through_limit_down:      bool = True   # 跌停可以挂卖 (实际可能成不了)


def infer_board(stock_code: str) -> BoardType:
    """根据股票代码推断板块."""
    if not stock_code:
        return "main"
    head = stock_code[:3]
    if head in ("300", "301"):
        return "chinext"
    if head in ("688", "689"):
        return "star"
    if stock_code[0] in ("4", "8", "9"):
        return "bj"
    return "main"


def get_limit_pct(board: BoardType, config: LimitBoardConfig) -> float:
    """该板块的涨跌停限度 (绝对值)."""
    if board == "main":   return config.main_limit_pct
    if board == "chinext": return config.chinext_limit_pct
    if board == "star":   return config.star_limit_pct
    if board == "bj":     return config.bj_limit_pct
    if board == "st":     return config.st_limit_pct
    raise ValueError(f"unknown board: {board!r}")


def is_limit_up(
    today_close: float, prev_close: float, board: BoardType, config: LimitBoardConfig,
) -> bool:
    """T 日是否涨停 (close 达到涨停限度)."""
    if not prev_close or prev_close <= 0:
        return False
    limit = get_limit_pct(board, config)
    return (today_close - prev_close) / prev_close >= limit * config.detect_buffer_pct


def is_limit_down(
    today_close: float, prev_close: float, board: BoardType, config: LimitBoardConfig,
) -> bool:
    if not prev_close or prev_close <= 0:
        return False
    limit = get_limit_pct(board, config)
    return (today_close - prev_close) / prev_close <= -limit * config.detect_buffer_pct


def is_one_word_limit_up(
    today_open: float, today_high: float, today_low: float, today_close: float,
    prev_close: float, board: BoardType, config: LimitBoardConfig,
) -> bool:
    """T 日一字涨停 (全日 OHLC 都达到涨停)."""
    if not (today_open and today_high and today_low and today_close and prev_close):
        return False
    if not (today_open == today_high == today_low == today_close):
        return False
    return is_limit_up(today_close, prev_close, board, config)


def is_one_word_limit_down(
    today_open: float, today_high: float, today_low: float, today_close: float,
    prev_close: float, board: BoardType, config: LimitBoardConfig,
) -> bool:
    if not (today_open and today_high and today_low and today_close and prev_close):
        return False
    if not (today_open == today_high == today_low == today_close):
        return False
    return is_limit_down(today_close, prev_close, board, config)


def is_suspended(today_volume: Optional[float]) -> bool:
    """停牌识别: 成交量为 0."""
    return today_volume is None or today_volume <= 0
