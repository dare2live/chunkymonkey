"""Shared read-side helpers for stock trend payloads.

Keep stock research summary and gate semantics in one place so the backend
owns the canonical facts and the frontend only renders them.
"""

from typing import Optional

from services.industry import attach_industry_aliases
from services.scoring import derive_stock_gate_from_priority


def _top_count_entries(counter: dict, limit: int) -> list[dict]:
    entries = []
    for key, count in (counter or {}).items():
        entries.append({"key": key, "count": int(count or 0)})
    entries.sort(key=lambda item: (-item["count"], str(item["key"] or "")))
    return entries[: max(int(limit or 0), 0)]


def has_stock_attention_coverage(item: dict) -> bool:
    return bool(
        item.get("external_attention_signal")
        or item.get("external_attention_score") is not None
        or item.get("attention_focus_index") is not None
        or item.get("attention_comment_trade_date")
        or item.get("attention_composite_score") is not None
    )


def turtle_state_group(state: Optional[str]) -> Optional[str]:
    return {
        "S2突破触发": "breakout",
        "S1突破触发": "breakout",
        "S2待突破": "watch",
        "S1待突破": "watch",
        "20日退出触发": "exit",
        "10日退出触发": "exit",
        "等待形态": "waiting",
    }.get(state or "", "covered" if state else None)


def stock_source_name(item: dict) -> str:
    return str(item.get("display_inst_name") or item.get("setup_inst_name") or "").strip()


def apply_stock_trend_gate(item: dict) -> dict:
    stock_gate, stock_gate_reason = derive_stock_gate_from_priority(
        item.get("priority_pool"),
        item.get("composite_priority_score"),
        item.get("priority_pool_reason"),
    )
    item["stock_gate"] = stock_gate
    item["stock_gate_reason"] = stock_gate_reason
    return item


