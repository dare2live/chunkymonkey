"""
signals_v2 HTTP 路由

前缀：/api/signals
默认只读 DuckDB 中已物化的 signals_v2 快照；refresh=true 才显式重建。
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from services.db import get_conn
from services.signals_v2 import (
    DEFAULT_CONFIG,
    PolicyConfig,
    load_today_signal_cache,
    load_config,
    materialize_today_signal_cache,
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


def _shape_today_signal_payload(
    payload: dict,
    *,
    freshness_days: int,
    action_filter: Optional[str],
    limit: int,
) -> dict:
    signals = list(payload.get("signals") or [])
    if action_filter:
        signals = [signal for signal in signals if signal.get("action") == action_filter]
    signals = signals[:limit]
    counts = {"follow": 0, "watch": 0, "skip": 0}
    for signal in signals:
        action = signal.get("action")
        counts[action] = counts.get(action, 0) + 1
    cache_status = payload.get("cache") or (payload.get("summary") or {}).get("cache")
    summary = {
        "total": len(signals),
        "by_action": counts,
        "freshness_days": freshness_days,
    }
    if cache_status:
        summary["cache"] = cache_status
    return {
        "summary": summary,
        "signals": signals,
    }


def _today_signal_cache_miss_payload(*, freshness_days: int) -> dict:
    cache_status = {
        "status": "miss",
        "built_at": None,
        "signal_count": 0,
        "source_max_notice_date": None,
        "current_source_max_notice_date": None,
        "stale": True,
        "requires_refresh": True,
        "message": "No materialized today-signal snapshot; use refresh=true or run the update pipeline.",
    }
    return {
        "summary": {
            "total": 0,
            "by_action": {"follow": 0, "watch": 0, "skip": 0},
            "freshness_days": int(freshness_days),
            "cache": cache_status,
        },
        "signals": [],
        "cache": cache_status,
    }


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
    refresh: bool = False,
):
    """
    返回最近 N 天的 buy 事件 + 每条的推荐。

    前端主视图的数据源。默认读取上一次物化结果，refresh=true 才重建。
    action_filter='follow' 只看真正可跟的。
    """
    conn = get_conn()
    try:
        cfg = load_config(conn)
        fresh_days = int(freshness_days or cfg.signal_freshness_days)
        if refresh:
            payload = materialize_today_signal_cache(
                conn,
                config=cfg,
                freshness_days=fresh_days,
            )
        else:
            payload = load_today_signal_cache(
                conn,
                config=cfg,
                freshness_days=fresh_days,
            ) or _today_signal_cache_miss_payload(freshness_days=fresh_days)
        return _shape_today_signal_payload(
            payload,
            freshness_days=fresh_days,
            action_filter=action_filter,
            limit=limit,
        )
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
# 事件总体统计（工作台健康卡数据源）
# ─────────────────────────────────────────────────────────────────────

@router.get("/events/stats")
async def get_event_stats():
    """
    fact_institution_event 层面的核心计数，供工作台「事件成熟」健康卡拉取。
    不再只看 total —— 同时暴露"成熟率" = 有 gain_60d 的事件数 / buy 事件总数。
    """
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS total_events,
                COUNT(DISTINCT institution_id) AS distinct_institutions,
                COUNT(DISTINCT stock_code) AS distinct_stocks,
                SUM(CASE WHEN event_type IN ('new_entry','increase') THEN 1 ELSE 0 END) AS buy_total,
                SUM(CASE WHEN event_type IN ('new_entry','increase') AND gain_60d IS NOT NULL THEN 1 ELSE 0 END) AS buy_matured,
                MAX(notice_date) AS latest_notice_date,
                MIN(notice_date) AS earliest_notice_date
            FROM fact_institution_event
        """).fetchone()
        buy_total = row["buy_total"] or 0
        buy_matured = row["buy_matured"] or 0
        return {
            "total_events": row["total_events"] or 0,
            "buy_total": buy_total,
            "buy_matured": buy_matured,
            "matured_ratio": (buy_matured / buy_total) if buy_total else 0.0,
            "distinct_institutions": row["distinct_institutions"] or 0,
            "distinct_stocks": row["distinct_stocks"] or 0,
            "latest_notice_date": row["latest_notice_date"],
            "earliest_notice_date": row["earliest_notice_date"],
        }
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
