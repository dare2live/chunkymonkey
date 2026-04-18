"""Shared read-side builder for sector/industry overview payloads.

This keeps dashboard and industry-page level sector facts on one read model
instead of letting individual routes rebuild the same aggregates differently.
"""

from datetime import date, timedelta
from typing import Optional

from services.industry import (
    industry_join_clause,
    industry_level_db_column,
    industry_level_nonempty_condition,
    industry_level_select,
)
from services.sector_forecast_engine import get_latest_sector_forecast_snapshot


SECTOR_LEVEL = 1


def _sector_expr(alias: str, *, snapshot: bool = False) -> str:
    return f"{alias}.{industry_level_db_column(SECTOR_LEVEL, snapshot=snapshot)}"


def _sector_select(alias: str, *, snapshot: bool = False) -> str:
    return industry_level_select(SECTOR_LEVEL, alias=alias, result_alias="sector_name", snapshot=snapshot)


def _sector_nonempty_condition(alias: str, *, snapshot: bool = False) -> str:
    return industry_level_nonempty_condition(SECTOR_LEVEL, alias=alias, snapshot=snapshot)


def _query_sector_map(conn, query: str, params: tuple = ()) -> dict[str, dict]:
    try:
        rows = conn.execute(query, params).fetchall()
    except Exception:
        return {}
    return {row["sector_name"]: dict(row) for row in rows}


def load_sector_momentum_map(conn) -> dict[str, dict]:
    return _query_sector_map(
        conn,
        """
        SELECT sector_name, sector_code, trend_state, macd_cross, momentum_score,
               return_1m, return_3m, return_6m, return_12m,
               excess_1m, excess_3m, excess_6m, excess_12m,
               rotation_score, rotation_rank, rotation_rank_1m, rotation_rank_3m,
               rotation_bucket, rotation_blacklisted
        FROM mart_sector_momentum
        ORDER BY momentum_score DESC, sector_name
        """,
    )


def load_sector_forecast_map(conn, sector_count: int) -> dict[str, dict]:
    try:
        rows = get_latest_sector_forecast_snapshot(
            conn,
            limit=max(int(sector_count or 0), 64),
            auto_build=True,
        )
    except Exception:
        return {}
    return {row["sector_name"]: row for row in rows}


def load_sector_active_map(conn) -> dict[str, dict]:
    return _query_sector_map(
        conn,
        f"""
        SELECT {_sector_select('rel')},
               COUNT(DISTINCT institution_id) AS active_institution_count,
               COUNT(DISTINCT stock_code) AS current_stock_count
        FROM mart_current_relationship rel
        WHERE {_sector_nonempty_condition('rel')}
        GROUP BY {_sector_expr('rel')}
        """,
    )