def load_stock_trends_payload(conn) -> dict:
    from services.industry import load_industry_map
    from services.screening_read import load_dual_confirm_snapshot_map, load_screening_snapshot_map

    blacklist_rows = conn.execute(
        """
        SELECT e.stock_code,
               COALESCE(
                   NULLIF(e.stock_name, ''),
                   d.stock_name,
                   (
                       SELECT mr.stock_name
                       FROM market_raw_holdings mr
                       WHERE mr.stock_code = e.stock_code
                       ORDER BY mr.report_date DESC, mr.notice_date DESC
                       LIMIT 1
                   ),
                   e.stock_code
               ) AS stock_name,
               e.reason,
               e.created_at
        FROM excluded_stocks e
        LEFT JOIN dim_active_a_stock d ON d.stock_code = e.stock_code
        WHERE e.category = 'MANUAL'
        ORDER BY e.created_at DESC
        """
    ).fetchall()
    blacklist_map = {row["stock_code"]: dict(row) for row in blacklist_rows}

    rows = conn.execute(
        """
        SELECT t.stock_code,
               t.stock_name,
               t.price_trend,
               t.latest_report_date,
               t.latest_notice_date,
               t.path_state,
               t.setup_tag,
               t.setup_priority,
               t.setup_reason,
               t.setup_confidence,
               t.setup_level,
               t.setup_inst_id,
               t.setup_inst_name,
               t.setup_event_type,
               t.setup_industry_name,
               t.setup_score_raw,
               t.industry_skill_raw,
               t.industry_skill_grade,
               t.followability_grade,
               t.premium_grade,
               t.report_recency_grade,
               t.reliability_grade,
               t.discovery_score,
               t.company_quality_score,
               t.stage_score,
               t.raw_composite_priority_score,
               t.composite_priority_score,
               t.composite_cap_score,
               t.composite_cap_reason,
               t.stock_archetype,
               t.priority_pool,
               t.priority_pool_reason,
               t.attention_comment_trade_date,
               t.attention_focus_index,
               t.attention_composite_score,
               t.attention_institution_participation,
               t.attention_turnover_rate,
               t.attention_rank_change,
               t.attention_survey_count_30d,
               t.attention_survey_count_90d,
               t.attention_survey_org_total_30d,
               t.attention_survey_org_total_90d,
               t.external_attention_score,
               t.external_crowding_penalty,
               t.external_attention_signal,
               t.turtle_execution_score,
               t.turtle_breakout_score,
               t.turtle_risk_score,
               t.turtle_score_delta,
               t.turtle_setup_state,
               t.turtle_preferred_system,
               t.turtle_reason,
               t.score_highlights,
               t.score_risks,
               COALESCE(
                   ii_setup.display_name,
                   ii_leader.display_name,
                   t.setup_inst_name,
                   ii_leader.name,
                   t.leader_inst
               ) AS display_inst_name,
               st.generic_stage_raw,
               st.stage_type_adjust_raw,
               st.stage_reason,
               st.path_max_gain_pct,
               st.path_max_drawdown_pct,
               st.max_drawdown_60d,
               st.dist_ma250_pct,
               st.above_ma250
        FROM mart_stock_trend t
        LEFT JOIN inst_institutions ii_setup ON ii_setup.id = t.setup_inst_id
        LEFT JOIN inst_institutions ii_leader ON ii_leader.id = t.leader_inst
        LEFT JOIN dim_stock_stage_latest st ON st.stock_code = t.stock_code
        ORDER BY
            CASE COALESCE(t.priority_pool, '')
                WHEN 'A池' THEN 0
                WHEN 'B池' THEN 1
                WHEN 'C池' THEN 2
                WHEN 'D池' THEN 3
                ELSE 9
            END,
            CASE WHEN t.composite_priority_score IS NOT NULL THEN 0 ELSE 1 END,
            COALESCE(t.composite_priority_score, 0) DESC,
            CASE WHEN t.setup_tag IS NOT NULL THEN 0 ELSE 1 END,
            COALESCE(t.setup_priority, 9),
            COALESCE(t.discovery_score, 0) DESC,
            COALESCE(t.setup_score_raw, 0) DESC,
            t.stock_code
        """
    ).fetchall()

    coverage_rows = conn.execute(
        """
        SELECT
            stock_code,
            COUNT(*) AS holder_total,
            SUM(CASE WHEN follow_gate = 'follow' THEN 1 ELSE 0 END) AS holder_follow_count,
            SUM(CASE WHEN follow_gate = 'watch' THEN 1 ELSE 0 END) AS holder_watch_count,
            SUM(CASE WHEN follow_gate = 'observe' THEN 1 ELSE 0 END) AS holder_observe_count,
            SUM(CASE WHEN follow_gate = 'avoid' THEN 1 ELSE 0 END) AS holder_avoid_count
        FROM mart_current_relationship
        GROUP BY stock_code
        """
    ).fetchall()
    coverage_map = {row["stock_code"]: dict(row) for row in coverage_rows}
    industry_map = load_industry_map(conn)
    screening_map = load_screening_snapshot_map(conn)
    dual_confirm_map = load_dual_confirm_snapshot_map(conn)
    return build_stock_trends_payload(
        rows,
        blacklist_map=blacklist_map,
        coverage_map=coverage_map,
        industry_map=industry_map,
        screening_map=screening_map,
        dual_confirm_map=dual_confirm_map,
    )


def build_stock_trends_payload(
    rows: list,
    blacklist_map: Optional[dict] = None,
    coverage_map: Optional[dict] = None,
    industry_map: Optional[dict] = None,
    screening_map: Optional[dict] = None,
    dual_confirm_map: Optional[dict] = None,
) -> dict:
    blacklist_map = blacklist_map or {}
    coverage_map = coverage_map or {}
    industry_map = industry_map or {}
    screening_map = screening_map or {}
    dual_confirm_map = dual_confirm_map or {}

    result = []
    seen = set()

    for row in rows or []:
        item = build_stock_trend_item(
            row,
            blacklist=blacklist_map.get(dict(row).get("stock_code")),
            coverage=coverage_map.get(dict(row).get("stock_code")),
            industry=industry_map.get(dict(row).get("stock_code")),
            screening=screening_map.get(dict(row).get("stock_code")),
            dual_confirm=dual_confirm_map.get(dict(row).get("stock_code")),
        )
        result.append(item)
        seen.add(item.get("stock_code"))

    for code, blacklist in blacklist_map.items():
        if code in seen:
            continue
        result.append(build_blacklisted_stock_trend_item(code, blacklist, industry=industry_map.get(code)))

    result.sort(key=_stock_trends_sort_key)
    for item in result:
        item.pop("_sort_blacklisted", None)

    return {"data": result, "summary": build_stock_trends_summary(result)}


