"""
机构管理路由

跟踪机构的 CRUD、简称映射、排除管理。
"""

import logging
import time
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional

from services.db import get_conn
from services.industry import load_industry_map
from services.market_signals import (
    load_shareholder_change_payload,
)
from services.institution_read import (
    load_institution_profile_detail,
    load_institution_profiles,
    load_institution_returns_history,
    search_institution_candidates,
    load_tracked_institutions,
)
from services.institution_aux_read import (
    load_event_rows,
    load_exclusion_categories,
    load_holdings_rows,
    load_industry_stat_rows,
)
from services.institution_scoring_read import (
    load_institution_scorecard_stats,
    load_institution_scoring_breakdown,
)
from services.institution_write import (
    batch_create_institution_records,
    create_institution_record,
    delete_institution_record,
    delete_manual_stock_blacklist,
    update_institution_record,
    upsert_manual_stock_blacklist,
    upsert_watchlist_entry,
)
from services.stock_detail_read import (
    extract_forecast_payload,
    extract_stage_payload,
    extract_turtle_payload,
    load_stock_attention_payload,
    load_stock_detail_context,
    load_stock_detail_timeline,
    load_stock_qlib_tdx_association,
)
from services.stock_watchlist_read import load_candidate_setup_rows, load_manual_stock_blacklist_rows, load_watchlist_rows
from services.stock_trends_read import (
    load_stock_trends_payload,
)

logger = logging.getLogger("cm-api")
router = APIRouter()

_STOCK_TRENDS_CACHE_TTL_SEC = 10
_stock_trends_cache = {"ts": 0.0, "payload": None}


# ============================================================
# 机构 CRUD
# ============================================================

@router.get("/institutions")
async def list_institutions(show: str = Query("active", description="active=正常, archived=已归档, all=全部")):
    """列出跟踪机构"""
    conn = get_conn()
    try:
        rows = load_tracked_institutions(conn, show=show)
        return {"ok": True, "data": [dict(r) for r in rows], "total": len(rows)}
    finally:
        conn.close()


class InstitutionCreate(BaseModel):
    name: str
    display_name: Optional[str] = ""
    type: Optional[str] = "other"
    aliases: Optional[list] = []


@router.get("/institutions/search")
async def search_institutions(
    keywords: str = Query(..., description="逗号分隔的关键词，AND 逻辑"),
    holder_type: str = Query("", description="东财分类筛选，如 QFII/社保/保险/基金/券商/信托/个人/其他"),
):
    """从本地数据库的全市场快照中搜索机构

    输入多个关键词（逗号/顿号分隔），名称必须同时包含所有关键词。
    可选按东财分类（holder_type）缩小搜索范围。
    返回匹配的机构名称、持仓股票数、最新公告日。
    已跟踪的机构会标记。
    """
    conn = get_conn()
    try:
        return search_institution_candidates(conn, keywords, holder_type=holder_type)
    finally:
        conn.close()


@router.post("/institutions")
async def create_institution(body: InstitutionCreate):
    """添加跟踪机构"""
    conn = get_conn()
    try:
        inst_id = create_institution_record(
            conn,
            body.name,
            display_name=body.display_name or "",
            institution_type=body.type or "other",
            aliases=body.aliases or [],
        )
        return {"ok": True, "id": inst_id}
    finally:
        conn.close()


@router.post("/institutions/batch")
async def batch_create_institutions(body: dict):
    """批量添加机构，添加后自动触发下游匹配和计算"""
    import asyncio
    items = body.get("institutions", [])
    conn = get_conn()
    try:
        created = batch_create_institution_records(conn, items)

        # 导入后自动触发下游 pipeline（异步，不阻塞响应）
        if created > 0:
            async def _auto_refresh():
                from routers.updater import RUNNERS
                from services.db import get_conn as gc
                steps = ["match_inst", "gen_events", "calc_returns", "build_profiles", "build_trends"]
                for step_id in steps:
                    try:
                        c = gc(timeout=120)
                        try:
                            await RUNNERS[step_id](c)
                        finally:
                            c.close()
                        logger.info(f"[自动刷新] {step_id} 完成")
                    except Exception as e:
                        logger.warning(f"[自动刷新] {step_id} 失败: {e}")
            asyncio.create_task(_auto_refresh())

        return {"ok": True, "created": created}
    finally:
        conn.close()


@router.put("/institutions/{inst_id}")
async def update_institution(inst_id: str, body: dict):
    """更新机构信息"""
    conn = get_conn()
    try:
        if not update_institution_record(conn, inst_id, body):
            return {"ok": False, "message": "无更新字段"}
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/institutions/{inst_id}")
async def delete_institution(inst_id: str):
    """删除跟踪机构，级联清除所有下游数据，并异步重算趋势"""
    import asyncio
    conn = get_conn()
    try:
        delete_institution_record(conn, inst_id)

        # 异步重算股票趋势（因为趋势按股票维度，需要整体重算）
        async def _refresh_trends():
            from routers.updater import RUNNERS
            from services.db import get_conn as gc
            try:
                c = gc(timeout=120)
                try:
                    await RUNNERS["build_trends"](c)
                finally:
                    c.close()
                logger.info("[自动刷新] 删除机构后重算趋势完成")
            except Exception as e:
                logger.warning(f"[自动刷新] 重算趋势失败: {e}")
        asyncio.create_task(_refresh_trends())

        return {"ok": True}
    finally:
        conn.close()


# ============================================================
# 持仓数据
# ============================================================

@router.get("/holdings", include_in_schema=False)
async def list_holdings(institution_id: str = None, stock_code: str = None):
    """内部分析接口：查询持仓记录。"""
    conn = get_conn()
    try:
        rows = load_holdings_rows(conn, institution_id=institution_id, stock_code=stock_code)
        return {"ok": True, "data": rows, "total": len(rows)}
    finally:
        conn.close()


# ============================================================
# 事件数据
# ============================================================