def load_sector_candidate_map(conn) -> dict[str, dict]:
    return _query_sector_map(
        conn,
        f"""
        SELECT {_sector_select('ctx')},
               COUNT(*) AS candidate_count,
               AVG(t.discovery_score) AS avg_discovery_score,
               AVG(t.company_quality_score) AS avg_quality_score,
               AVG(t.stage_score) AS avg_stage_score,
               AVG(t.composite_priority_score) AS avg_composite_score,
               SUM(CASE WHEN t.price_20d_pct IS NOT NULL THEN 1 ELSE 0 END) AS feedback_20d_count,
               AVG(CASE WHEN t.price_20d_pct IS NOT NULL THEN t.price_20d_pct END) AS avg_price_20d_pct,
               AVG(CASE WHEN t.price_20d_pct IS NOT NULL AND t.price_20d_pct > 0 THEN 1.0
                        WHEN t.price_20d_pct IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS win_rate_20d,
               SUM(CASE WHEN t.priority_pool = 'A池' THEN 1 ELSE 0 END) AS a_pool_count,
               SUM(CASE WHEN t.priority_pool = 'B池' THEN 1 ELSE 0 END) AS b_pool_count,
               SUM(CASE WHEN t.priority_pool = 'C池' THEN 1 ELSE 0 END) AS c_pool_count,
               SUM(CASE WHEN t.priority_pool = 'D池' THEN 1 ELSE 0 END) AS d_pool_count,
               SUM(CASE WHEN t.priority_pool IN ('A池', 'B池') AND t.price_20d_pct IS NOT NULL THEN 1 ELSE 0 END) AS ab_feedback_20d_count,
               AVG(CASE WHEN t.priority_pool IN ('A池', 'B池') AND t.price_20d_pct IS NOT NULL THEN t.price_20d_pct END) AS ab_avg_price_20d_pct,
               AVG(CASE WHEN t.priority_pool IN ('A池', 'B池') AND t.price_20d_pct IS NOT NULL AND t.price_20d_pct > 0 THEN 1.0
                        WHEN t.priority_pool IN ('A池', 'B池') AND t.price_20d_pct IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS ab_win_rate_20d,
               SUM(CASE WHEN t.priority_pool = 'A池' AND t.price_20d_pct IS NOT NULL THEN 1 ELSE 0 END) AS a_feedback_20d_count,
               AVG(CASE WHEN t.priority_pool = 'A池' AND t.price_20d_pct IS NOT NULL THEN t.price_20d_pct END) AS a_avg_price_20d_pct,
               AVG(CASE WHEN t.priority_pool = 'A池' AND t.price_20d_pct IS NOT NULL AND t.price_20d_pct > 0 THEN 1.0
                        WHEN t.priority_pool = 'A池' AND t.price_20d_pct IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS a_win_rate_20d,
               SUM(CASE WHEN t.setup_tag IS NOT NULL THEN 1 ELSE 0 END) AS setup_candidate_count,
               SUM(CASE WHEN t.company_quality_score >= 80 THEN 1 ELSE 0 END) AS quality_strong_count,
               SUM(CASE WHEN t.stage_score >= 80 THEN 1 ELSE 0 END) AS stage_strong_count,
               SUM(CASE WHEN COALESCE(t.company_quality_score, -1) >= 80 THEN 1 ELSE 0 END) AS quality_band_80_plus,
               SUM(CASE WHEN COALESCE(t.company_quality_score, -1) >= 65 AND COALESCE(t.company_quality_score, -1) < 80 THEN 1 ELSE 0 END) AS quality_band_65_80,
               SUM(CASE WHEN COALESCE(t.company_quality_score, -1) >= 50 AND COALESCE(t.company_quality_score, -1) < 65 THEN 1 ELSE 0 END) AS quality_band_50_65,
               SUM(CASE WHEN COALESCE(t.company_quality_score, -1) < 50 THEN 1 ELSE 0 END) AS quality_band_below_50,
               SUM(CASE WHEN COALESCE(t.stage_score, -1) >= 80 THEN 1 ELSE 0 END) AS stage_band_80_plus,
               SUM(CASE WHEN COALESCE(t.stage_score, -1) >= 60 AND COALESCE(t.stage_score, -1) < 80 THEN 1 ELSE 0 END) AS stage_band_60_80,
               SUM(CASE WHEN COALESCE(t.stage_score, -1) >= 40 AND COALESCE(t.stage_score, -1) < 60 THEN 1 ELSE 0 END) AS stage_band_40_60,
               SUM(CASE WHEN COALESCE(t.stage_score, -1) < 40 THEN 1 ELSE 0 END) AS stage_band_below_40,
               SUM(CASE WHEN COALESCE(t.composite_priority_score, -1) >= 75 THEN 1 ELSE 0 END) AS composite_band_75_plus,
               SUM(CASE WHEN COALESCE(t.composite_priority_score, -1) >= 60 AND COALESCE(t.composite_priority_score, -1) < 75 THEN 1 ELSE 0 END) AS composite_band_60_75,
               SUM(CASE WHEN COALESCE(t.composite_priority_score, -1) >= 45 AND COALESCE(t.composite_priority_score, -1) < 60 THEN 1 ELSE 0 END) AS composite_band_45_60,
               SUM(CASE WHEN COALESCE(t.composite_priority_score, -1) < 45 THEN 1 ELSE 0 END) AS composite_band_below_45
        FROM mart_stock_trend t
        INNER JOIN dim_stock_industry_context_latest ctx ON ctx.stock_code = t.stock_code
        WHERE {_sector_nonempty_condition('ctx')}
        GROUP BY {_sector_expr('ctx')}
        """,
    )


