"""Canonical read-side business facts shared by routers and services."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from services.industry import (
    industry_level_db_column,
    industry_level_nonempty_condition,
    industry_level_select,
)


HOLDER_GATE_COUNT_KEYS = (
    "holder_total",
    "holder_follow_count",
    "holder_watch_count",
    "holder_observe_count",
    "holder_avoid_count",
)


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_holder_gate_counts(row: dict | Any | None) -> dict[str, int]:
    """Return a complete holder-gate count payload with stable integer keys."""

    if row is None:
        return {key: 0 for key in HOLDER_GATE_COUNT_KEYS}
    data = dict(row)
    return {key: _as_int(data.get(key)) for key in HOLDER_GATE_COUNT_KEYS}


def _row_to_dict(row: Any, columns: list[str] | None = None) -> dict:
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        if columns is None:
            raise
        return {
            column: row[index] if index < len(row) else None
            for index, column in enumerate(columns)
        }


def _stock_code_filter(stock_codes: Iterable[str] | None) -> tuple[str, tuple[str, ...]]:
    if stock_codes is None:
        return "", ()
    codes = tuple(sorted({str(code).strip() for code in stock_codes if str(code).strip()}))
    if not codes:
        return " WHERE 1 = 0", ()
    placeholders = ", ".join("?" for _ in codes)
    return f" WHERE stock_code IN ({placeholders})", codes


def load_stock_holder_gate_coverage_map(conn, stock_codes: Iterable[str] | None = None) -> dict[str, dict[str, int]]:
    """Load stock-level holder follow_gate coverage from mart_current_relationship."""

    where_sql, params = _stock_code_filter(stock_codes)
    cursor = conn.execute(
        f"""
        SELECT
            stock_code,
            COUNT(*) AS holder_total,
            SUM(CASE WHEN follow_gate = 'follow' THEN 1 ELSE 0 END) AS holder_follow_count,
            SUM(CASE WHEN follow_gate = 'watch' THEN 1 ELSE 0 END) AS holder_watch_count,
            SUM(CASE WHEN follow_gate = 'observe' THEN 1 ELSE 0 END) AS holder_observe_count,
            SUM(CASE WHEN follow_gate = 'avoid' THEN 1 ELSE 0 END) AS holder_avoid_count
        FROM mart_current_relationship
        {where_sql}
        GROUP BY stock_code
        """,
        params,
    )
    rows = cursor.fetchall()
    columns = [desc[0] for desc in getattr(cursor, "description", []) or []] or None
    coverage = {}
    for row in rows:
        data = _row_to_dict(row, columns)
        coverage[data["stock_code"]] = normalize_holder_gate_counts(data)
    return coverage


def _query_keyed_map(conn, query: str, *, key: str, params: tuple = ()) -> dict[str, dict]:
    try:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
    except Exception:
        return {}
    columns = [desc[0] for desc in getattr(cursor, "description", []) or []] or None
    result = {}
    for row in rows:
        data = _row_to_dict(row, columns)
        result[data[key]] = data
    return result


def _fetch_dict_rows(conn, query: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(query, params)
    rows = cursor.fetchall()
    columns = [desc[0] for desc in getattr(cursor, "description", []) or []] or None
    return [_row_to_dict(row, columns) for row in rows]


def _fetch_dict_one(conn, query: str, params: tuple = ()) -> dict:
    cursor = conn.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        return {}
    columns = [desc[0] for desc in getattr(cursor, "description", []) or []] or None
    return _row_to_dict(row, columns)


def _industry_level_expr(level: int, alias: str, *, snapshot: bool = False) -> str:
    return f"{alias}.{industry_level_db_column(level, snapshot=snapshot)}"


def load_sector_active_business_facts_map(conn, *, sector_level: int = 1) -> dict[str, dict]:
    """Load sector-level current relationship breadth facts."""

    return _query_keyed_map(
        conn,
        f"""
        SELECT {industry_level_select(sector_level, alias='rel', result_alias='sector_name')},
               COUNT(DISTINCT institution_id) AS active_institution_count,
               COUNT(DISTINCT stock_code) AS current_stock_count
        FROM mart_current_relationship rel
        WHERE {industry_level_nonempty_condition(sector_level, alias='rel')}
        GROUP BY {_industry_level_expr(sector_level, 'rel')}
        """,
        key="sector_name",
    )


def load_sector_candidate_business_facts_map(conn, *, sector_level: int = 1) -> dict[str, dict]:
    """Load sector-level candidate, score-band, and 20d feedback facts."""

    return _query_keyed_map(
        conn,
        f"""
        SELECT {industry_level_select(sector_level, alias='ctx', result_alias='sector_name')},
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
        WHERE {industry_level_nonempty_condition(sector_level, alias='ctx')}
        GROUP BY {_industry_level_expr(sector_level, 'ctx')}
        """,
        key="sector_name",
    )


def load_institution_scorecard_business_facts(conn) -> dict[str, object]:
    """Load raw institution scorecard distribution facts."""

    summary = _fetch_dict_one(
        conn,
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
        """,
    )
    type_top = _fetch_dict_rows(
        conn,
        """
        SELECT COALESCE(inst_type, '未分类') AS inst_type,
               COUNT(*) AS total,
               AVG(quality_score) AS avg_quality_score,
               AVG(followability_score) AS avg_followability_score
        FROM mart_institution_profile
        GROUP BY COALESCE(inst_type, '未分类')
        ORDER BY COUNT(*) DESC, inst_type
        LIMIT 6
        """,
    )
    hint_top = _fetch_dict_rows(
        conn,
        """
        SELECT COALESCE(followability_hint, '未标注') AS followability_hint,
               COUNT(*) AS total
        FROM mart_institution_profile
        GROUP BY COALESCE(followability_hint, '未标注')
        ORDER BY COUNT(*) DESC, followability_hint
        LIMIT 6
        """,
    )
    confidence_rows = _fetch_dict_rows(
        conn,
        """
        WITH confidence_dist AS (
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
        )
        SELECT metric, confidence, total
        FROM confidence_dist
        ORDER BY metric,
                 total DESC,
                 CASE confidence
                     WHEN 'high' THEN 1
                     WHEN 'medium' THEN 2
                     WHEN 'low' THEN 3
                     WHEN '未标注' THEN 4
                     ELSE 5
                 END,
                 confidence
        """,
    )
    return {
        "summary": summary,
        "type_top": type_top,
        "hint_top": hint_top,
        "confidence_rows": confidence_rows,
    }
