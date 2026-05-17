"""Phase 4 #2 feature engineering — time-of-month features (Codex round 19 alpha #1 优先级).

A 股月初/月末效应 (业界 reported):
- 月初首 5 日: 新增基金流入 / 月度仓位调整
- 月末后 5 日: 公募季度排名压力 / 机构调仓
- 月中 (10-20 日): 财报披露 / 调研事件 集中

设计:
- day_of_month: 1-31, 直接日期字段
- days_to_month_end: 当日距月末交易日的天数
- days_from_month_start: 当日距月初交易日的天数
- month_phase: 0=early(1-7), 1=mid(8-22), 2=late(23-31) categorical
- is_first_week: bool (day_of_month <= 5)
- is_last_week: bool (day_of_month >= 23 OR days_to_month_end <= 5)

API:
    from services.features.time_of_month import build_time_of_month_features

    df_features = build_time_of_month_features(signal_dates_df)
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def build_time_of_month_features(df: pd.DataFrame, date_col: str = "signal_date") -> pd.DataFrame:
    """从 signal_date 算 time-of-month features.

    Args:
        df: DataFrame with date_col (datetime or string parseable).
        date_col: 列名 (default "signal_date").

    Returns:
        df with new columns added (in-place to copy):
            - tom_day_of_month (int8)
            - tom_days_to_month_end (int8)
            - tom_days_from_month_start (int8)
            - tom_month_phase (int8: 0/1/2)
            - tom_is_first_week (int8: 0/1)
            - tom_is_last_week (int8: 0/1)
            - tom_is_month_turn (int8: 月初首 3 OR 月末末 3)
    """
    out = df.copy()
    dt = pd.to_datetime(out[date_col])

    # day_of_month (1-31)
    out["tom_day_of_month"] = dt.dt.day.astype("int8")

    # Month end / start (calendar month, 非 trading month — 简化版)
    month_end = dt.dt.to_period("M").dt.end_time
    month_start = dt.dt.to_period("M").dt.start_time
    out["tom_days_to_month_end"] = (month_end - dt).dt.days.clip(0, 31).astype("int8")
    out["tom_days_from_month_start"] = (dt - month_start).dt.days.clip(0, 31).astype("int8")

    # month_phase: 0=early(1-7), 1=mid(8-22), 2=late(23+)
    dom = out["tom_day_of_month"]
    phase = pd.Series(1, index=out.index, dtype="int8")  # default mid
    phase[dom <= 7] = 0
    phase[dom >= 23] = 2
    out["tom_month_phase"] = phase

    # Booleans (int8 for ML compat)
    out["tom_is_first_week"] = (dom <= 5).astype("int8")
    out["tom_is_last_week"] = ((dom >= 23) | (out["tom_days_to_month_end"] <= 5)).astype("int8")
    out["tom_is_month_turn"] = (
        (dom <= 3) | (out["tom_days_to_month_end"] <= 3)
    ).astype("int8")

    return out


def feature_names() -> list[str]:
    """List of feature column names this module adds."""
    return [
        "tom_day_of_month",
        "tom_days_to_month_end",
        "tom_days_from_month_start",
        "tom_month_phase",
        "tom_is_first_week",
        "tom_is_last_week",
        "tom_is_month_turn",
    ]
