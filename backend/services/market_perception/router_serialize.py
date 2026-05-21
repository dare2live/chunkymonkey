"""市场感知 router 的 row → dict serialize helpers.

2026-05-21 从 backend/routers/v3_market_perception.py 拆出 (god-module 807 LOC 拆分计划 Step 2).
- 8 个 _serialize_*_row 函数 (各 endpoint 一个)
- 6 个 SELECT 字符串常量 (跟 serialize 配对的 SQL column 列表)
- 2 个 pure helper: _finite_float (走 services.utils 集中版), _clean_text

Pure functions, 无 conn 依赖, 无 wall-clock, 无 side effect. 安全 import 0 cycle.
"""
from __future__ import annotations

# 2026-05-21 集中: _finite_float 之前在 10 处重复定义, 改走 services.utils.finite_float.
from services.utils import finite_float as _finite_float  # noqa: F401


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _serialize_row(row) -> dict:
    """regime snapshot row → dict."""
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "regime_score": float(row[1]) if row[1] is not None else None,
        "breadth_state": row[2],
        "volatility_state": row[3],
        "sentiment_phase": row[4],
        "hs300_ret_60d": float(row[5]) if row[5] is not None else None,
        "hs300_vol_20d": float(row[6]) if row[6] is not None else None,
        "breadth_ratio": float(row[7]) if row[7] is not None else None,
        "breadth_p75_90d": float(row[8]) if row[8] is not None else None,
        "limit_up_count": int(row[9]) if row[9] is not None else None,
        "lhb_event_count": int(row[10]) if row[10] is not None else None,
        "n_obs_days": int(row[11]) if row[11] is not None else None,
        "source_engines": row[12],
        "pit_cutoff_date": str(row[13]) if row[13] is not None else None,
        "built_at": str(row[14]) if row[14] is not None else None,
    }


def _serialize_emotion_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "emotion_score": float(row[1]) if row[1] is not None else None,
        "emotion_state": row[2],
        "action_bias": row[3],
        "cycle_phase": row[4],
        "market_breadth": float(row[5]) if row[5] is not None else None,
        "up_count": int(row[6]) if row[6] is not None else None,
        "down_count": int(row[7]) if row[7] is not None else None,
        "limit_up_count": int(row[8]) if row[8] is not None else None,
        "limit_down_count": int(row[9]) if row[9] is not None else None,
        "first_board_count": int(row[10]) if row[10] is not None else None,
        "second_board_count": int(row[11]) if row[11] is not None else None,
        "third_plus_count": int(row[12]) if row[12] is not None else None,
        "promotion_rate_1_to_2": float(row[13]) if row[13] is not None else None,
        "promotion_rate_2_to_3": float(row[14]) if row[14] is not None else None,
        "open_board_rate": float(row[15]) if row[15] is not None else None,
        "next_day_premium": float(row[16]) if row[16] is not None else None,
        "turnover_concentration": float(row[17]) if row[17] is not None else None,
        "lhb_event_count": int(row[18]) if row[18] is not None else None,
        "n_stocks": int(row[19]) if row[19] is not None else None,
        "unknown_metrics": row[20],
        "source_engines": row[21],
        "pit_cutoff_date": str(row[22]) if row[22] is not None else None,
        "built_at": str(row[23]) if row[23] is not None else None,
    }


EMOTION_SELECT = """
    snapshot_date, emotion_score, emotion_state, action_bias, cycle_phase,
    market_breadth, up_count, down_count, limit_up_count, limit_down_count,
    first_board_count, second_board_count, third_plus_count,
    promotion_rate_1_to_2, promotion_rate_2_to_3, open_board_rate,
    next_day_premium, turnover_concentration, lhb_event_count, n_stocks,
    unknown_metrics, source_engines, pit_cutoff_date, built_at
"""


