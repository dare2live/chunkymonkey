"""
signals_v2 HTTP 路由

前缀：/api/signals
所有路由只读 SQLite + 调 signals_v2.py 服务，不触发任何 recompute。
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.db import get_conn
from services.signals_v2 import (
    DEFAULT_CONFIG,
    PolicyConfig,
    build_today_signals,
    load_config,
    save_config,
    ensure_defaults,
    institution_track_record,
    fetch_similar_for_event,
    backtest_historical,
)

logger = logging.getLogger("cm-api")
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────

class ConfigPatch(BaseModel):
    horizon_days: Optional[int] = Field(None, ge=10, le=120)
    min_sample: Optional[int] = Field(None, ge=1)
    ev_threshold_pct: Optional[float] = Field(None)
    win_threshold: Optional[float] = Field(None, ge=0, le=1)
    prefer_same_industry_min_sample: Optional[int] = Field(None, ge=1)
    signal_freshness_days: Optional[int] = Field(None, ge=1, le=365)


@router.get("/config")
async def get_signal_config():
    conn = get_conn()
    try:
        ensure_defaults(conn)
        cfg = load_config(conn)
        from dataclasses import asdict
        return {
            "current": asdict(cfg),
            "defaults": DEFAULT_CONFIG,
            "descriptions": {
                "horizon_days": "持有期天数（10/30/60/90/120，决定用哪个 gain_*d 列）",
                "min_sample": "机构历史 buy 事件最小样本量",
                "ev_threshold_pct": "follow 档 EV% 门槛",
                "win_threshold": "follow 档胜率门槛（0-1）",
                "prefer_same_industry_min_sample": "同行业子集样本≥此值优先用",
                "signal_freshness_days": "今日信号列表取多少天内的事件",
            },
        }
    finally:
        conn.close()


@router.post("/config")
async def update_signal_config(patch: ConfigPatch):
    conn = get_conn()
    try:
        updates = {k: v for k, v in patch.model_dump().items() if v is not None}
        if not updates:
            return {"ok": False, "message": "no changes"}
        # 额外校验 horizon_days 必须匹配有对应 gain_*d 列
        if "horizon_days" in updates and updates["horizon_days"] not in (10, 30, 60, 90, 120):
            raise HTTPException(400, "horizon_days must be one of 10/30/60/90/120")
        save_config(conn, updates)
        cfg = load_config(conn)
        from dataclasses import asdict
        return {"ok": True, "current": asdict(cfg)}
    finally:
        conn.close()


@router.post("/config/reset")
async def reset_signal_config():
    conn = get_conn()
    try:
        save_config(conn, DEFAULT_CONFIG)
        return {"ok": True, "current": DEFAULT_CONFIG}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# 今日信号
# ─────────────────────────────────────────────────────────────────────

@router.get("/today")
async def get_today_signals(
    freshness_days: Optional[int] = None,
    action_filter: Optional[str] = None,  # "follow" | "watch" | "skip" | None
    limit: int = 500,
):
    """
    返回最近 N 天的 buy 事件 + 每条的推荐。

    前端主视图的数据源。action_filter='follow' 只看真正可跟的。
    """
    conn = get_conn()
    try:
        signals = build_today_signals(conn, freshness_days=freshness_days)
        if action_filter:
            signals = [s for s in signals if s.action == action_filter]
        signals = signals[:limit]

        # 汇总
        counts = {"follow": 0, "watch": 0, "skip": 0}
        for s in signals:
            counts[s.action] = counts.get(s.action, 0) + 1

        return {
            "summary": {
                "total": len(signals),
                "by_action": counts,
                "freshness_days": freshness_days or load_config(conn).signal_freshness_days,
            },
            "signals": [s.to_dict() for s in signals],
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# 机构 track record
# ─────────────────────────────────────────────────────────────────────

@router.get("/institution/{institution_id}")
async def get_institution_track_record(institution_id: str):
    """
    一个机构"自己打过的成绩单"——没有评分，只有原始数字。
    """
    conn = get_conn()
    try:
        return institution_track_record(conn, institution_id)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# 事件详情：相似历史样本
# ─────────────────────────────────────────────────────────────────────

@router.get("/event/{event_id}/similar")
async def get_event_similar(event_id: str, limit: int = 50):
    """
    对一个事件，返回它推荐时用到的历史相似样本。
    event_id 格式：institution_id|stock_code|report_date（URL 需 encode）
    """
    conn = get_conn()
    try:
        result = fetch_similar_for_event(conn, event_id, limit=limit)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# 回测
# ─────────────────────────────────────────────────────────────────────

@router.get("/backtest")
async def run_backtest(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """
    跑一次全量或区间历史回测，返回 follow/watch/skip/blind 对比 + 季度趋势。

    执行约 10-15 秒。用于调参时"改配置 → 跑回测 → 看是否改善 P&L"。
    """
    conn = get_conn()
    try:
        result = backtest_historical(conn, start_date=start_date, end_date=end_date)
        return result
    finally:
        conn.close()