@router.get("/events")
async def list_events(
    institution_id: str = None,
    stock_code: str = None,
    event_type: str = None,
    limit: int = Query(200, le=5000),
):
    """查询机构事件"""
    conn = get_conn()
    try:
        payload = load_event_rows(
            conn,
            institution_id=institution_id,
            stock_code=stock_code,
            event_type=event_type,
            limit=limit,
        )
        return {"ok": True, **payload}
    finally:
        conn.close()


# ============================================================
# 机构画像
# ============================================================

@router.get("/profiles")
async def list_profiles():
    """机构画像列表（始终从 inst_institutions 取最新简称和类型）"""
    conn = get_conn()
    try:
        result = load_institution_profiles(conn)
        return {"ok": True, "data": result, "total": len(result)}
    finally:
        conn.close()


# ============================================================
# 股票趋势
# ============================================================

@router.get("/stock-trends")
async def list_stock_trends():
    """股票趋势列表"""
    now_ts = time.monotonic()
    cached_payload = _stock_trends_cache.get("payload")
    if cached_payload is not None and (now_ts - float(_stock_trends_cache.get("ts") or 0.0)) < _STOCK_TRENDS_CACHE_TTL_SEC:
        return {
            "ok": True,
            "data": list(cached_payload.get("data") or []),
            "summary": dict(cached_payload.get("summary") or {}),
            "total": len(cached_payload.get("data") or []),
            "cached": True,
        }

    conn = get_conn()
    try:
        blacklist_rows = conn.execute("""
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
        """).fetchall()
        blacklist_map = {r["stock_code"]: dict(r) for r in blacklist_rows}

        rows = conn.execute("""
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
                   t.forecast_score,
                   t.forecast_score_effective,
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
                   t.score_highlights,
                   t.score_risks,
                   t.qlib_rank,
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
                   st.above_ma250,
                   ff.forecast_20d_score,
                   ff.forecast_60d_excess_score,
                   ff.forecast_risk_adjusted_score,
                   ff.forecast_reason,
                   ff.model_id            AS forecast_model_id,
                   ff.predict_date        AS forecast_predict_date,
                   ff.industry_relative_group AS forecast_industry_relative_group
            FROM mart_stock_trend t
            LEFT JOIN inst_institutions ii_setup  ON ii_setup.id  = t.setup_inst_id
            LEFT JOIN inst_institutions ii_leader ON ii_leader.id = t.leader_inst
            LEFT JOIN dim_stock_stage_latest st ON st.stock_code = t.stock_code
            LEFT JOIN dim_stock_forecast_latest ff ON ff.stock_code = t.stock_code
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
        """).fetchall()
        coverage_rows = conn.execute("""
            SELECT
                stock_code,
                COUNT(*) AS holder_total,
                SUM(CASE WHEN follow_gate = 'follow'  THEN 1 ELSE 0 END) AS holder_follow_count,
                SUM(CASE WHEN follow_gate = 'watch'   THEN 1 ELSE 0 END) AS holder_watch_count,
                SUM(CASE WHEN follow_gate = 'observe' THEN 1 ELSE 0 END) AS holder_observe_count,
                SUM(CASE WHEN follow_gate = 'avoid'   THEN 1 ELSE 0 END) AS holder_avoid_count
            FROM mart_current_relationship
            GROUP BY stock_code
        """).fetchall()
        coverage_map = {r["stock_code"]: dict(r) for r in coverage_rows}
        industry_map = load_industry_map(conn)
        result = []
        seen = set()
        for row in rows:
            item = dict(row)
            blacklist = blacklist_map.get(item["stock_code"])
            industry = industry_map.get(item["stock_code"], {})
            item["tdx_l1"] = industry.get("tdx_l1")
            item["tdx_l2"] = industry.get("tdx_l2")
            item["tdx_l3"] = industry.get("tdx_l3")
            coverage = coverage_map.get(item["stock_code"], {})
            holder_total = coverage.get("holder_total") or 0
            item["holder_total"] = holder_total
            holder_follow = coverage.get("holder_follow_count") or 0
            holder_watch = coverage.get("holder_watch_count") or 0
            holder_observe = coverage.get("holder_observe_count") or 0
            holder_avoid = coverage.get("holder_avoid_count") or 0
            item["holder_follow_count"] = holder_follow
            item["holder_watch_count"] = holder_watch
            item["holder_observe_count"] = holder_observe
            item["holder_avoid_count"] = holder_avoid

            # 单一真相源：股票级 stock_gate（从 MCR 持仓机构 follow_gate 聚合，最高优先级胜出）
            # 与机构页 follow_gate 同一口径，避免「各算各的」
            if holder_follow > 0:
                stock_gate = "follow"
                stock_gate_reason = f"{holder_follow} 家持仓机构可跟"
            elif holder_watch > 0:
                stock_gate = "watch"
                stock_gate_reason = f"{holder_watch} 家持仓机构关注"
            elif holder_observe > 0:
                stock_gate = "observe"
                stock_gate_reason = f"{holder_observe} 家持仓机构观察"
            elif holder_avoid > 0:
                stock_gate = "avoid"
                stock_gate_reason = f"{holder_avoid} 家持仓机构回避"
            else:
                stock_gate = None
                stock_gate_reason = "暂无持仓机构数据" if not holder_total else "持仓机构均未生成 follow_gate"
            item["stock_gate"] = stock_gate
            item["stock_gate_reason"] = stock_gate_reason
            item["_sort_blacklisted"] = 1 if blacklist else 0
            result.append(item)
            seen.add(item["stock_code"])

        for code, blacklist in blacklist_map.items():
            if code in seen:
                continue
            industry = industry_map.get(code, {})
            result.append({
                "stock_code": code,
                "stock_name": blacklist.get("stock_name") or code,
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
                "display_inst_name": None,
                "discovery_score": None,
                "company_quality_score": None,
                "stage_score": None,
                "forecast_score": None,
                "forecast_score_effective": None,
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
                "forecast_20d_score": None,
                "forecast_60d_excess_score": None,
                "forecast_risk_adjusted_score": None,
                "forecast_reason": None,
                "forecast_model_id": None,
                "forecast_predict_date": None,
                "forecast_industry_relative_group": None,
                "tdx_l1": industry.get("tdx_l1"),
                "tdx_l2": industry.get("tdx_l2"),
                "tdx_l3": industry.get("tdx_l3"),
                "_sort_blacklisted": 1,
            })

        result.sort(
            key=lambda item: (
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
        )
        for item in result:
            item.pop("_sort_blacklisted", None)
        _stock_trends_cache["ts"] = now_ts
        _stock_trends_cache["payload"] = {"data": result}
        return {"ok": True, "data": result, "total": len(result), "cached": False}
    finally:
        conn.close()


@router.get("/candidate-setups")
async def list_candidate_setups(limit: int = Query(200, ge=1, le=1000)):
    """研究型候选 setup 队列（显式标签层，不自动正式入池）"""
    conn = get_conn()
    try:
        excluded = {
            row["stock_code"]
            for row in conn.execute(
                "SELECT stock_code FROM excluded_stocks WHERE category = 'MANUAL'"
            ).fetchall()
        }
        rows = conn.execute("""
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
        """, (limit,)).fetchall()
        industry_map = load_industry_map(conn)
        data = []
        for row in rows:
            item = dict(row)
            if item["stock_code"] in excluded:
                continue
            ind = industry_map.get(item["stock_code"], {})
            item["tdx_l1"] = ind.get("tdx_l1")
            item["tdx_l2"] = ind.get("tdx_l2")
            item["tdx_l3"] = ind.get("tdx_l3")
            data.append(item)
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


# ============================================================
# 股票池
# ============================================================

@router.get("/watchlist")
async def list_watchlist():
    """股票池列表"""
    conn = get_conn()
    try:
        data = load_watchlist_rows(conn)
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


@router.post("/watchlist")
async def add_to_watchlist(body: dict):
    """加入股票池"""
    conn = get_conn()
    try:
        upsert_watchlist_entry(conn, body)
        return {"ok": True}
    finally:
        conn.close()


class StockBlacklistBody(BaseModel):
    stock_code: str
    stock_name: Optional[str] = ""
    reason: Optional[str] = ""
    auto_refresh: Optional[bool] = True


async def _maybe_refresh_after_stock_blacklist() -> dict:
    import importlib

    updater_router = importlib.import_module("routers.updater")

    result = await updater_router.run_single_step("match_inst")
    if result and result.get("ok"):
        return {
            "triggered": True,
            "message": "已开始自动续跑匹配与下游链路",
        }
    return {
        "triggered": False,
        "message": (result or {}).get("message") or "当前已有更新任务，变更将在下一轮更新生效",
    }


@router.get("/stocks/blacklist")
async def list_stock_blacklist():
    """手工拉黑的股票列表"""
    conn = get_conn()
    try:
        data = load_manual_stock_blacklist_rows(conn)
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


@router.post("/stocks/blacklist")
async def add_stock_blacklist(body: StockBlacklistBody):
    """手工拉黑股票"""
    stock_code = (body.stock_code or "").strip()
    if not stock_code:
        return {"ok": False, "message": "缺少股票代码"}

    conn = get_conn()
    try:
        stock_name = upsert_manual_stock_blacklist(
            conn,
            stock_code,
            stock_name=(body.stock_name or ""),
            reason=(body.reason or ""),
        )
    finally:
        conn.close()

    refresh = {"triggered": False, "message": "已拉黑，该股票将在下一轮更新中被排除"}
    if body.auto_refresh:
        refresh = await _maybe_refresh_after_stock_blacklist()
    return {
        "ok": True,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "blacklisted": True,
        "triggered_rebuild": refresh["triggered"],
        "message": f"已拉黑 {stock_name}，{refresh['message']}",
    }


@router.delete("/stocks/blacklist/{stock_code}")
async def remove_stock_blacklist(stock_code: str, auto_refresh: int = Query(1, description="1=自动续跑下游")):
    """撤销股票拉黑"""
    code = (stock_code or "").strip()
    if not code:
        return {"ok": False, "message": "缺少股票代码"}

    conn = get_conn()
    try:
        stock_name = delete_manual_stock_blacklist(conn, code)
    finally:
        conn.close()

    refresh = {"triggered": False, "message": "已撤销拉黑，该股票将在下一轮更新中重新纳入"}
    if auto_refresh:
        refresh = await _maybe_refresh_after_stock_blacklist()
    return {
        "ok": True,
        "stock_code": code,
        "stock_name": stock_name,
        "blacklisted": False,
        "triggered_rebuild": refresh["triggered"],
        "message": f"已撤销 {stock_name} 的拉黑，{refresh['message']}",
    }


# ============================================================
# 排除管理
# ============================================================

@router.get("/profiles/detail/{inst_id}")
async def get_institution_detail(inst_id: str):
    """机构持仓明细 — 统一通过 holdings 模块查询"""
    conn = get_conn()
    try:
        result = get_inst_current_holdings(conn, inst_id)

        # 退出的股票也列出，标注 event_type=exit
        exits = get_inst_exits(conn, inst_id)
        for ex in exits:
            result.append({
                "stock_code": ex["stock_code"],
                "stock_name": ex["stock_name"],
                "report_date": ex["exit_report_date"],
                "notice_date": None,
                "hold_amount": 0,
                "hold_market_cap": 0,
                "hold_ratio": None,
                "event_type": "exit",
                "change_pct": -100.0,
                "gain_10d": None, "gain_30d": None, "gain_60d": None, "gain_120d": None,
                "other_institutions": [],
            })

        # 行业汇总（通过 industry resolver 批量加载）
        industry_summary = []
        stock_codes = [h["stock_code"] for h in result if h.get("event_type") != "exit"]
        if stock_codes:
            from services.industry import load_industry_map
            ind_map = load_industry_map(conn)
            ind_rows = [
                {"tdx_l1": ind_map[c].get("tdx_l1"), "tdx_l2": ind_map[c].get("tdx_l2"),
                 "tdx_l3": ind_map[c].get("tdx_l3"), "stock_code": c}
                for c in stock_codes if c in ind_map
            ]

            # 按一级 → 二级 → 三级 聚合
            from collections import defaultdict
            tree = defaultdict(lambda: {"stocks": 0, "children": defaultdict(lambda: {"stocks": 0, "children": defaultdict(int)})})
            for r in ind_rows:
                l1, l2, l3 = r["tdx_l1"] or "", r["tdx_l2"] or "", r["tdx_l3"] or ""
                if l1:
                    tree[l1]["stocks"] += 1
                    if l2:
                        tree[l1]["children"][l2]["stocks"] += 1
                        if l3:
                            tree[l1]["children"][l2]["children"][l3] += 1

            total_with_ind = len(ind_rows)
            for l1, v1 in sorted(tree.items(), key=lambda x: -x[1]["stocks"]):
                l1_data = {"level1": l1, "stock_count": v1["stocks"],
                           "pct": round(v1["stocks"] / max(total_with_ind, 1) * 100, 1),
                           "children": []}
                for l2, v2 in sorted(v1["children"].items(), key=lambda x: -x[1]["stocks"]):
                    l2_data = {"level2": l2, "stock_count": v2["stocks"], "children": []}
                    for l3, cnt in sorted(v2["children"].items(), key=lambda x: -x[1]):
                        l2_data["children"].append({"level3": l3, "stock_count": cnt})
                    # 历史表现（如有）
                    stat = conn.execute("""
                        SELECT avg_gain_30d, win_rate_30d FROM mart_institution_industry_stat
                        WHERE institution_id = ? AND industry_level = 'level2' AND industry_name = ?
                    """, (inst_id, l2)).fetchone()
                    if stat:
                        l2_data["avg_gain_30d"] = stat["avg_gain_30d"]
                        l2_data["win_rate_30d"] = stat["win_rate_30d"]
                    l1_data["children"].append(l2_data)
                # 一级行业也查业绩
                l1_stat = conn.execute("""
                    SELECT avg_gain_30d, win_rate_30d FROM mart_institution_industry_stat
                    WHERE institution_id = ? AND industry_level = 'level1' AND industry_name = ?
                """, (inst_id, l1)).fetchone()
                if l1_stat:
                    l1_data["avg_gain_30d"] = l1_stat["avg_gain_30d"]
                    l1_data["win_rate_30d"] = l1_stat["win_rate_30d"]
                industry_summary.append(l1_data)

        return {"ok": True, "data": result, "total": len(result), "industry_summary": industry_summary}
    finally:
        conn.close()


@router.get("/stocks/detail/{stock_code}")
async def get_stock_detail(stock_code: str):
    """股票持有机构明细 — 统一通过 holdings 模块查询"""
    from services.holdings import get_stock_institutions
    conn = get_conn()
    try:
        result, latest_rd = get_stock_institutions(conn, stock_code)
        shareholder_change_payload = load_shareholder_change_payload(stock_code)
        detail_payload = await load_stock_detail_context(
            conn,
            stock_code,
            result,
            shareholder_change_payload=shareholder_change_payload,
        )

        return {
            "ok": True,
            "stock_code": stock_code,
            "latest_report_date": latest_rd,
            "total": len(detail_payload["institutions"]),
            **detail_payload,
        }
    finally:
        conn.close()


@router.get("/stocks/attention/{stock_code}", include_in_schema=False)
async def get_stock_attention(stock_code: str):
    """内部验证接口：单股外部关注明细。"""
    conn = get_conn()
    try:
        snapshot = get_latest_stock_attention(conn, stock_code)
        stock_meta = conn.execute(
            "SELECT stock_name FROM dim_active_a_stock WHERE stock_code = ? LIMIT 1",
            (stock_code,),
        ).fetchone()
        if not stock_meta:
            stock_meta = conn.execute(
                "SELECT stock_name FROM market_raw_holdings WHERE stock_code = ? LIMIT 1",
                (stock_code,),
            ).fetchone()
        industry_meta = conn.execute(
            "SELECT tdx_l1, tdx_l2, tdx_l3 FROM dim_stock_tdx_industry WHERE stock_code = ? LIMIT 1",
            (stock_code,),
        ).fetchone()
    finally:
        conn.close()

    detail = await asyncio.to_thread(fetch_stock_attention_detail, stock_code)
    basic_info = dict(detail.get("basic_info") or {})
    fallback_name = (snapshot or {}).get("stock_name") or (stock_meta["stock_name"] if stock_meta else "")
    fallback_industry = ""
    if industry_meta:
        fallback_industry = industry_meta["tdx_l2"] or industry_meta["tdx_l1"] or industry_meta["tdx_l3"] or ""
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
        "diagnostics": detail.get("diagnostics") or {},
    }


