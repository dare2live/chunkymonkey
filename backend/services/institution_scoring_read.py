"""Shared read-side helpers for institution scoring payloads.

Keep institution scorecard stats and institution scoring breakdown payloads in
one backend-owned module so scorecard pages and detail drawers share the same
backend shaping logic.
"""

import math
from typing import Optional

from services.scoring import load_scoring_config


def _scorecard_row_payload(rows, fields: list[str]) -> list[dict]:
    result = []
    for row in rows:
        item = {}
        for field in fields:
            value = row[field]
            if isinstance(value, float):
                value = round(float(value), 2)
            item[field] = value
        result.append(item)
    return result


def load_institution_scorecard_stats(conn) -> dict:
    summary_row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN score_basis = 'buy' THEN 1 ELSE 0 END) AS buy_basis_count,
               SUM(CASE WHEN score_basis = 'fallback_all' THEN 1 ELSE 0 END) AS fallback_basis_count,
               SUM(CASE WHEN score_confidence = 'high' THEN 1 ELSE 0 END) AS quality_high_conf_count,
               SUM(CASE WHEN followability_confidence = 'high' THEN 1 ELSE 0 END) AS follow_high_conf_count,
               SUM(CASE WHEN quality_score >= 65 THEN 1 ELSE 0 END) AS quality_strong_count,
               SUM(CASE WHEN followability_score >= 65 THEN 1 ELSE 0 END) AS followability_strong_count,
               SUM(CASE WHEN safe_follow_event_count > 0 THEN 1 ELSE 0 END) AS safe_follow_inst_count,
               AVG(quality_score) AS avg_quality_score,
               AVG(followability_score) AS avg_followability_score,
               AVG(avg_premium_pct) AS avg_premium_pct,
               AVG(buy_event_count) AS avg_buy_event_count,
               AVG(safe_follow_event_count) AS avg_safe_follow_event_count
        FROM mart_institution_profile
        """
    ).fetchone()

    type_rows = conn.execute(
        """
        SELECT COALESCE(inst_type, '未分类') AS inst_type,
               COUNT(*) AS total,
               AVG(quality_score) AS avg_quality_score,
               AVG(followability_score) AS avg_followability_score
        FROM mart_institution_profile
        GROUP BY COALESCE(inst_type, '未分类')
        ORDER BY COUNT(*) DESC, inst_type
        LIMIT 6
        """
    ).fetchall()

    hint_rows = conn.execute(
        """
        SELECT COALESCE(followability_hint, '未标注') AS followability_hint,
               COUNT(*) AS total
        FROM mart_institution_profile
        GROUP BY COALESCE(followability_hint, '未标注')
        ORDER BY COUNT(*) DESC, followability_hint
        LIMIT 6
        """
    ).fetchall()

    confidence_rows = conn.execute(
        """
        SELECT 'quality' AS metric,
               COALESCE(score_confidence, '未标注') AS confidence,
               COUNT(*) AS total
        FROM mart_institution_profile
        GROUP BY COALESCE(score_confidence, '未标注')
        UNION ALL
        SELECT 'followability' AS metric,
               COALESCE(followability_confidence, '未标注') AS confidence,
               COUNT(*) AS total
        FROM mart_institution_profile
        GROUP BY COALESCE(followability_confidence, '未标注')
        """
    ).fetchall()

    confidence_map = {"quality": [], "followability": []}
    for row in confidence_rows:
        confidence_map[row["metric"]].append(
            {
                "confidence": row["confidence"],
                "total": int(row["total"] or 0),
            }
        )

    return {
        "summary": {
            "total": int(summary_row["total"] or 0),
            "buy_basis_count": int(summary_row["buy_basis_count"] or 0),
            "fallback_basis_count": int(summary_row["fallback_basis_count"] or 0),
            "quality_high_conf_count": int(summary_row["quality_high_conf_count"] or 0),
            "follow_high_conf_count": int(summary_row["follow_high_conf_count"] or 0),
            "quality_strong_count": int(summary_row["quality_strong_count"] or 0),
            "followability_strong_count": int(summary_row["followability_strong_count"] or 0),
            "safe_follow_inst_count": int(summary_row["safe_follow_inst_count"] or 0),
            "avg_quality_score": round(float(summary_row["avg_quality_score"]), 2) if summary_row["avg_quality_score"] is not None else None,
            "avg_followability_score": round(float(summary_row["avg_followability_score"]), 2) if summary_row["avg_followability_score"] is not None else None,
            "avg_premium_pct": round(float(summary_row["avg_premium_pct"]), 2) if summary_row["avg_premium_pct"] is not None else None,
            "avg_buy_event_count": round(float(summary_row["avg_buy_event_count"]), 2) if summary_row["avg_buy_event_count"] is not None else None,
            "avg_safe_follow_event_count": round(float(summary_row["avg_safe_follow_event_count"]), 2) if summary_row["avg_safe_follow_event_count"] is not None else None,
        },
        "type_top": _scorecard_row_payload(type_rows, ["inst_type", "total", "avg_quality_score", "avg_followability_score"]),
        "hint_top": _scorecard_row_payload(hint_rows, ["followability_hint", "total"]),
        "confidence": confidence_map,
    }


def build_institution_scoring_breakdown_payload(
    profile_row: Optional[dict],
    object_id: Optional[str] = None,
    config: Optional[dict] = None,
) -> Optional[dict]:
    if not profile_row:
        return None

    profile = dict(profile_row)
    config = config or {}
    has_buy = (profile.get("buy_event_count") or 0) > 0
    factors = []
    factor_defs = [
        (
            "sample_weight",
            "买入事件数" if has_buy else "事件总数",
            profile.get("buy_event_count") if has_buy else profile.get("total_events"),
            "fact_institution_event WHERE event_type IN ('new_entry','increase')" if has_buy else "fact_institution_event",
            "事件数越多越稳定",
        ),
        (
            "gain_30d_weight",
            "30日平均收益",
            profile.get("buy_avg_gain_30d") if has_buy else profile.get("avg_gain_30d"),
            "fact_institution_event.gain_30d 均值",
            "公告后30个交易日涨幅均值",
        ),
        (
            "gain_60d_weight",
            "60日平均收益",
            profile.get("buy_avg_gain_60d") if has_buy else profile.get("avg_gain_60d"),
            "fact_institution_event.gain_60d 均值",
            "公告后60个交易日涨幅均值",
        ),
        (
            "gain_120d_weight",
            "120日平均收益",
            profile.get("buy_avg_gain_120d") if has_buy else profile.get("avg_gain_120d"),
            "fact_institution_event.gain_120d 均值",
            "公告后120个交易日涨幅均值",
        ),
        (
            "win_rate_30d_weight",
            "30日胜率",
            profile.get("buy_win_rate_30d") if has_buy else profile.get("win_rate_30d"),
            "gain_30d > 0 的事件占比",
            "30日正收益事件占比",
        ),
        (
            "win_rate_60d_weight",
            "60日胜率",
            profile.get("buy_win_rate_60d") if has_buy else profile.get("win_rate_60d"),
            "gain_60d > 0 的事件占比",
            "60日正收益事件占比",
        ),
        (
            "win_rate_90d_weight",
            "120日胜率",
            profile.get("buy_win_rate_120d") if has_buy else profile.get("win_rate_90d"),
            "gain_120d > 0 的事件占比",
            "120日正收益事件占比",
        ),
        (
            "drawdown_weight",
            "回撤控制",
            profile.get("buy_median_max_drawdown_30d") if has_buy else profile.get("median_max_drawdown_30d"),
            "max_drawdown_30d 中位数",
            "越小越好（取负值排名）",
        ),
        (
            "stability_weight",
            "收益稳定性",
            None,
            "1 - |median_gain - avg_gain| / |avg_gain|",
            "中位数与均值偏差越小越稳定",
        ),
    ]
    for key, label, raw_value, source, description in factor_defs:
        factors.append(
            {
                "key": key,
                "label": label,
                "raw_value": round(raw_value, 2) if raw_value is not None else None,
                "weight": config.get(key, 0),
                "source": source,
                "description": description,
            }
        )

    buy_count = profile.get("buy_event_count") or profile.get("total_events") or 0
    confidence_factor = min(1.0, math.sqrt(buy_count / 10.0)) if buy_count > 0 else 0

    return {
        "ok": True,
        "card_type": "institution",
        "object_id": object_id or profile.get("institution_id"),
        "quality_score": profile.get("quality_score"),
        "followability_score": profile.get("followability_score"),
        "followability_confidence": profile.get("followability_confidence"),
        "score_basis": profile.get("score_basis"),
        "score_confidence": profile.get("score_confidence"),
        "confidence_factor": round(confidence_factor, 3),
        "data_completeness": profile.get("data_completeness"),
        "formula": "quality_score = (Σ percentile_rank_i × weight_i / Σ weight_i) × confidence_factor",
        "confidence_formula": "confidence_factor = min(1, √(buy_event_count / 10))",
        "factors": factors,
        "industry": {
            "main_industry": profile.get("main_industry_1"),
            "best_industry": profile.get("best_industry_1"),
            "concentration": profile.get("concentration"),
        },
        "followability": {
            "avg_premium_pct": profile.get("avg_premium_pct"),
            "safe_follow_event_count": profile.get("safe_follow_event_count"),
            "safe_follow_win_rate_30d": profile.get("safe_follow_win_rate_30d"),
            "safe_follow_avg_gain_30d": profile.get("safe_follow_avg_gain_30d"),
            "safe_follow_avg_drawdown_30d": profile.get("safe_follow_avg_drawdown_30d"),
            "premium_discount_event_count": profile.get("premium_discount_event_count"),
            "premium_discount_win_rate_30d": profile.get("premium_discount_win_rate_30d"),
            "premium_near_cost_event_count": profile.get("premium_near_cost_event_count"),
            "premium_near_cost_win_rate_30d": profile.get("premium_near_cost_win_rate_30d"),
            "premium_premium_event_count": profile.get("premium_premium_event_count"),
            "premium_premium_win_rate_30d": profile.get("premium_premium_win_rate_30d"),
            "premium_high_event_count": profile.get("premium_high_event_count"),
            "premium_high_win_rate_30d": profile.get("premium_high_win_rate_30d"),
            "signal_transfer_efficiency_30d": profile.get("signal_transfer_efficiency_30d"),
            "followability_hint": profile.get("followability_hint"),
        },
    }


def load_institution_scoring_breakdown(conn, institution_id: str) -> Optional[dict]:
    profile_row = conn.execute(
        """
        SELECT institution_id, quality_score, total_events,
               followability_score, followability_confidence,
               buy_event_count, buy_avg_gain_30d, buy_avg_gain_60d, buy_avg_gain_120d,
               buy_win_rate_30d, buy_win_rate_60d, buy_win_rate_120d,
               buy_median_max_drawdown_30d, median_gain_30d,
               avg_gain_30d, avg_gain_60d, avg_gain_120d,
               win_rate_30d, win_rate_60d, win_rate_90d,
               median_max_drawdown_30d,
               avg_premium_pct, safe_follow_event_count, safe_follow_win_rate_30d,
               safe_follow_avg_gain_30d, safe_follow_avg_drawdown_30d,
               premium_discount_event_count, premium_discount_win_rate_30d,
               premium_near_cost_event_count, premium_near_cost_win_rate_30d,
               premium_premium_event_count, premium_premium_win_rate_30d,
               premium_high_event_count, premium_high_win_rate_30d,
               signal_transfer_efficiency_30d, followability_hint,
               score_basis, score_confidence,
               main_industry_1, best_industry_1, concentration,
               data_completeness
        FROM mart_institution_profile
        WHERE institution_id = ?
        """,
        (institution_id,),
    ).fetchone()
    if not profile_row:
        return None
    config = load_scoring_config(conn, "scoring.institution")
    return build_institution_scoring_breakdown_payload(
        profile_row,
        object_id=institution_id,
        config=config,
    )