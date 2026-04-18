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
    load_stock_attention_payload,
    load_stock_detail_context,
    load_stock_detail_timeline,
    load_stock_qlib_tdx_association,
    load_stock_scoring_breakdown,
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
        trends_payload = load_stock_trends_payload(conn)
        result = trends_payload["data"]
        summary = trends_payload["summary"]
        _stock_trends_cache["ts"] = now_ts
        _stock_trends_cache["payload"] = {"data": result, "summary": summary}
        return {"ok": True, "data": result, "summary": summary, "total": len(result), "cached": False}
    finally:
        conn.close()


@router.get("/candidate-setups")
async def list_candidate_setups(limit: int = Query(200, ge=1, le=1000)):
    """研究型候选 setup 队列（显式标签层，不自动正式入池）"""
    conn = get_conn()
    try:
        data = load_candidate_setup_rows(conn, limit=limit)
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/setup-tracking/summary", include_in_schema=False)
async def get_setup_tracking_summary():
    """内部分析接口：Setup A 前瞻跟踪摘要。"""
    from services.setup_tracker import get_setup_tracking_summary

    conn = get_conn()
    try:
        data = get_setup_tracking_summary(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.get("/setup-tracking/snapshots")
async def get_setup_tracking_snapshots(limit: int = Query(120, ge=1, le=1000)):
    """最近的 Setup A 快照及其后验结果"""
    from services.setup_tracker import list_setup_tracking_snapshots

    conn = get_conn()
    try:
        data = list_setup_tracking_snapshots(conn, limit=limit)
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/setup-validation/report")
async def get_setup_validation_report():
    """Setup 前瞻验证报告：前瞻快照 + 历史 replay + 当前决策"""
    from services.setup_validation import get_setup_validation_report

    conn = get_conn()
    try:
        data = get_setup_validation_report(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.get("/stock-validation/report")
async def get_stock_validation_report(sector: Optional[str] = Query(None)):
    """四层股票评分体系验证报告"""
    from services.stock_validation import get_stock_validation_report

    conn = get_conn()
    try:
        data = get_stock_validation_report(conn, sector=sector)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.get("/setup-replay/summary", include_in_schema=False)
async def get_setup_replay_summary():
    """内部分析接口：历史 Setup 回放摘要。"""
    from services.setup_replay import get_setup_replay_summary

    conn = get_conn()
    try:
        data = get_setup_replay_summary(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.get("/setup-replay/factors", include_in_schema=False)
async def get_setup_replay_factors(
    factor: str = Query("", description="可选：setup_priority / matched_level / premium_grade 等"),
    limit: int = Query(200, ge=1, le=1000),
):
    """内部分析接口：历史 Setup 回放因子表现。"""
    from services.setup_replay import list_setup_replay_factors

    conn = get_conn()
    try:
        data = list_setup_replay_factors(conn, factor_name=(factor or None), limit=limit)
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/setup-replay/events", include_in_schema=False)
async def get_setup_replay_events(
    limit: int = Query(200, ge=1, le=1000),
    setup_only: bool = Query(True, description="只返回命中 setup 的历史事件"),
):
    """内部分析接口：历史 Setup 回放事件明细。"""
    from services.setup_replay import list_setup_replay_events

    conn = get_conn()
    try:
        data = list_setup_replay_events(conn, limit=limit, setup_only=setup_only)
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
        payload = load_institution_profile_detail(conn, inst_id)
        return {"ok": True, **payload}
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
        return await load_stock_attention_payload(conn, stock_code)
    finally:
        conn.close()


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
        from services.stock_validation import get_stock_scorecard_stats
        conn = get_conn()
        try:
            return {"ok": True, "data": STOCK_SCORING_FRAMEWORK, "stats": get_stock_scorecard_stats(conn)}
        finally:
            conn.close()
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
            payload = load_stock_scoring_breakdown(conn, object_id)
            if not payload:
                return {"ok": False, "message": "股票不存在"}
            return payload
        else:
            return {"ok": False, "message": f"未知类型: {card_type}"}
    finally:
        conn.close()


@router.post("/scoring/calculate/{card_type}")
async def calculate_scores(card_type: str):
    """计算评分"""
    from services.scoring import calculate_institution_scores, calculate_stock_scores
    from services.setup_tracker import refresh_setup_tracking
    conn = get_conn(timeout=120)
    try:
        if card_type == "institution":
            count = calculate_institution_scores(conn)
            return {"ok": True, "message": f"已计算 {count} 个机构评分"}
        elif card_type == "stock":
            count = calculate_stock_scores(conn)
            tracking = refresh_setup_tracking(conn)
            _stock_trends_cache["ts"] = 0.0
            _stock_trends_cache["payload"] = None
            industry_message = (
                f"；历史行业快照回填 {tracking['industry_backfilled']} 条"
                if tracking.get("industry_backfilled")
                else ""
            )
            return {
                "ok": True,
                "message": (
                    f"已计算 {count} 只股票评分；"
                    f"已刷新 {tracking['snapshot_date']} 的 Setup 跟踪快照 {tracking['snapshots']} 条"
                    f"{industry_message}"
                ),
            }
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