def build_stock_trend_item(
    row: dict,
    blacklist: Optional[dict] = None,
    coverage: Optional[dict] = None,
    industry: Optional[dict] = None,
    screening: Optional[dict] = None,
    dual_confirm: Optional[dict] = None,
) -> dict:
    base = dict(row)
    industry = industry or {}
    coverage = coverage or {}
    dual_confirm = dual_confirm or {}

    attach_industry_aliases(base, industry)
    base["holder_total"] = coverage.get("holder_total") or 0
    base["holder_follow_count"] = coverage.get("holder_follow_count") or 0
    base["holder_watch_count"] = coverage.get("holder_watch_count") or 0
    base["holder_observe_count"] = coverage.get("holder_observe_count") or 0
    base["holder_avoid_count"] = coverage.get("holder_avoid_count") or 0
    apply_stock_trend_gate(base)
    base["_screen"] = screening
    base["_dual_confirm"] = bool(dual_confirm)
    base["dual_confirm_count"] = dual_confirm.get("dual_confirm_count") or 0
    base["dual_confirm_latest_report_date"] = dual_confirm.get("dual_confirm_latest_report_date")
    base["_sort_blacklisted"] = 1 if blacklist else 0
    return base


def build_blacklisted_stock_trend_item(stock_code: str, blacklist: Optional[dict], industry: Optional[dict] = None) -> dict:
    industry = industry or {}
    blacklist = blacklist or {}
    item = {
        "stock_code": stock_code,
        "stock_name": blacklist.get("stock_name") or stock_code,
        "latest_report_date": None,
        "latest_notice_date": None,
        "price_trend": None,
        "path_state": None,
        "setup_tag": None,
        "setup_priority": None,
        "setup_reason": None,
        "setup_confidence": None,
        "setup_level": None,
        "setup_inst_id": None,
        "setup_inst_name": None,
        "setup_event_type": None,
        "setup_industry_name": None,
        "setup_score_raw": None,
        "industry_skill_raw": None,
        "industry_skill_grade": None,
        "followability_grade": None,
        "premium_grade": None,
        "report_recency_grade": None,
        "reliability_grade": None,
        "holder_total": None,
        "holder_follow_count": None,
        "holder_watch_count": None,
        "holder_observe_count": None,
        "holder_avoid_count": None,
        "stock_gate": None,
        "stock_gate_reason": "已拉黑",
        "_screen": None,
        "_dual_confirm": False,
        "dual_confirm_count": 0,
        "dual_confirm_latest_report_date": None,
        "display_inst_name": None,
        "discovery_score": None,
        "company_quality_score": None,
        "stage_score": None,
        "raw_composite_priority_score": None,
        "composite_priority_score": None,
        "composite_cap_score": None,
        "composite_cap_reason": None,
        "stock_archetype": None,
        "priority_pool": None,
        "priority_pool_reason": None,
        "attention_comment_trade_date": None,
        "attention_focus_index": None,
        "attention_composite_score": None,
        "attention_institution_participation": None,
        "attention_turnover_rate": None,
        "attention_rank_change": None,
        "attention_survey_count_30d": None,
        "attention_survey_count_90d": None,
        "attention_survey_org_total_30d": None,
        "attention_survey_org_total_90d": None,
        "external_attention_score": None,
        "external_crowding_penalty": None,
        "external_attention_signal": None,
        "turtle_execution_score": None,
        "turtle_breakout_score": None,
        "turtle_risk_score": None,
        "turtle_score_delta": None,
        "turtle_setup_state": None,
        "turtle_preferred_system": None,
        "turtle_reason": None,
        "score_highlights": None,
        "score_risks": None,
        "generic_stage_raw": None,
        "stage_type_adjust_raw": None,
        "stage_reason": None,
        "path_max_gain_pct": None,
        "path_max_drawdown_pct": None,
        "max_drawdown_60d": None,
        "dist_ma250_pct": None,
        "above_ma250": None,
        "_sort_blacklisted": 1,
    }
    return attach_industry_aliases(item, industry)


