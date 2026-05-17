"""Phase 4 #3 feature engineering — capital_flow (LHB + 高管 + 股东户数).

按 Codex round 19 #3 priority — 资金面信号.

源: fact_capital_flow_pit_daily (PIT 验证 backfill_capital_flow_pit.py)
- lhb_*: 龙虎榜 trailing 30/90d (PIT: l.trade_date <= signal_date)
- exec_*: 高管增减持 trailing 60d (PIT: e.notice_date <= signal_date)
- holder_*: 股东户数 季度 (PIT: h.available_date <= signal_date)

PIT audit verdict (2026-05-17):
- built_at 全 2026-05-14 = backfill 写盘日, 不是逻辑 as_of_date
- 每行的 features 算法用 strict <= signal_date trailing — PIT-safe
- 下游 JOIN 直接 signal.date = capital.trade_date, 不用 built_at filter

业界 reported alpha:
- LHB inst buy: 高 → 机构资金流入 (信号)
- exec buy_60d > sell_60d: 内部人看多 (信号)
- holder_count_change_q < 0: 户数减少 = 筹码集中 (信号)

API:
    from services.features.capital_flow import build_capital_flow_features

    df_with_features = build_capital_flow_features(signal_df, db_path)
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


_RAW_COLS = [
    "lhb_count_30d",
    "lhb_net_buy_pct_30d",
    "lhb_inst_buy_30d",
    "lhb_count_90d",
    "lhb_inst_buy_90d",
    "exec_buy_60d",
    "exec_sell_60d",
    "exec_buy_pct_60d",
    "exec_sell_pct_60d",
    "exec_net_signal",
    "holder_count_change_q_pct",
]


def build_capital_flow_features(
    signal_df: pd.DataFrame,
    db_path: str | Path,
    stock_col: str = "stock_code",
    date_col: str = "signal_date",
) -> pd.DataFrame:
    """JOIN signal panel 跟 fact_capital_flow_pit_daily + 派生 4 个 ratio.

    Args:
        signal_df: DataFrame with (stock_col, date_col) at minimum.
        db_path: path to smartmoney.duckdb.
        stock_col: stock code column name.
        date_col: signal date column name.

    Returns:
        signal_df 加 11 raw + 4 derived = 15 capital_flow features.
            派生 features:
            - cf_lhb_inst_ratio_30d: lhb_inst_buy_30d / NULLIF(lhb_count_30d,0)
            - cf_lhb_inst_ratio_90d: lhb_inst_buy_90d / NULLIF(lhb_count_90d,0)
            - cf_exec_buy_sell_ratio: exec_buy_60d / NULLIF(exec_buy+sell,0)
            - cf_holder_concentration: -holder_count_change_q_pct (符号反转, 户数减少为正)
    """
    out = signal_df.copy()
    if stock_col not in out.columns or date_col not in out.columns:
        raise ValueError(f"signal_df 必须含 {stock_col} 跟 {date_col}")

    # Load capital_flow PIT (read-only)
    con = duckdb.connect(str(db_path), read_only=True)
    cap_df = con.execute(
        "SELECT stock_code, trade_date, "
        + ", ".join(_RAW_COLS)
        + " FROM fact_capital_flow_pit_daily"
    ).df()
    con.close()

    # Normalize date column for JOIN (trade_date is VARCHAR YYYY-MM-DD)
    out["_join_date"] = pd.to_datetime(out[date_col]).dt.strftime("%Y-%m-%d")
    cap_df["trade_date"] = cap_df["trade_date"].astype(str)

    merged = out.merge(
        cap_df,
        left_on=[stock_col, "_join_date"],
        right_on=["stock_code", "trade_date"],
        how="left",
        suffixes=("", "_capdrop"),
    )
    # Drop helper cols
    merged = merged.drop(columns=["_join_date"])
    if "trade_date" in merged.columns:
        merged = merged.drop(columns=["trade_date"])
    if "stock_code_capdrop" in merged.columns:
        merged = merged.drop(columns=["stock_code_capdrop"])

    # Fill counts NaN → 0, ratios NaN → 0 (保守)
    for c in ["lhb_count_30d", "lhb_inst_buy_30d", "lhb_count_90d", "lhb_inst_buy_90d",
              "exec_buy_60d", "exec_sell_60d"]:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0).astype("int32")
    for c in ["lhb_net_buy_pct_30d", "exec_buy_pct_60d", "exec_sell_pct_60d",
              "exec_net_signal", "holder_count_change_q_pct"]:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0).astype("float32")

    # Derived features
    merged["cf_lhb_inst_ratio_30d"] = (
        merged["lhb_inst_buy_30d"] / merged["lhb_count_30d"].replace(0, np.nan)
    ).fillna(0).astype("float32")
    merged["cf_lhb_inst_ratio_90d"] = (
        merged["lhb_inst_buy_90d"] / merged["lhb_count_90d"].replace(0, np.nan)
    ).fillna(0).astype("float32")
    total_exec = merged["exec_buy_60d"] + merged["exec_sell_60d"]
    merged["cf_exec_buy_sell_ratio"] = (
        merged["exec_buy_60d"] / total_exec.replace(0, np.nan)
    ).fillna(0.5).astype("float32")  # 0.5 = neutral when 0 events
    merged["cf_holder_concentration"] = (-merged["holder_count_change_q_pct"]).astype("float32")

    return merged


def feature_names() -> list[str]:
    return _RAW_COLS + [
        "cf_lhb_inst_ratio_30d",
        "cf_lhb_inst_ratio_90d",
        "cf_exec_buy_sell_ratio",
        "cf_holder_concentration",
    ]
