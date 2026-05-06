"""Shared feature schema for the multidimensional LightGBM model."""
from __future__ import annotations

import json
import re
from typing import Iterable


FEATURE_SCHEMA_VERSION = "multidim_v2_rank_industry_margin"
DEFAULT_LABEL_NAME = "forward_ret_20d"
TDX_KEEP_CHALLENGER_SCHEMA_VERSION = "m8_tdx_keep_challenger_v1"


BASE_FEATURE_COLS = [
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "vol_z20d", "ma_ratio_5", "ma_ratio_20", "ma_ratio_60", "ma_ratio_250",
    "rz_balance", "rz_chg_5d_pct",
    "kmid", "klen", "kup", "klow", "ksft",
    "vol_ratio_5_20", "vol_std_5d", "vol_std_20d",
    "range_pos_20", "range_pos_60",
    "momentum_diff", "amount_chg_5d",
    "inst_event_count_30d", "inst_event_count_60d",
    "exec_buy_count_90d", "exec_buy_ge1_count_90d",
    "lhb_inst_buy_count_30d", "lhb_inst_buy_count_60d",
    "jgdy_count_60d", "dzjy_count_60d",
    "days_since_exec_buy", "days_since_lhb",
    "shareholder_count_qoq", "inst_count_qoq",
    "fund_count_qoq", "qfii_count_qoq",
    "yjyg_lower_pct", "yjyg_upper_pct", "roe", "eps_basic",
    "hs300_ret_20d", "hs300_ret_60d",
]


DENSE_V2_FEATURE_COLS = [
    "ret_20d_rank", "ret_60d_rank", "vol_z20d_rank", "amount_chg_5d_rank",
    "rz_balance_rank", "rz_chg_5d_pct_rank",
    "ret_20d_tdx_l1_rel", "ret_60d_tdx_l1_rel",
    "vol_z20d_tdx_l1_rel", "amount_chg_5d_tdx_l1_rel",
    "rz_balance_to_amount20",
]


REGIME_FEATURE_COLS = ["regime_up", "regime_flat", "regime_down"]


TDX_CANDIDATE_FEATURE_COLS = [
    "holder_count_change_pct_tdx",
    "avg_float_shares_change_pct_tdx",
    "top10_concentration_change",
    "tdx_inst_total_shares_qoq",
    "national_team_shares_qoq",
    "qfii_shares_qoq",
    "fund_shares_qoq",
    "contract_liabilities_to_revenue",
    "ocf_to_profit_tdx",
    "forecast_profit_yoy_mid",
]


TDX_KEEP_FEATURE_COLS = [
    "forecast_profit_yoy_mid",
    "avg_float_shares_change_pct_tdx",
    "ocf_to_profit_tdx",
    "fund_shares_qoq",
    "forecast_range_width",
]


TDX_KEEP_OPTIONAL_WATCH_FEATURE_COLS = [
    "auto_general_corp_count_event_nonzero",
    "auto_general_corp_shares_event_nonzero",
    "auto_general_corp_count_level",
    "auto_top10_float_holder_shares_event_nonzero",
    "auto_top1_holder_shares_event_nonzero",
    "auto_top10_holder_shares_event_nonzero",
    "auto_holder_count_event_nonzero",
    "auto_private_equity_shares_level",
]


def ordered_feature_cols(*, include_dense_v2: bool = True) -> list[str]:
    cols = list(BASE_FEATURE_COLS)
    if include_dense_v2:
        cols.extend(DENSE_V2_FEATURE_COLS)
    return cols


def tdx_keep_challenger_feature_cols(*, include_dense_v2: bool = True) -> list[str]:
    """Baseline production features plus the five validated TDX keep overlays."""
    return normalize_feature_cols(ordered_feature_cols(include_dense_v2=include_dense_v2) + TDX_KEEP_FEATURE_COLS)


def normalize_feature_cols(cols: Iterable[str]) -> list[str]:
    """Deduplicate while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for col in cols:
        if col and col not in seen:
            out.append(col)
            seen.add(col)
    return out


def feature_cols_to_json(cols: Iterable[str]) -> str:
    return json.dumps(normalize_feature_cols(cols), ensure_ascii=False)


def feature_cols_from_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("feature_cols_json must be a JSON list")
    return [str(v) for v in data]


def holding_period_from_label(label_name: str | None) -> int | None:
    if not label_name:
        return None
    match = re.fullmatch(r"(?:forward_ret|follow_net_return)_(\d+)d", str(label_name))
    return int(match.group(1)) if match else None