def _stock_trends_sort_key(item: dict) -> tuple:
    return (
        item.get("_sort_blacklisted") or 0,
        {
            "A池": 0,
            "B池": 1,
            "C池": 2,
            "D池": 3,
        }.get(item.get("priority_pool"), 9),
        -(item.get("composite_priority_score") or 0),
        0 if item.get("setup_tag") else 1,
        item.get("setup_priority") if item.get("setup_priority") is not None else 9,
        -(item.get("discovery_score") or 0),
        -(item.get("setup_score_raw") or 0),
        -(item.get("holder_total") or 0),
        item.get("stock_code") or "",
    )


def build_stock_trends_summary(rows: list[dict]) -> dict:
    summary = {
        "total": len(rows or []),
        "abTotal": 0,
        "followTotal": 0,
        "dualConfirm": 0,
        "setupTotal": 0,
        "pools": {},
        "gates": {"follow": 0, "watch": 0, "observe": 0, "avoid": 0},
        "signals": {},
        "industries": {},
        "sources": {},
        "attentionCovered": 0,
        "attentionBoosted": 0,
        "attentionCrowded": 0,
        "attentionSignals": {},
        "turtleCovered": 0,
        "turtleBreakout": 0,
        "turtleWatch": 0,
        "turtleExit": 0,
    }

    for item in rows or []:
        pool = item.get("priority_pool") or "未分池"
        gate = item.get("stock_gate") or ""
        source = stock_source_name(item)
        industry = (
            item.get("setup_industry_name")
            or item.get("tdx_l2_name")
            or item.get("tdx_l1_name")
            or ""
        )
        attention_signal = item.get("external_attention_signal") or ""
        turtle_group = turtle_state_group(item.get("turtle_setup_state"))

        summary["pools"][pool] = int(summary["pools"].get(pool) or 0) + 1
        if pool in {"A池", "B池"}:
            summary["abTotal"] += 1
        if gate in summary["gates"]:
            summary["gates"][gate] += 1
        if gate == "follow":
            summary["followTotal"] += 1
        if item.get("setup_tag"):
            summary["setupTotal"] += 1
            signal_key = f"A{item.get('setup_priority') if item.get('setup_priority') is not None else '?'}"
            summary["signals"][signal_key] = int(summary["signals"].get(signal_key) or 0) + 1
        if industry:
            summary["industries"][industry] = int(summary["industries"].get(industry) or 0) + 1
        if source and source != "-":
            summary["sources"][source] = int(summary["sources"].get(source) or 0) + 1
        if item.get("_dual_confirm"):
            summary["dualConfirm"] += 1
        if has_stock_attention_coverage(item):
            summary["attentionCovered"] += 1
        if attention_signal:
            summary["attentionSignals"][attention_signal] = int(summary["attentionSignals"].get(attention_signal) or 0) + 1
            if attention_signal == "热度拥挤":
                summary["attentionCrowded"] += 1
            else:
                summary["attentionBoosted"] += 1
        if turtle_group:
            summary["turtleCovered"] += 1
            if turtle_group == "breakout":
                summary["turtleBreakout"] += 1
            elif turtle_group == "watch":
                summary["turtleWatch"] += 1
            elif turtle_group == "exit":
                summary["turtleExit"] += 1

    summary["topIndustries"] = _top_count_entries(summary["industries"], 4)
    summary["topSignals"] = _top_count_entries(summary["signals"], 4)
    summary["topSources"] = _top_count_entries(summary["sources"], 3)
    summary["topAttentionSignals"] = _top_count_entries(summary["attentionSignals"], 4)
    return summary