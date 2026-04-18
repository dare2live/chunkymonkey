"""Shared read-side helpers for candidate setup and watchlist payloads.

Keep the stock research workspace lists consistent so candidate setups and the
manual watchlist reuse the same backend-owned stock gate and provenance rules.
"""

from services.industry import attach_industry_aliases, load_industry_map
from services.stock_trends_read import apply_stock_trend_gate


def load_manual_stock_blacklist_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, reason, created_at
        FROM excluded_stocks
        WHERE category = 'MANUAL'
        ORDER BY created_at DESC, stock_code
        """
    ).fetchall()
    return [dict(row) for row in rows]


def load_candidate_setup_rows(conn, limit: int = 200) -> list[dict]:
    excluded_codes = {
        row["stock_code"]
        for row in conn.execute(
            "SELECT stock_code FROM excluded_stocks WHERE category = 'MANUAL'"
        ).fetchall()
    }
    rows = conn.execute(
        """
        SELECT stock_code, stock_name,
               latest_report_date, latest_notice_date, path_state,
               setup_tag, setup_priority, setup_reason, setup_confidence,
               setup_level, setup_inst_id, setup_inst_name, setup_event_type,
               setup_industry_name, setup_score_raw,
               setup_execution_gate, setup_execution_reason,
               industry_skill_raw, industry_skill_grade,
               followability_grade, premium_grade, report_recency_grade,
               reliability_grade, report_age_days,
               discovery_score, company_quality_score, stage_score,
               forecast_score, forecast_score_effective,
               raw_composite_priority_score, composite_priority_score,
               composite_cap_score, composite_cap_reason,
               stock_archetype, priority_pool, priority_pool_reason,
               score_highlights, score_risks,
               crowding_bucket, crowding_yield_raw, crowding_yield_grade,
               crowding_stability_raw, crowding_stability_grade,
               crowding_fit_raw, crowding_fit_grade, crowding_fit_sample,
               crowding_fit_source, qlib_rank
        FROM mart_stock_trend
        WHERE setup_tag IS NOT NULL
        ORDER BY
            CASE COALESCE(priority_pool, '')
                WHEN 'A池' THEN 0
                WHEN 'B池' THEN 1
                WHEN 'C池' THEN 2
                WHEN 'D池' THEN 3
                ELSE 9
            END,
            CASE WHEN composite_priority_score IS NOT NULL THEN 0 ELSE 1 END,
            COALESCE(composite_priority_score, 0) DESC,
            COALESCE(setup_priority, 9),
            COALESCE(setup_score_raw, 0) DESC,
            COALESCE(discovery_score, 0) DESC,
            COALESCE(latest_report_date, '') DESC,
            stock_code
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    industry_map = load_industry_map(conn)
    data = []
    for row in rows:
        item = dict(row)
        if item["stock_code"] in excluded_codes:
            continue
        industry = industry_map.get(item["stock_code"], {})
        attach_industry_aliases(item, industry)
        apply_stock_trend_gate(item)
        data.append(item)
    return data


def load_watchlist_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT w.*,
               CASE WHEN t.stock_code IS NULL THEN 0 ELSE 1 END AS has_trend_row,
               t.setup_tag, t.setup_priority, t.setup_reason, t.setup_confidence,
               t.discovery_score, t.company_quality_score,
               t.company_quality_score_source,
               t.quality_feature_snapshot_date,
               t.stage_score,
               t.forecast_score, t.raw_composite_priority_score, t.composite_priority_score, t.priority_pool,
               t.priority_pool_reason, t.composite_cap_reason,
               t.external_attention_score, t.external_crowding_penalty, t.external_attention_signal,
               t.score_highlights, t.score_risks
        FROM stock_watchlist w
        LEFT JOIN mart_stock_trend t ON w.stock_code = t.stock_code
        ORDER BY
            CASE WHEN w.status = 'active' THEN 0 ELSE 1 END,
            w.added_date DESC
        """
    ).fetchall()
    data = []
    for row in rows:
        item = dict(row)
        has_trend_row = bool(item.pop("has_trend_row", 0))
        if has_trend_row:
            item["company_quality_score_source"] = item.get("company_quality_score_source") or "stock_scoring_v2"
            apply_stock_trend_gate(item)
        else:
            item["company_quality_score_source"] = None
            item["stock_gate"] = None
            item["stock_gate_reason"] = None
        data.append(item)
    return data