def load_sector_snapshot_feedback_map(conn) -> dict[str, dict]:
    return _query_sector_map(
        conn,
        f"""
        SELECT {_sector_select('snap', snapshot=True)},
               COUNT(*) AS snapshot_total_count,
               COUNT(DISTINCT snapshot_date) AS snapshot_date_count,
               MIN(snapshot_date) AS snapshot_first_date,
               MAX(snapshot_date) AS snapshot_last_date,
               SUM(CASE WHEN priority_pool IS NOT NULL AND priority_pool != '' THEN 1 ELSE 0 END) AS snapshot_scored_count,
               COUNT(DISTINCT CASE WHEN priority_pool IS NOT NULL AND priority_pool != '' THEN snapshot_date END) AS snapshot_scored_date_count,
               SUM(CASE WHEN matured_10d = 1 AND gain_10d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_feedback_10d_count,
               AVG(CASE WHEN matured_10d = 1 THEN gain_10d END) AS snapshot_avg_gain_10d,
               AVG(CASE WHEN matured_10d = 1 AND gain_10d > 0 THEN 1.0
                        WHEN matured_10d = 1 AND gain_10d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_win_rate_10d,
               SUM(CASE WHEN matured_30d = 1 AND gain_30d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_feedback_30d_count,
               AVG(CASE WHEN matured_30d = 1 THEN gain_30d END) AS snapshot_avg_gain_30d,
               AVG(CASE WHEN matured_30d = 1 AND gain_30d > 0 THEN 1.0
                        WHEN matured_30d = 1 AND gain_30d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_win_rate_30d,
               SUM(CASE WHEN matured_60d = 1 AND gain_60d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_feedback_60d_count,
               AVG(CASE WHEN matured_60d = 1 THEN gain_60d END) AS snapshot_avg_gain_60d,
               AVG(CASE WHEN matured_60d = 1 AND gain_60d > 0 THEN 1.0
                        WHEN matured_60d = 1 AND gain_60d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_win_rate_60d,
               SUM(CASE WHEN priority_pool = 'A池' AND matured_10d = 1 AND gain_10d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_a_feedback_10d_count,
               AVG(CASE WHEN priority_pool = 'A池' AND matured_10d = 1 THEN gain_10d END) AS snapshot_a_avg_gain_10d,
               AVG(CASE WHEN priority_pool = 'A池' AND matured_10d = 1 AND gain_10d > 0 THEN 1.0
                        WHEN priority_pool = 'A池' AND matured_10d = 1 AND gain_10d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_a_win_rate_10d,
               SUM(CASE WHEN priority_pool IN ('A池', 'B池') AND matured_30d = 1 AND gain_30d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_ab_feedback_30d_count,
               AVG(CASE WHEN priority_pool IN ('A池', 'B池') AND matured_30d = 1 THEN gain_30d END) AS snapshot_ab_avg_gain_30d,
               AVG(CASE WHEN priority_pool IN ('A池', 'B池') AND matured_30d = 1 AND gain_30d > 0 THEN 1.0
                        WHEN priority_pool IN ('A池', 'B池') AND matured_30d = 1 AND gain_30d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_ab_win_rate_30d,
               SUM(CASE WHEN priority_pool = 'A池' AND matured_30d = 1 AND gain_30d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_a_feedback_30d_count,
               AVG(CASE WHEN priority_pool = 'A池' AND matured_30d = 1 THEN gain_30d END) AS snapshot_a_avg_gain_30d,
               AVG(CASE WHEN priority_pool = 'A池' AND matured_30d = 1 AND gain_30d > 0 THEN 1.0
                        WHEN priority_pool = 'A池' AND matured_30d = 1 AND gain_30d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_a_win_rate_30d,
               SUM(CASE WHEN priority_pool = 'A池' AND matured_60d = 1 AND gain_60d IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_a_feedback_60d_count,
               AVG(CASE WHEN priority_pool = 'A池' AND matured_60d = 1 THEN gain_60d END) AS snapshot_a_avg_gain_60d,
               AVG(CASE WHEN priority_pool = 'A池' AND matured_60d = 1 AND gain_60d > 0 THEN 1.0
                        WHEN priority_pool = 'A池' AND matured_60d = 1 AND gain_60d IS NOT NULL THEN 0.0
                        ELSE NULL END) * 100 AS snapshot_a_win_rate_60d
        FROM fact_setup_snapshot snap
        WHERE {_sector_nonempty_condition('snap', snapshot=True)}
        GROUP BY {_sector_expr('snap', snapshot=True)}
        """,
    )


