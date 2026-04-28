"""Shared read-side helpers for stock detail timeline payloads.

Keep stock detail price/event assembly in one place so the backend owns the
canonical timeline facts and routers only orchestrate the response payload.
"""

import asyncio
from collections import Counter
from datetime import datetime, timedelta
import logging
import math
from typing import Optional

from services.industry import resolve_industry, with_industry_aliases
from services.market_signals import load_margin_balance_overlay


logger = logging.getLogger("cm-api")

_GPCW_COMPLETE_REPORT_MIN_STOCKS = 4000
_TDX_BLOCK_CATEGORY_LABELS = {
    "gn": "概念板块",
    "fg": "风格板块",
    "zs": "指数/行业板块",
}


def empty_price_timeline() -> dict:
    return {
        "points": [],
        "point_count": 0,
        "raw_point_count": 0,
        "start_date": None,
        "end_date": None,
        "start_close": None,
        "end_close": None,
        "high_close": None,
        "low_close": None,
        "change_pct": None,
    }


def empty_tdx_block_payload() -> dict:
    return {
        "categories": [],
        "total_blocks": 0,
        "source": None,
        "updated_at": None,
    }


def load_stock_name(conn, stock_code: str) -> str:
    row = conn.execute(
        """
        SELECT COALESCE(
            (
                SELECT NULLIF(stock_name, '')
                FROM dim_active_a_stock
                WHERE stock_code = ?
                LIMIT 1
            ),
            (
                SELECT NULLIF(stock_name, '')
                FROM fact_top10_holder_period
                WHERE stock_code = ?
                  AND holder_set = 'free'
                  AND NOT is_secondary_class
                  AND NOT is_exit_row
                LIMIT 1
            ),
            ?
        ) AS stock_name
        """,
        (stock_code, stock_code, stock_code),
    ).fetchone()
    if row and row["stock_name"]:
        return row["stock_name"]
    return stock_code


def load_stock_tdx_block_memberships(conn, stock_code: str) -> dict:
    try:
        rows = conn.execute(
            """
            SELECT b.block_category, b.block_name, b.block_type,
                   c.member_count,
                   COALESCE(b.source, c.source) AS source,
                   COALESCE(b.updated_at, c.updated_at) AS updated_at
            FROM dim_stock_tdx_block b
            LEFT JOIN dim_tdx_block_catalog c
              ON c.block_category = b.block_category
             AND c.block_name = b.block_name
            WHERE b.stock_code = ?
            ORDER BY CASE b.block_category
                       WHEN 'gn' THEN 1
                       WHEN 'fg' THEN 2
                       WHEN 'zs' THEN 3
                       ELSE 9
                     END,
                     COALESCE(c.member_count, 0) DESC,
                     b.block_name
            """,
            (stock_code,),
        ).fetchall()
    except Exception:
        logger.exception("load stock tdx blocks failed for %s", stock_code)
        return empty_tdx_block_payload()

    if not rows:
        return empty_tdx_block_payload()

    categories = []
    category_map = {}
    source = None
    updated_at = None
    total_blocks = 0

    for row in rows:
        category = row["block_category"] or "other"
        entry = category_map.get(category)
        if entry is None:
            entry = {
                "category": category,
                "label": _TDX_BLOCK_CATEGORY_LABELS.get(category, category.upper()),
                "count": 0,
                "blocks": [],
            }
            category_map[category] = entry
            categories.append(entry)

        entry["count"] += 1
        total_blocks += 1
        entry["blocks"].append(
            {
                "name": row["block_name"],
                "member_count": row["member_count"],
                "block_type": row["block_type"],
            }
        )

        if not source and row["source"]:
            source = row["source"]
        if row["updated_at"] and (updated_at is None or row["updated_at"] > updated_at):
            updated_at = row["updated_at"]

    return {
        "categories": categories,
        "total_blocks": total_blocks,
        "source": source,
        "updated_at": updated_at,
    }


def load_stock_detail_timeline(conn, stock_code: str, shareholder_change_payload: Optional[dict] = None, years: int = 3) -> dict:
    tdx_quarterly_overlay = _load_tdx_quarterly_overlay(conn, stock_code, years=years)
    base_timeline_events = _load_stock_timeline_events(conn, stock_code, years=years)
    latest_close_row = None
    price_timeline = empty_price_timeline()
    timeline_events = []

    try:
        from services.market_db import get_market_conn

        mkt_conn = get_market_conn()
        try:
            latest_close_row = _latest_daily_close(stock_code, mkt_conn=mkt_conn)
            price_timeline = _load_stock_price_timeline(stock_code, mkt_conn=mkt_conn, years=years)
            timeline_events = merge_timeline_events(
                base_timeline_events,
                _build_tdx_quarterly_events(tdx_quarterly_overlay),
                _load_xdxr_timeline_events(stock_code, mkt_conn=mkt_conn, years=years),
                (shareholder_change_payload or {}).get("events"),
            )
        finally:
            mkt_conn.close()
    except Exception:
        logger.exception("load stock detail price timeline failed for %s", stock_code)

    if not timeline_events:
        timeline_events = merge_timeline_events(
            base_timeline_events,
            _build_tdx_quarterly_events(tdx_quarterly_overlay),
            (shareholder_change_payload or {}).get("events"),
        )

    return {
        "latest_close_row": latest_close_row,
        "price_timeline": price_timeline,
        "tdx_quarterly_overlay": tdx_quarterly_overlay,
        "timeline_events": timeline_events,
    }


