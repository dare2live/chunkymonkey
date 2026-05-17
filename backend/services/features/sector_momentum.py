"""Phase 4 #5 feature engineering — sector_momentum.

按 Codex round 19 #5 priority — 板块动量信号 (28 行业 / TDX L1 13 行业).

源:
- fact_sector_momentum_daily (sector × date): ret_5d/20d/60d/120d, excess_20d/60d,
  price_vs_ma20/60, vol_60d
- mart_stock_industry_pit (stock × effective_from/to): PIT industry membership

PIT 设计 (Step 3 audit):
- mart_stock_industry_pit 含 confidence_level: 'observed_snapshot' (85.7%) /
  'current_label_fallback' (14.3% — Pattern D contamination)
- 严格 PIT: 只用 observed_snapshot 行, fallback 全 NULL (不污染训练)
- effective_from <= signal_date < effective_to (strict 半开区间)
- fact_sector_momentum_daily.date <= signal_date (strict PIT, sector momentum 当日可计算)

业界 reported alpha:
- ret_60d sector 高 → 板块动量延续
- excess_60d > 0 → 板块跑赢市场, 涨势中
- price_vs_ma20 上方 → 短期强势
- vol_60d 低 → 稳定板块

API:
    from services.features.sector_momentum import build_sector_momentum_features

    df = build_sector_momentum_features(signal_df, db_path)
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


_RAW_COLS = [
    "ret_5d",
    "ret_20d",
    "ret_60d",
    "ret_120d",
    "excess_20d",
    "excess_60d",
    "price_vs_ma20",
    "price_vs_ma60",
    "vol_60d",
]


def build_sector_momentum_features(
    signal_df: pd.DataFrame,
    db_path: str | Path,
    stock_col: str = "stock_code",
    date_col: str = "signal_date",
    *,
    include_fallback: bool = False,
) -> pd.DataFrame:
    """JOIN signal panel × mart_stock_industry_pit × fact_sector_momentum_daily.

    Args:
        signal_df: DataFrame with stock_col + date_col.
        db_path: smartmoney.duckdb path.
        stock_col: stock code column.
        date_col: signal date column.
        include_fallback: if False (default, PIT-strict), 排除 current_label_fallback
            rows. fallback stocks 全 NULL sector_momentum cols.

    Returns:
        signal_df + sector_name + 9 raw sector momentum cols + 2 derived:
            - sec_mom_score: 综合动量 (excess_60d + 0.3*excess_20d)
            - sec_mom_rank_60d: cross-section rank of ret_60d per signal_date (0-1)
    """
    out = signal_df.copy()
    if stock_col not in out.columns or date_col not in out.columns:
        raise ValueError(f"signal_df 必须含 {stock_col} 跟 {date_col}")
    out["_join_date_str"] = pd.to_datetime(out[date_col]).dt.strftime("%Y-%m-%d")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        # 1. PIT industry lookup: per (stock_code, signal_date)
        signals_unique = out[[stock_col, "_join_date_str"]].drop_duplicates()
        con.register("__signals", signals_unique)
        confidence_filter = (
            "AND sip.confidence_level = 'observed_snapshot'"
            if not include_fallback else ""
        )
        # Use ASOF-style join: pick row where effective_from <= signal_date < effective_to
        sector_lookup = con.execute(f"""
            SELECT s.{stock_col} AS stock_code, s._join_date_str AS signal_date,
                   sip.tdx_l1_name AS sector_name
              FROM __signals s
              LEFT JOIN mart_stock_industry_pit sip
                ON sip.stock_code = s.{stock_col}
               AND sip.effective_from <= s._join_date_str
               AND (sip.effective_to > s._join_date_str OR sip.effective_to IS NULL)
               {confidence_filter}
        """).df()
        # If multiple PIT rows (shouldn't happen but defensive), pick latest effective_from
        sector_lookup = (
            sector_lookup.sort_values(["stock_code", "signal_date"])
            .drop_duplicates(["stock_code", "signal_date"], keep="last")
        )

        # 2. JOIN fact_sector_momentum_daily on (signal_date, sector_name)
        select_cols = ", ".join([f"sm.{c}" for c in _RAW_COLS])
        sectors_unique = sector_lookup[
            sector_lookup["sector_name"].notna()
        ][["signal_date", "sector_name"]].drop_duplicates()
        if len(sectors_unique) == 0:
            mom_df = pd.DataFrame(
                columns=["signal_date", "sector_name", *_RAW_COLS]
            )
        else:
            con.register("__sectors", sectors_unique)
            mom_df = con.execute(f"""
                SELECT s.signal_date, s.sector_name, {select_cols}
                  FROM __sectors s
                  LEFT JOIN fact_sector_momentum_daily sm
                    ON sm.sector_name = s.sector_name
                   AND sm.date = s.signal_date
            """).df()
    finally:
        con.close()

    # 3. Merge sector_lookup + mom_df back to signal_df
    merged = out.merge(
        sector_lookup,
        left_on=[stock_col, "_join_date_str"],
        right_on=["stock_code", "signal_date"],
        how="left",
        suffixes=("", "_sl"),
    )
    # Drop sector_lookup join helpers
    for c in ["stock_code_sl", "signal_date_sl"]:
        if c in merged.columns:
            merged = merged.drop(columns=[c])

    merged = merged.merge(
        mom_df,
        left_on=["_join_date_str", "sector_name"],
        right_on=["signal_date", "sector_name"],
        how="left",
        suffixes=("", "_mom"),
    )
    # Cleanup
    merged = merged.drop(columns=["_join_date_str"])
    if "signal_date_mom" in merged.columns:
        merged = merged.drop(columns=["signal_date_mom"])

    # Cast raw cols to float32 (NaN allowed — fallback stocks stay NULL by design)
    for c in _RAW_COLS:
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="coerce").astype("float32")

    # Derived: composite score (excess_60d weighted)
    merged["sec_mom_score"] = (
        merged["excess_60d"].fillna(0) + 0.3 * merged["excess_20d"].fillna(0)
    ).astype("float32")

    # Derived: cross-section rank of ret_60d per signal_date (0=worst, 1=best sector)
    merged["sec_mom_rank_60d"] = (
        merged.groupby(date_col)["ret_60d"]
        .rank(method="average", pct=True)
        .fillna(0.5)
        .astype("float32")
    )

    return merged


def feature_names() -> list[str]:
    return _RAW_COLS + ["sec_mom_score", "sec_mom_rank_60d"]