def load_sector_context_map(conn) -> dict[str, dict]:
    return _query_sector_map(
        conn,
        f"""
        SELECT {_sector_select('ctx')},
               AVG(industry_tailwind_score) AS avg_tailwind_score,
               SUM(CASE WHEN dual_confirm_recent_180d > 0 THEN 1 ELSE 0 END) AS dual_confirm_stock_count,
               SUM(dual_confirm_recent_180d) AS dual_confirm_signal_count
        FROM dim_stock_industry_context_latest ctx
        WHERE {_sector_nonempty_condition('ctx')}
        GROUP BY {_sector_expr('ctx')}
        """,
    )


def load_sector_recent_event_map(conn, *, cutoff: str) -> dict[str, dict]:
    return _query_sector_map(
        conn,
        f"""
                SELECT {_sector_select('industry_dim')},
               SUM(CASE WHEN e.event_type = 'new_entry' THEN 1 ELSE 0 END) AS recent_new_entry_count,
               COUNT(DISTINCT CASE WHEN e.event_type = 'new_entry' THEN e.stock_code END) AS recent_new_entry_stock_count,
               SUM(CASE WHEN e.event_type IN ('new_entry', 'increase') THEN 1 ELSE 0 END) AS recent_buy_signal_count,
               COUNT(DISTINCT CASE WHEN e.event_type IN ('new_entry', 'increase') THEN e.stock_code END) AS recent_buy_signal_stock_count
        FROM fact_institution_event e
        {industry_join_clause('e.stock_code', alias='industry_dim', join_type='INNER')}
                WHERE {_sector_nonempty_condition('industry_dim')}
          AND COALESCE(NULLIF(REPLACE(e.notice_date, '-', ''), ''), REPLACE(e.report_date, '-', '')) >= ?
                GROUP BY {_sector_expr('industry_dim')}
        """,
        (cutoff,),
    )


def load_sector_top_stock_map(conn, *, topn: int) -> dict[str, list[dict]]:
    try:
        rows = conn.execute(
            f"""
            SELECT sector_name, stock_code, stock_name, stock_archetype, priority_pool,
                   composite_priority_score, company_quality_score, stage_score, setup_tag
            FROM (
                SELECT {_sector_select('ctx')},
                       t.stock_code,
                       t.stock_name,
                       t.stock_archetype,
                       t.priority_pool,
                       t.composite_priority_score,
                       t.company_quality_score,
                       t.stage_score,
                       t.setup_tag,
                       ROW_NUMBER() OVER (
                           PARTITION BY {_sector_expr('ctx')}
                           ORDER BY
                               CASE COALESCE(t.priority_pool, '')
                                   WHEN 'A池' THEN 0
                                   WHEN 'B池' THEN 1
                                   WHEN 'C池' THEN 2
                                   WHEN 'D池' THEN 3
                                   ELSE 9
                               END,
                               COALESCE(t.composite_priority_score, 0) DESC,
                               t.stock_code
                       ) AS rn
                FROM mart_stock_trend t
                INNER JOIN dim_stock_industry_context_latest ctx ON ctx.stock_code = t.stock_code
                  WHERE {_sector_nonempty_condition('ctx')}
            )
            WHERE rn <= ?
            ORDER BY sector_name, rn
            """,
            (topn,),
        ).fetchall()
    except Exception:
        return {}

    top_stock_map = {}
    for row in rows:
        top_stock_map.setdefault(row["sector_name"], []).append(dict(row))
    return top_stock_map