@router.get("/profiles/returns-history/{inst_id}")
async def get_returns_history(inst_id: str):
    """获取机构历史收益序列（用于绘制收益曲线）"""
    conn = get_conn()
    try:
        payload = load_institution_returns_history(conn, inst_id)
        return {"ok": True, **payload}
    finally:
        conn.close()


@router.get("/exclusions/categories")
async def get_exclusion_categories():
    conn = get_conn()
    try:
        return {"ok": True, "data": load_exclusion_categories(conn)}
    finally:
        conn.close()


# ============================================================
# 评分卡配置 & 计算
# ============================================================

STOCK_SCORING_FRAMEWORK = {
    "title": "四层研究 + 海龟执行框架",
    "summary": "系统先按 发现 -> 质量 -> 阶段 -> 预测 得到研究原始分 Raw，再叠加外部关注确认与热度拥挤裁决，最后用海龟执行层做小幅执行加减分；四层与外部关注证据权重都支持评分卡热更新，Qlib 仍只承担排序增强，不替代研究逻辑本身。",
    "formula": "Raw = (Discovery×W1 + Quality×W2 + Stage×W3 + Forecast_effective×W4) / (W1+W2+W3+W4)；Composite_base = clamp(Raw + ExternalBoost - CrowdingPenalty)；Composite = clamp(Composite_base + TurtleDelta)",
    "layers": [
        {
            "key": "discovery",
            "label": "发现层 Discovery",
            "weight": 35,
            "role": "机构发现",
            "summary": "保留系统最强的差异化能力，回答“是谁、何时、以多大力度买了这只股”。",
            "items": [
                "机构行业能力：历史命中率、超额收益、样本充足度",
                "新进入新鲜度：优先使用 notice_date，再回退 report_date",
                "持仓强度：持股比例、市值、十大股东排位",
                "变化方向：新进、增持、持平、减持"
            ],
        },
        {
            "key": "quality",
            "label": "质量层 Quality",
            "weight": 30,
            "role": "公司确认",
            "summary": "回答“机构买的是不是值得长期研究的公司”，当前优先使用 mootdx 基础财务 + AKShare 增强指标。",
            "items": [
                "收入质量：连续性、中位增速、行业相对、库存匹配",
                "利润质量：营业利润/净利润同步改善、利润率、波动约束",
                "现金质量：经营现金流兑现率、连续性、行业分位",
                "资产负债稳健：资产负债率、流动比率、净资产改善",
                "资本纪律：分红、回购、解禁压力、股本稀释"
            ],
        },
        {
            "key": "stage",
            "label": "阶段层 Stage",
            "weight": 20,
            "role": "买入过滤",
            "summary": "彼得林奇式过滤层，回答“现在是否仍适合买”，而不只是“这只股票曾经好不好”。",
            "items": [
                "高质量稳健型：基本面续航、趋势健康、过热惩罚",
                "成长兑现型：增长延续、放缓惩罚、价格透支惩罚",
                "周期/事件驱动型：修复验证、兑现惩罚、不确定性惩罚",
                "统一沉淀 path_state、收益区间、均线偏离、回撤与量能"
            ],
        },
        {
            "key": "forecast",
            "label": "预测层 Forecast",
            "weight": 15,
            "role": "排序增强",
            "summary": "Qlib 只做同等条件下的排序增强，不直接覆盖前三层研究判断。",
            "items": [
                "20日收益概率分：来自最新 Qlib 排名和分位",
                "60日相对行业分：优先 SW2，再回退 SW1 / 全市场",
                "波动收益性价比分：结合波动率和 60 日回撤",
                "最终只使用生效预测分进入综合分"
            ],
        },
        {
            "key": "turtle",
            "label": "执行层 Turtle",
            "weight": 0,
            "role": "执行覆盖",
            "summary": "海龟层不替代前四层研究分，只在临近突破、突破触发或退出触发时，对最终执行优先级做小幅加减分。",
            "items": [
                "20/55日突破位、10/20日退出位、ATR 风险与参考止损/加仓位统一沉淀在海龟特征层。",
                "S1/S2 突破触发时增加执行优先级；10/20日退出触发时降低执行优先级。",
                "阶段分和生效预测分偏弱时，海龟正向加分会自动受限，避免执行层盖过研究层。"
            ],
        },
    ],
    "effective_forecast": {
        "label": "生效预测分",
        "formula": "Forecast_effective = Forecast × max(Stage / 60, 0.5)",
        "meaning": "阶段差时自动压缩 Qlib 影响力，避免预测分在错误阶段把总分顶上去。",
    },
    "external_overlay": {
        "label": "外部关注叠加层",
        "summary": "内部四层先给出 Raw，再用外部确认做加分、用热度拥挤做扣分，最后再判断是否晋升、降池或封顶。",
        "items": [
            "AttentionScore 以评论综合分 + 关注指数 + 机构参与度为主；有调研快照时再叠加调研活跃分，这四个证据权重可在评分卡中调整。",
            "ExternalBoost = min(max(AttentionScore - 55, 0) × 0.18 + 调研补分, 8.0)。",
            "CrowdingPenalty 由关注指数、换手率、机构参与、排名跃升、调研活跃、阶段分、近20日与近1月涨幅累加，最高 10 分。",
            "Attention ≥ 72 记为“外部确认增强”；60-72 记为“关注度抬升”；Penalty ≥ 6 且 Attention ≥ 60 记为“热度拥挤”。"
        ],
    },
    "caps": [
        "Stage < 40：综合优先分最高封顶 69",
        "Quality < 45 且非周期/事件驱动型：综合优先分最高封顶 64",
        "CrowdingPenalty ≥ 8：综合优先分最高封顶 69",
        "CrowdingPenalty ≥ 6 且 Stage < 60：综合优先分最高封顶 74",
        "Discovery < 50：不允许进入 A 池",
    ],
    "pools": [
        {
            "label": "A池",
            "gate": "Composite ≥ 75 且 Stage ≥ 50 且 Quality ≥ 55 且 Discovery ≥ 50",
            "meaning": "重点优先池",
        },
        {
            "label": "B池",
            "gate": "60 ≤ Composite < 75，或综合分达标但未通过 A 池门槛",
            "meaning": "持续跟踪池",
        },
        {
            "label": "C池",
            "gate": "45 ≤ Composite < 60",
            "meaning": "观察池",
        },
        {
            "label": "D池",
            "gate": "Composite < 45 或 Stage < 40",
            "meaning": "排除 / 兑现池",
        },
    ],
    "editable_factors": [
        {"key": "composite_discovery_weight", "label": "发现层权重", "description": "控制机构发现层对综合分的贡献", "source": "mart_stock_trend.discovery_score"},
        {"key": "composite_quality_weight", "label": "质量层权重", "description": "控制公司质量层对综合分的贡献", "source": "mart_stock_trend.company_quality_score"},
        {"key": "composite_stage_weight", "label": "阶段层权重", "description": "控制买入阶段层对综合分的贡献", "source": "mart_stock_trend.stage_score"},
        {"key": "composite_forecast_weight", "label": "预测层权重", "description": "控制 Qlib 预测层对综合分的贡献", "source": "mart_stock_trend.forecast_score_effective"},
        {"key": "attention_composite_weight", "label": "评论综合分权重", "description": "控制外部关注层中评论综合分的权重", "source": "attention_composite_score"},
        {"key": "attention_focus_weight", "label": "关注指数权重", "description": "控制外部关注层中关注指数的权重", "source": "attention_focus_index"},
        {"key": "attention_participation_weight", "label": "机构参与度权重", "description": "控制外部关注层中机构参与度的权重", "source": "attention_institution_participation"},
        {"key": "attention_survey_weight", "label": "调研活跃权重", "description": "控制外部关注层中调研活跃分的权重", "source": "attention_survey_count_30d / 90d"},
    ],
}