async def load_stock_detail_context(
    conn,
    stock_code: str,
    institutions: list[dict],
    shareholder_change_payload: Optional[dict] = None,
) -> dict:
    stock_name = load_stock_name(conn, stock_code)
    industry = resolve_industry(conn, stock_code)
    if industry:
        industry = with_industry_aliases(industry)
    detail_timeline = load_stock_detail_timeline(conn, stock_code, shareholder_change_payload)
    latest_close_row = detail_timeline["latest_close_row"]
    price_timeline = detail_timeline.get("price_timeline") or empty_price_timeline()
    timeline_events = detail_timeline.get("timeline_events") or []
    tdx_quarterly_overlay = detail_timeline.get("tdx_quarterly_overlay") or {}
    tdx_blocks = load_stock_tdx_block_memberships(conn, stock_code)
    margin_balance_overlay = load_margin_balance_overlay(stock_code)
    enriched_institutions = enrich_stock_institutions(institutions, latest_close_row)
    setup_row = load_stock_setup_row(conn, stock_code)
    setup_payload = build_stock_setup_payload(setup_row, enriched_institutions)
    attention = await load_stock_attention_payload(conn, stock_code)
    merged_timeline_events = merge_stock_attention_timeline_events(
        timeline_events,
        attention,
        setup_payload["setup"],
    )
    latest_notice_date = max((inst.get("notice_date") or "") for inst in enriched_institutions) if enriched_institutions else ""

    return {
        "stock_name": stock_name,
        "industry": industry,
        "institutions": enriched_institutions,
        "setup": setup_payload["setup"],
        "stage": setup_payload["stage"],
        "turtle": setup_payload["turtle"],
        "attention": attention,
        "price_timeline": price_timeline,
        "timeline_events": merged_timeline_events,
        "tdx_quarterly_overlay": tdx_quarterly_overlay,
        "tdx_blocks": tdx_blocks,
        "margin_balance_overlay": margin_balance_overlay,
        "shareholder_change_summary": (shareholder_change_payload or {}).get("recent_180d"),
        "latest_close_date": latest_close_row["date"] if latest_close_row else None,
        "latest_notice_date": latest_notice_date or None,
    }


def merge_stock_attention_timeline_events(timeline_events: list[dict], attention: Optional[dict], stock_row: Optional[dict]) -> list[dict]:
    attention_events = _build_attention_timeline_events(attention, stock_row)
    return merge_timeline_events(timeline_events, attention_events)


async def load_stock_attention_payload(conn, stock_code: str) -> dict:
    from services.external_attention import fetch_stock_attention_detail, get_latest_stock_attention

    snapshot = get_latest_stock_attention(conn, stock_code)
    industry_meta = resolve_industry(conn, stock_code)

    detail = {}
    try:
        detail = await asyncio.to_thread(fetch_stock_attention_detail, stock_code)
    except Exception:
        logger.exception("load stock attention payload failed for %s", stock_code)

    basic_info = dict(detail.get("basic_info") or {})
    fallback_name = (snapshot or {}).get("stock_name") or load_stock_name(conn, stock_code)
    fallback_industry = ""
    if industry_meta:
        fallback_industry = (
            industry_meta.get("tdx_l2_name")
            or industry_meta.get("tdx_l1_name")
            or industry_meta.get("tdx_l3_name")
            or ""
        )
    if not basic_info:
        basic_info = {
            "股票代码": detail.get("stock_code") or stock_code,
            "股票简称": fallback_name,
            "行业": fallback_industry,
        }
    else:
        basic_info.setdefault("股票代码", detail.get("stock_code") or stock_code)
        if fallback_name:
            basic_info.setdefault("股票简称", fallback_name)
        if fallback_industry:
            basic_info.setdefault("行业", fallback_industry)

    return {
        "ok": True,
        "stock_code": detail.get("stock_code") or stock_code,
        "stock_name": detail.get("stock_name") or fallback_name,
        "snapshot": snapshot,
        "basic_info": basic_info,
        "series": detail.get("series") or {},
        "research": detail.get("research") or {},
        "news": detail.get("news") or {},
        "timeline_events": detail.get("timeline_events") or [],
        "diagnostics": detail.get("diagnostics") or {},
    }