def build_sector_overview_item(
    sector_name: str,
    *,
    sector_map: dict[str, dict],
    sector_forecast_map: dict[str, dict],
    active_map: dict[str, dict],
    candidate_map: dict[str, dict],
    snapshot_feedback_map: dict[str, dict],
    context_map: dict[str, dict],
    recent_event_map: dict[str, dict],
    top_stock_map: dict[str, list[dict]],
) -> dict:
    sector = sector_map.get(sector_name) or {}
    sector_forecast = sector_forecast_map.get(sector_name) or {}
    active = active_map.get(sector_name) or {}
    candidate = candidate_map.get(sector_name) or {}
    snapshot_feedback = snapshot_feedback_map.get(sector_name) or {}
    context = context_map.get(sector_name) or {}
    recent = recent_event_map.get(sector_name) or {}

    return {
        "sector_name": sector_name,
        "sector_code": sector.get("sector_code"),
        "trend_state": sector.get("trend_state"),
        "macd_cross": sector.get("macd_cross"),
        "momentum_score": sector.get("momentum_score"),
        "return_1m": sector.get("return_1m"),
        "return_3m": sector.get("return_3m"),
        "return_6m": sector.get("return_6m"),
        "return_12m": sector.get("return_12m"),
        "excess_1m": sector.get("excess_1m"),
        "excess_3m": sector.get("excess_3m"),
        "excess_6m": sector.get("excess_6m"),
        "excess_12m": sector.get("excess_12m"),
        "rotation_score": sector.get("rotation_score"),
        "rotation_rank": sector.get("rotation_rank"),
        "rotation_rank_1m": sector.get("rotation_rank_1m"),
        "rotation_rank_3m": sector.get("rotation_rank_3m"),
        "rotation_bucket": sector.get("rotation_bucket"),
        "rotation_blacklisted": sector.get("rotation_blacklisted", 0),
        "qlib_sector_model_id": sector_forecast.get("model_id"),
        "qlib_sector_snapshot_date": sector_forecast.get("snapshot_date"),
        "qlib_stock_count": sector_forecast.get("stock_count"),
        "avg_qlib_score": sector_forecast.get("avg_qlib_score"),
        "avg_qlib_percentile": sector_forecast.get("avg_qlib_percentile"),
        "avg_forecast_cross_section_score": sector_forecast.get("avg_forecast_cross_section_score") if sector_forecast.get("avg_forecast_cross_section_score") is not None else sector_forecast.get("avg_forecast_20d_score"),
        "avg_forecast_20d_score": sector_forecast.get("avg_forecast_20d_score"),
        "avg_forecast_industry_relative_score": sector_forecast.get("avg_forecast_industry_relative_score") if sector_forecast.get("avg_forecast_industry_relative_score") is not None else sector_forecast.get("avg_forecast_60d_excess_score"),
        "avg_forecast_60d_excess_score": sector_forecast.get("avg_forecast_60d_excess_score"),
        "avg_forecast_risk_adjusted_score": sector_forecast.get("avg_forecast_risk_adjusted_score"),
        "high_conviction_count": sector_forecast.get("high_conviction_count", 0),
        "next_rotation_score": sector_forecast.get("next_rotation_score"),
        "next_rotation_label": sector_forecast.get("next_rotation_label"),
        "next_rotation_reason": sector_forecast.get("next_rotation_reason"),
        "active_institution_count": active.get("active_institution_count", 0),
        "current_stock_count": active.get("current_stock_count", 0),
        "recent_new_entry_count": recent.get("recent_new_entry_count", 0),
        "recent_new_entry_stock_count": recent.get("recent_new_entry_stock_count", 0),
        "recent_buy_signal_count": recent.get("recent_buy_signal_count", 0),
        "recent_buy_signal_stock_count": recent.get("recent_buy_signal_stock_count", 0),
        "candidate_count": candidate.get("candidate_count", 0),
        "feedback_20d_count": candidate.get("feedback_20d_count", 0),
        "avg_price_20d_pct": candidate.get("avg_price_20d_pct"),
        "win_rate_20d": candidate.get("win_rate_20d"),
        "snapshot_total_count": snapshot_feedback.get("snapshot_total_count", 0),
        "snapshot_date_count": snapshot_feedback.get("snapshot_date_count", 0),
        "snapshot_first_date": snapshot_feedback.get("snapshot_first_date"),
        "snapshot_last_date": snapshot_feedback.get("snapshot_last_date"),
        "snapshot_scored_count": snapshot_feedback.get("snapshot_scored_count", 0),
        "snapshot_scored_date_count": snapshot_feedback.get("snapshot_scored_date_count", 0),
        "snapshot_feedback_10d_count": snapshot_feedback.get("snapshot_feedback_10d_count", 0),
        "snapshot_avg_gain_10d": snapshot_feedback.get("snapshot_avg_gain_10d"),
        "snapshot_win_rate_10d": snapshot_feedback.get("snapshot_win_rate_10d"),
        "snapshot_feedback_30d_count": snapshot_feedback.get("snapshot_feedback_30d_count", 0),
        "snapshot_avg_gain_30d": snapshot_feedback.get("snapshot_avg_gain_30d"),
        "snapshot_win_rate_30d": snapshot_feedback.get("snapshot_win_rate_30d"),
        "snapshot_feedback_60d_count": snapshot_feedback.get("snapshot_feedback_60d_count", 0),
        "snapshot_avg_gain_60d": snapshot_feedback.get("snapshot_avg_gain_60d"),
        "snapshot_win_rate_60d": snapshot_feedback.get("snapshot_win_rate_60d"),
        "snapshot_a_feedback_10d_count": snapshot_feedback.get("snapshot_a_feedback_10d_count", 0),
        "snapshot_a_avg_gain_10d": snapshot_feedback.get("snapshot_a_avg_gain_10d"),
        "snapshot_a_win_rate_10d": snapshot_feedback.get("snapshot_a_win_rate_10d"),
        "snapshot_ab_feedback_30d_count": snapshot_feedback.get("snapshot_ab_feedback_30d_count", 0),
        "snapshot_ab_avg_gain_30d": snapshot_feedback.get("snapshot_ab_avg_gain_30d"),
        "snapshot_ab_win_rate_30d": snapshot_feedback.get("snapshot_ab_win_rate_30d"),
        "snapshot_a_feedback_30d_count": snapshot_feedback.get("snapshot_a_feedback_30d_count", 0),
        "snapshot_a_avg_gain_30d": snapshot_feedback.get("snapshot_a_avg_gain_30d"),
        "snapshot_a_win_rate_30d": snapshot_feedback.get("snapshot_a_win_rate_30d"),
        "snapshot_a_feedback_60d_count": snapshot_feedback.get("snapshot_a_feedback_60d_count", 0),
        "snapshot_a_avg_gain_60d": snapshot_feedback.get("snapshot_a_avg_gain_60d"),
        "snapshot_a_win_rate_60d": snapshot_feedback.get("snapshot_a_win_rate_60d"),
        "setup_candidate_count": candidate.get("setup_candidate_count", 0),
        "a_pool_count": candidate.get("a_pool_count", 0),
        "b_pool_count": candidate.get("b_pool_count", 0),
        "c_pool_count": candidate.get("c_pool_count", 0),
        "d_pool_count": candidate.get("d_pool_count", 0),
        "ab_feedback_20d_count": candidate.get("ab_feedback_20d_count", 0),
        "ab_avg_price_20d_pct": candidate.get("ab_avg_price_20d_pct"),
        "ab_win_rate_20d": candidate.get("ab_win_rate_20d"),
        "a_feedback_20d_count": candidate.get("a_feedback_20d_count", 0),
        "a_avg_price_20d_pct": candidate.get("a_avg_price_20d_pct"),
        "a_win_rate_20d": candidate.get("a_win_rate_20d"),
        "quality_strong_count": candidate.get("quality_strong_count", 0),
        "stage_strong_count": candidate.get("stage_strong_count", 0),
        "avg_discovery_score": candidate.get("avg_discovery_score"),
        "avg_quality_score": candidate.get("avg_quality_score"),
        "avg_stage_score": candidate.get("avg_stage_score"),
        "avg_composite_score": candidate.get("avg_composite_score"),
        "quality_band_80_plus": candidate.get("quality_band_80_plus", 0),
        "quality_band_65_80": candidate.get("quality_band_65_80", 0),
        "quality_band_50_65": candidate.get("quality_band_50_65", 0),
        "quality_band_below_50": candidate.get("quality_band_below_50", 0),
        "stage_band_80_plus": candidate.get("stage_band_80_plus", 0),
        "stage_band_60_80": candidate.get("stage_band_60_80", 0),
        "stage_band_40_60": candidate.get("stage_band_40_60", 0),
        "stage_band_below_40": candidate.get("stage_band_below_40", 0),
        "composite_band_75_plus": candidate.get("composite_band_75_plus", 0),
        "composite_band_60_75": candidate.get("composite_band_60_75", 0),
        "composite_band_45_60": candidate.get("composite_band_45_60", 0),
        "composite_band_below_45": candidate.get("composite_band_below_45", 0),
        "avg_tailwind_score": context.get("avg_tailwind_score"),
        "dual_confirm_stock_count": context.get("dual_confirm_stock_count", 0),
        "dual_confirm_signal_count": context.get("dual_confirm_signal_count", 0),
        "top_stocks": top_stock_map.get(sector_name, []),
    }


