"""Phase 4 #6 feature engineering — institution_survey (机构调研活动).

按 Codex round 19 #5/6 priority — 机构调研事件 alpha (公司接待机构调研).

源: mart_stock_survey_features (191,618 rows, 3,312 stocks, 253 dates)
- as_of_date 2025-04-23 ~ 2026-05-12 (~13 months 覆盖)
- ⚠ Training panel 2024-01 起, pre-2025-04-23 全 NULL (~15 months coverage gap)
- 接受 partial coverage: ML 学 "NULL = 早期数据缺" + "高 survey_count = 信号"

PIT 设计:
- as_of_date 是 daily PIT 观察日 (rows unique per (stock, as_of_date))
- 训练用 signal_date = as_of_date 直接 JOIN

业界 reported alpha:
- survey_count_30d 高 → 公司热度高, 机构兴趣增 (信号)
- survey_inst_30d / survey_count 比率高 → 高质量机构关注 (vs 散户机构混合)
- survey_bin (categorical): 调研活跃度分组

API:
    from services.features.institution_survey import build_institution_survey_features

    df = build_institution_survey_features(signal_df, db_path)
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


_RAW_COLS = [
    "survey_count_30d",
    "survey_count_60d",
    "survey_inst_30d",
    "survey_inst_60d",
]


def build_institution_survey_features(
    signal_df: pd.DataFrame,
    db_path: str | Path,
    stock_col: str = "stock_code",
    date_col: str = "signal_date",
) -> pd.DataFrame:
    """JOIN signal panel × mart_stock_survey_features (PIT-safe).

    Returns:
        signal_df + 4 raw cols + 3 derived:
            - is_inst_survey_30d: inst_30d / count_30d
            - is_inst_survey_60d: inst_60d / count_60d
            - is_survey_active: count_60d > 0 binary
    """
    out = signal_df.copy()
    if stock_col not in out.columns or date_col not in out.columns:
        raise ValueError(f"signal_df 必须含 {stock_col} 跟 {date_col}")
    out["_join_date"] = pd.to_datetime(out[date_col]).dt.strftime("%Y-%m-%d")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        survey_df = con.execute(
            "SELECT stock_code, as_of_date, "
            + ", ".join(_RAW_COLS)
            + " FROM mart_stock_survey_features"
        ).df()
    finally:
        con.close()

    survey_df["as_of_date"] = survey_df["as_of_date"].astype(str)

    merged = out.merge(
        survey_df,
        left_on=[stock_col, "_join_date"],
        right_on=["stock_code", "as_of_date"],
        how="left",
        suffixes=("", "_sv"),
    )
    merged = merged.drop(columns=["_join_date"])
    if "as_of_date" in merged.columns:
        merged = merged.drop(columns=["as_of_date"])
    if "stock_code_sv" in merged.columns:
        merged = merged.drop(columns=["stock_code_sv"])

    # Raw cols: int32 with 0 fillna (NULL = no survey activity = 0 signal)
    for c in _RAW_COLS:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0).astype("int32")

    # Derived: institutional ratio
    merged["is_inst_survey_30d"] = (
        merged["survey_inst_30d"] / merged["survey_count_30d"].replace(0, np.nan)
    ).fillna(0).astype("float32")
    merged["is_inst_survey_60d"] = (
        merged["survey_inst_60d"] / merged["survey_count_60d"].replace(0, np.nan)
    ).fillna(0).astype("float32")
    merged["is_survey_active"] = (merged["survey_count_60d"] > 0).astype("int8")

    return merged


def feature_names() -> list[str]:
    return _RAW_COLS + ["is_inst_survey_30d", "is_inst_survey_60d", "is_survey_active"]
