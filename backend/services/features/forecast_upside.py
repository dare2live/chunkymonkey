"""Phase 4 #7 — forecast_upside (一致预期 EPS × target_PE 上升空间 model).

按 Codex round 19+ verdict + 用户业绩预测+Optuna joint vision.

公式:
    upside = fy1_eps_consensus × target_pe / current_price - 1

target_pe 候选 (4 tier):
1. self_pe_median_Nd: 本股 N 日 PE 中位 (PIT-safe from fact_financial_pit_daily)
2. industry_pe_median_pit: 行业 PE 中位 (PIT-safe via mart_stock_industry_pit observed_snapshot)
3. blend_self_industry: 加权混合 (Optuna 搜 weight)
4. consensus_pe_avg: 券商共识 PE (注 SHADOW only — 没有 PIT 历史快照, 不入训练)

设计原则 (Codex CRITICAL):
- 纯函数, 不读 DB (输入 dataframes, 输出 dataframe)
- 历史 backtest 等 daily PIT snapshot 累积数月后才跑
- 当前阶段框架先行, 跑 shadow validation 收数据

API:
    from services.features.forecast_upside import (
        compute_target_pe_self_median,
        compute_target_pe_industry_median,
        compute_target_pe_blend,
        compute_upside,
        build_forecast_upside_features,
    )

    target_pe = compute_target_pe_blend(self_median, industry_median, blend_self_weight=0.6)
    upside = compute_upside(fy1_eps, target_pe, current_price)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_target_pe_self_median(
    pe_history: pd.Series,
    window_days: int = 480,
    min_periods: int = 60,
    winsor_pct: tuple[float, float] = (0.05, 0.95),
) -> pd.Series:
    """Rolling self median PE (PIT-safe trailing window).

    Args:
        pe_history: pd.Series of PE values indexed by date (sorted ascending).
        window_days: rolling window size (480 trade days ≈ 2 年).
        min_periods: minimum non-NA in window to compute median.
        winsor_pct: winsorize before median (5th/95th).

    Returns:
        pd.Series of rolling median PE.
    """
    pe = pe_history.astype("float64").where(pe_history > 0, np.nan)
    if winsor_pct:
        lo_q, hi_q = winsor_pct
        # PIT-safe rolling winsorize — clip each value by its own trailing-window quantiles only
        # (Codex round 19+ CRITICAL fix: 之前用 global quantile 是 forward leakage)
        lo_series = pe.rolling(window_days, min_periods=min_periods).quantile(lo_q)
        hi_series = pe.rolling(window_days, min_periods=min_periods).quantile(hi_q)
        pe = pe.clip(lower=lo_series, upper=hi_series)
    return pe.rolling(window_days, min_periods=min_periods).median()


def compute_target_pe_industry_median(
    panel: pd.DataFrame,
    pe_col: str = "pe_ttm",
    industry_col: str = "industry",
    date_col: str = "signal_date",
) -> pd.Series:
    """Per-date industry median PE (cross-section).

    Args:
        panel: DataFrame with (date_col, industry_col, pe_col).
        pe_col: PE column.
        industry_col: industry classification column.
        date_col: signal date column.

    Returns:
        pd.Series aligned to panel index — industry median PE per (date, industry).
    """
    pe = panel[pe_col].where(panel[pe_col] > 0, np.nan)
    df = pd.DataFrame({
        date_col: panel[date_col],
        industry_col: panel[industry_col],
        "_pe": pe,
    })
    return df.groupby([date_col, industry_col])["_pe"].transform("median")


def compute_target_pe_blend(
    self_median: pd.Series,
    industry_median: pd.Series,
    blend_self_weight: float = 0.6,
    floor: float = 5.0,
    cap: float = 80.0,
) -> pd.Series:
    """Blend self + industry median PE with bounds.

    Args:
        self_median: rolling self PE median.
        industry_median: cross-section industry PE median.
        blend_self_weight: weight on self_median (0-1).
        floor / cap: bound on output target PE (avoid extreme values).

    Returns:
        pd.Series of blended target PE.
    """
    w = float(np.clip(blend_self_weight, 0.0, 1.0))
    blend = w * self_median + (1 - w) * industry_median
    # If one is NaN, fall back to other
    blend = blend.fillna(self_median).fillna(industry_median)
    return blend.clip(lower=floor, upper=cap)


def compute_upside(
    fy_eps_consensus: pd.Series,
    target_pe: pd.Series,
    current_price: pd.Series,
    *,
    eps_floor: float = 0.0,
    upside_clip: tuple[float, float] = (-0.9, 5.0),
) -> pd.Series:
    """Compute upside = fy_eps × target_pe / current_price - 1.

    Args:
        fy_eps_consensus: forecasted EPS (consensus).
        target_pe: target valuation PE.
        current_price: current stock price (for forward-fair-value comparison).
        eps_floor: EPS must be > floor (default 0 — exclude loss-makers).
        upside_clip: clip output to avoid extreme (default [-90%, +500%]).

    Returns:
        pd.Series of upside ratio (e.g. 0.30 = 30% upside).
        NaN when inputs invalid (negative EPS / 0 price).
    """
    eps = fy_eps_consensus.where(fy_eps_consensus > eps_floor, np.nan)
    pe = target_pe.where(target_pe > 0, np.nan)
    price = current_price.where(current_price > 0, np.nan)
    fair_value = eps * pe
    upside = fair_value / price - 1
    lo, hi = upside_clip
    return upside.clip(lower=lo, upper=hi)


def build_forecast_upside_features(
    panel: pd.DataFrame,
    *,
    pe_col: str = "pe_ttm",
    industry_col: str = "industry",
    eps_col: str = "fy1_eps_consensus",
    price_col: str = "close",
    date_col: str = "signal_date",
    stock_col: str = "stock_code",
    self_window_days: int = 480,
    blend_self_weight: float = 0.6,
    target_pe_floor: float = 5.0,
    target_pe_cap: float = 80.0,
) -> pd.DataFrame:
    """End-to-end forecast upside feature build.

    输入 panel 应含: stock_code, signal_date, pe_ttm, industry, fy1_eps_consensus, close.

    返回 panel + 新增 cols:
        - target_pe_self: rolling self median PE
        - target_pe_industry: per-date industry median PE
        - target_pe_blend: blended target
        - upside_self: upside using target_pe_self
        - upside_industry: upside using target_pe_industry
        - upside_blend: upside using target_pe_blend (primary)
    """
    out = panel.copy()

    # Self median: per-stock rolling
    out["target_pe_self"] = (
        out.sort_values([stock_col, date_col])
        .groupby(stock_col)[pe_col]
        .transform(lambda s: compute_target_pe_self_median(
            s, window_days=self_window_days
        ))
    )

    # Industry median: per (date, industry) cross-section
    out["target_pe_industry"] = compute_target_pe_industry_median(
        out, pe_col=pe_col, industry_col=industry_col, date_col=date_col,
    )

    out["target_pe_blend"] = compute_target_pe_blend(
        out["target_pe_self"], out["target_pe_industry"],
        blend_self_weight=blend_self_weight,
        floor=target_pe_floor, cap=target_pe_cap,
    )

    for label, pe in [
        ("upside_self", out["target_pe_self"]),
        ("upside_industry", out["target_pe_industry"]),
        ("upside_blend", out["target_pe_blend"]),
    ]:
        out[label] = compute_upside(
            out[eps_col], pe, out[price_col],
        ).astype("float32")

    # Cast target PE to float32
    for c in ["target_pe_self", "target_pe_industry", "target_pe_blend"]:
        out[c] = out[c].astype("float32")

    return out


def feature_names() -> list[str]:
    return [
        "target_pe_self",
        "target_pe_industry",
        "target_pe_blend",
        "upside_self",
        "upside_industry",
        "upside_blend",
    ]