def _serialize_theme_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "theme_name": row[1],
        "theme_score": float(row[2]) if row[2] is not None else None,
        "lifecycle_stage": row[3],
        "mainline_rank": int(row[4]) if row[4] is not None else None,
        "is_mainline": bool(row[5]) if row[5] is not None else None,
        "diffusion_state": row[6],
        "sector_breadth": float(row[7]) if row[7] is not None else None,
        "sector_ret_20d": float(row[8]) if row[8] is not None else None,
        "sector_ret_60d": float(row[9]) if row[9] is not None else None,
        "sector_excess_20d": float(row[10]) if row[10] is not None else None,
        "sector_excess_60d": float(row[11]) if row[11] is not None else None,
        "price_vs_ma20": float(row[12]) if row[12] is not None else None,
        "price_vs_ma60": float(row[13]) if row[13] is not None else None,
        "limit_up_count": int(row[14]) if row[14] is not None else None,
        "n_stocks": int(row[15]) if row[15] is not None else None,
        "top3_turnover_share": float(row[16]) if row[16] is not None else None,
        "pit_member_confidence": row[17],
        "source_engines": row[18],
        "pit_cutoff_date": str(row[19]) if row[19] is not None else None,
        "built_at": str(row[20]) if row[20] is not None else None,
    }


THEME_SELECT = """
    snapshot_date, theme_name, theme_score, lifecycle_stage, mainline_rank,
    is_mainline, diffusion_state, sector_breadth, sector_ret_20d,
    sector_ret_60d, sector_excess_20d, sector_excess_60d, price_vs_ma20,
    price_vs_ma60, limit_up_count, n_stocks, top3_turnover_share,
    pit_member_confidence, source_engines, pit_cutoff_date, built_at
"""


def _serialize_under_reaction_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "stock_code": row[1],
        "under_reaction_score": float(row[2]) if row[2] is not None else None,
        "fund_anomaly_score": float(row[3]) if row[3] is not None else None,
        "price_reaction_score": float(row[4]) if row[4] is not None else None,
        "capital_flow_score": float(row[5]) if row[5] is not None else None,
        "amount_expansion_score": float(row[6]) if row[6] is not None else None,
        "crowding_penalty": float(row[7]) if row[7] is not None else None,
        "ret_5d": float(row[8]) if row[8] is not None else None,
        "ret_20d": float(row[9]) if row[9] is not None else None,
        "amount_ratio_5_20": float(row[10]) if row[10] is not None else None,
        "lhb_count_30d": int(row[11]) if row[11] is not None else None,
        "lhb_inst_buy_30d": int(row[12]) if row[12] is not None else None,
        "lhb_net_buy_pct_30d": float(row[13]) if row[13] is not None else None,
        "exec_net_signal": float(row[14]) if row[14] is not None else None,
        "holder_count_change_q_pct": float(row[15]) if row[15] is not None else None,
        "theme_name": row[16],
        "theme_score": float(row[17]) if row[17] is not None else None,
        "lifecycle_stage": row[18],
        "pit_cutoff_date": str(row[19]) if row[19] is not None else None,
        "source_engines": row[20],
        "built_at": str(row[21]) if row[21] is not None else None,
    }


UNDER_REACTION_SELECT = """
    snapshot_date, stock_code, under_reaction_score, fund_anomaly_score,
    price_reaction_score, capital_flow_score, amount_expansion_score,
    crowding_penalty, ret_5d, ret_20d, amount_ratio_5_20, lhb_count_30d,
    lhb_inst_buy_30d, lhb_net_buy_pct_30d, exec_net_signal,
    holder_count_change_q_pct, theme_name, theme_score, lifecycle_stage,
    pit_cutoff_date, source_engines, built_at
"""


def _serialize_leader_follower_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "theme_name": row[1],
        "leader_stock_code": row[2],
        "follower_stock_code": row[3],
        "relation_type": row[4],
        "lag_days": int(row[5]) if row[5] is not None else None,
        "leader_strength_score": float(row[6]) if row[6] is not None else None,
        "follower_lag_score": float(row[7]) if row[7] is not None else None,
        "diffusion_score": float(row[8]) if row[8] is not None else None,
        "leader_ret_5d": float(row[9]) if row[9] is not None else None,
        "leader_ret_20d": float(row[10]) if row[10] is not None else None,
        "follower_ret_1d": float(row[11]) if row[11] is not None else None,
        "follower_ret_3d": float(row[12]) if row[12] is not None else None,
        "follower_ret_5d": float(row[13]) if row[13] is not None else None,
        "follower_ret_20d": float(row[14]) if row[14] is not None else None,
        "follower_amount_ratio_5_20": float(row[15]) if row[15] is not None else None,
        "theme_score": float(row[16]) if row[16] is not None else None,
        "lifecycle_stage": row[17],
        "pit_member_confidence": row[18],
        "pit_cutoff_date": str(row[19]) if row[19] is not None else None,
        "source_engines": row[20],
        "built_at": str(row[21]) if row[21] is not None else None,
    }