def _sector_names(*maps: dict[str, object]) -> list[str]:
    sector_names = set()
    for mapping in maps:
        sector_names.update(mapping.keys())
    return sorted(sector_names)


def _sector_sort_key(item: dict) -> tuple:
    return (
        0 if item.get("next_rotation_score") is not None else 1,
        -(item.get("next_rotation_score") or 0),
        -(item.get("a_pool_count") or 0),
        -(item.get("avg_composite_score") or 0),
        -(item.get("momentum_score") or 0),
        item.get("sector_name") or "",
    )


def _strongest_sector_name(data: list[dict], metric_key: str) -> Optional[str]:
    strongest_name = None
    strongest_value = None
    for item in data:
        value = item.get(metric_key)
        if value is None:
            continue
        if strongest_value is None or value > strongest_value:
            strongest_value = value
            strongest_name = item.get("sector_name")
    return strongest_name


def _build_sector_focus(data: list[dict]) -> list[dict]:
    return [
        {
            "sector_name": item.get("sector_name"),
            "next_rotation_score": item.get("next_rotation_score"),
            "next_rotation_label": item.get("next_rotation_label"),
            "next_rotation_reason": item.get("next_rotation_reason"),
            "stock_count": item.get("qlib_stock_count"),
            "sector_momentum_score": item.get("momentum_score"),
            "source": "qlib_sector_forecast",
        }
        for item in data
        if item.get("next_rotation_score") is not None
    ][:4]