def enrich_stock_institutions(institutions: list[dict], latest_close_row: Optional[dict]) -> list[dict]:
    latest_close = latest_close_row.get("close") if latest_close_row else None
    latest_close_date = latest_close_row.get("date") if latest_close_row else None

    for inst in institutions or []:
        report_return_to_now = _compute_return_pct(latest_close, inst.get("inst_ref_cost"))
        notice_return_to_now = _safe_number(inst.get("return_to_now"))
        if notice_return_to_now is not None:
            notice_return_to_now = round(notice_return_to_now, 2)
        if notice_return_to_now is None:
            notice_return_to_now = _compute_return_pct(latest_close, inst.get("price_entry"))
        inst["report_return_to_now"] = report_return_to_now
        inst["notice_return_to_now"] = notice_return_to_now
        inst["notice_return_status"] = None if notice_return_to_now is not None else "待最新收盘"
        inst["latest_close_date"] = latest_close_date
    return institutions or []


def load_stock_setup_row(conn, stock_code: str):
    return conn.execute(
        """
        SELECT t.setup_tag, t.setup_priority, t.setup_reason, t.setup_confidence,
               t.setup_level, t.setup_inst_id, t.setup_inst_name, t.setup_event_type,
               t.setup_industry_name, t.setup_score_raw, t.setup_execution_gate, t.setup_execution_reason,
               t.leader_inst, t.leader_score, t.consensus_count,
               t.industry_skill_raw,
               t.industry_skill_grade, t.followability_grade, t.premium_grade,
               t.report_recency_grade, t.reliability_grade, t.crowding_bucket,
               t.crowding_yield_raw, t.crowding_yield_grade,
               t.crowding_stability_raw, t.crowding_stability_grade,
               t.crowding_fit_raw, t.crowding_fit_grade, t.crowding_fit_sample,
               t.crowding_fit_source, t.report_age_days,
               t.path_state, t.latest_report_date, t.latest_notice_date,
               t.discovery_score, t.company_quality_score, t.stage_score,
               t.raw_composite_priority_score,
               t.composite_priority_score, t.composite_cap_score, t.composite_cap_reason,
               t.stock_archetype, t.priority_pool, t.priority_pool_reason,
               t.attention_comment_trade_date, t.attention_focus_index, t.attention_composite_score,
               t.attention_institution_participation, t.attention_turnover_rate, t.attention_rank_change,
               t.attention_survey_count_30d, t.attention_survey_count_90d,
               t.attention_survey_org_total_30d, t.attention_survey_org_total_90d,
               t.external_attention_score, t.external_crowding_penalty, t.external_attention_signal,
               t.turtle_execution_score, t.turtle_breakout_score,
               t.turtle_risk_score, t.turtle_score_delta,
               t.turtle_setup_state, t.turtle_preferred_system,
               t.turtle_reason,
               t.score_highlights, t.score_risks,
               st.path_max_gain_pct, st.path_max_drawdown_pct,
               st.generic_stage_raw, st.stage_type_adjust_raw, st.stage_reason,
               st.return_1m, st.return_3m, st.return_6m, st.return_12m,
               st.amount_ratio_20_120, st.volatility_20d, st.amplitude_20d,
               st.stock_gate, st.gate_follow_count, st.gate_watch_count,
               st.gate_observe_count, st.gate_avoid_count,
               st.stage_quality_continuity_raw, st.stage_quality_trend_raw,
               st.stage_quality_overheat_penalty, st.stage_growth_continuity_raw,
               st.stage_growth_slowdown_penalty, st.stage_growth_stretch_penalty,
               st.stage_cycle_recovery_raw, st.stage_cycle_realization_penalty,
               st.stage_cycle_uncertainty_penalty,
               st.max_drawdown_60d, st.dist_ma250_pct, st.above_ma250,
               q.snapshot_date AS quality_snapshot_date,
               q.latest_financial_report_date AS quality_latest_financial_report_date,
               q.latest_indicator_report_date AS quality_latest_indicator_report_date,
               q.roe, q.roa_ak, q.gross_margin, q.ocf_to_profit,
               q.debt_ratio, q.current_ratio, q.contract_to_revenue,
               q.revenue_growth_yoy_ak, q.net_profit_growth_yoy_ak,
               q.dividend_financing_ratio, q.future_unlock_ratio_180d,
               q.holder_count_change_pct, q.total_shares_growth_3y,
               q.net_profit_positive_8q, q.operating_cashflow_positive_8q,
               q.revenue_yoy_positive_4q, q.profit_yoy_positive_4q,
               q.quality_profit_raw, q.quality_cash_raw, q.quality_balance_raw,
               q.quality_margin_raw, q.quality_contract_raw, q.quality_freshness_raw,
               q.quality_capital_raw, q.quality_efficiency_raw, q.quality_growth_raw,
               q.quality_score_v1,
               tf.snapshot_date AS turtle_snapshot_date,
               tf.latest_trade_date, tf.close_price, tf.atr_14, tf.atr_14_pct,
               tf.entry_level_20, tf.entry_level_55, tf.exit_level_10, tf.exit_level_20,
               tf.breakout_dist_20_pct, tf.breakout_dist_55_pct,
               tf.exit_dist_10_pct, tf.exit_dist_20_pct,
               tf.stop_level_20_2n, tf.stop_level_55_2n,
               tf.add_level_20_1, tf.add_level_20_2, tf.add_level_20_3,
               tf.add_level_55_1, tf.add_level_55_2, tf.add_level_55_3,
               tf.entry_signal_20, tf.entry_signal_55, tf.exit_signal_10, tf.exit_signal_20
        FROM mart_stock_trend t
        LEFT JOIN dim_stock_stage_latest st ON st.stock_code = t.stock_code
        LEFT JOIN dim_stock_quality_latest q ON q.stock_code = t.stock_code
        LEFT JOIN dim_stock_turtle_latest tf ON tf.stock_code = t.stock_code
        WHERE t.stock_code = ?
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()


def build_stock_setup_payload(setup_row: Optional[dict], institutions: list[dict]) -> dict:
    if not setup_row:
        return {"setup": None, "stage": None, "turtle": None}

    setup = dict(setup_row)
    quality_report_date = str(setup.get("quality_latest_financial_report_date") or "").strip()
    stock_report_date = str(setup.get("latest_report_date") or "").strip()
    quality_score_v1 = _safe_number(setup.get("quality_score_v1"))
    company_quality_score = _safe_number(setup.get("company_quality_score"))
    is_quality_snapshot_fresh = (
        quality_score_v1 is not None
        and (not stock_report_date or quality_report_date == stock_report_date)
    )
    setup["company_quality_score_source"] = (
        "quality_feature_v1"
        if is_quality_snapshot_fresh
        and company_quality_score is not None
        and abs(company_quality_score - quality_score_v1) <= 0.01
        else "stock_scoring_v2"
    )

    source = next((inst for inst in institutions or [] if inst.get("institution_id") == setup.get("setup_inst_id")), None)
    if source:
        setup["setup_follow_gate"] = source.get("follow_gate")
        setup["setup_follow_gate_reason"] = source.get("follow_gate_reason")
        setup["setup_premium_pct"] = source.get("premium_pct")
        setup["setup_premium_bucket"] = source.get("premium_bucket")
        setup["setup_report_return_to_now"] = source.get("report_return_to_now")
        setup["setup_notice_return_to_now"] = source.get("notice_return_to_now")

    return {
        "setup": setup,
        "stage": extract_stage_payload(setup),
        "turtle": extract_turtle_payload(setup),
    }


def _format_money_amount(value: object) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    if abs(number) >= 1e8:
        return f"{number / 1e8:.2f}亿"
    if abs(number) >= 1e4:
        return f"{number / 1e4:.1f}万"
    return f"{number:.0f}"


def extract_stage_payload(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    fields = [
        "path_state",
        "path_max_gain_pct",
        "path_max_drawdown_pct",
        "generic_stage_raw",
        "stage_type_adjust_raw",
        "stage_score_v1",
        "stage_reason",
        "max_drawdown_60d",
        "dist_ma250_pct",
        "above_ma250",
    ]
    payload = {field: row.get(field) for field in fields if field in row}
    return payload if any(value is not None for value in payload.values()) else None


def extract_turtle_payload(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    fields = [
        "turtle_execution_score",
        "turtle_breakout_score",
        "turtle_risk_score",
        "turtle_score_delta",
        "turtle_setup_state",
        "turtle_preferred_system",
        "turtle_reason",
        "turtle_snapshot_date",
        "latest_trade_date",
        "close_price",
        "atr_14",
        "atr_14_pct",
        "entry_level_20",
        "entry_level_55",
        "exit_level_10",
        "exit_level_20",
        "breakout_dist_20_pct",
        "breakout_dist_55_pct",
        "exit_dist_10_pct",
        "exit_dist_20_pct",
        "stop_level_20_2n",
        "stop_level_55_2n",
        "add_level_20_1",
        "add_level_20_2",
        "add_level_20_3",
        "add_level_55_1",
        "add_level_55_2",
        "add_level_55_3",
        "entry_signal_20",
        "entry_signal_55",
        "exit_signal_10",
        "exit_signal_20",
    ]
    payload = {field: row.get(field) for field in fields if field in row}
    return payload if any(value is not None for value in payload.values()) else None


def _latest_daily_close(stock_code: str, mkt_conn=None):
    from services.market_db import get_market_conn

    own_conn = mkt_conn is None
    if own_conn:
        mkt_conn = get_market_conn()
    try:
        row = mkt_conn.execute(
            "SELECT date, close FROM price_kline "
            "WHERE code=? AND freq='daily' AND adjust='qfq' "
            "ORDER BY date DESC LIMIT 1",
            (stock_code,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if own_conn and mkt_conn is not None:
            mkt_conn.close()


def _load_stock_price_timeline(stock_code: str, mkt_conn=None, years: int = 3, max_points: int = 260) -> dict:
    from services.market_db import get_market_conn

    own_conn = mkt_conn is None
    if own_conn:
        mkt_conn = get_market_conn()
    try:
        since_date = (datetime.utcnow() - timedelta(days=max(years, 1) * 370)).strftime("%Y%m%d")
        rows = mkt_conn.execute(
            "SELECT date, close FROM price_kline "
            "WHERE code=? AND freq='daily' AND adjust='qfq' AND date>=? "
            "ORDER BY date",
            (stock_code, since_date),
        ).fetchall()
        points = []
        for row in rows:
            close_value = row["close"]
            if close_value is None:
                continue
            try:
                close_number = round(float(close_value), 2)
            except Exception:
                continue
            points.append({"date": row["date"], "close": close_number})
        raw_point_count = len(points)
        if len(points) > max_points:
            step = max(1, (len(points) + max_points - 1) // max_points)
            sampled = points[::step]
            if sampled and sampled[-1]["date"] != points[-1]["date"]:
                sampled.append(points[-1])
            points = sampled
        if not points:
            return empty_price_timeline() | {"raw_point_count": raw_point_count}
        closes = [point["close"] for point in points]
        start_close = points[0]["close"]
        end_close = points[-1]["close"]
        change_pct = None
        if start_close not in (None, 0):
            change_pct = round((end_close - start_close) / start_close * 100, 2)
        return {
            "points": points,
            "point_count": len(points),
            "raw_point_count": raw_point_count,
            "start_date": points[0]["date"],
            "end_date": points[-1]["date"],
            "start_close": start_close,
            "end_close": end_close,
            "high_close": round(max(closes), 2),
            "low_close": round(min(closes), 2),
            "change_pct": change_pct,
        }
    finally:
        if own_conn and mkt_conn is not None:
            mkt_conn.close()


def _normalize_timeline_date(value: object) -> Optional[str]:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _timeline_sort_value(value: Optional[str]) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:14]


def _event_type_label(event_type: Optional[str]) -> str:
    return {
        "new_entry": "新进",
        "increase": "增持",
        "decrease": "减持",
        "exit": "退出",
        "unchanged": "持仓不变",
    }.get(event_type or "", event_type or "动作")


def _summarize_inst_names(names: list[str], limit: int = 2) -> str:
    ordered = []
    seen = set()
    for name in names:
        text = str(name or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    if not ordered:
        return ""
    if len(ordered) <= limit:
        return "、".join(ordered)
    return "、".join(ordered[:limit]) + f" 等{len(ordered)}家"


def _event_mix_text(counter: Counter) -> str:
    parts = []
    for key in ("new_entry", "increase", "decrease", "unchanged", "exit"):
        count = int(counter.get(key) or 0)
        if not count:
            continue
        parts.append(f"{_event_type_label(key)} {count}家")
    return " · ".join(parts)


def _load_stock_timeline_events(conn, stock_code: str, years: int = 3) -> list[dict]:
    since_report_date = (datetime.utcnow() - timedelta(days=max(years, 1) * 370)).strftime("%Y%m%d")
    rows = conn.execute(
        """
        SELECT e.report_date,
               e.notice_date,
               e.event_type,
               COALESCE(NULLIF(i.display_name, ''), i.name, e.holder_name) AS inst_name
        FROM fact_institution_event e
        LEFT JOIN inst_institutions i ON e.institution_id = i.id
        WHERE e.stock_code = ?
          AND e.report_date >= ?
        ORDER BY e.report_date, COALESCE(e.notice_date, ''), inst_name
        """,
        (stock_code, since_report_date),
    ).fetchall()
    if not rows:
        return []

    report_groups: dict[str, dict] = {}
    notice_groups: dict[str, dict] = {}
    for row in rows:
        inst_name = str(row["inst_name"] or "").strip()
        event_type = row["event_type"] or "unchanged"

        report_date = _normalize_timeline_date(row["report_date"])
        if report_date:
            group = report_groups.setdefault(report_date, {"names": [], "actions": Counter(), "count": 0})
            group["count"] += 1
            group["actions"][event_type] += 1
            if inst_name:
                group["names"].append(inst_name)

        notice_date = _normalize_timeline_date(row["notice_date"])
        if notice_date:
            group = notice_groups.setdefault(notice_date, {"names": [], "actions": Counter(), "count": 0})
            group["count"] += 1
            group["actions"][event_type] += 1
            if inst_name:
                group["names"].append(inst_name)

    events = []
    for report_date, payload in report_groups.items():
        body_parts = [f"{payload['count']} 家机构进入该报告期"]
        mix_text = _event_mix_text(payload["actions"])
        if mix_text:
            body_parts.append(mix_text)
        names_text = _summarize_inst_names(payload["names"])
        if names_text:
            body_parts.append(f"代表 {names_text}")
        events.append({
            "date": report_date,
            "lane": "report",
            "tone": "report",
            "title": "机构报告期",
            "body": " · ".join(body_parts),
        })

    for notice_date, payload in notice_groups.items():
        body_parts = [f"{payload['count']} 家机构完成公告披露"]
        mix_text = _event_mix_text(payload["actions"])
        if mix_text:
            body_parts.append(mix_text)
        names_text = _summarize_inst_names(payload["names"])
        if names_text:
            body_parts.append(f"代表 {names_text}")
        events.append({
            "date": notice_date,
            "lane": "notice",
            "tone": "notice",
            "title": "公告披露",
            "body": " · ".join(body_parts),
        })

    events.sort(key=lambda item: (_timeline_sort_value(item.get("date")), item.get("lane") or ""))
    return events


def _safe_number(value: object) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _compute_return_pct(current_price: object, base_price: object) -> Optional[float]:
    current_number = _safe_number(current_price)
    base_number = _safe_number(base_price)
    if current_number is None or base_number in (None, 0):
        return None
    return round((current_number - base_number) / base_number * 100, 2)


def _delta_value(current: object, previous: object) -> Optional[float]:
    current_number = _safe_number(current)
    previous_number = _safe_number(previous)
    if current_number is None or previous_number is None:
        return None
    return round(current_number - previous_number, 2)


def _pct_change(current: object, previous: object) -> Optional[float]:
    current_number = _safe_number(current)
    previous_number = _safe_number(previous)
    if current_number is None or previous_number in (None, 0):
        return None
    return round((current_number - previous_number) / previous_number * 100, 2)


def _signed_text(value: Optional[float], decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{decimals}f}{suffix}"


def _format_household_count(value: object) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    if abs(number) >= 10000:
        return f"{number / 10000:.1f}万户"
    return f"{number:.0f}户"


def _format_share_count(value: object) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    if abs(number) >= 1e8:
        return f"{number / 1e8:.2f}亿股"
    if abs(number) >= 1e4:
        return f"{number / 1e4:.1f}万股"
    return f"{number:.0f}股"


def _format_wan_shares(value: object) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    if abs(number) >= 10000:
        return f"{number / 10000:.2f}亿股"
    return f"{number:.0f}万股"


def _load_gpcw_report_coverage(conn, limit: int = 16) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT report_date, COUNT(*) AS stock_count FROM raw_gpcw_detail GROUP BY report_date ORDER BY report_date DESC LIMIT ?",
            (max(limit, 1),),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "report_date": row["report_date"],
            "date": _normalize_timeline_date(row["report_date"]),
            "stock_count": int(row["stock_count"] or 0),
        }
        for row in rows
    ]


def _load_tdx_quarterly_overlay(conn, stock_code: str, years: int = 3) -> dict:
    target_quarters = max(years * 4, 4)
    since_date = (datetime.utcnow() - timedelta(days=max(years, 1) * 370)).strftime("%Y-%m-%d")
    try:
        rows = conn.execute(
            """
            SELECT report_date,
                   holder_count,
                   inst_total_count,
                   fund_count,
                   insurance_count,
                   broker_count,
                   social_security_count,
                   qfii_count,
                   national_team_shares_wan,
                   total_shares
            FROM raw_gpcw_detail
            WHERE stock_code = ?
              AND report_date >= ?
            ORDER BY report_date
            """,
            (stock_code, since_date),
        ).fetchall()
    except Exception:
        rows = []

    series = []
    previous = None
    for row in rows:
        item = {
            "report_date": row["report_date"],
            "date": _normalize_timeline_date(row["report_date"]),
            "holder_count": _safe_number(row["holder_count"]),
            "inst_total_count": _safe_number(row["inst_total_count"]),
            "fund_count": _safe_number(row["fund_count"]),
            "insurance_count": _safe_number(row["insurance_count"]),
            "broker_count": _safe_number(row["broker_count"]),
            "social_security_count": _safe_number(row["social_security_count"]),
            "qfii_count": _safe_number(row["qfii_count"]),
            "national_team_shares_wan": _safe_number(row["national_team_shares_wan"]),
            "total_shares": _safe_number(row["total_shares"]),
        }
        if previous:
            item["holder_count_delta"] = _delta_value(item["holder_count"], previous.get("holder_count"))
            item["holder_count_delta_pct"] = _pct_change(item["holder_count"], previous.get("holder_count"))
            item["inst_total_count_delta"] = _delta_value(item["inst_total_count"], previous.get("inst_total_count"))
            item["fund_count_delta"] = _delta_value(item["fund_count"], previous.get("fund_count"))
            item["insurance_count_delta"] = _delta_value(item["insurance_count"], previous.get("insurance_count"))
            item["broker_count_delta"] = _delta_value(item["broker_count"], previous.get("broker_count"))
            item["social_security_count_delta"] = _delta_value(item["social_security_count"], previous.get("social_security_count"))
            item["qfii_count_delta"] = _delta_value(item["qfii_count"], previous.get("qfii_count"))
            item["national_team_shares_wan_delta"] = _delta_value(item["national_team_shares_wan"], previous.get("national_team_shares_wan"))
            item["total_shares_delta_pct"] = _pct_change(item["total_shares"], previous.get("total_shares"))
        series.append(item)
        previous = item

    coverage = _load_gpcw_report_coverage(conn, limit=max(target_quarters + 4, 12))
    latest_available = coverage[0] if coverage else None
    latest_complete = next(
        (item for item in coverage if int(item.get("stock_count") or 0) >= _GPCW_COMPLETE_REPORT_MIN_STOCKS),
        latest_available,
    )
    note_parts = [
        "TDXHub 已接入 gpcw 季度股东人数、机构类型、国家队持股与 xdxr 除权除息/股本变化。",
        "融资余额、融券余额与高管/股东增减持当前不在本地 TDX 落库口径内。",
    ]
    if latest_available and latest_complete and latest_available["report_date"] != latest_complete["report_date"]:
        note_parts.append(
            f"最新可用季度 {latest_available['report_date']} 当前仅覆盖 {latest_available['stock_count']} 只股票，联动摘要暂退回 {latest_complete['report_date']}。"
        )

    return {
        "source": "tdxhub_gpcw_xdxr",
        "target_quarters": target_quarters,
        "quarters_loaded": len(series),
        "latest_report_date": series[-1]["report_date"] if series else None,
        "latest_complete_report_date": latest_complete["report_date"] if latest_complete else None,
        "latest_complete_stock_count": latest_complete["stock_count"] if latest_complete else None,
        "latest_available_report_date": latest_available["report_date"] if latest_available else None,
        "latest_available_stock_count": latest_available["stock_count"] if latest_available else None,
        "capability_note": " ".join(note_parts),
        "series": series,
    }


def _build_tdx_quarterly_events(overlay: Optional[dict]) -> list[dict]:
    events = []
    for item in (overlay or {}).get("series") or []:
        parts = []
        if item.get("holder_count") is not None:
            text = f"股东 {_format_household_count(item['holder_count'])}"
            if item.get("holder_count_delta_pct") is not None:
                text += f" ({_signed_text(item['holder_count_delta_pct'], 1, '%')})"
            parts.append(text)
        if item.get("inst_total_count") is not None:
            text = f"机构 {int(round(item['inst_total_count']))}家"
            if item.get("inst_total_count_delta") not in (None, 0):
                text += f" ({_signed_text(item['inst_total_count_delta'], 0, '家')})"
            parts.append(text)
        if item.get("fund_count") is not None:
            text = f"基金 {int(round(item['fund_count']))}家"
            if item.get("fund_count_delta") not in (None, 0):
                text += f" ({_signed_text(item['fund_count_delta'], 0, '家')})"
            parts.append(text)
        if item.get("national_team_shares_wan") is not None:
            text = f"国家队 {_format_wan_shares(item['national_team_shares_wan'])}"
            parts.append(text)
        body = " · ".join(part for part in parts if part)
        if not body or not item.get("date"):
            continue
        events.append({
            "date": item["date"],
            "lane": "tdx",
            "tone": "tdx",
            "title": "TDX 季度结构",
            "body": body,
            "shortLabel": "TDX",
        })
    return events


def _load_xdxr_timeline_events(stock_code: str, mkt_conn=None, years: int = 3) -> list[dict]:
    from services.market_db import get_market_conn

    own_conn = mkt_conn is None
    if own_conn:
        mkt_conn = get_market_conn()
    try:
        since_date = (datetime.utcnow() - timedelta(days=max(years, 1) * 370)).strftime("%Y%m%d")
        rows = mkt_conn.execute(
            """
            SELECT date,
                   category,
                   name,
                   fenhong,
                   peigujia,
                   peigu,
                   songzhuangu,
                   panqianliutong,
                   panhouliutong,
                   qianzongguben,
                   houzongguben
            FROM price_xdxr
            WHERE code = ?
              AND date >= ?
            ORDER BY date
            """,
            (stock_code, since_date),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        if own_conn and mkt_conn is not None:
            mkt_conn.close()

    events = []
    for row in rows:
        body_parts = []
        if _safe_number(row["fenhong"]):
            body_parts.append(f"分红 {float(row['fenhong']):.2f}")
        if _safe_number(row["songzhuangu"]):
            body_parts.append(f"送转 {float(row['songzhuangu']):.2f}")
        if _safe_number(row["peigu"]):
            body_parts.append(f"配股 {float(row['peigu']):.2f}")
        if _safe_number(row["peigujia"]):
            body_parts.append(f"配股价 {float(row['peigujia']):.2f}")
        if row["qianzongguben"] and row["houzongguben"] and row["qianzongguben"] != row["houzongguben"]:
            body_parts.append(f"总股本 {_format_share_count(row['qianzongguben'])} -> {_format_share_count(row['houzongguben'])}")
        elif row["panqianliutong"] and row["panhouliutong"] and row["panqianliutong"] != row["panhouliutong"]:
            body_parts.append(f"流通股 {_format_share_count(row['panqianliutong'])} -> {_format_share_count(row['panhouliutong'])}")
        title = str(row["name"] or "资本事项").strip() or "资本事项"
        events.append({
            "date": _normalize_timeline_date(row["date"]),
            "lane": "capital",
            "tone": "capital",
            "title": title,
            "body": " · ".join(body_parts) or title,
            "shortLabel": title,
        })
    return events


def merge_timeline_events(*groups) -> list[dict]:
    events = []
    seen = set()
    for group in groups:
        for item in group or []:
            date_text = _normalize_timeline_date(item.get("date"))
            title = str(item.get("title") or "").strip()
            body = str(item.get("body") or "").strip()
            if not date_text or not title or not body:
                continue
            lane = str(item.get("lane") or "notice")
            tone = str(item.get("tone") or lane)
            key = (date_text, lane, title, body)
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "date": date_text,
                "lane": lane,
                "tone": tone,
                "title": title,
                "body": body,
                "shortLabel": item.get("shortLabel") or title,
            })
    events.sort(key=lambda item: (_timeline_sort_value(item.get("date")), item.get("lane") or ""))
    return events


def _build_attention_timeline_events(attention: Optional[dict], stock_row: Optional[dict]) -> list[dict]:
    payload = attention or {}
    snapshot = payload.get("snapshot") or {}
    research = payload.get("research") or {}
    news = payload.get("news") or {}
    stock_row = stock_row or {}

    events = list(payload.get("timeline_events") or [])

    comment_date = _normalize_timeline_date(
        stock_row.get("attention_comment_trade_date") or snapshot.get("comment_trade_date")
    )
    if comment_date:
        attention_score = _safe_number(stock_row.get("external_attention_score"))
        if attention_score is None:
            attention_score = _safe_number(snapshot.get("composite_score"))
        focus_index = _safe_number(stock_row.get("attention_focus_index"))
        if focus_index is None:
            focus_index = _safe_number(snapshot.get("focus_index"))
        body_parts = [
            str(stock_row.get("external_attention_signal") or "").strip() or "外部覆盖",
        ]
        if attention_score is not None:
            body_parts.append(f"确认 {attention_score:.1f}")
        if focus_index is not None:
            body_parts.append(f"关注 {focus_index:.1f}")
        events.append({
            "date": comment_date,
            "lane": "signal",
            "tone": "signal",
            "title": "外部关注",
            "body": " · ".join(body_parts),
            "shortLabel": "外部",
        })

    survey_date = _normalize_timeline_date(
        snapshot.get("last_survey_date") or snapshot.get("last_survey_notice_date")
    )
    if survey_date:
        survey_30 = int(
            _safe_number(stock_row.get("attention_survey_count_30d"))
            or _safe_number(snapshot.get("survey_count_30d"))
            or 0
        )
        survey_90 = int(
            _safe_number(stock_row.get("attention_survey_count_90d"))
            or _safe_number(snapshot.get("survey_count_90d"))
            or 0
        )
        body_parts = []
        if survey_30:
            body_parts.append(f"30天 {survey_30} 次")
        if survey_90:
            body_parts.append(f"90天 {survey_90} 次")
        if snapshot.get("last_survey_reception"):
            body_parts.append(f"方式 {snapshot['last_survey_reception']}")
        events.append({
            "date": survey_date,
            "lane": "survey",
            "tone": "survey",
            "title": "机构调研",
            "body": " · ".join(body_parts) or "机构调研更新",
            "shortLabel": "调研",
        })

    if not payload.get("timeline_events") and research.get("latest_date"):
        body_parts = []
        if research.get("count_90d") is not None:
            body_parts.append(f"90天 {int(research['count_90d'] or 0)} 篇")
        if research.get("count_30d") is not None:
            body_parts.append(f"30天 {int(research['count_30d'] or 0)} 篇")
        events.append({
            "date": research.get("latest_date"),
            "lane": "research",
            "tone": "research",
            "title": "个股研报",
            "body": " · ".join(body_parts) or "个股研报更新",
            "shortLabel": "研报",
        })

    if not payload.get("timeline_events") and news.get("latest_time"):
        body = "新闻脉冲更新"
        if news.get("count_30d") is not None:
            body = f"30天 {int(news['count_30d'] or 0)} 条"
        events.append({
            "date": news.get("latest_time"),
            "lane": "news",
            "tone": "news",
            "title": "新闻脉冲",
            "body": body,
            "shortLabel": "新闻",
        })

    return events