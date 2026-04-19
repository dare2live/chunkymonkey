"""
signals_v2 HTTP 路由

前缀：/api/signals
所有路由只读 SQLite + 调 signals_v2.py 服务，不触发任何 recompute。
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

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
    cohort_recent_matured,
    institution_multi_horizon,
)

logger = logging.getLogger("cm-api")
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────

# 所有配置键的人读说明；键白名单以 DEFAULT_CONFIG 为准（这里只配文案）。
CONFIG_DESCRIPTIONS: Dict[str, str] = {
    "horizon_days": "持有期天数（10/30/60/90/120，决定用哪个 gain_*d 列）",
    "min_sample": "机构历史 buy 事件最小样本量",
    "ev_threshold_pct": "follow 档 EV% 门槛",
    "win_threshold": "follow 档胜率门槛（0-1）",
    "prefer_same_industry_min_sample": "同行业子集样本≥此值优先用",
    "signal_freshness_days": "今日信号列表取多少天内的事件",
    "cooldown_days": "严谨左切：事件需 N 日后才算成熟样本（防 look-ahead），0=老逻辑",
    "short_window_days": "双口径 KNN 短期窗口天数",
    "short_min_sample": "短期窗口最小样本量",
    "max_premium_pct": "溢价硬顶（%），超过直接 skip；99999=不启用",
    "min_hold_ratio": "持仓占流通股下限（%）；0=不启用",
    "inst_type_blacklist": "机构类型黑名单（逗号分隔，负 alpha 类型）",
    "inst_type_preferred": "机构类型优选（逗号分隔，历史 beat blind 类型）",
    "max_holder_yoy_pct": "D1 股东人数 YoY 上限（%），越小越严；99999=不启用",
    "min_forecast_profit_yoy": "D3 业绩预告利润 YoY 下限（%）；-9999=不启用",
    "max_unlock_ratio_180d": "D5 180 天解禁比例上限（%）；99999=不启用",
    "min_survey_count_90d": "D8 近 90 日机构调研次数下限；0=不启用",
}


def _coerce_config_value(key: str, raw: Any) -> Any:
    """按 DEFAULT_CONFIG 目标类型强转；非法值抛 HTTPException。"""
    default = DEFAULT_CONFIG[key]
    try:
        if isinstance(default, bool):
            # 放在 int 之前：bool 是 int 的子类
            return bool(raw) if not isinstance(raw, str) else raw.strip().lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(float(raw))
        if isinstance(default, float):
            return float(raw)
        return str(raw)
    except (ValueError, TypeError):
        raise HTTPException(400, f"invalid value for {key}: {raw!r}")


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
            "descriptions": CONFIG_DESCRIPTIONS,
        }
    finally:
        conn.close()


@router.post("/config")
async def update_signal_config(request: Request):
    try:
        patch_raw = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    if not isinstance(patch_raw, dict):
        raise HTTPException(400, "body must be a JSON object")

    unknown = [k for k in patch_raw.keys() if k not in DEFAULT_CONFIG]
    if unknown:
        raise HTTPException(400, f"unknown config keys: {unknown}")

    updates = {k: _coerce_config_value(k, v) for k, v in patch_raw.items()}
    if not updates:
        return {"ok": False, "message": "no changes"}

    if "horizon_days" in updates and updates["horizon_days"] not in (10, 30, 60, 90, 120):
        raise HTTPException(400, "horizon_days must be one of 10/30/60/90/120")

    conn = get_conn()
    try:
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
# 反馈闭环：最近已成熟 cohort（真实 out-of-sample 结果）
# ─────────────────────────────────────────────────────────────────────

@router.get("/cohort/recent")
async def get_recent_cohort(lookback_days: int = 180):
    """
    "最近已成熟"cohort 的实际表现。

    回答一个问题：过去系统打的 follow/watch/skip 档，到期实际是几档收益？
    这是系统的诚实体检——不是拿当下样本回测，而是 out-of-sample 跟踪。
    """
    conn = get_conn()
    try:
        return cohort_recent_matured(conn, lookback_days=lookback_days)
    finally:
        conn.close()


@router.get("/institution/{institution_id}/multi-horizon")
async def get_institution_multi_horizon(institution_id: str):
    """
    机构在 30/60/90/120 天不同持有期的 EV/胜率对比。
    揭示"这个机构的 edge 是短线还是长线"。
    """
    conn = get_conn()
    try:
        return institution_multi_horizon(conn, institution_id)
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