def build_industry_overview_summary(data: list[dict]) -> dict:
    sector_focus = _build_sector_focus(data)
    strongest_sector = _strongest_sector_name(data, "momentum_score")
    strongest_qlib_sector = _strongest_sector_name(data, "next_rotation_score")
    strongest_sector_source = "qlib_sector_forecast" if sector_focus else "sector_momentum"
    strongest_sector_note = "按 Qlib 行业前瞻排序" if sector_focus else "按行业动量排序"

    return {
        "sector_count": len(data),
        "strongest_sector": strongest_qlib_sector or strongest_sector,
        "strongest_sector_source": strongest_sector_source,
        "strongest_sector_note": strongest_sector_note,
        "sector_focus": sector_focus,
        "qlib_sector_count": sum(1 for item in data if item.get("next_rotation_score") is not None),
        "a_pool_total": sum(item.get("a_pool_count") or 0 for item in data),
        "setup_total": sum(item.get("setup_candidate_count") or 0 for item in data),
        "dual_confirm_total": sum(item.get("dual_confirm_stock_count") or 0 for item in data),
        "positive_trend_count": sum(1 for item in data if item.get("trend_state") in ("bullish", "recovering")),
        "feedback_ready_total": sum(item.get("feedback_20d_count") or 0 for item in data),
        "snapshot_feedback_ready_10d_total": sum(item.get("snapshot_feedback_10d_count") or 0 for item in data),
        "snapshot_feedback_ready_total": sum(item.get("snapshot_feedback_30d_count") or 0 for item in data),
        "snapshot_feedback_ready_60d_total": sum(item.get("snapshot_feedback_60d_count") or 0 for item in data),
        "snapshot_feedback_sector_count": sum(1 for item in data if (item.get("snapshot_feedback_30d_count") or 0) > 0),
    }


def get_industry_overview_payload(conn, *, topn: int = 3) -> dict:
    sector_map = load_sector_momentum_map(conn)
    sector_forecast_map = load_sector_forecast_map(conn, len(sector_map))
    active_map = load_sector_active_map(conn)
    candidate_map = load_sector_candidate_map(conn)
    snapshot_feedback_map = load_sector_snapshot_feedback_map(conn)
    context_map = load_sector_context_map(conn)
    recent_event_map = load_sector_recent_event_map(
        conn,
        cutoff=(date.today() - timedelta(days=120)).strftime("%Y%m%d"),
    )
    top_stock_map = load_sector_top_stock_map(conn, topn=topn)

    data = [
        build_sector_overview_item(
            sector_name,
            sector_map=sector_map,
            sector_forecast_map=sector_forecast_map,
            active_map=active_map,
            candidate_map=candidate_map,
            snapshot_feedback_map=snapshot_feedback_map,
            context_map=context_map,
            recent_event_map=recent_event_map,
            top_stock_map=top_stock_map,
        )
        for sector_name in _sector_names(
            sector_map,
            sector_forecast_map,
            active_map,
            candidate_map,
            snapshot_feedback_map,
            context_map,
            recent_event_map,
            top_stock_map,
        )
    ]
    data.sort(key=_sector_sort_key)
    return {"ok": True, "count": len(data), "summary": build_industry_overview_summary(data), "data": data}