"""Phase 4 #2 feature engineering — industry beta + residual return.

按 Codex round 19 #1 priority — stock vs industry rolling beta + residual return.

设计:
- Rolling 60d regression: stock_ret_60d = α + β × industry_ret_60d
- industry_beta_60d: 该 stock 跟 industry 的 beta (敏感度)
- industry_residual_60d: stock_60d_ret - β × industry_60d_ret (alpha)
- industry_excess_60d: stock_60d_ret - industry_60d_ret (simpler version, no regression)

业界 reported alpha:
- 高 industry_beta + 低 residual: 跟随板块, 无 alpha
- 低 industry_beta + 高 residual: 独立 alpha, 真有 stock-picking edge
- residual_60d > 0 stocks tend to keep outperforming (momentum)

API:
    from services.features.industry_beta import build_industry_beta_features

    df = build_industry_beta_features(stock_panel_df, industry_panel_df, lookback_days=60)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_industry_beta_features(
    stock_returns: pd.DataFrame,
    industry_returns: pd.DataFrame,
    lookback_days: int = 60,
) -> pd.DataFrame:
    """从 stock 跟 industry returns 算 rolling beta + residual.

    Args:
        stock_returns: pd.DataFrame with columns: stock_code, signal_date, ret_1d, industry.
        industry_returns: pd.DataFrame with columns: industry, signal_date, ret_1d.
        lookback_days: rolling window size (default 60).

    Returns:
        DataFrame with new columns:
            - ind_beta_<N>d: rolling beta
            - ind_residual_<N>d: stock_ret - beta × industry_ret (cumulative N-day)
            - ind_excess_<N>d: simpler version (stock_ret_Nd - industry_ret_Nd)
            - ind_alpha_ratio_<N>d: |residual| / |stock_ret| (informativeness)
    """
    Nd = lookback_days
    # Merge stock + industry returns
    merged = stock_returns.merge(
        industry_returns.rename(columns={"ret_1d": "ind_ret_1d"}),
        on=["industry", "signal_date"], how="left",
    )
    merged = merged.sort_values(["stock_code", "signal_date"]).reset_index(drop=True)

    # Rolling beta + residual per stock_code
    def _per_stock(group):
        s = group["ret_1d"].astype("float64")
        i = group["ind_ret_1d"].astype("float64")

        # Rolling covariance and variance
        cov = s.rolling(Nd, min_periods=Nd // 2).cov(i)
        var = i.rolling(Nd, min_periods=Nd // 2).var()
        beta = (cov / var.replace(0, np.nan)).fillna(0)

        # Cumulative N-day returns
        s_cum = (1 + s).rolling(Nd).apply(np.prod, raw=True) - 1
        i_cum = (1 + i).rolling(Nd).apply(np.prod, raw=True) - 1

        residual = s_cum - beta * i_cum
        excess = s_cum - i_cum

        # informativeness
        alpha_ratio = (residual.abs() / s_cum.abs().replace(0, np.nan)).clip(0, 1).fillna(0)

        group[f"ind_beta_{Nd}d"] = beta.astype("float32")
        group[f"ind_residual_{Nd}d"] = residual.astype("float32")
        group[f"ind_excess_{Nd}d"] = excess.astype("float32")
        group[f"ind_alpha_ratio_{Nd}d"] = alpha_ratio.astype("float32")
        return group

    result = merged.groupby("stock_code", group_keys=False).apply(_per_stock)
    return result


def feature_names(lookback_days: int = 60) -> list[str]:
    return [
        f"ind_beta_{lookback_days}d",
        f"ind_residual_{lookback_days}d",
        f"ind_excess_{lookback_days}d",
        f"ind_alpha_ratio_{lookback_days}d",
    ]