INSTITUTION_SCORING_FRAMEWORK = {
    "title": "机构评分框架",
    "summary": "机构页当前以历史事件质量和可跟性画像为主，优先使用买入类事件，不足时回退全事件。评分目标是回答“这家机构的历史信号质量是否稳定、是否值得持续跟踪”。",
    "formula": "quality_score = (Σ percentile_rank_i × weight_i / Σ weight_i) × confidence_factor",
    "confidence": "confidence_factor = min(1, √(buy_event_count / 10))，买入事件不足时自动降权",
    "layers": [
        {
            "label": "样本与稳健性",
            "weight": 20,
            "role": "避免少量幸运样本把机构评分顶高",
            "summary": "先看这家机构有没有足够历史样本，再看收益是否稳定，避免一两次偶然高收益造成误判。",
            "items": [
                "买入事件数：事件越多，统计意义越强",
                "收益稳定性：收益均值和中位数越接近越好",
                "回撤控制：最大回撤中位数越低越稳"
            ],
        },
        {
            "label": "收益兑现",
            "weight": 40,
            "role": "看机构信号的中期兑现能力",
            "summary": "重点看公告后 30/60/120 日的平均收益，不只判断会不会涨，也判断涨幅能不能持续。",
            "items": [
                "30日平均收益",
                "60日平均收益",
                "120日平均收益"
            ],
        },
        {
            "label": "胜率延续",
            "weight": 30,
            "role": "看机构信号的命中率",
            "summary": "同样用 30/60/120 日口径看正收益占比，避免只靠少数大涨样本抬高平均收益。",
            "items": [
                "30日胜率",
                "60日胜率",
                "120日胜率"
            ],
        },
    ],
    "editable_factors": [
        {"key": "sample_weight", "label": "买入事件数", "description": "事件越多评分越稳定，按百分位排名", "source": "buy_event_count / total_events"},
        {"key": "gain_30d_weight", "label": "30日平均收益", "description": "公告后30个交易日涨幅均值", "source": "gain_30d"},
        {"key": "gain_60d_weight", "label": "60日平均收益", "description": "公告后60个交易日涨幅均值", "source": "gain_60d"},
        {"key": "gain_120d_weight", "label": "120日平均收益", "description": "公告后120个交易日涨幅均值", "source": "gain_120d"},
        {"key": "win_rate_30d_weight", "label": "30日胜率", "description": "30日内正收益事件占比", "source": "gain_30d > 0"},
        {"key": "win_rate_60d_weight", "label": "60日胜率", "description": "60日内正收益事件占比", "source": "gain_60d > 0"},
        {"key": "win_rate_90d_weight", "label": "120日胜率", "description": "120日内正收益事件占比", "source": "gain_120d > 0"},
        {"key": "drawdown_weight", "label": "回撤控制", "description": "30日最大回撤中位数，越小越好", "source": "max_drawdown_30d"},
        {"key": "stability_weight", "label": "收益稳定性", "description": "收益均值与中位数偏差越小越稳定", "source": "median_gain_30d / avg_gain_30d"},
    ],
}

