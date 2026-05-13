"""Phase ε.1 — 持仓周期单位 (单一职责).

⚠ 解决 audit Bug #4: holding_days 在回测里是交易日, 在 paper engine 里是自然日,
   两者比对错位, 实盘持仓比预期短 30-40%.

本模块定义唯一的 HorizonUnit 枚举 + 转换函数, 所有下游必须用.
"""
from __future__ import annotations

from datetime import date as _date
from enum import Enum
from typing import Optional


class HorizonUnit(Enum):
    TRADING_DAYS = "trading_days"      # 默认: 跳过周末/法定假日
    CALENDAR_DAYS = "calendar_days"     # 自然日


def add_holding_period(
    base_date: str,
    hp: int,
    unit: HorizonUnit,
    trading_dates: Optional[list[str]] = None,
) -> Optional[str]:
    """从 base_date 出发, 经过 hp 个周期, 返回目标日期.

    Args:
        base_date: 起始日 (YYYY-MM-DD)
        hp:        周期数 (例如 hp=5)
        unit:      TRADING_DAYS / CALENDAR_DAYS
        trading_dates: TRADING_DAYS 模式必须提供 (已排序的交易日列表)

    Returns:
        目标日期字符串, 或 None (越界)
    """
    if unit == HorizonUnit.CALENDAR_DAYS:
        from datetime import timedelta
        return (_date.fromisoformat(base_date) + timedelta(days=hp)).isoformat()
    if unit == HorizonUnit.TRADING_DAYS:
        if trading_dates is None:
            raise ValueError("TRADING_DAYS mode requires trading_dates list")
        try:
            idx = trading_dates.index(base_date)
        except ValueError:
            return None
        target_idx = idx + hp
        if target_idx >= len(trading_dates):
            return None
        return trading_dates[target_idx]
    raise ValueError(f"unknown HorizonUnit: {unit}")


def count_holding_period(
    start_date: str,
    end_date: str,
    unit: HorizonUnit,
    trading_dates: Optional[list[str]] = None,
) -> int:
    """算 start_date 到 end_date 之间的 holding period 长度.

    Returns:
        交易日数 或 自然日数 (取决于 unit)
    """
    if unit == HorizonUnit.CALENDAR_DAYS:
        return (_date.fromisoformat(end_date) - _date.fromisoformat(start_date)).days
    if unit == HorizonUnit.TRADING_DAYS:
        if trading_dates is None:
            raise ValueError("TRADING_DAYS mode requires trading_dates list")
        try:
            i0 = trading_dates.index(start_date)
            i1 = trading_dates.index(end_date)
        except ValueError:
            return -1
        return i1 - i0
    raise ValueError(f"unknown HorizonUnit: {unit}")