LEADER_FOLLOWER_SELECT = """
    snapshot_date, theme_name, leader_stock_code, follower_stock_code,
    relation_type, lag_days, leader_strength_score, follower_lag_score,
    diffusion_score, leader_ret_5d, leader_ret_20d, follower_ret_1d,
    follower_ret_3d, follower_ret_5d, follower_ret_20d,
    follower_amount_ratio_5_20, theme_score, lifecycle_stage,
    pit_member_confidence, pit_cutoff_date, source_engines, built_at
"""


def _serialize_style_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "style_rotation_score": _finite_float(row[1]),
        "style_bias": row[2],
        "size_preference_score": _finite_float(row[3]),
        "trend_preference_score": _finite_float(row[4]),
        "crowding_risk_score": _finite_float(row[5]),
        "overheat_reversal_risk": _finite_float(row[6]),
        "small_ret_1d": _finite_float(row[7]),
        "mid_ret_1d": _finite_float(row[8]),
        "large_ret_1d": _finite_float(row[9]),
        "trend_ret_1d": _finite_float(row[10]),
        "reversal_ret_1d": _finite_float(row[11]),
        "top_decile_turnover_share": _finite_float(row[12]),
        "hot_stock_share": _finite_float(row[13]),
        "style_source": row[14],
        "emotion_score": _finite_float(row[15]),
        "emotion_state": row[16],
        "pit_cutoff_date": str(row[17]) if row[17] is not None else None,
        "source_engines": row[18],
        "built_at": str(row[19]) if row[19] is not None else None,
    }


STYLE_SELECT = """
    snapshot_date, style_rotation_score, style_bias, size_preference_score,
    trend_preference_score, crowding_risk_score, overheat_reversal_risk,
    small_ret_1d, mid_ret_1d, large_ret_1d, trend_ret_1d, reversal_ret_1d,
    top_decile_turnover_share, hot_stock_share, style_source, emotion_score,
    emotion_state, pit_cutoff_date, source_engines, built_at
"""


def _serialize_stock_context_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "stock_code": row[1],
        "context_score": _finite_float(row[2]),
        "context_state": row[3],
        "market_regime_score": _finite_float(row[4]),
        "emotion_score": _finite_float(row[5]),
        "emotion_state": row[6],
        "theme_name": row[7],
        "theme_score": _finite_float(row[8]),
        "lifecycle_stage": row[9],
        "under_reaction_score": _finite_float(row[10]),
        "fund_anomaly_score": _finite_float(row[11]),
        "leader_follow_score": _finite_float(row[12]),
        "leader_stock_code": _clean_text(row[13]),
        "chain_diffusion_score": _finite_float(row[14]),
        "style_rotation_score": _finite_float(row[15]),
        "style_bias": row[16],
        "crowding_risk_score": _finite_float(row[17]),
        "overheat_reversal_risk": _finite_float(row[18]),
        "data_completeness_score": _finite_float(row[19]),
        "missing_context_fields": row[20],
        "pit_cutoff_date": str(row[21]) if row[21] is not None else None,
        "source_engines": row[22],
        "built_at": str(row[23]) if row[23] is not None else None,
    }


STOCK_CONTEXT_SELECT = """
    snapshot_date, stock_code, context_score, context_state,
    market_regime_score, emotion_score, emotion_state, theme_name,
    theme_score, lifecycle_stage, under_reaction_score, fund_anomaly_score,
    leader_follow_score, leader_stock_code, chain_diffusion_score,
    style_rotation_score, style_bias, crowding_risk_score,
    overheat_reversal_risk, data_completeness_score, missing_context_fields,
    pit_cutoff_date, source_engines, built_at
"""
