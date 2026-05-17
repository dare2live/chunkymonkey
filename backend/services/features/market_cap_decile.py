"""Phase 4 #2 feature engineering — market-cap decile (size factor / SMB).

A 股 size factor (业界 reported):
- 小盘 (低市值 decile 1-3): 高波动 + 高 alpha + 高换手 (散户主导)
- 大盘 (高市值 decile 8-10): 低波动 + 稳定 + 机构主导
- 中盘 (decile 4-7): 中间

设计:
- log_market_cap: log10(market_cap_total) 减 skew
- mc_decile: 1-10 按 cross-section 排名 (per signal_date)
- mc_quintile: 1-5 (粗粒度)
- mc_rank_normalized: 0-1 cross-section rank

API:
    from services.features.market_cap_decile import build_market_cap_features

    df = build_market_cap_features(signal_dates_df, market_cap_col="market_cap_total")
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_market_cap_features(
    df: pd.DataFrame,
    cap_col: str = "market_cap_total",
    date_col: str = "signal_date",
) -> pd.DataFrame:
    """从 market_cap 列算 size factor features.

    Args:
        df: DataFrame with cap_col + date_col.
        cap_col: 市值列名 (default "market_cap_total", unit 万元 or 元).
        date_col: 日期列名 (用于 per-date ranking).

    Returns:
        df with new columns:
            - mc_log_cap (float32): log10(cap)
            - mc_decile (int8): 1-10 per-date decile
            - mc_quintile (int8): 1-5
            - mc_rank_normalized (float32): 0-1 cross-section rank
            - mc_is_small (int8): bool decile <= 3
            - mc_is_large (int8): bool decile >= 8
    """
    out = df.copy()

    # Handle missing / 0 / negative cap
    cap = out[cap_col].fillna(0).astype("float64")
    safe_cap = cap.where(cap > 0, np.nan)  # NaN for invalid

    out["mc_log_cap"] = np.log10(safe_cap.fillna(1)).astype("float32")  # log10 0 → -inf, use 1 as floor
    out.loc[safe_cap.isna(), "mc_log_cap"] = np.float32(np.nan)

    # Per-date ranking
    out["mc_decile"] = (
        out.groupby(date_col)[cap_col]
        .transform(lambda x: pd.qcut(x.rank(method="first"), 10, labels=False, duplicates="drop") + 1)
        .fillna(5)  # NaN → middle (decile 5)
        .astype("int8")
    )
    out["mc_quintile"] = (
        out.groupby(date_col)[cap_col]
        .transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop") + 1)
        .fillna(3)
        .astype("int8")
    )
    out["mc_rank_normalized"] = (
        out.groupby(date_col)[cap_col]
        .rank(method="average", pct=True)
        .fillna(0.5)
        .astype("float32")
    )
    out["mc_is_small"] = (out["mc_decile"] <= 3).astype("int8")
    out["mc_is_large"] = (out["mc_decile"] >= 8).astype("int8")

    return out


def feature_names() -> list[str]:
    return [
        "mc_log_cap",
        "mc_decile",
        "mc_quintile",
        "mc_rank_normalized",
        "mc_is_small",
        "mc_is_large",
    ]