FOLLOWABILITY_SCORING_FRAMEWORK = {
    "title": "可跟性评分框架",
    "summary": "可跟性评分不是看机构本身强不强，而是看普通跟随者能否在合理溢价下复现这家机构的信号收益。",
    "formula": "followability_score = Σ percentile_rank_i × weight_i / Σ weight_i",
    "confidence": "安全跟随样本越多、溢价越低、收益传导越稳定，可跟分越高",
    "layers": [
        {
            "label": "安全样本",
            "weight": 45,
            "role": "先确认有多少真实可跟案例",
            "summary": "只有在接近机构参考成本、且没有明显高溢价追价时的样本，才算安全跟随样本。",
            "items": [
                "安全跟随样本充足度",
                "安全跟随30日胜率",
                "安全跟随30日平均收益"
            ],
        },
        {
            "label": "代价与回撤",
            "weight": 20,
            "role": "避免高溢价跟进去后收益被吞掉",
            "summary": "就算机构本身胜率高，如果普通跟随者必须付出过高溢价或者承担太大回撤，也不算真正好跟。",
            "items": [
                "平均跟随溢价越低越好",
                "安全跟随平均回撤越低越好"
            ],
        },
        {
            "label": "传导效率",
            "weight": 35,
            "role": "看机构信号是否容易外溢到市场价格",
            "summary": "如果机构一出现，后续 30 日内价格持续兑现且跟随成本仍可接受，说明其信号更适合实际跟踪。",
            "items": [
                "信号传递效率",
                "不同溢价分层的胜率差异"
            ],
        },
    ],
    "editable_factors": [
        {"key": "safe_sample_weight", "label": "安全样本充足度", "description": "安全跟随样本越多越可靠", "source": "safe_follow_event_count"},
        {"key": "safe_win_rate_30d_weight", "label": "安全30日胜率", "description": "安全样本中30日正收益占比", "source": "safe_follow_win_rate_30d"},
        {"key": "safe_gain_30d_weight", "label": "安全30日收益", "description": "安全样本中30日平均收益", "source": "safe_follow_avg_gain_30d"},
        {"key": "safe_drawdown_weight", "label": "安全平均回撤", "description": "安全样本中30日平均回撤，越低越好", "source": "safe_follow_avg_drawdown_30d"},
        {"key": "transfer_efficiency_weight", "label": "传递效率", "description": "信号出现后30日价格传导效率", "source": "signal_transfer_efficiency_30d"},
        {"key": "avg_premium_weight", "label": "平均跟随溢价", "description": "相对机构参考成本的平均溢价，越低越好", "source": "avg_premium_pct"},
    ],
}
@router.get("/scoring/config/{card_type}")
async def get_scoring_config(card_type: str):
    """获取评分卡配置。"""
    from services.scoring import load_scoring_config, INST_SCORE_DEFAULTS, FOLLOW_SCORE_DEFAULTS, STOCK_SCORE_DEFAULTS
    conn = get_conn()
    try:
        if card_type == "institution":
            config = load_scoring_config(conn, "scoring.institution")
            defaults = INST_SCORE_DEFAULTS
        elif card_type == "followability":
            config = load_scoring_config(conn, "scoring.followability")
            defaults = FOLLOW_SCORE_DEFAULTS
        elif card_type == "stock":
            config = load_scoring_config(conn, "scoring.stock")
            defaults = STOCK_SCORE_DEFAULTS
        else:
            return {"ok": False, "message": f"未知评分卡类型: {card_type}"}
        return {"ok": True, "config": config, "defaults": defaults}
    finally:
        conn.close()


