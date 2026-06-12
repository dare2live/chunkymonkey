"""Phase η++++ — 滚动窗口聚合 (纯函数, 单一职责).

输入: 单股的事件时间序列 [(date, value), ...]
输出: 每个 as_of_date 上的滚动窗口聚合结果

只关心窗口数学, 不读 DB, 不写 DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, timedelta
from typing import Iterable


@dataclass(frozen=True)
class SurveyWindowResult:
    """单股单日的窗口聚合结果."""
    stock_code: str
    as_of_date: str
    count_30d: int   # 30 日内调研次数
    count_60d: int   # 60 日内调研次数
    inst_30d: int    # 30 日内累计机构数
    inst_60d: int    # 60 日内累计机构数


def compute_survey_windows(
    stock_code: str,
    events: list[tuple[str, int]],   # [(survey_date, inst_count), ...] — 必须按日期升序
    short_days: int = 30,
    long_days: int = 60,
) -> list[SurveyWindowResult]:
    """对单股的所有调研事件, 在每个 event_date 上算 short/long 窗口聚合.

    Args:
        stock_code: 股票代码
        events: [(survey_date, inst_count), ...] (按日期升序)
        short_days: 短窗口天数 (默认 30)
        long_days: 长窗口天数 (默认 60)

    Returns:
        每个 event_date 对应一个 SurveyWindowResult.

    Notes:
        - 窗口 [d - N+1, d] (闭区间)
        - 当日多场调研: events 多条同 date, 都会计入
    """
    if not events:
        return []
    out: list[SurveyWindowResult] = []
    for i, (sd, _) in enumerate(events):
        d_anchor = _date.fromisoformat(sd)
        d_short_lo = (d_anchor - timedelta(days=short_days - 1)).isoformat()
        d_long_lo  = (d_anchor - timedelta(days=long_days  - 1)).isoformat()
        c30, c60, i30, i60 = 0, 0, 0, 0
        # 因为 events 已按 date 升序, 只扫到 i 即可 (后面的 date > sd, 不在窗口)
        for j in range(i, -1, -1):
            d_j, ic_j = events[j]
            if d_j > sd:
                continue  # 防御
            if d_j < d_long_lo:
                break  # 升序时, 后续都更早, 提前退出
            c60 += 1
            i60 += ic_j
            if d_j >= d_short_lo:
                c30 += 1
                i30 += ic_j
        out.append(SurveyWindowResult(
            stock_code=stock_code,
            as_of_date=sd,
            count_30d=c30,
            count_60d=c60,
            inst_30d=i30,
            inst_60d=i60,
        ))
    return out




def daily_grid_from_events(
    stock_code: str,
    events: list[tuple[str, int]],
    grid_start: str,
    grid_end: str,
    short_days: int = 30,
    long_days: int = 60,
    trading_dates: list[str] | None = None,
) -> list[SurveyWindowResult]:
    """从 raw events 直接生成 grid_start..grid_end 上的每日快照 (含老化).

    Args:
        stock_code: 股票代码
        events: 该股全部历史调研 [(date, inst_count), ...] (按 date 升序)
        grid_start / grid_end: 输出网格起止
        short_days / long_days: 窗口长度
        trading_dates: 若提供, 仅在交易日产出 (否则全自然日)

    Returns:
        每天一条 (跳过 events 为空时窗口全 0 的日子可选).
    """
    if not events:
        return []
    # 用 sorted list + pointer 跑滑动窗口
    from datetime import date as _date, timedelta
    grid_start_d = _date.fromisoformat(grid_start)
    grid_end_d = _date.fromisoformat(grid_end)
    out: list[SurveyWindowResult] = []
    cur = grid_start_d
    trading_set = set(trading_dates) if trading_dates is not None else None
    while cur <= grid_end_d:
        cur_iso = cur.isoformat()
        if trading_set is None or cur_iso in trading_set:
            d_short_lo = (cur - timedelta(days=short_days - 1)).isoformat()
            d_long_lo  = (cur - timedelta(days=long_days  - 1)).isoformat()
            c30, c60, i30, i60 = 0, 0, 0, 0
            for d, ic in events:
                if d > cur_iso:
                    break
                if d < d_long_lo:
                    continue
                c60 += 1
                i60 += ic
                if d >= d_short_lo:
                    c30 += 1
                    i30 += ic
            # 跳过全 0 行 (节省存储)
            if c60 > 0:
                out.append(SurveyWindowResult(
                    stock_code=stock_code,
                    as_of_date=cur_iso,
                    count_30d=c30,
                    count_60d=c60,
                    inst_30d=i30,
                    inst_60d=i60,
                ))
        cur += timedelta(days=1)
    return out