@router.get("/scoring/framework/{card_type}")
async def get_scoring_framework(card_type: str):
    """获取评分框架字典，用于评分卡说明页。"""
    if card_type == "stock":
        return {"ok": True, "data": STOCK_SCORING_FRAMEWORK, "stats": {}}
    if card_type == "institution":
        conn = get_conn()
        try:
            return {"ok": True, "data": INSTITUTION_SCORING_FRAMEWORK, "stats": load_institution_scorecard_stats(conn)}
        finally:
            conn.close()
    if card_type == "followability":
        return {"ok": True, "data": FOLLOWABILITY_SCORING_FRAMEWORK}
    return {"ok": False, "message": f"未知评分框架类型: {card_type}"}


@router.post("/scoring/config/{card_type}")
async def save_scoring_config_api(card_type: str, body: dict):
    """保存评分卡配置"""
    from services.scoring import save_scoring_config
    if card_type not in {"institution", "followability", "stock"}:
        return {"ok": False, "message": f"未知评分卡类型: {card_type}"}
    conn = get_conn()
    try:
        prefix = f"scoring.{card_type}"
        config = body.get("config", {})
        save_scoring_config(conn, prefix, config)
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/scoring/config/{card_type}")
async def delete_scoring_config_api(card_type: str):
    """删除评分卡自定义配置，恢复默认权重。"""
    from services.scoring import delete_scoring_config

    if card_type not in {"institution", "followability", "stock"}:
        return {"ok": False, "message": f"未知评分卡类型: {card_type}"}
    conn = get_conn()
    try:
        delete_scoring_config(conn, f"scoring.{card_type}")
        return {"ok": True}
    finally:
        conn.close()


@router.get("/scoring/breakdown/{card_type}/{object_id}")
async def scoring_breakdown(card_type: str, object_id: str):
    """评分拆解：展示某个机构/股票的评分贡献明细（三可原则：可见+可追溯+可复核）"""
    conn = get_conn()
    try:
        if card_type == "institution":
            payload = load_institution_scoring_breakdown(conn, object_id)
            if not payload:
                return {"ok": False, "message": "机构不存在"}
            return payload

        elif card_type == "stock":
            s = conn.execute("""
                SELECT t.stock_code, t.stock_name,
                       t.leader_inst, t.leader_score, t.consensus_count, t.path_state,
                       t.data_completeness, t.latest_notice_date,
                       t.discovery_score, t.company_quality_score,
                       t.company_quality_score_source, t.quality_feature_snapshot_date,
                       t.stage_score,
                       t.forecast_score, t.forecast_score_effective,
                       t.raw_composite_priority_score, t.composite_priority_score,
                       t.composite_cap_score, t.composite_cap_reason,
                       t.priority_pool_reason,
                       t.stock_archetype, t.priority_pool, t.score_highlights, t.score_risks,
                       t.setup_tag, t.setup_priority, t.setup_reason, t.setup_confidence,
                       t.setup_level, t.setup_inst_name, t.setup_event_type,
                       t.setup_industry_name, t.setup_score_raw,
                       t.setup_execution_gate, t.setup_execution_reason,
                       t.industry_skill_raw,
                       t.industry_skill_grade, t.followability_grade, t.premium_grade,
                       t.report_recency_grade, t.reliability_grade,
                       t.crowding_bucket, t.crowding_yield_raw, t.crowding_yield_grade,
                       t.crowding_stability_raw, t.crowding_stability_grade,
                       t.crowding_fit_raw, t.crowding_fit_grade, t.crowding_fit_sample,
                       t.crowding_fit_source, t.report_age_days,
                       st.path_max_gain_pct, st.path_max_drawdown_pct,
                       st.generic_stage_raw, st.stage_type_adjust_raw, st.stage_reason,
                       st.max_drawdown_60d, st.dist_ma250_pct, st.above_ma250,
                       ff.forecast_20d_score, ff.forecast_60d_excess_score,
                       ff.forecast_risk_adjusted_score, ff.forecast_reason,
                       ff.model_id AS forecast_model_id,
                       ff.predict_date AS forecast_predict_date,
                       ff.industry_relative_group AS forecast_industry_relative_group,
                       t.turtle_execution_score, t.turtle_breakout_score, t.turtle_risk_score,
                       t.turtle_score_delta, t.turtle_setup_state, t.turtle_preferred_system, t.turtle_reason,
                       m.tdx_l2, m.notice_age_days, m.price_entry, m.return_to_now,
                       m.inst_ref_cost, m.inst_cost_method,
                       m.premium_pct, m.premium_bucket, m.follow_gate
                FROM mart_stock_trend t
                LEFT JOIN dim_stock_stage_latest st ON st.stock_code = t.stock_code
                LEFT JOIN dim_stock_forecast_latest ff ON ff.stock_code = t.stock_code
                LEFT JOIN mart_current_relationship m ON t.stock_code = m.stock_code
                WHERE t.stock_code = ?
                LIMIT 1
            """, (object_id,)).fetchone()
            if not s:
                return {"ok": False, "message": "股票不存在"}
            s = dict(s)
            return {
                "ok": True, "card_type": "stock", "object_id": object_id,
                "discovery_score": s.get("discovery_score"),
                "company_quality_score": s.get("company_quality_score"),
                "company_quality_score_source": s.get("company_quality_score_source"),
                "quality_feature_snapshot_date": s.get("quality_feature_snapshot_date"),
                "quality_snapshot_date": s.get("quality_feature_snapshot_date"),
                "stage_score": s.get("stage_score"),
                "forecast_score": s.get("forecast_score"),
                "forecast_score_effective": s.get("forecast_score_effective"),
                "raw_composite_priority_score": s.get("raw_composite_priority_score"),
                "composite_priority_score": s.get("composite_priority_score"),
                "composite_cap_score": s.get("composite_cap_score"),
                "composite_cap_reason": s.get("composite_cap_reason"),
                "stock_archetype": s.get("stock_archetype"),
                "priority_pool": s.get("priority_pool"),
                "priority_pool_reason": s.get("priority_pool_reason"),
                "score_highlights": s.get("score_highlights"),
                "score_risks": s.get("score_risks"),
                "path_state": s.get("path_state"),
                "data_completeness": s.get("data_completeness"),
                "stage": extract_stage_payload(s),
                "forecast": extract_forecast_payload(s),
                "turtle": extract_turtle_payload(s),
                "formula": "Composite = 发现35% + 质量30% + 阶段20% + 生效预测15%；Stage<40 封顶69；Quality<45 且非周期/事件型封顶64；A池要求 Composite≥75 且 Stage≥50 且 Quality≥55 且 Discovery≥50",
                "factors": {
                    "quality": {
                        "score": s.get("company_quality_score"),
                        "source_type": s.get("company_quality_score_source"),
                        "snapshot_date": s.get("quality_feature_snapshot_date"),
                        "source": "dim_stock_quality_latest.quality_score_v1 对齐最新财报后生效，否则回退 scoring.py stock_scoring_v2",
                    },
                    "leader": {"inst": s.get("leader_inst"), "score": s.get("leader_score"),
                               "source": "mart_institution_profile.quality_score", "weight": "30%"},
                    "industry_match": {"stock_industry": s.get("tdx_l2"),
                                       "source": "mart_current_relationship.tdx_l2 vs leader best_industry",
                                       "weight": "25%"},
                    "consensus": {"count": s.get("consensus_count"),
                                  "source": "mart_current_relationship 中 quality_score ≥ 75th 的机构数",
                                  "weight": "10%"},
                    "timeliness": {"notice_age_days": s.get("notice_age_days"),
                                   "notice_date": s.get("latest_notice_date"),
                                   "source": "notice_date 距今天数（30日=100分，180日=0分）",
                                   "weight": "10%"},
                    "price_path": {"entry_price": s.get("price_entry"),
                                   "inst_ref_cost": s.get("inst_ref_cost"),
                                   "inst_cost_method": s.get("inst_cost_method"),
                                   "premium_pct": s.get("premium_pct"),
                                   "premium_bucket": s.get("premium_bucket"),
                                   "follow_gate": s.get("follow_gate"),
                                   "return_to_now": s.get("return_to_now"),
                                   "path_state": s.get("path_state"),
                                   "source": "market_data.db 日K线计算"},
                    "setup": {
                        "tag": s.get("setup_tag"),
                        "priority": s.get("setup_priority"),
                        "reason": s.get("setup_reason"),
                        "confidence": s.get("setup_confidence"),
                        "level": s.get("setup_level"),
                        "institution": s.get("setup_inst_name"),
                        "event_type": s.get("setup_event_type"),
                        "industry_name": s.get("setup_industry_name"),
                        "setup_score_raw": s.get("setup_score_raw"),
                        "setup_execution_gate": s.get("setup_execution_gate"),
                        "setup_execution_reason": s.get("setup_execution_reason"),
                        "industry_skill_raw": s.get("industry_skill_raw"),
                        "industry_skill_grade": s.get("industry_skill_grade"),
                        "followability_grade": s.get("followability_grade"),
                        "premium_grade": s.get("premium_grade"),
                        "report_recency_grade": s.get("report_recency_grade"),
                        "reliability_grade": s.get("reliability_grade"),
                        "crowding_bucket": s.get("crowding_bucket"),
                        "crowding_yield_raw": s.get("crowding_yield_raw"),
                        "crowding_yield_grade": s.get("crowding_yield_grade"),
                        "crowding_stability_raw": s.get("crowding_stability_raw"),
                        "crowding_stability_grade": s.get("crowding_stability_grade"),
                        "crowding_fit_raw": s.get("crowding_fit_raw"),
                        "crowding_fit_grade": s.get("crowding_fit_grade"),
                        "crowding_fit_sample": s.get("crowding_fit_sample"),
                        "crowding_fit_source": s.get("crowding_fit_source"),
                        "report_age_days": s.get("report_age_days"),
                        "source": "mart_stock_trend Setup A 叠加层",
                    },
                },
            }
        else:
            return {"ok": False, "message": f"未知类型: {card_type}"}
    finally:
        conn.close()


@router.post("/scoring/calculate/{card_type}")
async def calculate_scores(card_type: str):
    """计算评分"""
    from services.scoring import calculate_institution_scores, calculate_stock_scores
    conn = get_conn(timeout=120)
    try:
        if card_type == "institution":
            count = calculate_institution_scores(conn)
            return {"ok": True, "message": f"已计算 {count} 个机构评分"}
        elif card_type == "stock":
            count = calculate_stock_scores(conn)
            _stock_trends_cache["ts"] = 0.0
            _stock_trends_cache["payload"] = None
            return {"ok": True, "message": f"已计算 {count} 只股票评分"}
        else:
            return {"ok": False, "message": f"未知类型: {card_type}"}
    finally:
        conn.close()


@router.get("/industry-stats", include_in_schema=False)
async def get_industry_stats(institution_id: str = None):
    """内部分析接口：查询机构行业统计。"""
    conn = get_conn()
    try:
        rows = load_industry_stat_rows(conn, institution_id=institution_id)
        return {"ok": True, "data": rows, "total": len(rows)}
    finally:
        conn.close()


