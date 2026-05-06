"""
数据更新管线

当前主 DAG（Phase 5 清理后）：
    0. sync_calendar            — 交易日历前置校验
    1. sync_raw                 — 下载十大股东
    2. match_inst               — 匹配跟踪机构
    3. sync_market_data         — 同步行情数据
    4. sync_financial           — 同步财务数据
    5. gen_events               — 生成事件
    6. calc_returns             — 计算收益
    7. sync_industry            — 通达信行业
    8. calc_financial_derived   — 计算财务指标
    9. build_current_rel        — 构建当前关系
 10. build_profiles           — 机构画像
 11. build_industry_stat      — 行业统计
 12. build_trends             — 生成股票列表
 13. calc_screening           — TDX 选股筛选
 14. calc_sector_momentum     — 板块动量分析
 15. build_external_attention — 外部关注快照
 16. build_stage_features     — 阶段特征构建
 17. build_turtle_features    — 海龟执行特征
 18. calc_inst_scores         — 机构评分
 19. calc_stock_scores        — 股票评分
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from services.db import get_conn
from services.gap_queue import (
    load_tracked_stock_names,
    mark_current_missing_as,
    mark_gap_failed,
    mark_gap_resolved,
    mark_gap_retrying,
    reconcile_gap_queue_snapshot,
    summarize_gap_queue,
)
from services.industry import industry_join_clause, summarize_industry_coverage
from services.market_db import get_canonical_kline_qfq_relation
from services.source_policy import normalize_kline_write_source
from services.tdx_source import iter_tdx_servers
from services.utils import latest_completed_trade_date

logger = logging.getLogger("cm-api")
router = APIRouter()
KLINE_DAILY_QFQ_RELATION = get_canonical_kline_qfq_relation()

_UI_LOG_LIMIT = 400
_ui_logs = []
_ui_log_seq = 0
CALENDAR_MIN_ROWS = 700
CALENDAR_FUTURE_COVER_DAYS = 30
CALENDAR_DATA_FETCH_STEPS = {
    "sync_raw",
    "sync_market_data",
    "sync_financial",
    "sync_industry",
    "sync_surveys",
    "sync_qfii",
    "sync_margin",
    "sync_lhb",
    "sync_aif10_holder_count",
    "sync_aif10_valuation_quantile",
    "sync_aif10_peer_valuation",
    "sync_aif10_forecast_consensus",
    "sync_aif10_financial_history",
}


class _UILogHandler(logging.Handler):
    """把 cm-api 日志同步到前端状态接口，供工作台展示。"""

    def emit(self, record):
        global _ui_log_seq
        try:
            message = record.getMessage()
            if not message:
                return
            _ui_log_seq += 1
            _ui_logs.append({
                "id": _ui_log_seq,
                "ts": datetime.now().isoformat(),
                "level": record.levelname.lower(),
                "message": message,
            })
            if len(_ui_logs) > _UI_LOG_LIMIT:
                del _ui_logs[:-_UI_LOG_LIMIT]
        except Exception as _e:
            import sys; print(f"[UILogHandler] emit error: {_e}", file=sys.stderr)


if not getattr(logger, "_cm_ui_handler_attached", False):
    _ui_handler = _UILogHandler(level=logging.INFO)
    logger.addHandler(_ui_handler)
    logger._cm_ui_handler_attached = True


def _reset_ui_logs():
    global _ui_logs, _ui_log_seq
    _ui_logs = []
    _ui_log_seq = 0


def _record_sync_source_metric(stats: dict, source: str, elapsed_sec: float, rows: int = 0) -> None:
    entry = stats.setdefault(source, {
        "count": 0,
        "rows": 0,
        "elapsed_total_sec": 0.0,
        "max_elapsed_sec": 0.0,
    })
    entry["count"] += 1
    entry["rows"] += max(0, int(rows or 0))
    entry["elapsed_total_sec"] += max(0.0, float(elapsed_sec or 0.0))
    entry["max_elapsed_sec"] = max(entry["max_elapsed_sec"], max(0.0, float(elapsed_sec or 0.0)))


def _snapshot_sync_source_metrics(stats: dict) -> dict:
    snapshot = {}
    for source, entry in sorted(stats.items()):
        count = int(entry.get("count") or 0)
        elapsed_total = float(entry.get("elapsed_total_sec") or 0.0)
        snapshot[source] = {
            "count": count,
            "rows": int(entry.get("rows") or 0),
            "avg_elapsed_sec": round(elapsed_total / count, 3) if count else 0.0,
            "max_elapsed_sec": round(float(entry.get("max_elapsed_sec") or 0.0), 3),
            "elapsed_total_sec": round(elapsed_total, 3),
        }
    return snapshot


def _format_sync_source_metrics(stats: dict) -> str:
    if not stats:
        return "无成功来源"
    parts = []
    for source, entry in stats.items():
        parts.append(
            f"{source}={entry['count']}只/均{entry['avg_elapsed_sec']:.2f}s/峰{entry['max_elapsed_sec']:.2f}s/行{entry['rows']}"
        )
    return "；".join(parts)


def _build_daily_sync_batch_summary(
    range_start: int,
    range_end: int,
    *,
    stats: dict,
    batch_elapsed_sec: float,
) -> dict:
    snapshot = _snapshot_sync_source_metrics(stats)
    success_count = sum(int(entry.get("count") or 0) for entry in snapshot.values())
    batch_total = max(0, range_end - range_start + 1)
    return {
        "range_start": range_start,
        "range_end": range_end,
        "count": batch_total,
        "success_count": success_count,
        "failed_count": max(0, batch_total - success_count),
        "batch_elapsed_sec": round(max(0.0, float(batch_elapsed_sec or 0.0)), 3),
        "source_stats": snapshot,
    }


def _normalize_update_step_detail(detail: Optional[dict]) -> Optional[dict]:
    if not isinstance(detail, dict):
        return None

    normalized = dict(detail)
    daily_sync = normalized.get("daily_sync")
    if isinstance(daily_sync, dict):
        normalized_daily_sync = dict(daily_sync)
        normalized_daily_sync.setdefault("prefer_fallback", False)
        normalized_daily_sync.setdefault("strategy_reason", None)
        normalized_daily_sync.setdefault("preflight_sample", None)
        normalized["daily_sync"] = normalized_daily_sync

    return normalized


def _coerce_step_record_count(value) -> Optional[int]:
    """Return a numeric step record count from clean or legacy status values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        pass
    match = re.search(r"""['"]?count['"]?\s*:\s*([0-9]+)""", text)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    return None


def _extract_legacy_step_field(raw: str, key: str) -> Optional[str]:
    text = str(raw or "")
    match = re.search(rf"""['"]?{re.escape(key)}['"]?\s*:\s*('([^']*)'|"([^"]*)"|[^,}}]+)""", text)
    if not match:
        return None
    value = match.group(2) if match.group(2) is not None else (
        match.group(3) if match.group(3) is not None else match.group(1)
    )
    value = str(value).strip().strip("'\"")
    return value or None


def _parse_step_detail(raw) -> Optional[dict]:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return _normalize_update_step_detail(parsed)


def _legacy_step_detail_from_records(raw) -> Optional[dict]:
    """Recover detail from old rows where a whole dict was written into records."""
    text = str(raw or "").strip()
    if not text.startswith("{"):
        return None
    detail: dict = {}
    count = _coerce_step_record_count(text)
    if count is not None:
        detail["count"] = count
    for key in ("status", "mode", "message", "target_date", "range", "report_date", "trade_date"):
        value = _extract_legacy_step_field(text, key)
        if value:
            detail[key] = value
    for key in ("written", "skipped", "empty", "failed", "total", "existing", "mart_rows"):
        value = _extract_legacy_step_field(text, key)
        if value is None:
            continue
        try:
            detail[key] = int(float(value))
        except Exception:
            detail[key] = value
    return _normalize_update_step_detail(detail) if detail else None


def _sanitize_step_status_item(item: dict) -> dict:
    """Normalize legacy step_status rows before sending them to the UI."""
    cleaned = dict(item)
    detail = _parse_step_detail(cleaned.get("error"))
    if detail is None:
        detail = _legacy_step_detail_from_records(cleaned.get("records"))
    if detail is not None:
        cleaned["detail"] = detail

    count = _coerce_step_record_count(cleaned.get("records"))
    cleaned["records"] = count if count is not None else 0
    if detail and detail.get("status") and cleaned.get("status") not in {"failed", "stopped", "running", "pending"}:
        cleaned["status"] = _normalize_step_status(detail.get("status"))
    return cleaned


def _normalize_step_status(status) -> str:
    text = str(status or "completed").strip().lower()
    return {
        "success": "completed",
        "done": "completed",
        "ok": "completed",
        "complete": "completed",
        "skip": "skipped",
        "warning": "partial",
        "partial_success": "partial",
        "error": "failed",
    }.get(text, text if text in {"completed", "partial", "failed", "blocked", "skipped", "stopped", "running", "pending"} else "completed")


def _format_step_result_for_log(status: str, count: int, detail_text: Optional[str]) -> str:
    detail = _parse_step_detail(detail_text)
    if detail and detail.get("message"):
        return str(detail["message"])
    if detail:
        for key in ("written", "count", "total", "existing"):
            if detail.get(key) is not None:
                return f"{detail.get(key)}"
    if status == "skipped":
        return str(detail_text or "已是最新，无需更新")
    if status == "blocked":
        return str(detail_text or "阻断")
    if status == "partial":
        return f"{count} 条，有缺口"
    return f"{count}"

# ============================================================
# 步骤定义
# ============================================================

STEPS = [
    {"id": "sync_calendar",         "name": "交易日历前置",    "group": "data", "order": 0},
    {"id": "sync_raw",              "name": "下载十大股东",     "group": "data", "order": 1},
    {"id": "match_inst",            "name": "匹配跟踪机构",    "group": "data", "order": 2},
    {"id": "sync_market_data",      "name": "同步行情数据",    "group": "data", "order": 3},
    {"id": "sync_financial",        "name": "同步财务数据",    "group": "data", "order": 4},
    {"id": "gen_events",            "name": "生成事件",        "group": "calc", "order": 5},
    {"id": "calc_returns",          "name": "计算收益",        "group": "calc", "order": 6},
    {"id": "sync_industry",         "name": "通达信行业",      "group": "data", "order": 7},
    {"id": "sync_surveys",          "name": "机构调研",        "group": "data", "order": 7.5},
    {"id": "sync_qfii",             "name": "QFII 季报",       "group": "data", "order": 7.6},
    {"id": "sync_margin",           "name": "融资融券",        "group": "data", "order": 7.7},
    {"id": "sync_lhb",              "name": "龙虎榜",          "group": "data", "order": 7.8},
    # P1.5 (2026-04-28): 5 个妙想独家 capability sync step
    {"id": "sync_aif10_holder_count",     "name": "妙想股东人数",     "group": "data", "order": 7.81},
    {"id": "sync_aif10_valuation_quantile", "name": "妙想估值分位",   "group": "data", "order": 7.82},
    {"id": "sync_aif10_peer_valuation",    "name": "妙想同行估值",   "group": "data", "order": 7.83},
    {"id": "sync_aif10_forecast_consensus","name": "妙想一致预期",   "group": "data", "order": 7.84},
    {"id": "sync_aif10_financial_history","name": "妙想财务 200 期", "group": "data", "order": 7.85},
    {"id": "calc_financial_derived","name": "计算财务指标",    "group": "calc", "order": 8},
    {"id": "build_current_rel",     "name": "构建当前关系",    "group": "mart", "order": 9},
    {"id": "build_profiles",        "name": "机构画像",        "group": "mart", "order": 10},
    {"id": "build_industry_stat",   "name": "行业统计",        "group": "mart", "order": 11},
    {"id": "build_trends",          "name": "生成股票列表",    "group": "mart", "order": 12},
    {"id": "calc_screening",        "name": "TDX选股筛选",     "group": "mart", "order": 13},
    {"id": "calc_sector_momentum",  "name": "板块动量分析",    "group": "mart", "order": 14},
    {"id": "build_external_attention","name": "外部关注快照",  "group": "mart", "order": 15},
    {"id": "build_stage_features",  "name": "阶段特征构建",    "group": "mart", "order": 16},
    {"id": "calc_risk_factors",     "name": "风险因子",        "group": "mart", "order": 16.5},
    {"id": "calc_prediction_outcomes","name": "预测 outcome", "group": "mart", "order": 16.6},
    {"id": "build_turtle_features", "name": "海龟执行特征",    "group": "mart", "order": 17.5},
    {"id": "calc_inst_scores",      "name": "机构评分",        "group": "mart", "order": 18},
    {"id": "calc_stock_scores",     "name": "股票评分",        "group": "mart", "order": 19},
]

# 硬依赖：failed → 跳过本步骤
HARD_DEPS = {
    "sync_calendar": [],
    "sync_raw": ["sync_calendar"],
    "match_inst": ["sync_raw"],
    "sync_market_data": ["sync_calendar", "match_inst"],
    "sync_financial": ["sync_calendar"],
    "gen_events": ["match_inst"],
    "calc_returns": ["gen_events"],
    "sync_industry": ["sync_calendar", "match_inst"],
    "sync_surveys": ["sync_calendar"],
    "sync_qfii": ["sync_calendar"],
    "sync_margin": ["sync_calendar"],
    "sync_lhb": ["sync_calendar"],
    "sync_aif10_holder_count": ["sync_calendar"],
    "sync_aif10_valuation_quantile": ["sync_calendar"],
    "sync_aif10_peer_valuation": ["sync_calendar"],
    "sync_aif10_forecast_consensus": ["sync_calendar"],
    "sync_aif10_financial_history": ["sync_calendar"],
    "calc_financial_derived": ["sync_financial"],
    "build_current_rel": ["gen_events"],
    "build_profiles": ["build_current_rel"],
    "build_industry_stat": ["build_current_rel"],
    "build_trends": ["build_current_rel"],
    "calc_screening": ["sync_market_data"],
    "calc_sector_momentum": ["sync_market_data", "sync_industry"],
    "build_external_attention": [],
    "build_stage_features": ["build_trends", "calc_sector_momentum"],
    "build_turtle_features": ["build_stage_features"],
    "calc_inst_scores": ["build_profiles", "build_industry_stat"],
    "calc_stock_scores": ["calc_inst_scores", "build_stage_features"],
}

# 软依赖：failed/skipped → 继续执行但标注 data_completeness='partial'
SOFT_DEPS = {
    "calc_returns": ["sync_market_data"],
    "build_current_rel": ["calc_returns", "sync_industry"],
    "build_profiles": ["calc_returns"],
    "build_industry_stat": ["calc_returns", "sync_industry"],
    "build_trends": ["calc_returns", "sync_industry"],
    "calc_screening": ["calc_financial_derived"],
    "calc_sector_momentum": ["build_trends"],
    "build_external_attention": [],
    "build_stage_features": ["calc_financial_derived"],
    "build_turtle_features": [],
    "calc_inst_scores": ["calc_returns"],
    "calc_stock_scores": ["calc_returns", "build_external_attention"],
}

MANUAL_ONLY_STEPS = {"calc_screening", "build_turtle_features"}

STEP_BUDGET_SECONDS = {
    "sync_calendar": 20,
    "sync_market_data": 30,
    "sync_financial": 45,
    "sync_raw": 45,
    "sync_industry": 30,
    "sync_surveys": 60,
    "sync_qfii": 60,
    "sync_margin": 60,
    "sync_lhb": 60,
    "sync_aif10_holder_count": 60,
    "sync_aif10_valuation_quantile": 60,
    "sync_aif10_peer_valuation": 60,
    "sync_aif10_forecast_consensus": 60,
    "sync_aif10_financial_history": 60,
    "calc_financial_derived": 45,
    "build_stage_features": 45,
    "build_external_attention": 45,
    "calc_stock_scores": 45,
    "calc_inst_scores": 45,
}

STEP_SOURCE_DOMAINS = {
    "sync_calendar": ("trading_calendar", "akshare_calendar", 3),
    "sync_market_data": ("kline_daily", "tdxhub_quote", 1),
    "sync_financial": ("financial_gpcw_8q", "tdxhub_gpcw", 1),
    "sync_raw": ("holders_top10_float", "tdxhub_holders", 1),
    "sync_industry": ("industry_sw", "tdxhub_block", 1),
    "sync_surveys": ("institution_survey", "aif10_survey", 2),
    "sync_qfii": ("qfii_holding_quarterly", "aif10_qfii", 2),
    "sync_lhb": ("lhb_daily", "aif10_lhb", 2),
    "sync_margin": ("margin_financing", "aif10_margin", 2),
    "sync_aif10_holder_count": ("aif10_holder_count", "aif10", 2),
    "sync_aif10_valuation_quantile": ("aif10_valuation_quantile", "aif10", 2),
    "sync_aif10_peer_valuation": ("aif10_peer_valuation", "aif10", 2),
    "sync_aif10_forecast_consensus": ("aif10_forecast_consensus", "aif10", 2),
    "sync_aif10_financial_history": ("aif10_financial_history", "aif10", 2),
}

DAILY_NON_CRITICAL_STEPS = {
    "sync_surveys",
    "calc_financial_derived",
    "build_external_attention",
    "build_stage_features",
    "calc_stock_scores",
}

_is_running = False
_stop_requested = False
_run_context = None
_last_run_context = None
_audit_snapshot_refresh_task = None


class _RunStopped(Exception):
    """用户主动停止当前更新链路。"""


def _raise_if_stop():
    if _stop_requested:
        raise _RunStopped("用户已停止")


def _step_budget_seconds(step_id: str) -> Optional[int]:
    return STEP_BUDGET_SECONDS.get(step_id)


def _plan_with_budgets(plan: dict) -> dict:
    out = dict(plan or {})
    steps = list(out.get("steps") or [])
    budgets = {step_id: STEP_BUDGET_SECONDS[step_id] for step_id in steps if step_id in STEP_BUDGET_SECONDS}
    out["budgets"] = budgets
    out["estimated_budget_s"] = sum(int(value or 0) for value in budgets.values())
    return out


def _critical_daily_plan(plan: dict) -> dict:
    out = dict(plan or {})
    steps = list(out.get("steps") or [])
    removed = [step for step in steps if step in DAILY_NON_CRITICAL_STEPS]
    out["steps"] = [step for step in steps if step not in DAILY_NON_CRITICAL_STEPS]
    skip_reasons = dict(out.get("skip_reasons") or {})
    for step in removed:
        skip_reasons[step] = "daily critical sync skips non-critical dashboard/research step; run full smart or nightly research"
    out["skip_reasons"] = skip_reasons
    if removed:
        reasons = list(out.get("reason") or [])
        reasons.append("daily critical_only filtered: " + ", ".join(removed))
        out["reason"] = reasons
        out["critical_only_removed_steps"] = removed
    return _plan_with_budgets(out)


def _ensure_calendar_step_for_data_fetch(steps: list[str]) -> list[str]:
    """Insert calendar preflight before any source fetch step."""

    if "sync_calendar" in steps:
        return steps
    if not any(step in CALENDAR_DATA_FETCH_STEPS for step in steps):
        return steps
    return ["sync_calendar", *steps]


def _ensure_trading_calendar_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_trading INTEGER DEFAULT 1
        )
        """
    )


def _trading_calendar_status(conn, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    try:
        _ensure_trading_calendar_table(conn)
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt, MIN(trade_date) AS min_date, MAX(trade_date) AS max_date
              FROM dim_trading_calendar
             WHERE is_trading = 1
            """
        ).fetchone()
    except Exception as exc:
        return {
            "exists": False,
            "count": 0,
            "min_date": None,
            "max_date": None,
            "latest_completed_trade_date": None,
            "needs_refresh": True,
            "reason": f"calendar_query_failed: {exc}",
        }

    count = int((row["cnt"] if hasattr(row, "keys") else row[0]) or 0) if row else 0
    min_date = (row["min_date"] if hasattr(row, "keys") else row[1]) if row else None
    max_date = (row["max_date"] if hasattr(row, "keys") else row[2]) if row else None
    cover_target = (now.date() + timedelta(days=CALENDAR_FUTURE_COVER_DAYS)).strftime("%Y-%m-%d")
    latest_trade = latest_completed_trade_date(conn, now=now) if count else None
    reasons = []
    if count < CALENDAR_MIN_ROWS:
        reasons.append(f"rows<{CALENDAR_MIN_ROWS}")
    if not max_date or str(max_date) < cover_target:
        reasons.append(f"max_date<{cover_target}")
    if not latest_trade:
        reasons.append("no_completed_trade_date")
    return {
        "exists": True,
        "count": count,
        "min_date": min_date,
        "max_date": max_date,
        "latest_completed_trade_date": latest_trade,
        "needs_refresh": bool(reasons),
        "reason": ",".join(reasons) if reasons else "fresh",
    }


async def _refresh_trading_calendar(conn) -> int:
    from services.akshare_client import fetch_trading_calendar

    days = await fetch_trading_calendar()
    unique_days = sorted({str(day)[:10] for day in days if day})
    if len(unique_days) < CALENDAR_MIN_ROWS:
        raise RuntimeError(f"交易日历刷新结果过少: {len(unique_days)}")

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO dim_trading_calendar(trade_date, is_trading)
            VALUES (?, 1)
            """,
            [(day,) for day in unique_days],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(unique_days)


async def _step_sync_calendar(conn) -> dict:
    """全局数据获取前置步骤：先确认交易日历可用且覆盖未来窗口。"""

    before = _trading_calendar_status(conn)
    refreshed = 0
    if before["needs_refresh"]:
        logger.info(f"[交易日历] 需要刷新: {before['reason']}")
        refreshed = await _refresh_trading_calendar(conn)
    after = _trading_calendar_status(conn)
    if after["needs_refresh"] or not after["latest_completed_trade_date"]:
        return {
            "count": refreshed,
            "status": "failed",
            "error": f"交易日历不可用: {after['reason']}",
            "calendar": after,
        }

    logger.info(
        "[交易日历] ready: latest=%s range=%s~%s rows=%d refreshed=%d",
        after["latest_completed_trade_date"],
        after["min_date"],
        after["max_date"],
        after["count"],
        refreshed,
    )
    return {
        "count": refreshed,
        "status": "completed",
        "latest_trade_date": after["latest_completed_trade_date"],
        "calendar": after,
        "message": (
            f"latest={after['latest_completed_trade_date']} "
            f"range={after['min_date']}~{after['max_date']} refreshed={refreshed}"
        ),
    }


def _touch_run_heartbeat(step_id: Optional[str] = None):
    if not _run_context:
        return
    _run_context["heartbeat_at"] = datetime.now().isoformat()
    if step_id:
        _run_context["step_id"] = step_id


def _record_step_source_state(conn, step_id: str, status: str, error_text: Optional[str] = None) -> None:
    spec = STEP_SOURCE_DOMAINS.get(step_id)
    if not spec:
        return
    data_domain, source_name, source_tier = spec
    try:
        from services.source_watermarks import record_source_failure, resolve_source_failures

        if status in {"failed", "blocked", "partial"}:
            record_source_failure(
                conn,
                data_domain=data_domain,
                source_name=source_name,
                source_tier=source_tier,
                error_type=f"step_{status}",
                last_error=error_text or status,
            )
        elif status in {"completed", "skipped"}:
            resolve_source_failures(conn, data_domain=data_domain, source_name=source_name)
    except Exception as exc:
        logger.warning("[source_failure_queue] update failed for %s: %s", step_id, exc)


def _set_run_context(mode: str, step_id: Optional[str] = None, step_name: Optional[str] = None, step_ids=None):
    global _run_context
    _run_context = {
        "mode": mode,
        "step_id": step_id,
        "step_name": step_name,
        "step_ids": list(step_ids) if step_ids else None,
        "started_at": datetime.now().isoformat(),
        "heartbeat_at": datetime.now().isoformat(),
    }


def _is_daily_critical_context() -> bool:
    return bool(_run_context and _run_context.get("critical_only"))


def _set_last_noop_context(mode: str, message: str):
    global _last_run_context
    now = datetime.now().isoformat()
    _last_run_context = {
        "mode": mode,
        "step_id": None,
        "step_name": None,
        "step_ids": [],
        "started_at": now,
        "finished_at": now,
        "noop": True,
        "message": message,
    }


def _finish_run_context(extra: Optional[dict] = None):
    global _run_context, _last_run_context
    if _run_context:
        ctx = dict(_run_context)
        ctx["finished_at"] = datetime.now().isoformat()
        if extra:
            ctx.update(extra)
        _last_run_context = ctx
    _run_context = None
    # 跑完任何更新后立即让 audit 缓存失效，下一次 /update/audit 走最新数据
    try:
        from services.audit import invalidate_audit_cache
        from services.etf_snapshot_manager import invalidate_etf_snapshot_cache

        invalidate_audit_cache()
        invalidate_etf_snapshot_cache()
    except Exception as e:
        logger.warning(f"[更新结束] 缓存失效操作异常: {e}")


_DERIVED_RESET_TABLES = [
    ("events", "fact_institution_event"),
    ("current_rel", "mart_current_relationship"),
    ("profiles", "mart_institution_profile"),
    ("industry_stat", "mart_institution_industry_stat"),
    ("trends", "mart_stock_trend"),
    ("steps", "step_status"),
]


_INDUSTRY_RESET_TABLES = [
    ("setup_snapshots", "fact_setup_snapshot"),
    ("current_rel", "mart_current_relationship"),
    ("profiles", "mart_institution_profile"),
    ("industry_stat", "mart_institution_industry_stat"),
    ("trends", "mart_stock_trend"),
    ("sector_momentum", "mart_sector_momentum"),
    ("industry_context_latest", "dim_stock_industry_context_latest"),
    ("quality_latest", "dim_stock_quality_latest"),
    ("stage_latest", "dim_stock_stage_latest"),
    ("turtle_latest", "dim_stock_turtle_latest"),
    ("stock_archetype_fact", "fact_stock_archetype"),
    ("stock_archetype_latest", "dim_stock_archetype_latest"),
    ("steps", "step_status"),
]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def _reset_tables(conn, tables):
    counts = {}
    missing_tables = []
    existing_tables = []
    conn.execute("BEGIN TRANSACTION")
    try:
        for key, table_name in tables:
            if not _table_exists(conn, table_name):
                counts[key] = 0
                missing_tables.append(table_name)
                continue
            existing_tables.append(table_name)
            counts[key] = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

        for table_name in existing_tables:
            conn.execute(f"DELETE FROM {table_name}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts, missing_tables


def _is_audit_snapshot_refreshing() -> bool:
    return bool(_audit_snapshot_refresh_task and not _audit_snapshot_refresh_task.done())


def _refresh_holder_audit_snapshot_sync(source: str):
    from services.audit import refresh_quality_audit_snapshot

    conn = get_conn(timeout=120)
    try:
        refresh_quality_audit_snapshot(conn, source=source)
    finally:
        conn.close()


def _schedule_holder_audit_snapshot_refresh(source: str):
    global _audit_snapshot_refresh_task
    if _is_audit_snapshot_refreshing():
        logger.info("[审计快照] 已有刷新任务在运行，跳过重复触发")
        return

    async def _run():
        try:
            logger.info(f"[审计快照] 开始刷新: {source}")
            await asyncio.to_thread(_refresh_holder_audit_snapshot_sync, source)
            logger.info(f"[审计快照] 刷新完成: {source}")
        except Exception as exc:
            logger.warning(f"[审计快照] 刷新失败: {source}: {exc}")

    _audit_snapshot_refresh_task = asyncio.create_task(_run())


def _prime_step_status_rows(conn, active_step_ids, *, inactive_mode: str = "idle",
                            skip_reasons: Optional[dict] = None):
    """在后台任务真正启动前，先把本轮 step_status 写成 pending/idle/skipped。"""
    valid_ids = {s["id"] for s in STEPS}
    conn.execute(
        "DELETE FROM step_status WHERE step_id NOT IN ({})".format(
            ",".join("?" * len(valid_ids))
        ), list(valid_ids)
    )
    selected = set(active_step_ids or [])
    skip_reasons = skip_reasons or {}
    for s in STEPS:
        sid = s["id"]
        if sid in selected:
            conn.execute("""
                INSERT OR REPLACE INTO step_status
                (step_id, group_name, step_name, step_order, status, error, records, started_at, finished_at)
                VALUES (?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL)
            """, (sid, s["group"], s["name"], s["order"]))
        else:
            status = "skipped" if inactive_mode == "skipped" else "idle"
            error = skip_reasons.get(sid, "数据已是最新，无需更新") if status == "skipped" else None
            conn.execute("""
                INSERT OR REPLACE INTO step_status
                (step_id, group_name, step_name, step_order, status, error, records, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, NULL, NULL)
            """, (sid, s["group"], s["name"], s["order"], status, error))
    conn.commit()


def _scope_rows(rows, context: Optional[dict]):
    if not rows:
        return []
    if context:
        step_ids = context.get("step_ids") or []
        if step_ids:
            selected = set(step_ids)
            scoped = [r for r in rows if r.get("step_id") in selected]
            if scoped:
                return scoped
        step_id = context.get("step_id")
        if step_id:
            scoped = [r for r in rows if r.get("step_id") == step_id]
            if scoped:
                return scoped
    return [
        r for r in rows
        if (r.get("status") and r.get("status") != "idle")
        or r.get("started_at") or r.get("finished_at") or r.get("records")
    ]


def _summarize_rows(rows):
    summary = {
        "total": len(rows),
        "done": 0,
        "completed": 0,
        "partial": 0,
        "failed": 0,
        "blocked": 0,
        "skipped": 0,
        "stopped": 0,
        "running": 0,
        "pending": 0,
        "latest_at": "",
    }
    latest_ms = 0
    for row in rows:
        status = row.get("status")
        if status in {"completed", "partial", "failed", "blocked", "skipped", "stopped"}:
            summary["done"] += 1
        if status == "completed":
            summary["completed"] += 1
        elif status == "partial":
            summary["partial"] += 1
        elif status == "failed":
            summary["failed"] += 1
        elif status == "blocked":
            summary["blocked"] += 1
        elif status == "skipped":
            summary["skipped"] += 1
        elif status == "stopped":
            summary["stopped"] += 1
        elif status == "running":
            summary["running"] += 1
        elif status == "pending":
            summary["pending"] += 1
        ts = row.get("finished_at") or row.get("started_at") or ""
        parsed = _parse_sync_time(ts)
        if parsed:
            ms = parsed.timestamp()
            if ms >= latest_ms:
                latest_ms = ms
                summary["latest_at"] = ts
    summary["pct"] = round(summary["done"] / summary["total"] * 100) if summary["total"] else 0
    return summary


def _is_blocking_upstream_state(conn, step_id: str) -> bool:
    row = conn.execute("SELECT status, error FROM step_status WHERE step_id = ?", (step_id,)).fetchone()
    if not row:
        return False
    status = row["status"] if isinstance(row, dict) or hasattr(row, "__getitem__") else row[0]
    error = (row["error"] if isinstance(row, dict) or hasattr(row, "__getitem__") else row[1]) or ""
    if status in {"failed", "blocked", "stopped"}:
        return True
    if status != "skipped":
        return False
    benign_tokens = ("无需更新", "已是最新", "无新增", "已完整")
    return not any(token in error for token in benign_tokens)


def _mode_label(mode: Optional[str]) -> str:
    return {
        "smart": "智能更新",
        "single": "单步更新",
        "all": "全量更新",
    }.get(mode or "", "更新")


def _build_status_summary(rows, running: bool, stop_requested: bool,
                          run_context: Optional[dict], last_run_context: Optional[dict]):
    def _format_done_counts(stat: dict) -> str:
        parts = [f"{stat.get('completed', 0)}成功"]
        if stat.get("partial", 0):
            parts.append(f"{stat.get('partial', 0)}有缺口")
        parts.append(f"{stat.get('failed', 0)}失败")
        if stat.get("blocked", 0):
            parts.append(f"{stat.get('blocked', 0)}阻断")
        if stat.get("skipped", 0):
            parts.append(f"{stat.get('skipped', 0)}已最新")
        else:
            parts.append("0已最新")
        return " · ".join(parts)

    def _active_rows(items):
        return [row for row in items if row.get("status") == "running"]

    def _activity_meta(items):
        active = _active_rows(items)
        active_names = [
            row.get("step_name") or row.get("step_id")
            for row in active
            if (row.get("step_name") or row.get("step_id"))
        ]
        latest_at = ""
        latest_ms = 0
        for row in items:
            for key in ("finished_at", "started_at"):
                parsed = _parse_sync_time(row.get(key) or "")
                if not parsed:
                    continue
                ms = parsed.timestamp()
                if ms >= latest_ms:
                    latest_ms = ms
                    latest_at = row.get(key) or ""
        return {
            "active_step_ids": [row.get("step_id") for row in active if row.get("step_id")],
            "active_step_names": active_names,
            "latest_status_at": latest_at,
        }

    if running and run_context:
        scoped = _scope_rows(rows, run_context)
        stat = _summarize_rows(scoped)
        activity = _activity_meta(scoped)
        mode = run_context.get("mode")
        label = _mode_label(mode)
        if mode == "single":
            scope_name = run_context.get("step_name") or label
            if stat["total"] > 1:
                message = f"{scope_name}续跑链路 · {stat['done']}/{stat['total']} · {stat['pct']}%"
            else:
                message = f"{scope_name} · {stat['done']}/{stat['total']} · {stat['pct']}%"
        else:
            message = f"{label} · {stat['done']}/{stat['total']} · {stat['pct']}%"
        if stop_requested:
            message = "停止中 · " + message
        if activity["active_step_names"]:
            message += " · 当前：" + " / ".join(activity["active_step_names"][:2])
        return {
            "kind": "running",
            "mode": mode,
            "show_progress": True,
            "pct": stat["pct"],
            "message": message,
            "counts": stat,
            **activity,
        }

    if last_run_context and last_run_context.get("noop"):
        return {
            "kind": "noop",
            "mode": last_run_context.get("mode"),
            "show_progress": False,
            "pct": 0,
            "message": last_run_context.get("message") or "数据已是最新，无需更新",
            "counts": {
                "total": 0,
                "done": 0,
                "completed": 0,
                "partial": 0,
                "failed": 0,
                "blocked": 0,
                "skipped": 0,
                "stopped": 0,
                "latest_at": last_run_context.get("finished_at") or "",
                "pct": 0,
            },
        }

    context = last_run_context
    scoped = _scope_rows(rows, context)
    if scoped:
        stat = _summarize_rows(scoped)
        activity = _activity_meta(scoped)
        mode = (context or {}).get("mode")
        label = _mode_label(mode)
        if mode == "single":
            title = (context or {}).get("step_name") or label
            if stat["total"] > 1:
                message = (
                    f"上次续跑 {title} · {_format_done_counts(stat)}"
                )
            else:
                message = (
                    f"上次单步 {title} · {_format_done_counts(stat)}"
                )
        else:
            message = (
                f"上次{label} {_format_done_counts(stat)}"
            )
        if stat["stopped"]:
            message += f" · {stat['stopped']}停止"
        return {
            "kind": "last",
            "mode": mode,
            "show_progress": True,
            "pct": stat["pct"],
            "message": message,
            "counts": stat,
            **activity,
        }

    return {
        "kind": "idle",
        "mode": None,
        "show_progress": False,
        "pct": 0,
        "message": "暂无更新记录",
        "counts": {
            "total": 0,
            "done": 0,
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "blocked": 0,
            "skipped": 0,
            "stopped": 0,
            "running": 0,
            "pending": 0,
            "latest_at": "",
            "pct": 0,
        },
        "active_step_ids": [],
        "active_step_names": [],
        "latest_status_at": "",
    }


def _tracked_stock_names(conn) -> dict[str, Optional[str]]:
    return load_tracked_stock_names(conn)


def _mark_steps_status(conn, step_ids, status: str, error: str, *,
                       started_at: Optional[str] = None,
                       finished_at: Optional[str] = None):
    if not step_ids:
        return
    now = datetime.now().isoformat()
    started = started_at if started_at is not None else now
    finished = finished_at if finished_at is not None else now
    for sid in step_ids:
        conn.execute(
            "UPDATE step_status SET status=?, error=?, "
            "started_at=COALESCE(started_at, ?), finished_at=? "
            "WHERE step_id=?",
            (status, error, started, finished, sid),
        )
    conn.commit()


def _fail_unfinished_steps(conn, step_ids, error: str):
    ids = [sid for sid in (step_ids or []) if sid]
    if not ids:
        return
    now = datetime.now().isoformat()
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"""
        UPDATE step_status
        SET status = 'failed',
            error = ?,
            started_at = COALESCE(started_at, ?),
            finished_at = ?
        WHERE step_id IN ({placeholders})
          AND (status IS NULL OR status IN ('pending', 'running'))
        """,
        [str(error)[:200], now, now, *ids],
    )
    conn.commit()


def _parse_sync_time(value: str):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _is_recent_successful_sync(state: dict, cooldown_hours: int = 24) -> bool:
    if not state:
        return False
    if state.get("last_error"):
        return False
    synced_at = _parse_sync_time(
        state.get("last_success_at") or state.get("last_attempt_at") or ""
    )
    if not synced_at:
        return False
    return datetime.now() - synced_at < timedelta(hours=cooldown_hours)


def _monthly_stale_cutoff() -> str:
    """月线只要求覆盖到“上一个完整月份”。

    月线源通常不会在月初就稳定提供当月 K，因此不能拿“本月第一天”当过期阈值，
    否则每次手动同步都会把上月已完整的股票重复判成 stale。
    """
    first_day_this_month = datetime.now().replace(day=1)
    first_day_prev_month = (first_day_this_month - timedelta(days=1)).replace(day=1)
    return first_day_prev_month.strftime("%Y-%m-%d")


def _collect_downstream_steps(start_step_id):
    """返回包含自身在内、受该步骤影响的下游步骤（按 DAG 顺序）"""
    valid_ids = {s["id"] for s in STEPS}
    reverse = {sid: set() for sid in valid_ids}
    for child, deps in HARD_DEPS.items():
        for dep in deps:
            if dep in valid_ids and child in valid_ids:
                reverse[dep].add(child)
    for child, deps in SOFT_DEPS.items():
        for dep in deps:
            if dep in valid_ids and child in valid_ids:
                reverse[dep].add(child)

    seen = {start_step_id}
    queue = [start_step_id]
    while queue:
        current = queue.pop(0)
        for nxt in reverse.get(current, set()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    if start_step_id not in MANUAL_ONLY_STEPS:
        seen -= MANUAL_ONLY_STEPS
    return [s["id"] for s in STEPS if s["id"] in seen]

# ============================================================
# 连通性检测
# ============================================================

_CONNECTIVITY_TARGETS = {
    # P7 (2026-04-28): 股东源切到 tdxhub. 这里 ping 一个 tdxhub 服务器列表里
    # 较稳定的端点, 仅作连通性指示 (实际抓取走 HolderFetcher 自带的池).
    "holdings_source": "http://gw.tdx.com.cn:7708/",
}

_CONNECTIVITY_LABELS = {
    "holdings_source": "股东源",
    "kline_source": "K线源",
    "industry_source": "行业源",
}

_CONNECTIVITY_CACHE_TTL_SECONDS = 300
_connectivity_cache = {
    "checked_at": 0.0,
    "data": None,
}


async def _compute_connectivity() -> dict:
    results = {}

    async def _check_holdings():
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                resp = await client.get(_CONNECTIVITY_TARGETS["holdings_source"])
                ok = resp.status_code < 500
                return {
                    "holdings_source": ok,
                    "holdings_source_detail": f"HTTP {resp.status_code}" if ok else None,
                }
        except Exception:
            return {"holdings_source": False}

    async def _check_kline():
        from services.akshare_client import test_kline_availability

        try:
            probe = await asyncio.wait_for(test_kline_availability(), timeout=20)
            payload = {"kline_source": bool(probe.get("available"))}
            payload["kline_source_degraded"] = bool(
                probe.get("available")
                and (probe.get("effective_source") or "") != "tdxhub"
            )
            if probe.get("detail"):
                payload["kline_source_detail"] = probe.get("detail")
            payload["kline_source_meta"] = probe
            return payload
        except Exception:
            return {
                "kline_source": False,
                "kline_source_degraded": False,
                "kline_source_detail": "probe timeout",
                "kline_source_meta": {
                    "available": False,
                    "detail": "probe timeout",
                },
            }

    async def _check_industry():
        """通达信行业源连通性探测：尝试从 tdxhy.cfg 服务器拉取首包。"""
        from services.tdx_industry_client import _fetch_tdxhy_bytes

        def _probe():
            try:
                data, source = _fetch_tdxhy_bytes()
                return bool(data), source
            except Exception:
                return False, ""

        try:
            # tdxhub 轮询 117 台服务器，冷启动找到第一台能用的服务器可能需要 ~16s，
            # 之后 server cursor 粘滞后续调用 <1s；给 25s 容忍冷启动延迟
            industry_ok, industry_source = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _probe),
                timeout=25,
            )
            payload = {"industry_source": industry_ok}
            if industry_source:
                payload["industry_source_detail"] = industry_source
            return payload
        except Exception:
            return {"industry_source": False}

    parts = await asyncio.gather(_check_holdings(), _check_kline(), _check_industry())
    for part in parts:
        results.update(part)

    unreachable = []
    for key, label in _CONNECTIVITY_LABELS.items():
        if not results.get(key):
            unreachable.append(label)
    degraded = []
    if results.get("kline_source_degraded"):
        degraded.append(f"K线源已降级（{results.get('kline_source_detail') or 'fallback'}）")

    if not unreachable and not degraded:
        results["message"] = "所有数据源正常"
    elif not unreachable:
        results["message"] = "；".join(degraded)
    else:
        parts = [f"{'、'.join(unreachable)}不可用，建议切换至手机热点"]
        parts.extend(degraded)
        results["message"] = "；".join(parts)
    return results


async def check_connectivity(force: bool = False) -> dict:
    """测试数据源连通性，返回 {source: bool, ...} + message

    K线源用 requests 库测试（与 akshare 保持一致），
    股东源用 httpx 测试（与 sync_raw 保持一致）。
    """
    now = time.time()
    cached = _connectivity_cache.get("data")
    checked_at = float(_connectivity_cache.get("checked_at") or 0.0)
    if not force and cached and (now - checked_at) < _CONNECTIVITY_CACHE_TTL_SECONDS:
        results = dict(cached)
        results["cached"] = True
        results["checked_at"] = datetime.fromtimestamp(checked_at).isoformat()
        results["cache_age_seconds"] = int(now - checked_at)
        return results

    results = await _compute_connectivity()
    _connectivity_cache["data"] = dict(results)
    _connectivity_cache["checked_at"] = now
    results["cached"] = False
    results["checked_at"] = datetime.fromtimestamp(now).isoformat()
    results["cache_age_seconds"] = 0
    return results


def get_cached_connectivity() -> dict:
    now = time.time()
    cached = _connectivity_cache.get("data")
    checked_at = float(_connectivity_cache.get("checked_at") or 0.0)
    if cached:
        results = dict(cached)
        results["cached"] = True
        results["checked_at"] = datetime.fromtimestamp(checked_at).isoformat() if checked_at else None
        results["cache_age_seconds"] = int(now - checked_at) if checked_at else None
        return results
    return {
        "holdings_source": None,
        "kline_source": None,
        "industry_source": None,
        "message": "尚未执行连通性探测",
        "cached": True,
        "pending": True,
        "checked_at": None,
        "cache_age_seconds": None,
    }


def _should_stop():
    return _stop_requested


# ============================================================
# 步骤执行函数
# ============================================================

# P7 (2026-04-28): 老的 _download_with_filter / 分页逻辑随同 miaoxiang
# RPT_F10_EH_FREEHOLDERS 一并下架. 抓取迁到 backend/scripts/ingest_holders_tdxhub.py
# (tdxhub.holders.HolderFetcher). 2026-05 起默认先落 raw, 再由 raw replay
# 生成 fact_top10_holder_period, 避免抓取失败污染 canonical.


async def _step_sync_raw(conn) -> dict:
    """十大流通股东 — 调 tdxhub raw→parse ingest.

    P7 起 canonical 表是 fact_top10_holder_period (替代 market_raw_holdings).
    抓取和重放逻辑封装在 backend/scripts/ingest_holders_tdxhub.py:run(), 这里
    直接 in-process 调用 — 复用 backend 的 conn, DuckDB 内部 mutex 保线程安全.
    (此前用 subprocess + 子进程自开 connection, 触发 IO Error: Could not set lock.)
    """
    canonical_where = (
        "holder_set = 'free' AND NOT is_secondary_class AND NOT is_exit_row"
    )
    before = conn.execute(
        f"SELECT COUNT(*) FROM fact_top10_holder_period WHERE {canonical_where}"
    ).fetchone()[0]
    logger.info(f"[下载/tdxhub] 现有 {before} 条 (fact_top10_holder_period free)")

    # in-process 调用. ingest 脚本里 4 个 worker thread 用 con_lock 串行写, 安全.
    # 解包 DuckConn 拿原生 duckdb connection (脚本里的 SQL 是原生写法).
    raw_con = conn._con if hasattr(conn, "_con") else conn

    from scripts.ingest_holders_tdxhub import run as ingest_run

    loop = asyncio.get_event_loop()

    def _do() -> dict:
        return ingest_run(workers=4, con=raw_con)

    progress = await loop.run_in_executor(None, _do)
    attempted = int(progress.get("done") or 0)
    ok_count = int(progress.get("ok") or 0)
    err_count = int(progress.get("err") or 0)
    skipped_unchanged = int(progress.get("skipped_unchanged") or 0)
    skipped_no_f10 = int(progress.get("skipped_no_f10") or 0)
    raw_written = int(progress.get("raw_written") or 0)
    parsed_count = int(progress.get("parsed") or 0)
    tdx_err = int(progress.get("tdx_err") or err_count)
    fallback_ok = int(progress.get("fallback_ok") or 0)
    err_rate = (err_count / attempted) if attempted else 0.0

    result_status = "completed"
    if err_count > 0 and ok_count == 0:
        result_status = "failed"
    elif err_rate >= 0.20:
        result_status = "failed"
    elif err_count > 0:
        result_status = "partial"

    after = conn.execute(
        f"SELECT COUNT(*) FROM fact_top10_holder_period WHERE {canonical_where}"
    ).fetchone()[0]
    written = max(0, after - before)

    from services.tdx_f10_extra_client import sync_tdx_f10_extra_facts

    try:
        extra_stats = await loop.run_in_executor(
            None,
            lambda: sync_tdx_f10_extra_facts(raw_con),
        )
    except Exception as exc:
        logger.warning("[下载/tdxhub] F10 extra parse failed: %s", exc)
        extra_stats = {
            "status": "failed",
            "raw_rows": 0,
            "holder_count_rows": 0,
            "trade_b_rows": 0,
            "control_rows": 0,
            "common_major_holder_rows": 0,
            "fund_holding_rows": 0,
            "fund_holding_rejected_rows": 0,
            "skipped_non_format_b": 0,
            "skipped_no_extra_section": 0,
            "errors": [str(exc)],
        }
    if (
        (
            extra_stats.get("errors")
            or extra_stats.get("fund_holding_rejected_rows")
            or extra_stats.get("status") == "completed_with_rejections"
        )
        and result_status == "completed"
    ):
        result_status = "partial"
    message = (
        f"attempted={attempted}, ok={ok_count}, err={err_count}, "
        f"tdx_err={tdx_err}, fallback_ok={fallback_ok}, "
        f"raw_written={raw_written}, parsed={parsed_count}, "
        f"unchanged={skipped_unchanged}, no_f10={skipped_no_f10}, "
        f"err_rate={err_rate:.1%}, written={written}, "
        f"extra_holder_count={int(extra_stats.get('holder_count_rows') or 0)}, "
        f"extra_trade_b={int(extra_stats.get('trade_b_rows') or 0)}, "
        f"extra_common_major={int(extra_stats.get('common_major_holder_rows') or 0)}, "
        f"extra_fund_holding={int(extra_stats.get('fund_holding_rows') or 0)}, "
        f"extra_fund_rejected={int(extra_stats.get('fund_holding_rejected_rows') or 0)}, "
        f"extra_skip_non_b={int(extra_stats.get('skipped_non_format_b') or 0)}, "
        f"extra_skip_empty={int(extra_stats.get('skipped_no_extra_section') or 0)}"
    )
    if result_status == "failed":
        logger.error(f"[下载/tdxhub] 失败: {message}")
    elif result_status == "partial":
        logger.warning(f"[下载/tdxhub] 部分失败: {message}")
    else:
        logger.info(f"[下载/tdxhub] 完成: +{written}, 总 {after}, 失败 {err_count}")
    return {
        "status": result_status,
        "count": after,
        "written": written,
        "total": after,
        "attempted": attempted,
        "ok": ok_count,
        "err": err_count,
        "err_rate": round(err_rate, 4),
        "skipped_unchanged": skipped_unchanged,
        "skipped_no_f10": skipped_no_f10,
        "tdx_f10_extra": extra_stats,
        "message": message,
    }


def _build_exclusion_set(conn) -> set:
    """构建排除股票代码集合（主数据过滤 + 类别规则 + 手工股票拉黑）"""
    from services.security_master import get_active_a_stock_codes

    excluded = set()
    invalid_master_codes = set()
    manual_rows = conn.execute(
        "SELECT DISTINCT stock_code FROM excluded_stocks WHERE stock_code IS NOT NULL"
    ).fetchall()
    manual_codes = {r["stock_code"] for r in manual_rows if r["stock_code"]}
    excluded.update(manual_codes)

    active_codes = None
    try:
        active_codes = get_active_a_stock_codes(conn)
    except Exception as e:
        logger.warning(f"[排除] 当前A股主数据不可用，回退分类规则: {e}")

    # 加载启用的排除类别
    categories = conn.execute(
        "SELECT category FROM exclusion_categories WHERE enabled = 1"
    ).fetchall()
    enabled_cats = {r["category"] for r in categories}

    # 从 fact_top10_holder_period (canonical, 替代 market_raw_holdings) 获取
    # 所有唯一的 (stock_code, stock_name).
    all_stocks = conn.execute(
        """
        SELECT DISTINCT stock_code, stock_name
          FROM fact_top10_holder_period
         WHERE stock_code IS NOT NULL
           AND holder_set = 'free'
           AND NOT is_secondary_class
           AND NOT is_exit_row
        """
    ).fetchall()

    for row in all_stocks:
        code = row["stock_code"]
        name = row["stock_name"] or ""

        if not code or len(code) != 6 or not code.isdigit():
            invalid_master_codes.add(code)
            excluded.add(code)
            continue

        # 基础有效性：必须出现在当前A股主数据里
        if active_codes is not None and code not in active_codes:
            invalid_master_codes.add(code)
            excluded.add(code)
            continue

        # ST/*ST：按股票名称判断
        if "ST" in enabled_cats and ("ST" in name.upper()):
            excluded.add(code)
            continue

        # 北交所：8/9开头的6位代码
        if "BSE" in enabled_cats and code and len(code) == 6 and code[0] in ("8", "9"):
            excluded.add(code)
            continue

        # 新三板：4开头（包含老三板400开头）
        if code and len(code) == 6 and code[0] == "4":
            if "OTC" in enabled_cats and code.startswith("400"):
                excluded.add(code)
                continue
            if "NEEQ" in enabled_cats:
                excluded.add(code)
                continue

        # B股：200/900开头
        if "B_SHARE" in enabled_cats and code and len(code) == 6:
            if code.startswith("200") or code.startswith("900"):
                excluded.add(code)
                continue

        # 退市股：名称含"退"字
        if "DELISTED" in enabled_cats and "退" in name:
            excluded.add(code)
            continue

    if invalid_master_codes:
        preview = ",".join(sorted(invalid_master_codes)[:10])
        suffix = "..." if len(invalid_master_codes) > 10 else ""
        logger.info(
            f"[排除] 当前A股主数据过滤 {len(invalid_master_codes)} 只无效代码: {preview}{suffix}"
        )

    logger.info(
        f"[排除] 主数据过滤 + 分类规则 + 手工拉黑，共 {len(excluded)} 只股票被排除"
        f"（手工 {len(manual_codes)} 只）"
    )
    return excluded


def _step_match_inst_sync(conn) -> int:
    """匹配跟踪机构持仓"""
    _step_match_inst_sync._insert_errors = 0
    institutions = conn.execute(
        "SELECT id, name, aliases FROM inst_institutions WHERE enabled = 1 AND blacklisted = 0 AND merged_into IS NULL"
    ).fetchall()

    if not institutions:
        logger.warning("[匹配] 无跟踪机构")
        return 0

    logger.info(f"[匹配] 加载 {len(institutions)} 个机构")

    # 构建排除集合
    excluded_codes = _build_exclusion_set(conn)

    match_rows = []
    seq = 0
    global_seen_names = set()
    for inst in institutions:
        inst_id = inst["id"]
        inst_name = inst["name"]
        names = [inst_name]
        try:
            aliases = json.loads(inst["aliases"] or "[]")
            names.extend([a for a in aliases if a])
        except Exception as e:
            logger.warning(f"[匹配] 机构 {inst_id} 别名解析失败: {e}")

        seen_names = set()
        for name in names:
            normalized = str(name or "").strip()
            if not normalized or normalized in seen_names or normalized in global_seen_names:
                continue
            match_rows.append((seq, inst_id, normalized))
            seen_names.add(normalized)
            global_seen_names.add(normalized)
            seq += 1

    if not match_rows:
        logger.warning("[匹配] 无可用机构名称/别名")
        return 0

    conn.execute("DROP TABLE IF EXISTS tmp_inst_match_names")
    conn.execute("""
        CREATE TEMP TABLE tmp_inst_match_names (
            seq INTEGER,
            institution_id TEXT,
            holder_name TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO tmp_inst_match_names VALUES (?, ?, ?)",
        match_rows,
    )

    conn.execute("DROP TABLE IF EXISTS tmp_inst_excluded_codes")
    conn.execute("CREATE TEMP TABLE tmp_inst_excluded_codes (stock_code TEXT)")
    if excluded_codes:
        conn.executemany(
            "INSERT INTO tmp_inst_excluded_codes VALUES (?)",
            [(code,) for code in sorted(excluded_codes)],
        )

    # 清空旧匹配结果并重建（事务保护）
    now = datetime.now().isoformat()

    conn.execute("DROP TABLE IF EXISTS tmp_inst_holdings_rebuild")
    conn.execute("""
        CREATE TEMP TABLE tmp_inst_holdings_rebuild AS
        SELECT institution_id, holder_name, holder_type, stock_code, stock_name,
               report_date, notice_date, holder_rank, hold_amount, hold_market_cap,
               hold_ratio, hold_change, hold_change_num, ? AS created_at
        FROM (
            SELECT
                candidate.*,
                ROW_NUMBER() OVER (
                    PARTITION BY holder_name, stock_code, report_date
                    ORDER BY match_seq, holder_rank_sort, notice_date DESC, institution_id
                ) AS rn
            FROM (
                SELECT
                    m.seq AS match_seq,
                    m.institution_id,
                    TRIM(r.holder_name) AS holder_name,
                    r.holder_type,
                    TRIM(r.stock_code) AS stock_code,
                    r.stock_name,
                    TRIM(r.report_date) AS report_date,
                    r.notice_date,
                    r.holder_rank,
                    COALESCE(TRY_CAST(r.holder_rank AS INTEGER), 999999) AS holder_rank_sort,
                    r.hold_amount,
                    r.hold_market_cap,
                    r.hold_ratio,
                    r.hold_change,
                    r.hold_change_num
                FROM fact_top10_holder_period r
                JOIN tmp_inst_match_names m ON TRIM(r.holder_name) = m.holder_name
                LEFT JOIN tmp_inst_excluded_codes x ON TRIM(r.stock_code) = x.stock_code
                WHERE x.stock_code IS NULL
                  AND r.holder_set = 'free'
                  AND NOT r.is_secondary_class
                  AND NOT r.is_exit_row
            ) candidate
        ) deduped
        WHERE rn = 1
    """, (now,))

    total = conn.execute("SELECT COUNT(*) FROM tmp_inst_holdings_rebuild").fetchone()[0]
    if total == 0:
        raise RuntimeError("[匹配] 已尝试写入持仓但重建结果为空")

    duplicate = conn.execute("""
        SELECT holder_name, stock_code, report_date, COUNT(*) AS cnt
        FROM tmp_inst_holdings_rebuild
        GROUP BY holder_name, stock_code, report_date
        HAVING COUNT(*) > 1
        LIMIT 1
    """).fetchone()
    if duplicate:
        raise RuntimeError(
            "[匹配] 重建结果存在重复键: "
            f"{duplicate['holder_name']} {duplicate['stock_code']} {duplicate['report_date']}"
        )

    try:
        for index_name in (
            "idx_ih_inst",
            "idx_ih_stock",
            "idx_ih_report",
            "idx_ih_unique_holder_stock_report",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")
        conn.execute("""
            CREATE OR REPLACE TABLE inst_holdings AS
            SELECT
                CAST(institution_id AS TEXT) AS institution_id,
                CAST(holder_name AS TEXT) AS holder_name,
                CAST(holder_type AS TEXT) AS holder_type,
                CAST(stock_code AS TEXT) AS stock_code,
                CAST(stock_name AS TEXT) AS stock_name,
                CAST(report_date AS TEXT) AS report_date,
                CAST(notice_date AS TEXT) AS notice_date,
                CAST(holder_rank AS INTEGER) AS holder_rank,
                CAST(hold_amount AS DOUBLE) AS hold_amount,
                CAST(hold_market_cap AS DOUBLE) AS hold_market_cap,
                CAST(hold_ratio AS DOUBLE) AS hold_ratio,
                CAST(hold_change AS TEXT) AS hold_change,
                CAST(hold_change_num AS DOUBLE) AS hold_change_num,
                CAST(created_at AS TEXT) AS created_at
            FROM tmp_inst_holdings_rebuild
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_inst ON inst_holdings(institution_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_stock ON inst_holdings(stock_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ih_report ON inst_holdings(report_date)")
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ih_unique_holder_stock_report
            ON inst_holdings(holder_name, stock_code, report_date)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[匹配] 完成: {total} 条持仓记录")
    return total


async def _step_match_inst(conn) -> int:
    """匹配跟踪机构持仓"""
    return await _run_blocking_db_task(_step_match_inst_sync)



# [Phase 5 已删除] _step_kline_monthly 和 _step_kline_daily 已被 _step_sync_market_data 替代


async def _step_gen_events(conn) -> dict:
    """生成机构事件 (§4.25 #2 幂等化: 输入签名不变就跳过 DELETE+INSERT)."""
    from services.event_engine import (
        generate_events, generate_exit_events,
        compute_gen_events_input_signature,
        get_last_step_fingerprint,
        update_step_fingerprint,
    )

    def _worker(worker_conn):
        new_fp, n_holdings = compute_gen_events_input_signature(worker_conn)
        last_fp, last_count = get_last_step_fingerprint(worker_conn, "gen_events")
        # 当前事件总数
        current_total = worker_conn.execute(
            "SELECT COUNT(*) FROM fact_institution_event"
        ).fetchone()[0]

        if last_fp and new_fp == last_fp and current_total > 0:
            logger.info(
                f"[事件] 输入签名未变 ({new_fp[:12]}...), 持仓 {n_holdings} 行, "
                f"跳过重建 ({current_total} 条事件保留, calc_returns 不需重算)"
            )
            return {
                "count": current_total,
                "status": "skipped",
                "skipped": current_total,
                "message": f"输入签名未变, 保留 {current_total} 条事件 (持仓 {n_holdings} 行)",
            }

        # 签名变化 → 重建
        if last_fp:
            logger.info(f"[事件] 输入签名变化 (旧 {last_fp[:12]}... → 新 {new_fp[:12]}...), 重建事件表")
        else:
            logger.info(f"[事件] 首次记录签名 ({new_fp[:12]}...), 生成事件")
        count = generate_events(worker_conn)
        count += generate_exit_events(worker_conn)
        update_step_fingerprint(worker_conn, "gen_events", new_fp, count)
        return {
            "count": count,
            "status": "completed",
            "written": count,
            "message": f"重建 {count} 条事件 (输入持仓 {n_holdings} 行)",
        }

    return await _run_blocking_db_task(_worker)


# Phase 3b-3: fact_institution_event_industry_snapshot 已退役。
# _capture_missing_event_industry_snapshots + snapshot 表本身均已删除,
# _step_build_industry_stat_sync / backtest_engine / scoring 统一走 dim_stock_tdx_industry 直 JOIN。


async def _run_blocking_db_task(task_fn, timeout: int = 120):
    """把纯本地重算移到线程里，避免阻塞状态接口轮询。"""
    def _worker():
        worker_conn = get_conn(timeout=timeout)
        try:
            return task_fn(worker_conn)
        finally:
            worker_conn.close()
    return await asyncio.to_thread(_worker)


async def _run_blocking_market_db_task(task_fn, timeout: int = 120):
    """把同时依赖业务库和行情库的本地重算移到线程里。"""
    from services.market_db import get_market_conn

    def _worker():
        worker_conn = get_conn(timeout=timeout)
        worker_mkt_conn = get_market_conn()
        try:
            return task_fn(worker_conn, worker_mkt_conn)
        finally:
            worker_mkt_conn.close()
            worker_conn.close()

    return await asyncio.to_thread(_worker)


async def _step_calc_returns(conn) -> int:
    """计算事件收益"""
    from services.return_engine import calculate_returns
    return await _run_blocking_db_task(calculate_returns)


def _median(sorted_vals: list) -> Optional[float]:
    """严格 median：偶数样本取中间两数均值，奇数取中间。审计 2.2.2 整改。"""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2


def _parse_notice_date(s: Optional[str]):
    """审计 2.2.3 整改：fact_institution_event.notice_date 实际是 YYYYMMDD 格式，
    之前代码用 '%Y-%m-%d' 解析必失败，被 except 静默吞掉，导致
    historical_median_holding_days 100% 空。
    """
    if not s:
        return None
    raw = str(s).strip()
    if not raw:
        return None
    try:
        if len(raw) >= 8 and raw[:8].isdigit():
            return datetime.strptime(raw[:8], "%Y%m%d")
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _step_build_profiles_sync(conn) -> int:
    """计算机构画像 mart_institution_profile"""
    from services.holdings import refresh_stock_latest_cache

    def _followability_hint(safe_cnt, safe_wr30, eff30, high_cnt, high_wr30):
        """根据可跟统计给出简短提示。"""
        safe_cnt = safe_cnt or 0
        high_cnt = high_cnt or 0
        if safe_cnt < 5:
            return "样本偏少"
        if eff30 is not None and eff30 >= 80 and (safe_wr30 or 0) >= 60:
            return "可跟性强"
        if high_cnt >= 5 and safe_wr30 is not None and high_wr30 is not None and high_wr30 + 10 < safe_wr30:
            return "不宜追高"
        if eff30 is not None and eff30 >= 50 and (safe_wr30 or 0) >= 50:
            return "可跟性中等"
        return "信号损耗较大"

    refresh_stock_latest_cache(conn)
    now = datetime.now().isoformat()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_institution_profile")

        institutions = conn.execute(
            "SELECT id, name, display_name, type FROM inst_institutions WHERE enabled = 1 AND blacklisted = 0 AND merged_into IS NULL"
        ).fetchall()

        # 刷新缓存表
        from services.holdings import refresh_stock_latest_cache
        refresh_stock_latest_cache(conn)
        # 一次性预计算所有机构的持仓摘要
        _inst_summaries = {}
        for r in conn.execute("""
            SELECT h.institution_id,
                   COUNT(*) as stock_count,
                   SUM(h.hold_market_cap) as total_cap,
                   MAX(h.notice_date) as latest_notice
            FROM inst_holdings h
            INNER JOIN (
                SELECT stock_code, max_rd
                FROM _cache_stock_latest_rd
            ) lat ON h.stock_code = lat.stock_code AND h.report_date = lat.max_rd
            GROUP BY h.institution_id
        """).fetchall():
            _inst_summaries[r["institution_id"]] = dict(r)

        count = 0
        for inst in institutions:
            _raise_if_stop()
            inst_id = inst["id"]

            # 基础统计
            stats = conn.execute("""
                SELECT COUNT(*) as total_events, COUNT(DISTINCT stock_code) as total_stocks,
                       COUNT(DISTINCT report_date) as total_periods
                FROM fact_institution_event WHERE institution_id = ?
            """, (inst_id,)).fetchone()

            # 收益统计（从增强后的 fact_institution_event 直接读取）
            returns = conn.execute("""
                SELECT AVG(e.gain_10d), AVG(e.gain_30d), AVG(e.gain_60d), AVG(e.gain_120d)
                FROM fact_institution_event e
                WHERE e.institution_id = ? AND e.gain_30d IS NOT NULL
            """, (inst_id,)).fetchone()

            # 回撤中位数在 Python 端计算，避免依赖数据库方言。
            # 审计 2.2.2 整改：偶数样本取 (a[n//2-1]+a[n//2])/2，严格 median
            dd_all_rows = conn.execute("""
                SELECT max_drawdown_30d, max_drawdown_60d FROM fact_institution_event
                WHERE institution_id = ? AND gain_30d IS NOT NULL
            """, (inst_id,)).fetchall()
            dd30_all = sorted(r[0] for r in dd_all_rows if r[0] is not None)
            dd60_all = sorted(r[1] for r in dd_all_rows if r[1] is not None)
            median_dd30 = _median(dd30_all)
            median_dd60 = _median(dd60_all)

            # 胜率（性能优化：合并 30/60/90/120 + total 为一次 query）
            wr_row = conn.execute("""
                SELECT
                    100.0 * SUM(CASE WHEN e.gain_30d > 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_30d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr30,
                    100.0 * SUM(CASE WHEN e.gain_60d > 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr60,
                    100.0 * SUM(CASE WHEN e.gain_90d > 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_90d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr90,
                    100.0 * SUM(CASE WHEN e.gain_120d > 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_120d IS NOT NULL THEN 1 ELSE 0 END), 0) AS wr120,
                    100.0 * SUM(CASE WHEN COALESCE(e.gain_30d, 0) > 0 OR COALESCE(e.gain_60d, 0) > 0
                                          OR COALESCE(e.gain_90d, 0) > 0 OR COALESCE(e.gain_120d, 0) > 0
                                     THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS total_wr
                FROM fact_institution_event e
                WHERE e.institution_id = ?
            """, (inst_id,)).fetchone()
            win30 = (wr_row["wr30"],)
            win60 = (wr_row["wr60"],)
            win90 = (wr_row["wr90"],)
            win120 = (wr_row["wr120"],)
            total_wr = (wr_row["total_wr"],)

            # Phase 1: 买入类事件统计（new_entry + increase）
            buy_stats = conn.execute("""
                SELECT COUNT(*) as cnt,
                       AVG(e.gain_30d) as avg30, AVG(e.gain_60d) as avg60, AVG(e.gain_120d) as avg120,
                       SUM(CASE WHEN e.gain_30d > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as wr30,
                       SUM(CASE WHEN e.gain_60d > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as wr60,
                       SUM(CASE WHEN e.gain_120d > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) as wr120
                FROM fact_institution_event e
                WHERE e.institution_id = ?
                  AND e.event_type IN ('new_entry', 'increase')
                  AND e.gain_30d IS NOT NULL
            """, (inst_id,)).fetchone()

            # 买入类回撤中位数（Python 端）
            buy_dd_rows = conn.execute("""
                SELECT max_drawdown_30d, max_drawdown_60d FROM fact_institution_event
                WHERE institution_id = ?
                  AND event_type IN ('new_entry', 'increase')
                  AND gain_30d IS NOT NULL
            """, (inst_id,)).fetchall()
            buy_dd30_vals = sorted(r[0] for r in buy_dd_rows if r[0] is not None)
            buy_dd60_vals = sorted(r[1] for r in buy_dd_rows if r[1] is not None)
            buy_median_dd30 = _median(buy_dd30_vals)
            buy_median_dd60 = _median(buy_dd60_vals)

            # 审计 5.2：退出/减持表现沉淀到 mart（原在 institution_read.load_institution_profiles 即席算）
            exit_row = conn.execute("""
                SELECT COUNT(*) AS cnt,
                    AVG(e.gain_30d) AS post_avg30,
                    AVG(e.gain_60d) AS post_avg60,
                    AVG(e.gain_120d) AS post_avg120,
                    100.0 * SUM(CASE WHEN e.gain_30d <= 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_30d IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS avoid30,
                    100.0 * SUM(CASE WHEN e.gain_60d <= 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS avoid60,
                    100.0 * SUM(CASE WHEN e.gain_120d <= 0 THEN 1 ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN e.gain_120d IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS avoid120
                FROM fact_institution_event e
                WHERE e.institution_id = ? AND e.event_type IN ('decrease', 'exit')
            """, (inst_id,)).fetchone()

            follow_stats = conn.execute("""
                SELECT
                    AVG(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_pct IS NOT NULL
                        THEN e.premium_pct END) as avg_premium,
                    COUNT(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_pct <= 5
                        THEN 1 END) as safe_cnt,
                    AVG(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_pct <= 5
                        THEN e.gain_30d END) as safe_avg30,
                    AVG(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_pct <= 5
                        THEN e.max_drawdown_30d END) as safe_dd30,
                    COALESCE(
                        SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_pct <= 5 AND e.gain_30d > 0
                            THEN 1 ELSE 0 END) * 100.0 /
                        NULLIF(SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_pct <= 5
                            THEN 1 ELSE 0 END), 0), 0) as safe_wr30,

                    COUNT(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'discount'
                        THEN 1 END) as discount_cnt,
                    COALESCE(
                        SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'discount' AND e.gain_30d > 0
                            THEN 1 ELSE 0 END) * 100.0 /
                        NULLIF(SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'discount'
                            THEN 1 ELSE 0 END), 0), 0) as discount_wr30,

                    COUNT(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'near_cost'
                        THEN 1 END) as near_cnt,
                    COALESCE(
                        SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'near_cost' AND e.gain_30d > 0
                            THEN 1 ELSE 0 END) * 100.0 /
                        NULLIF(SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'near_cost'
                            THEN 1 ELSE 0 END), 0), 0) as near_wr30,

                    COUNT(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'premium'
                        THEN 1 END) as premium_cnt,
                    COALESCE(
                        SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'premium' AND e.gain_30d > 0
                            THEN 1 ELSE 0 END) * 100.0 /
                        NULLIF(SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'premium'
                            THEN 1 ELSE 0 END), 0), 0) as premium_wr30,

                    COUNT(CASE
                        WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'high_premium'
                        THEN 1 END) as high_cnt,
                    COALESCE(
                        SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'high_premium' AND e.gain_30d > 0
                            THEN 1 ELSE 0 END) * 100.0 /
                        NULLIF(SUM(CASE
                            WHEN e.event_type IN ('new_entry', 'increase') AND e.premium_bucket = 'high_premium'
                            THEN 1 ELSE 0 END), 0), 0) as high_wr30
                FROM fact_institution_event e
                WHERE e.institution_id = ?
                  AND e.event_type IN ('new_entry', 'increase')
                  AND e.gain_30d IS NOT NULL
            """, (inst_id,)).fetchone()

            signal_transfer_eff = None
            buy_avg30 = buy_stats["avg30"] if buy_stats else None
            safe_avg30 = follow_stats["safe_avg30"] if follow_stats else None
            if buy_avg30 is not None and buy_avg30 > 0 and safe_avg30 is not None:
                signal_transfer_eff = round(safe_avg30 / buy_avg30 * 100, 2)

            follow_hint = _followability_hint(
                follow_stats["safe_cnt"] if follow_stats else 0,
                follow_stats["safe_wr30"] if follow_stats else None,
                signal_transfer_eff,
                follow_stats["high_cnt"] if follow_stats else 0,
                follow_stats["high_wr30"] if follow_stats else None,
            )

            # Phase 4: 持仓周期计算
            # historical_median_holding_days: 已闭合持仓周期的中位天数
            # new_entry.notice_date → exit.notice_date = 一个闭合周期
            closed_periods = []
            holding_events = conn.execute("""
                SELECT stock_code, event_type, notice_date
                FROM fact_institution_event
                WHERE institution_id = ? AND notice_date IS NOT NULL AND notice_date != ''
                ORDER BY stock_code, report_date
            """, (inst_id,)).fetchall()
            # 按 stock_code 分组找闭合周期
            # 审计 2.2.3 整改：notice_date 实际为 YYYYMMDD，用 _parse_notice_date 兼容
            _stock_entries = {}
            for he in holding_events:
                sc = he["stock_code"]
                if he["event_type"] == "new_entry":
                    _stock_entries[sc] = he["notice_date"]
                elif he["event_type"] == "exit" and sc in _stock_entries:
                    entry_d = _parse_notice_date(_stock_entries[sc])
                    exit_d = _parse_notice_date(he["notice_date"])
                    if entry_d and exit_d:
                        days = (exit_d - entry_d).days
                        if days > 0:
                            closed_periods.append(days)
                    _stock_entries.pop(sc, None)

            hist_median_days = None
            if closed_periods:
                closed_periods.sort()
                m = _median(closed_periods)
                hist_median_days = int(m) if m is not None else None

            # current_avg_held_days: 当前持仓的平均估算持有天数
            curr_held = conn.execute("""
                SELECT AVG(current_held_days) FROM mart_current_relationship
                WHERE institution_id = ? AND current_held_days IS NOT NULL
            """, (inst_id,)).fetchone()
            curr_avg_held = int(curr_held[0]) if curr_held and curr_held[0] else None

            # 当前持仓（从预计算的摘要中取）
            _s = _inst_summaries.get(inst_id, {})
            current = (_s.get("stock_count", 0), _s.get("total_cap"), _s.get("latest_notice"))

            # 近期事件统计
            recent = conn.execute("""
                SELECT COUNT(CASE WHEN e.event_type = 'new_entry' THEN 1 END),
                       COUNT(CASE WHEN e.event_type = 'increase' THEN 1 END),
                       COUNT(CASE WHEN e.event_type = 'exit' THEN 1 END)
                FROM fact_institution_event e
                INNER JOIN mart_current_relationship m
                    ON e.institution_id = m.institution_id AND e.stock_code = m.stock_code
                    AND e.report_date = m.report_date
                WHERE e.institution_id = ?
            """, (inst_id,)).fetchone()

            conn.execute("""
                INSERT OR REPLACE INTO mart_institution_profile
                (institution_id, institution_name, display_name, inst_type,
                 total_events, total_stocks, total_periods,
                 avg_gain_10d, avg_gain_30d, avg_gain_60d, avg_gain_120d,
                 win_rate_30d, win_rate_60d, win_rate_90d, win_rate_120d, total_win_rate,
                median_max_drawdown_30d, median_max_drawdown_60d,
                current_stock_count, current_total_cap, latest_notice_date,
                recent_new_entry_count, recent_increase_count, recent_exit_count,
                buy_event_count, buy_avg_gain_30d, buy_avg_gain_60d, buy_avg_gain_120d,
                buy_win_rate_30d, buy_win_rate_60d, buy_win_rate_120d,
                buy_median_max_drawdown_30d, buy_median_max_drawdown_60d,
                avg_premium_pct, safe_follow_event_count, safe_follow_win_rate_30d,
                safe_follow_avg_gain_30d, safe_follow_avg_drawdown_30d,
                premium_discount_event_count, premium_discount_win_rate_30d,
                premium_near_cost_event_count, premium_near_cost_win_rate_30d,
                premium_premium_event_count, premium_premium_win_rate_30d,
                premium_high_event_count, premium_high_win_rate_30d,
                signal_transfer_efficiency_30d, followability_hint,
                historical_median_holding_days, current_avg_held_days,
                exit_event_count, exit_post_avg_gain_30d, exit_post_avg_gain_60d, exit_post_avg_gain_120d,
                exit_avoid_loss_rate_30d, exit_avoid_loss_rate_60d, exit_avoid_loss_rate_120d,
                updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                inst_id, inst["name"], inst["display_name"], inst["type"],
                stats["total_events"], stats["total_stocks"], stats["total_periods"],
                returns[0], returns[1], returns[2], returns[3],
                win30[0] if win30 else None, win60[0] if win60 else None,
                win90[0] if win90 else None,
                win120[0] if win120 else None,
                total_wr[0] if total_wr else None,
                median_dd30, median_dd60,
                current[0], current[1], current[2],
                recent[0], recent[1], recent[2],
                buy_stats["cnt"] if buy_stats else 0,
                buy_stats["avg30"] if buy_stats else None,
                buy_stats["avg60"] if buy_stats else None,
                buy_stats["avg120"] if buy_stats else None,
                buy_stats["wr30"] if buy_stats else None,
                buy_stats["wr60"] if buy_stats else None,
                buy_stats["wr120"] if buy_stats else None,
                buy_median_dd30, buy_median_dd60,
                follow_stats["avg_premium"] if follow_stats else None,
                follow_stats["safe_cnt"] if follow_stats else 0,
                follow_stats["safe_wr30"] if follow_stats else None,
                follow_stats["safe_avg30"] if follow_stats else None,
                follow_stats["safe_dd30"] if follow_stats else None,
                follow_stats["discount_cnt"] if follow_stats else 0,
                follow_stats["discount_wr30"] if follow_stats else None,
                follow_stats["near_cnt"] if follow_stats else 0,
                follow_stats["near_wr30"] if follow_stats else None,
                follow_stats["premium_cnt"] if follow_stats else 0,
                follow_stats["premium_wr30"] if follow_stats else None,
                follow_stats["high_cnt"] if follow_stats else 0,
                follow_stats["high_wr30"] if follow_stats else None,
                signal_transfer_eff, follow_hint,
                hist_median_days, curr_avg_held,
                exit_row["cnt"] if exit_row else 0,
                exit_row["post_avg30"] if exit_row else None,
                exit_row["post_avg60"] if exit_row else None,
                exit_row["post_avg120"] if exit_row else None,
                exit_row["avoid30"] if exit_row else None,
                exit_row["avoid60"] if exit_row else None,
                exit_row["avoid120"] if exit_row else None,
                now
            ))
            count += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[画像] 完成: {count} 个机构")
    return count


async def _step_build_profiles(conn) -> int:
    """计算机构画像 mart_institution_profile"""
    return await _run_blocking_db_task(_step_build_profiles_sync)


def _step_build_trends_sync(conn) -> int:
    """计算股票趋势 mart_stock_trend.

    性能优化（审计性能诊断）：原 N+1 query 6× × 3285 股 ≈ 20k queries → 17s。
    重构为批量预聚合：一次性拉所有股票的 inst_holdings / latest_events / price_kline，
    in-memory 分组查询，目标耗时 < 3s。
    """
    from collections import defaultdict

    from services.holdings import refresh_stock_latest_cache
    try:
        conn.execute("SET preserve_insertion_order=false")
        conn.execute("SET threads=2")
    except Exception:
        pass
    refresh_stock_latest_cache(conn)
    now = datetime.now().isoformat()
    _mkt = None
    transaction_started = False
    try:
        # 股票列表骨架以 mart_current_relationship 为真相源，
        # 历史机构数/资金趋势再回看 inst_holdings 的近3期数据。
        stocks = conn.execute("""
            SELECT DISTINCT stock_code, stock_name
            FROM mart_current_relationship
            WHERE stock_code IS NOT NULL
        """).fetchall()
        logger.info(f"[趋势] 股票范围: {len(stocks)} 只")

        # 批量预聚合 1：每股近 3 期机构家数 + 合计持仓（取代 N+1 的 stock_periods + inst_counts/caps）
        # 一次性 aggregate：(code, report_date) → (n_inst, sum_cap)
        # 然后 Python 侧按 code 取最近 3 期
        agg_rows = conn.execute("""
            SELECT stock_code, report_date,
                   COUNT(DISTINCT institution_id) AS n_inst,
                   SUM(hold_market_cap) AS total_cap
            FROM inst_holdings
            WHERE stock_code IS NOT NULL
            GROUP BY stock_code, report_date
        """).fetchall()
        logger.info(f"[趋势] 持仓期数聚合: {len(agg_rows)} 行")
        per_stock_periods: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
        for r in agg_rows:
            per_stock_periods[r[0]].append((r[1], r[2] or 0, r[3] or 0))
        for v in per_stock_periods.values():
            v.sort(key=lambda t: t[0], reverse=True)

        # 批量预聚合 2：每股最近 3 个事件（取代 fact_institution_event N+1）
                # 用 window function 处理同组排序。
        ev_rows = conn.execute("""
            SELECT stock_code, event_type, holder_name, change_pct, report_date, notice_date
            FROM (
                SELECT stock_code, event_type, holder_name, change_pct, report_date, notice_date,
                       ROW_NUMBER() OVER (
                         PARTITION BY stock_code
                         ORDER BY report_date DESC, notice_date DESC
                       ) AS rn
                FROM fact_institution_event
            )
            WHERE rn <= 3
        """).fetchall()
        logger.info(f"[趋势] 最新事件聚合: {len(ev_rows)} 行")
        per_stock_events: dict[str, list] = defaultdict(list)
        for r in ev_rows:
            per_stock_events[r[0]].append(r)

        # 批量预聚合 3：每股最近 3 个月 K 线 + 最近 21 日 K 线。
        # 不在 DuckDB 里做全表 ORDER/window，避免中间排序把内存顶满；这里读必要列后按股票本地取 Top N。
        from services.market_db import get_market_conn as _get_mkt_conn
        _mkt = _get_mkt_conn()
        try:
            _mkt.execute("SET preserve_insertion_order=false")
            _mkt.execute("SET threads=2")
            _mkt.execute("SET memory_limit='2GB'")
        except Exception:
            pass
        trend_code_set = {stock["stock_code"] for stock in stocks if stock["stock_code"]}

        def _fetch_price_rows(freq: str, *, min_date: Optional[str] = None, batch_size: int = 200):
            rows = []
            codes = sorted(trend_code_set)
            def _append_plain(batch_rows):
                for row in batch_rows:
                    rows.append((row[0], row[1], row[2]))

            for i in range(0, len(codes), batch_size):
                batch = codes[i:i + batch_size]
                if not batch:
                    continue
                placeholders = ",".join("?" * len(batch))
                relation = KLINE_DAILY_QFQ_RELATION if freq == "daily" else "price_kline"
                if min_date:
                    _append_plain(_mkt.execute(
                        f"""
                        SELECT code, date, close
                        FROM {relation}
                        WHERE freq=? AND adjust='qfq' AND date >= ?
                          AND code IN ({placeholders})
                        """,
                        [freq, min_date, *batch],
                    ).fetchall())
                else:
                    _append_plain(_mkt.execute(
                        f"""
                        SELECT code, date, close
                        FROM {relation}
                        WHERE freq=? AND adjust='qfq'
                          AND code IN ({placeholders})
                        """,
                        [freq, *batch],
                    ).fetchall())
            return rows

        monthly_rows = _fetch_price_rows("monthly")
        logger.info(f"[趋势] 月线读取: {len(monthly_rows)} 行")
        per_stock_monthly: dict[str, list] = defaultdict(list)
        for code, trade_date, close in monthly_rows:
            if code in trend_code_set:
                per_stock_monthly[code].append((trade_date, close))
        for code, values in list(per_stock_monthly.items()):
            per_stock_monthly[code] = [
                close for _, close in sorted(values, key=lambda item: item[0], reverse=True)[:3]
            ]
        del monthly_rows

        # daily 限定近 45 天（21 交易日 + 缓冲），避免全表扫
        from datetime import timedelta
        cutoff_daily = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        daily_rows = _fetch_price_rows("daily", min_date=cutoff_daily)
        logger.info(f"[趋势] 日线读取: {len(daily_rows)} 行")
        if _mkt is not None:
            _mkt.close()
            _mkt = None
        per_stock_daily: dict[str, list] = defaultdict(list)
        for code, trade_date, close in daily_rows:
            if code in trend_code_set:
                per_stock_daily[code].append((trade_date, close))
        for code, values in list(per_stock_daily.items()):
            per_stock_daily[code] = [
                close for _, close in sorted(values, key=lambda item: item[0], reverse=True)[:21]
            ]
        del daily_rows
        import gc
        gc.collect()

        count = 0
        insert_batch = []
        for stock in stocks:
            _raise_if_stop()
            code = stock["stock_code"]
            name = stock["stock_name"]

            # 机构增减趋势：近 3 期家数 + 合计持仓（从 per_stock_periods 取）
            periods = per_stock_periods.get(code, [])[:3]
            inst_counts = [p[1] for p in periods]
            inst_caps = [p[2] for p in periods]
            while len(inst_counts) < 3:
                inst_counts.append(0)
                inst_caps.append(0)

            # 趋势计算
            def trend_str(vals):
                if len(vals) < 2:
                    return "—"
                parts = []
                for i in range(len(vals) - 1):
                    if vals[i] > vals[i + 1]:
                        parts.append("↑")
                    elif vals[i] < vals[i + 1]:
                        parts.append("↓")
                    else:
                        parts.append("→")
                return "".join(parts)

            inst_trend = trend_str(inst_counts)
            cap_trend = trend_str(inst_caps)

            # 最新事件（从 per_stock_events 取）
            latest_ev = per_stock_events.get(code, [])
            latest_events_json = json.dumps(
                [{"inst": (e[2] or "")[:20], "type": e[1], "pct": e[3]} for e in latest_ev],
                ensure_ascii=False
            ) if latest_ev else "[]"
            latest_rd = latest_ev[0][4] if latest_ev else None
            latest_nd = latest_ev[0][5] if latest_ev else None

            # 股价趋势（从预加载的 per_stock_monthly/daily 取）
            monthly_closes = per_stock_monthly.get(code, [])  # 已按 DESC
            daily_closes = per_stock_daily.get(code, [])       # 已按 DESC

            price_1m = None
            price_20d = None
            price_trend = "—"
            if len(monthly_closes) >= 2 and monthly_closes[1] and monthly_closes[1] > 0:
                price_1m = (monthly_closes[0] - monthly_closes[1]) / monthly_closes[1] * 100

            if len(daily_closes) >= 21 and daily_closes[20] and daily_closes[20] > 0:
                price_20d = (daily_closes[0] - daily_closes[20]) / daily_closes[20] * 100

            if len(monthly_closes) >= 3:
                ups = sum(
                    1 for i in range(len(monthly_closes) - 1)
                    if monthly_closes[i] and monthly_closes[i + 1]
                    and monthly_closes[i] > monthly_closes[i + 1]
                )
                if ups >= 2:
                    price_trend = "连涨"
                elif ups == 0:
                    price_trend = "连跌"
                else:
                    price_trend = "震荡"

            insert_batch.append((
                code, name, inst_counts[0], inst_counts[1], inst_counts[2],
                inst_caps[0], inst_caps[1], inst_caps[2], inst_trend, cap_trend,
                latest_events_json, latest_rd, latest_nd,
                price_1m, price_20d, price_trend, now
            ))
            count += 1

        logger.info(f"[趋势] 准备写入: {len(insert_batch)} 行")
        existing_codes = {
            r[0] for r in conn.execute("SELECT stock_code FROM mart_stock_trend").fetchall()
            if r[0]
        }
        expected_codes = {row[0] for row in insert_batch if row[0]}
        extra_codes = sorted(existing_codes - expected_codes)
        for i in range(0, len(extra_codes), 200):
            batch = extra_codes[i:i + 200]
            placeholders = ",".join("?" * len(batch))
            conn.execute(f"DELETE FROM mart_stock_trend WHERE stock_code IN ({placeholders})", batch)

        update_sql = """
            UPDATE mart_stock_trend SET
                stock_name=?,
                inst_count_t0=?, inst_count_t1=?, inst_count_t2=?,
                inst_cap_t0=?, inst_cap_t1=?, inst_cap_t2=?,
                inst_trend=?, cap_trend=?,
                latest_events=?, latest_report_date=?, latest_notice_date=?,
                price_1m_pct=?, price_20d_pct=?, price_trend=?,
                updated_at=?
            WHERE stock_code=?
        """
        update_batch = [
            (
                row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                row[8], row[9], row[10], row[11], row[12], row[13],
                row[14], row[15], row[16], row[0],
            )
            for row in insert_batch
            if row[0] in existing_codes
        ]
        for i in range(0, len(update_batch), 500):
            conn.executemany(update_sql, update_batch[i:i + 500])

        # 对新增股票补 insert；常规路径大多只有 update。
        insert_sql = """
            INSERT INTO mart_stock_trend
            (stock_code, stock_name, inst_count_t0, inst_count_t1, inst_count_t2,
             inst_cap_t0, inst_cap_t1, inst_cap_t2, inst_trend, cap_trend,
             latest_events, latest_report_date, latest_notice_date,
             price_1m_pct, price_20d_pct, price_trend, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        new_batch = [row for row in insert_batch if row[0] not in existing_codes]
        for i in range(0, len(new_batch), 500):
            conn.executemany(insert_sql, new_batch[i:i + 500])

        conn.commit()
        transaction_started = False
    except Exception:
        if _mkt is not None:
            _mkt.close()
        if transaction_started:
            conn.rollback()
        raise
    logger.info(f"[趋势] 完成: {count} 只股票")
    return count


async def _step_build_trends(conn) -> int:
    """计算股票趋势 mart_stock_trend"""
    return await _run_blocking_db_task(_step_build_trends_sync)


async def _step_sync_industry(conn) -> int:
    """通达信行业同步 — 拉取 tdxhy.cfg 并全量 upsert 到 dim_stock_tdx_industry"""
    from services.tdx_industry_client import sync_tdx_industry

    stock_names = _tracked_stock_names(conn)
    reconcile_gap_queue_snapshot(conn, stock_names=stock_names, datasets=("industry",), commit=True)

    detail = {
        "industry_sync": {
            "status": "running",
            "updated_rows": 0,
            "source": "",
            "source_degraded": False,
            "before_missing": summarize_gap_queue(conn, datasets=("industry",))["datasets"][0]["unresolved"],
            "after_missing": None,
            "gap_summary": summarize_gap_queue(conn, datasets=("industry",), limit_per_dataset=6)["datasets"][0],
        },
        "block_sync": {
            "status": "pending",
            "member_rows": 0,
            "catalog_rows": 0,
        },
    }

    count = 0

    def _push_progress():
        _update_step(
            conn,
            "sync_industry",
            error=json.dumps(detail, ensure_ascii=False),
            records=count,
        )

    _raise_if_stop()
    # sync_tdx_industry 是同步函数（TDX 服务器下载 + 本地解析 + executemany），
    # 放到线程池避免阻塞事件循环。DuckDB 连接不跨线程传递，在 executor 内
    # 单独开一个连接，写完后主线程的 conn 无需感知（写入同一个 DB 文件）。
    def _run_in_thread():
        thread_conn = get_conn(timeout=120)
        try:
            return sync_tdx_industry(thread_conn)
        finally:
            thread_conn.close()

    tdx_result = await asyncio.get_event_loop().run_in_executor(None, _run_in_thread)

    count = int(tdx_result.get("rows_upserted") or 0)
    errors = tdx_result.get("errors") or []

    if count == 0:
        mark_current_missing_as(
            conn,
            "industry",
            status="blocked",
            reason="通达信行业源无返回，当前未执行补齐",
            last_error=";".join(errors) or "tdx_industry_source_empty",
            stock_names=stock_names,
            commit=False,
        )
        gap_summary = summarize_gap_queue(conn, datasets=("industry",), limit_per_dataset=6)["datasets"][0]
        detail["industry_sync"] = {
            "status": "blocked",
            "updated_rows": 0,
            "source": tdx_result.get("source", ""),
            "source_degraded": False,
            "before_missing": detail["industry_sync"]["before_missing"],
            "after_missing": gap_summary["unresolved"],
            "reason": "通达信行业源无返回，当前未执行补齐",
            "errors": errors,
            "gap_summary": gap_summary,
        }
        conn.commit()
        _push_progress()
        logger.warning("[通达信行业] 未获取到数据")
        return 0

    reconcile_gap_queue_snapshot(conn, stock_names=stock_names, datasets=("industry",), commit=False)
    gap_summary = summarize_gap_queue(conn, datasets=("industry",), limit_per_dataset=6)["datasets"][0]
    detail["industry_sync"] = {
        "status": "partial" if gap_summary["unresolved"] else "success",
        "updated_rows": count,
        "source": tdx_result.get("source", ""),
        "source_degraded": False,
        "before_missing": detail["industry_sync"]["before_missing"],
        "after_missing": gap_summary["unresolved"],
        "fetched_at": tdx_result.get("fetched_at"),
        "l1_count": tdx_result.get("l1_count"),
        "l2_count": tdx_result.get("l2_count"),
        "l3_count": tdx_result.get("l3_count"),
        "errors": errors,
        "gap_summary": gap_summary,
    }
    conn.commit()
    _push_progress()
    logger.info(
        f"[通达信行业] 完成: {count} 只股票, "
        f"L1={tdx_result.get('l1_count')}/L2={tdx_result.get('l2_count')}/L3={tdx_result.get('l3_count')}"
    )
    return count


def _step_build_industry_stat_sync(conn) -> int:
    """计算机构在各行业 (TDX 一二三级) 的表现统计。

    [审计 4.4 标注] 口径：**当前行业**
    事件现任所属股票 → dim_stock_tdx_industry 的当前 tdx_l{1,2,3}。
    这意味着：股票被行业重分类时，历史事件会被映射到最新行业，
    机构过去在某一行业积累的真实能力会被后来的行业映射改写。

    Phase 3b-3 之前曾用 fact_institution_event_industry_snapshot 表存事件时点的
    行业快照, 已合并入 fact_institution_event 主表 (sw_level* 字段); 行业重分类
    历史影响请改读 fact_institution_event 内的 sw_level 字段或 dim_stock_tdx_industry.
    前端/解释层请明确标注"当前行业口径".
    """
    now = datetime.now().isoformat()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_institution_industry_stat")

        institutions = conn.execute(
            "SELECT id FROM inst_institutions WHERE enabled = 1 AND blacklisted = 0 AND merged_into IS NULL"
        ).fetchall()

        level_specs = [
            (1, "level1", "tdx_l1", "tdx_l1_name"),
            (2, "level2", "tdx_l2", "tdx_l2_name"),
            (3, "level3", "tdx_l3", "tdx_l3_name"),
        ]

        count = 0
        for inst in institutions:
            _raise_if_stop()
            inst_id = inst["id"]

            for _, level_name, code_col, name_col in level_specs:
                _raise_if_stop()
                rows = conn.execute(f"""
                    SELECT i.{code_col} AS tdx_code,
                           i.{name_col} AS industry_name,
                           COUNT(*) as cnt,
                           AVG(e.gain_30d) as avg30, AVG(e.gain_60d) as avg60,
                           AVG(e.gain_90d) as avg90, AVG(e.gain_120d) as avg120,
                           SUM(CASE WHEN e.gain_30d > 0 THEN 1 WHEN e.gain_30d IS NOT NULL THEN 0 ELSE NULL END)
                               * 100.0 / NULLIF(SUM(CASE WHEN e.gain_30d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr30,
                           SUM(CASE WHEN e.gain_60d > 0 THEN 1 WHEN e.gain_60d IS NOT NULL THEN 0 ELSE NULL END)
                               * 100.0 / NULLIF(SUM(CASE WHEN e.gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr60,
                           SUM(CASE WHEN e.gain_90d > 0 THEN 1 WHEN e.gain_90d IS NOT NULL THEN 0 ELSE NULL END)
                               * 100.0 / NULLIF(SUM(CASE WHEN e.gain_90d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr90,
                           SUM(CASE WHEN e.gain_30d > 0 OR e.gain_60d > 0 THEN 1 WHEN e.gain_30d IS NOT NULL OR e.gain_60d IS NOT NULL THEN 0 ELSE NULL END)
                               * 100.0 / NULLIF(SUM(CASE WHEN e.gain_30d IS NOT NULL OR e.gain_60d IS NOT NULL THEN 1 ELSE 0 END), 0) as wr_total,
                           AVG(e.max_drawdown_30d) as dd30, AVG(e.max_drawdown_60d) as dd60
                    FROM fact_institution_event e
                    INNER JOIN dim_stock_tdx_industry i ON i.stock_code = e.stock_code
                    WHERE e.institution_id = ?
                      AND i.{code_col} IS NOT NULL AND i.{code_col} != ''
                      AND i.{name_col} IS NOT NULL AND i.{name_col} != ''
                    GROUP BY i.{code_col}, i.{name_col}
                    HAVING cnt >= 1
                """, (inst_id,)).fetchall()

                for r in rows:
                    conn.execute("""
                        INSERT OR REPLACE INTO mart_institution_industry_stat
                        (institution_id, industry_level, industry_name, tdx_code, sample_events,
                         avg_gain_30d, avg_gain_60d, avg_gain_90d, avg_gain_120d,
                         win_rate_30d, win_rate_60d, win_rate_90d, total_win_rate,
                         max_drawdown_30d, max_drawdown_60d, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (inst_id, level_name, r["industry_name"], r["tdx_code"], r["cnt"],
                          r["avg30"], r["avg60"], r["avg90"], r["avg120"],
                          r["wr30"], r["wr60"], r["wr90"], r["wr_total"],
                          r["dd30"], r["dd60"], now))
                    count += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[行业统计] 完成: {count} 条 (基于 dim_stock_tdx_industry)")
    return count


async def _step_build_industry_stat(conn) -> int:
    """计算机构在各行业的表现统计"""
    return await _run_blocking_db_task(_step_build_industry_stat_sync)


async def _step_sync_market_data(conn) -> int:
    """同步行情数据：合并原 kline_monthly + kline_daily，写入 market.duckdb"""
    import json as _json
    from services.market_db import (
        get_market_conn, upsert_price_rows, upsert_price_kline_tdxhub_rows, update_sync_state,
        get_all_sync_states
    )
    from services.akshare_client import (
        fetch_stock_kline_monthly,
        fetch_stock_kline_daily,
        probe_stock_kline_fallback_preference,
    )
    from services.xdxr_client import sync_xdxr_for_codes

    mkt_conn = get_market_conn()
    sub_status = {}
    stock_names = _tracked_stock_names(conn)
    codes = list(stock_names.keys())
    total_rows = 0

    def _dataset_gap_summary(dataset: str, limit: int = 6) -> dict:
        return summarize_gap_queue(conn, datasets=(dataset,), limit_per_dataset=limit)["datasets"][0]

    def _push_progress():
        _update_step(
            conn,
            "sync_market_data",
            error=_json.dumps(sub_status, ensure_ascii=False),
            records=total_rows,
        )

    if not codes:
        mkt_conn.close()
        return 0

    reconcile_gap_queue_snapshot(
        conn,
        stock_names=stock_names,
        datasets=("monthly_kline", "daily_kline"),
        mkt_conn=mkt_conn,
        commit=True,
    )

    # --- 月 K ---
    monthly_rows_total = 0
    try:
        existing_monthly = {s["code"]: s for s in get_all_sync_states(mkt_conn, "monthly")}
        monthly_price_codes = {
            r["code"]
            for r in mkt_conn.execute(
                "SELECT DISTINCT code FROM price_kline WHERE freq='monthly' AND adjust='qfq'"
            ).fetchall()
        }
        missing_m = [c for c in codes if c not in monthly_price_codes]
        missing_m_set = set(missing_m)
        stale_cutoff = _monthly_stale_cutoff()
        # stale: 月线只要求覆盖到上一个完整月份；否则会在月初反复重拉上月已完整的数据
        stale_m = [c for c in codes if c in existing_monthly
                   and existing_monthly[c]["max_date"]
                   and existing_monthly[c]["max_date"] < stale_cutoff]
        cooldown_m = [
            c for c in stale_m
            if c in existing_monthly and _is_recent_successful_sync(existing_monthly[c])
        ]
        to_fetch_m = [
            c for c in set(missing_m + stale_m)
            if c not in set(cooldown_m)
        ]

        processed_m = 0
        success_m = 0
        failed_m_codes = []
        monthly_gap_before = _dataset_gap_summary("monthly_kline")
        sub_status["monthly_sync"] = {
            "status": "running" if to_fetch_m else "skipped",
            "done_codes": 0,
            "total_codes": len(to_fetch_m),
            "success_codes": 0,
            "rows": 0,
            "failed_count": 0,
            "failed_codes": [],
            "before_missing": monthly_gap_before["unresolved"],
            "after_missing": monthly_gap_before["unresolved"],
            "gap_summary": monthly_gap_before,
        }
        logger.info(f"[行情同步] 月K待同步: {len(to_fetch_m)} 只")
        if cooldown_m:
            logger.info(f"[行情同步] 月K跳过近期已校验: {len(cooldown_m)} 只")
        _push_progress()
        for code in to_fetch_m:
            _raise_if_stop()
            try:
                if code in missing_m_set:
                    mark_gap_retrying(
                        conn,
                        "monthly_kline",
                        code,
                        stock_name=stock_names.get(code),
                        reason="正在尝试补齐月K",
                        commit=False,
                    )
                kline_records, source = await fetch_stock_kline_monthly(code, limit=36, start_date="20230101")
                if kline_records:
                    rows_data = [
                        {"code": code, "date": str(r["date"])[:10], "freq": "monthly",
                         "adjust": "qfq", "open": r["open"], "high": r["high"],
                         "low": r["low"], "close": r["close"],
                         "volume": r.get("volume"), "amount": r.get("amount")}
                        for r in kline_records
                    ]
                    write_source = normalize_kline_write_source(source)
                    upsert_price_rows(mkt_conn, rows_data, source=write_source)
                    dates = [r["date"] for r in rows_data]
                    update_sync_state(mkt_conn, code, "monthly", source=write_source,
                                      min_date=min(dates), max_date=max(dates),
                                      row_count=len(rows_data))
                    success_m += 1
                    total_rows += len(rows_data)
                    monthly_rows_total += len(rows_data)
                    if code in missing_m_set:
                        mark_gap_resolved(
                            conn,
                            "monthly_kline",
                            code,
                            stock_name=stock_names.get(code),
                            reason="月K已补齐",
                            commit=False,
                        )
                else:
                    failed_m_codes.append(code)
                    empty_error = f"{source or 'unknown'}_empty"
                    update_sync_state(
                        mkt_conn, code, "monthly", row_count=0,
                        error=empty_error,
                    )
                    if code in missing_m_set:
                        mark_gap_failed(
                            conn,
                            "monthly_kline",
                            code,
                            stock_name=stock_names.get(code),
                            last_error=empty_error,
                            touched_attempt=False,
                            commit=False,
                        )
            except _RunStopped:
                monthly_gap = _dataset_gap_summary("monthly_kline")
                sub_status["monthly_sync"].update({
                    "status": "stopped",
                    "done_codes": processed_m,
                    "success_codes": success_m,
                    "rows": monthly_rows_total,
                    "failed_count": len(failed_m_codes),
                    "failed_codes": failed_m_codes[:20],
                    "current_code": code,
                    "after_missing": monthly_gap["unresolved"],
                    "gap_summary": monthly_gap,
                })
                _push_progress()
                raise
            except Exception as e:
                failed_m_codes.append(code)
                update_sync_state(
                    mkt_conn, code, "monthly", row_count=0,
                    error=str(e)[:200],
                )
                if code in missing_m_set:
                    mark_gap_failed(
                        conn,
                        "monthly_kline",
                        code,
                        stock_name=stock_names.get(code),
                        last_error=str(e)[:200],
                        touched_attempt=False,
                        commit=False,
                    )
                logger.warning(f"[行情同步] 月K {code} 失败: {e}")
            processed_m += 1
            monthly_gap = _dataset_gap_summary("monthly_kline")
            sub_status["monthly_sync"].update({
                "done_codes": processed_m,
                "success_codes": success_m,
                "rows": monthly_rows_total,
                "failed_count": len(failed_m_codes),
                "failed_codes": failed_m_codes[:20],
                "current_code": code,
                "after_missing": monthly_gap["unresolved"],
                "gap_summary": monthly_gap,
            })
            if len(to_fetch_m) <= 20 or processed_m == len(to_fetch_m) or processed_m % 10 == 0:
                logger.info(
                    f"[行情同步] 月K进度: {processed_m}/{len(to_fetch_m)}"
                    f"，失败 {len(failed_m_codes)}"
                )
                _push_progress()

        reconcile_gap_queue_snapshot(
            conn,
            stock_names=stock_names,
            datasets=("monthly_kline",),
            mkt_conn=mkt_conn,
            commit=False,
        )
        monthly_gap = _dataset_gap_summary("monthly_kline")
        sub_status["monthly_sync"] = {
            "status": (
                "skipped" if not to_fetch_m
                else ("success" if not failed_m_codes else "partial")
            ),
            "done_codes": processed_m,
            "success_codes": success_m,
            "total_codes": len(to_fetch_m),
            "rows": monthly_rows_total,
            "failed_count": len(failed_m_codes),
            "failed_codes": failed_m_codes[:20],
            "before_missing": monthly_gap_before["unresolved"],
            "after_missing": monthly_gap["unresolved"],
            "gap_summary": monthly_gap,
        }
        if failed_m_codes:
            logger.warning("[行情同步] 月K未获取到: " + ", ".join(failed_m_codes[:20]))
        _push_progress()
    except Exception as e:
        monthly_gap = _dataset_gap_summary("monthly_kline")
        sub_status["monthly_sync"] = {
            "status": "stopped" if isinstance(e, _RunStopped) else "failed",
            "done_codes": sub_status.get("monthly_sync", {}).get("done_codes", 0),
            "total_codes": sub_status.get("monthly_sync", {}).get("total_codes", 0),
            "rows": monthly_rows_total,
            "success_codes": sub_status.get("monthly_sync", {}).get("success_codes", 0),
            "failed_count": sub_status.get("monthly_sync", {}).get("failed_count", 0),
            "failed_codes": sub_status.get("monthly_sync", {}).get("failed_codes", []),
            "before_missing": sub_status.get("monthly_sync", {}).get("before_missing"),
            "after_missing": monthly_gap["unresolved"],
            "gap_summary": monthly_gap,
            "error": str(e)[:200],
        }
        _push_progress()
        if isinstance(e, _RunStopped):
            raise
        logger.error(f"[行情同步] 月K失败: {e}")

    # --- 日 K ---
    daily_rows_total = 0
    try:
        existing_daily = {s["code"]: s for s in get_all_sync_states(mkt_conn, "daily")}
        daily_price_codes = {
            r["code"]
            for r in mkt_conn.execute(
                f"SELECT DISTINCT code FROM {KLINE_DAILY_QFQ_RELATION} WHERE freq='daily' AND adjust='qfq'"
            ).fetchall()
        }
        missing_d = [c for c in codes if c not in daily_price_codes]
        missing_d_set = set(missing_d)
        # 用交易日历判断：max_date < 最新已收盘交易日 → 需补差额
        latest_trade_date = latest_completed_trade_date(conn) or datetime.now().strftime("%Y-%m-%d")
        # 查询当前停牌列表（从东财停复牌接口）
        suspended_codes = set()
        try:
            import akshare as ak
            tfp_df = await asyncio.to_thread(
                ak.stock_tfp_em, date=latest_trade_date.replace("-", "")
            )
            if tfp_df is not None and not tfp_df.empty:
                suspended_codes = {str(r).strip() for r in tfp_df["代码"].tolist() if r}
                logger.info(f"[行情同步] 停复牌接口: {len(suspended_codes)} 只股票当前停牌")
        except Exception as e:
            logger.warning(f"[行情同步] 停复牌查询失败（不影响同步）: {e}")

        stale_d = []
        suspended_d = []
        for c in codes:
            if c not in existing_daily:
                continue
            state = existing_daily[c]
            if not state.get("max_date") or state["max_date"] >= latest_trade_date:
                continue
            if c in suspended_codes:
                suspended_d.append(c)
                continue
            stale_d.append(c)
        uptodate_d = len(codes) - len(missing_d) - len(stale_d) - len(suspended_d)
        logger.info(f"[行情同步] 最新交易日={latest_trade_date}, 已最新={uptodate_d}只, 需补={len(stale_d)}只, 停牌={len(suspended_d)}只, 缺失={len(missing_d)}只")
        to_fetch_d = list(set(missing_d + stale_d))

        d_count = 0
        processed_d = 0
        failed_codes = []
        daily_concurrency = max(16, min(32, max(1, len(iter_tdx_servers())) * 4))
        progress_every = 10 if len(to_fetch_d) >= 10 else 1
        batch_size = 100
        sem = asyncio.Semaphore(daily_concurrency)
        total_source_stats = {}
        batch_source_stats = {}
        recent_batches = []
        batch_start_index = 1
        batch_started_at = time.monotonic()
        daily_gap_before = _dataset_gap_summary("daily_kline")

        def _daily_fetch_start_date(code: str) -> str:
            state = existing_daily.get(code)
            if state and state.get("max_date"):
                try:
                    start_dt = datetime.strptime(state["max_date"][:10], "%Y-%m-%d") - timedelta(days=20)
                    return start_dt.strftime("%Y%m%d")
                except Exception:
                    return "20230101"
            return "20230101"

        daily_end_date = datetime.now().strftime("%Y%m%d")
        daily_preflight = None
        daily_prefer_fallback = False
        if to_fetch_d:
            sample_code = to_fetch_d[0]
            try:
                daily_preflight = await probe_stock_kline_fallback_preference(
                    sample_code,
                    _daily_fetch_start_date(sample_code),
                    daily_end_date,
                )
                daily_prefer_fallback = bool(daily_preflight.get("prefer_fallback"))
            except Exception as e:
                daily_preflight = {
                    "sample_code": sample_code,
                    "prefer_fallback": False,
                    "reason": f"preflight_failed:{str(e)[:120]}",
                    "elapsed_sec": 0.0,
                    "timeout_failures": 0,
                }
                logger.warning(f"[行情同步] 日K预检失败，继续默认 tdxhub 首选: {e}")

        sub_status["daily_sync"] = {
            "status": "running" if to_fetch_d else "skipped",
            "done_codes": 0,
            "total_codes": len(to_fetch_d),
            "success_codes": 0,
            "rows": 0,
            "failed_count": 0,
            "failed_codes": [],
            "concurrency": daily_concurrency,
            "batch_size": batch_size,
            "source_stats": {},
            "recent_batches": [],
            "before_missing": daily_gap_before["unresolved"],
            "after_missing": daily_gap_before["unresolved"],
            "gap_summary": daily_gap_before,
            "prefer_fallback": daily_prefer_fallback,
            "strategy_reason": (daily_preflight or {}).get("reason"),
            "preflight_sample": (daily_preflight or {}).get("sample_code"),
        }
        if daily_prefer_fallback:
            logger.warning(
                f"[行情同步] 日K批次预检命中 fallback-first: "
                f"{daily_preflight.get('sample_code')} -> {daily_preflight.get('reason')}"
            )
        elif daily_preflight:
            logger.info(
                f"[行情同步] 日K批次预检通过: "
                f"{daily_preflight.get('sample_code')} -> {daily_preflight.get('reason')}"
            )
        logger.info(f"[行情同步] 日K待同步: {len(to_fetch_d)} 只，并发 {daily_concurrency}")
        _push_progress()

        async def _fetch_one(code):
            nonlocal d_count, daily_rows_total
            async with sem:
                _raise_if_stop()
                started_at = time.monotonic()
                source = ""
                rows_written = 0
                ok = False
                try:
                    if code in missing_d_set:
                        mark_gap_retrying(
                            conn,
                            "daily_kline",
                            code,
                            stock_name=stock_names.get(code),
                            reason="正在尝试补齐日K",
                            commit=False,
                        )
                    start_date = _daily_fetch_start_date(code)

                    kline_records, source = await fetch_stock_kline_daily(
                        code,
                        days=150,
                        start_date=start_date,
                        end_date=daily_end_date,
                        prefer_fallback=daily_prefer_fallback,
                    )
                    if kline_records:
                        rows_data = [
                            {"code": code, "date": str(r["date"])[:10], "freq": "daily",
                             "adjust": "qfq", "open": r["open"], "high": r["high"],
                             "low": r["low"], "close": r["close"],
                             "volume": r.get("volume"), "amount": r.get("amount")}
                            for r in kline_records
                        ]
                        rows_written = len(rows_data)
                        write_source = normalize_kline_write_source(source)
                        if write_source.startswith("tdxhub"):
                            upsert_price_kline_tdxhub_rows(mkt_conn, rows_data, source=write_source)
                        else:
                            upsert_price_rows(mkt_conn, rows_data, source=write_source)
                        dates = [r["date"] for r in rows_data]
                        update_sync_state(mkt_conn, code, "daily", source=write_source,
                                          min_date=min(dates), max_date=max(dates),
                                          row_count=len(rows_data))
                        d_count += 1
                        daily_rows_total += len(rows_data)
                        ok = True
                        if code in missing_d_set:
                            mark_gap_resolved(
                                conn,
                                "daily_kline",
                                code,
                                stock_name=stock_names.get(code),
                                reason="日K已补齐",
                                commit=False,
                            )
                    else:
                        failed_codes.append(code)
                        empty_error = f"{source or 'unknown'}_empty"
                        update_sync_state(
                            mkt_conn, code, "daily", row_count=0,
                            error=empty_error,
                        )
                        if code in missing_d_set:
                            mark_gap_failed(
                                conn,
                                "daily_kline",
                                code,
                                stock_name=stock_names.get(code),
                                last_error=empty_error,
                                touched_attempt=False,
                                commit=False,
                            )
                except _RunStopped:
                    raise
                except Exception as e:
                    failed_codes.append(code)
                    update_sync_state(
                        mkt_conn, code, "daily", row_count=0,
                        error=str(e)[:200],
                    )
                    if code in missing_d_set:
                        mark_gap_failed(
                            conn,
                            "daily_kline",
                            code,
                            stock_name=stock_names.get(code),
                            last_error=str(e)[:200],
                            touched_attempt=False,
                            commit=False,
                        )
                    logger.warning(f"[行情同步] 日K {code} 失败: {e}")
                return {
                    "code": code,
                    "ok": ok,
                    "source": source or "unknown",
                    "rows": rows_written,
                    "elapsed_sec": round(time.monotonic() - started_at, 3),
                }

        tasks = [asyncio.create_task(_fetch_one(code)) for code in to_fetch_d]
        try:
            for task in asyncio.as_completed(tasks):
                _raise_if_stop()
                result = await task
                processed_d += 1
                latest_fetch = {
                    "code": result.get("code"),
                    "source": result.get("source"),
                    "ok": bool(result.get("ok")),
                    "rows": int(result.get("rows") or 0),
                    "elapsed_sec": float(result.get("elapsed_sec") or 0.0),
                }
                if latest_fetch["ok"]:
                    _record_sync_source_metric(
                        total_source_stats,
                        latest_fetch["source"],
                        latest_fetch["elapsed_sec"],
                        latest_fetch["rows"],
                    )
                    _record_sync_source_metric(
                        batch_source_stats,
                        latest_fetch["source"],
                        latest_fetch["elapsed_sec"],
                        latest_fetch["rows"],
                    )

                if processed_d % batch_size == 0 or processed_d == len(to_fetch_d):
                    batch_summary = _build_daily_sync_batch_summary(
                        batch_start_index,
                        processed_d,
                        stats=batch_source_stats,
                        batch_elapsed_sec=time.monotonic() - batch_started_at,
                    )
                    recent_batches = (recent_batches + [batch_summary])[-5:]
                    logger.info(
                        f"[行情同步] 日K批次 {batch_summary['range_start']}-{batch_summary['range_end']}: "
                        f"来源 {_format_sync_source_metrics(batch_summary['source_stats'])}"
                        f"，失败 {batch_summary['failed_count']}"
                        f"，批耗时 {batch_summary['batch_elapsed_sec']:.2f}s"
                    )
                    batch_source_stats = {}
                    batch_start_index = processed_d + 1
                    batch_started_at = time.monotonic()

                if (
                    processed_d == len(to_fetch_d)
                    or processed_d % progress_every == 0
                ):
                    daily_gap = _dataset_gap_summary("daily_kline")
                    sub_status["daily_sync"].update({
                        "done_codes": processed_d,
                        "success_codes": d_count,
                        "rows": daily_rows_total,
                        "failed_count": len(failed_codes),
                        "failed_codes": failed_codes[:20],
                        "concurrency": daily_concurrency,
                        "batch_size": batch_size,
                        "source_stats": _snapshot_sync_source_metrics(total_source_stats),
                        "recent_batches": recent_batches,
                        "latest_fetch": latest_fetch,
                        "after_missing": daily_gap["unresolved"],
                        "gap_summary": daily_gap,
                    })
                    logger.info(
                        f"[行情同步] 日K进度: {processed_d}/{len(to_fetch_d)}"
                        f"，失败 {len(failed_codes)}"
                        f"，并发 {daily_concurrency}"
                    )
                    _push_progress()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        reconcile_gap_queue_snapshot(
            conn,
            stock_names=stock_names,
            datasets=("daily_kline",),
            mkt_conn=mkt_conn,
            commit=False,
        )
        daily_gap = _dataset_gap_summary("daily_kline")
        total_rows += daily_rows_total
        status = "skipped" if not to_fetch_d else ("success" if not failed_codes else "partial")
        sub_status["daily_sync"] = {
            "status": status,
            "done_codes": processed_d,
            "success_codes": d_count,
            "total_codes": len(to_fetch_d),
            "rows": daily_rows_total,
            "failed_count": len(failed_codes),
            "failed_codes": failed_codes[:20],  # 只保留前 20 个
            "concurrency": daily_concurrency,
            "batch_size": batch_size,
            "source_stats": _snapshot_sync_source_metrics(total_source_stats),
            "recent_batches": recent_batches,
            "before_missing": daily_gap_before["unresolved"],
            "after_missing": daily_gap["unresolved"],
            "gap_summary": daily_gap,
            "prefer_fallback": daily_prefer_fallback,
            "strategy_reason": (daily_preflight or {}).get("reason"),
            "preflight_sample": (daily_preflight or {}).get("sample_code"),
        }
        if failed_codes:
            logger.warning("[行情同步] 日K未获取到: " + ", ".join(failed_codes[:20]))
        _push_progress()
    except Exception as e:
        daily_gap = _dataset_gap_summary("daily_kline")
        sub_status["daily_sync"] = {
            "status": "stopped" if isinstance(e, _RunStopped) else "failed",
            "done_codes": sub_status.get("daily_sync", {}).get("done_codes", 0),
            "total_codes": sub_status.get("daily_sync", {}).get("total_codes", 0),
            "rows": daily_rows_total,
            "success_codes": sub_status.get("daily_sync", {}).get("success_codes", 0),
            "failed_count": len(failed_codes) if "failed_codes" in locals() else 0,
            "failed_codes": failed_codes[:20] if "failed_codes" in locals() else [],
            "concurrency": sub_status.get("daily_sync", {}).get("concurrency"),
            "batch_size": sub_status.get("daily_sync", {}).get("batch_size"),
            "source_stats": sub_status.get("daily_sync", {}).get("source_stats", {}),
            "recent_batches": sub_status.get("daily_sync", {}).get("recent_batches", []),
            "latest_fetch": sub_status.get("daily_sync", {}).get("latest_fetch"),
            "before_missing": sub_status.get("daily_sync", {}).get("before_missing"),
            "after_missing": daily_gap["unresolved"],
            "gap_summary": daily_gap,
            "prefer_fallback": sub_status.get("daily_sync", {}).get("prefer_fallback", False),
            "strategy_reason": sub_status.get("daily_sync", {}).get("strategy_reason"),
            "preflight_sample": sub_status.get("daily_sync", {}).get("preflight_sample"),
            "error": str(e)[:200],
        }
        _push_progress()
        if isinstance(e, _RunStopped):
            raise
        logger.error(f"[行情同步] 日K失败: {e}")

    # --- xdxr ---
    try:
        sub_status["xdxr_sync"] = {
            "status": "running" if codes else "skipped",
            "done_codes": 0,
            "total_codes": len(codes),
            "success_codes": 0,
            "rows": 0,
            "failed_count": 0,
            "failed_codes": [],
            "skipped_recent": 0,
            "concurrency": 0,
        }
        _push_progress()

        def _on_xdxr_progress(progress: dict):
            sub_status["xdxr_sync"] = progress
            _push_progress()

        xdxr_status = await sync_xdxr_for_codes(
            mkt_conn,
            codes,
            should_stop=_raise_if_stop,
            progress_callback=_on_xdxr_progress,
        )
        total_rows += xdxr_status.get("rows", 0)
        sub_status["xdxr_sync"] = xdxr_status
        _push_progress()
    except Exception as e:
        sub_status["xdxr_sync"] = {
            "status": "stopped" if isinstance(e, _RunStopped) else "failed",
            "done_codes": sub_status.get("xdxr_sync", {}).get("done_codes", 0),
            "total_codes": sub_status.get("xdxr_sync", {}).get("total_codes", 0),
            "rows": sub_status.get("xdxr_sync", {}).get("rows", 0),
            "success_codes": sub_status.get("xdxr_sync", {}).get("success_codes", 0),
            "failed_count": sub_status.get("xdxr_sync", {}).get("failed_count", 0),
            "failed_codes": sub_status.get("xdxr_sync", {}).get("failed_codes", []),
            "skipped_recent": sub_status.get("xdxr_sync", {}).get("skipped_recent", 0),
            "error": str(e)[:200],
        }
        _push_progress()
        if isinstance(e, _RunStopped):
            raise
        logger.error(f"[行情同步] xdxr失败: {e}")

    sub_status["sync_state_refresh"] = {"status": "success"}
    mkt_conn.close()

    # 把子阶段详情写入 step_status.error（JSON 格式）
    _push_progress()

    logger.info(f"[行情同步] 完成: {total_rows} 行")
    return total_rows


async def _step_build_current_rel(conn) -> int:
    """构建 mart_current_relationship 物化表"""
    from services.holdings import build_current_relationship
    return await _run_blocking_db_task(build_current_relationship)


async def _step_sync_financial(conn) -> dict:
    """同步财务数据（tdxhub finance）.

    §4.25 #4: 返回 dict 含 partial 语义 — 当 5 个子阶段
    (history/snapshot/capital/indicator/gpcw) 中部分失败但部分成功时,
    status='partial', 让 UI 显示有缺口而非误报 completed.
    """
    import json as _json
    from services.financial_client import sync_financial_data
    from services.tdx_affair_client import sync_gpcw_files

    progress_records = 0
    last_progress = {}
    daily_critical = _is_daily_critical_context()

    def _on_progress(progress: dict):
        nonlocal progress_records, last_progress
        last_progress = progress or {}
        progress_records = ((progress.get("summary") or {}).get("records") or 0)
        _update_step(
            conn,
            "sync_financial",
            error=_json.dumps(progress, ensure_ascii=False),
            records=progress_records,
        )

    total = await sync_financial_data(
        conn,
        progress_callback=_on_progress,
        should_stop=_raise_if_stop,
        include_history=not daily_critical,
        include_capital=not daily_critical,
        include_indicator=not daily_critical,
    )

    def _sync_gpcw_and_features(worker_conn):
        from scripts.profile_tdx_gpcw_fields import profile_tdx_gpcw_fields
        from scripts.build_tdx_gpcw_auto_features import build_tdx_gpcw_auto_features

        result = sync_gpcw_files(worker_conn, quarters=12)
        affected_dates = list(result.get("affected_report_dates") or [])
        if affected_dates:
            profile = profile_tdx_gpcw_fields(worker_conn)
            auto_features = build_tdx_gpcw_auto_features(
                worker_conn,
                profile_run_id=profile["profile_run_id"],
                report_dates=affected_dates,
            )
            result["field_profile"] = {
                "profile_run_id": profile["profile_run_id"],
                "field_count": profile["field_count"],
                "model_candidate_count": profile["model_candidate_count"],
            }
            result["auto_feature_rebuild"] = {
                "report_dates": auto_features["rebuilt_report_dates"],
                "rows": auto_features["rebuilt_rows"],
                "features": auto_features["rebuilt_features"],
            }
        return result

    if daily_critical:
        gpcw_progress = {
            "status": "skipped",
            "quarters": 12,
            "files_synced": 0,
            "rows_upserted": 0,
            "wide_rows_upserted": 0,
            "errors": [],
            "skip_reason": "daily critical sync skips gpcw history profiling",
        }
    else:
        try:
            gpcw_result = await _run_blocking_db_task(
                _sync_gpcw_and_features,
                timeout=300,
            )
            gpcw_progress = {
                "status": "partial" if gpcw_result.get("errors") else "success",
                "quarters": 12,
                "files_synced": int(gpcw_result.get("files_synced") or 0),
                "rows_upserted": int(gpcw_result.get("rows_upserted") or 0),
                "wide_rows_upserted": int(gpcw_result.get("wide_rows_upserted") or 0),
                "skipped_unchanged": int(gpcw_result.get("skipped_unchanged") or 0),
                "skipped_existing": int(gpcw_result.get("skipped_existing") or 0),
                "affected_report_dates": list(gpcw_result.get("affected_report_dates") or []),
                "deleted_slices": dict(gpcw_result.get("deleted_slices") or {}),
                "manifest_rows_upserted": int(gpcw_result.get("manifest_rows_upserted") or 0),
                "field_profile": dict(gpcw_result.get("field_profile") or {}),
                "auto_feature_rebuild": dict(gpcw_result.get("auto_feature_rebuild") or {}),
                "errors": list(gpcw_result.get("errors") or []),
            }
        except Exception as exc:
            logger.exception("[sync_financial] gpcw history backfill failed")
            gpcw_progress = {
                "status": "error",
                "quarters": 12,
                "files_synced": 0,
                "rows_upserted": 0,
                "wide_rows_upserted": 0,
                "errors": [str(exc)],
            }

    merged_progress = dict(last_progress or {})
    merged_progress["gpcw_history"] = gpcw_progress

    # 子阶段状态聚合: 任一 failed/error → partial (除非全失败)
    sub_status_map = {
        "history": (merged_progress.get("history_backfill") or {}).get("status"),
        "snapshot": (merged_progress.get("snapshot_sync") or {}).get("status"),
        "capital": (merged_progress.get("capital_behavior") or {}).get("status"),
        "indicator": (merged_progress.get("financial_indicator") or {}).get("status"),
        "gpcw": gpcw_progress.get("status"),
    }
    failed_subs = [k for k, v in sub_status_map.items() if v in ("failed", "error")]
    partial_subs = [k for k, v in sub_status_map.items() if v == "partial"]
    success_count = sum(1 for v in sub_status_map.values() if v == "success")

    history_rows = int((merged_progress.get("history_backfill") or {}).get("rows") or 0)
    snapshot_rows = int((merged_progress.get("snapshot_sync") or {}).get("rows") or 0)
    capital_rows = int((merged_progress.get("capital_behavior") or {}).get("rows") or 0)
    indicator_rows = int((merged_progress.get("financial_indicator") or {}).get("rows") or 0)
    gpcw_rows = int(gpcw_progress.get("rows_upserted") or 0)

    base_msg = (
        f"历史 {history_rows} / 最新 {snapshot_rows} / "
        f"资本 {capital_rows} / 指标 {indicator_rows} / GPCW {gpcw_rows}"
    )
    if failed_subs and success_count == 0:
        agg_status = "failed"
        message = f"{base_msg} · 全部子阶段失败"
    elif failed_subs:
        agg_status = "partial"
        message = f"{base_msg} · {'/'.join(failed_subs)} 失败"
    elif partial_subs:
        agg_status = "partial"
        message = f"{base_msg} · {'/'.join(partial_subs)} 部分"
    else:
        agg_status = "completed"
        message = base_msg

    # 写最终 detail (含子阶段 + message + status), 供前端 renderFinancialSyncDetail 渲染
    detail_payload = dict(merged_progress)
    detail_payload["message"] = message
    detail_payload["status"] = agg_status
    detail_payload["count"] = int(total)
    _update_step(
        conn,
        "sync_financial",
        error=_json.dumps(detail_payload, ensure_ascii=False),
        records=progress_records,
    )
    return detail_payload


async def _step_calc_financial_derived(conn) -> int:
    """计算财务派生指标"""
    from services.financial_client import calc_financial_derived
    return await _run_blocking_db_task(calc_financial_derived)


async def _step_calc_screening(conn) -> int:
    """TDX 选股筛选"""
    from services.screening_engine import run_all_screens
    return await _run_blocking_market_db_task(run_all_screens)


async def _step_calc_sector_momentum(conn) -> int:
    """板块动量分析 + 双重确认信号"""
    from services.sector_momentum import calc_sector_momentum, calc_dual_confirm
    from services.industry_context_engine import build_stock_industry_context

    def _worker(worker_conn, worker_mkt_conn):
        sector_count = calc_sector_momentum(worker_conn, worker_mkt_conn)
        dual_count = calc_dual_confirm(worker_conn)
        context_count = build_stock_industry_context(worker_conn)
        return sector_count + dual_count + context_count

    return await _run_blocking_market_db_task(_worker)


async def _step_build_external_attention(conn) -> int:
    """外部关注快照"""
    from services.external_attention import sync_external_attention_snapshot

    return await _run_blocking_db_task(sync_external_attention_snapshot)


async def _step_sync_surveys(conn) -> dict:
    """机构调研同步（D8 数据源）"""
    from services.institution_survey_client import sync_institution_surveys

    def _worker(worker_conn):
        return sync_institution_surveys(worker_conn, days_back=180)

    result = await _run_blocking_db_task(_worker)
    errors = result.get("errors") or []
    if errors:
        logger.warning(f"[机构调研] 同步错误: {errors}")
    written = int(result.get("rows_upserted") or 0)
    mart_rows = int(result.get("mart_rows") or 0)
    logger.info(f"[机构调研] raw={written} · mart={mart_rows}")
    return {
        "count": written,
        "status": "completed",
        "written": written,
        "mart_rows": mart_rows,
        "message": f"原始 {written} 条 · 聚合 {mart_rows} 条",
    }


async def _step_sync_margin(conn) -> dict:
    """融资融券日度同步（外资退役后最有信息量的杠杆资金维度）。

    同步最近一个交易日的 SH+SZ 明细；如果 DB 里已有该日，则跳过。
    两融 T 日数据通常要等到 T 日晚上才披露完整，白天跑会拿到空响应；
    因此开启 fallback_days=2，源未披露时自动降级到 T-1、T-2。
    """
    from services.margin_client import ensure_tables, sync_margin_day

    ensure_tables(conn)
    trade_date = latest_completed_trade_date(conn)
    if not trade_date:
        logger.warning("[两融] 未找到最近完成交易日，跳过同步")
        return {"count": 0, "status": "skipped", "message": "未找到最近完成交易日"}

    row = conn.execute(
        "SELECT COUNT(*) FROM raw_margin_daily WHERE trade_date = ?",
        (trade_date,),
    ).fetchone()
    existing = int(row[0] or 0) if row else 0
    if existing > 0:
        logger.info(f"[两融] 交易日 {trade_date} 已有 {existing} 条，跳过")
        return {
            "count": 0,
            "status": "skipped",
            "existing": existing,
            "trade_date": trade_date,
            "message": f"{trade_date} 已有 {existing} 条, 跳过",
        }

    logger.info(f"[两融] 开始同步交易日 {trade_date}（允许 T-1/T-2 降级）")
    result = await sync_margin_day(conn, trade_date, fallback_days=2)
    if result.get("fallback_used"):
        logger.info(
            f"[两融] 已降级到 {result.get('trade_date')} "
            f"（原请求 {result.get('requested_date')}），"
            f"written={result.get('written_rows')}"
        )
    written = int(result.get("written_rows") or 0)
    msg = f"写入 {written} 条 ({result.get('trade_date') or trade_date})"
    if result.get("fallback_used"):
        msg += f" [fallback from {result.get('requested_date')}]"
    return {
        "count": written,
        "status": "completed" if written > 0 else "skipped",
        "written": written,
        "trade_date": result.get("trade_date") or trade_date,
        "fallback_used": bool(result.get("fallback_used")),
        "message": msg if written > 0 else f"{result.get('trade_date') or trade_date} 源未披露/无新数据",
    }


async def _step_sync_lhb(conn) -> dict:
    """龙虎榜日度同步（短线机构与游资痕迹）.

    增量策略 (2026-04-27 修复):
    - DB 有数据: 起点 = MAX(trade_date) + 1 天, 终点 = latest_completed_trade_date
      已入库的日期不再重传, 节省 ~5x 带宽.
    - DB 空: 首次回拉 5 天兜底.
    - 起点 > 终点: skipped (DB 已最新)
    """
    from services.lhb_client import ensure_tables, sync_lhb_range

    ensure_tables(conn)
    trade_date = latest_completed_trade_date(conn)
    if not trade_date:
        logger.warning("[龙虎榜] 未找到最近完成交易日，跳过同步")
        return {"count": 0, "status": "skipped", "message": "未找到最近完成交易日"}

    end_dt = datetime.strptime(trade_date, "%Y-%m-%d")

    # DB 已有数据 → 增量起点 = MAX(trade_date) + 1 天
    row = conn.execute(
        "SELECT MAX(trade_date) FROM raw_lhb_daily WHERE trade_date IS NOT NULL"
    ).fetchone()
    db_max = row[0] if row and row[0] else None
    if db_max:
        try:
            start_dt = datetime.strptime(db_max[:10], "%Y-%m-%d") + timedelta(days=1)
            if start_dt.date() > end_dt.date():
                logger.info(f"[龙虎榜] DB 已是最新 (MAX={db_max} >= target={trade_date}), 跳过")
                return {
                    "count": 0,
                    "status": "skipped",
                    "existing": db_max,
                    "trade_date": trade_date,
                    "message": f"DB 已最新 (MAX={db_max}), 无需同步",
                }
        except ValueError:
            start_dt = end_dt - timedelta(days=5)
    else:
        # 首次同步, 回拉 5 天作兜底
        start_dt = end_dt - timedelta(days=5)
        logger.info("[龙虎榜] 首次同步, 回拉 5 天")

    start_str = start_dt.strftime("%Y-%m-%d")
    logger.info(f"[龙虎榜] 增量同步 {start_str} ~ {trade_date}")
    result = await sync_lhb_range(conn, start_str, trade_date)
    if result.get("status") == "source_unavailable":
        raise RuntimeError(f"lhb_source_failed:{result.get('error')}")
    written = int(result.get("written_rows") or 0)
    return {
        "count": written,
        "status": "completed",
        "written": written,
        "range": f"{start_str} ~ {trade_date}",
        "message": f"写入 {written} 条 ({start_str} ~ {trade_date})",
    }


async def _step_sync_qfii(conn) -> dict:
    """QFII 季度持股同步（北向陆股通退役后的外资维度替代）。

    只同步"最近一个已披露季度末"：距今至少 30 天且 DB 里还没有该季度数据时才请求。
    """
    from services.qfii_client import (
        ensure_tables,
        latest_plannable_report_date,
        sync_qfii_quarter,
    )

    ensure_tables(conn)
    target = latest_plannable_report_date()
    if not target:
        logger.info("[QFII] 尚无可同步的季度末")
        return {"count": 0, "status": "skipped", "message": "尚无可同步季度末 (距今 < 30 天)"}

    row = conn.execute(
        "SELECT COUNT(*) FROM raw_qfii_holding_quarterly WHERE report_date = ?",
        (target,),
    ).fetchone()
    existing = int(row[0] or 0) if row else 0
    if existing > 0:
        logger.info(f"[QFII] 季度 {target} 已有 {existing} 条，跳过")
        return {
            "count": 0,
            "status": "skipped",
            "existing": existing,
            "report_date": target,
            "message": f"季度 {target} 已有 {existing} 条, 跳过",
        }

    logger.info(f"[QFII] 开始同步季度 {target}")
    result = await sync_qfii_quarter(conn, target)
    if result.get("status") == "source_unavailable":
        raise RuntimeError(f"qfii_source_failed:{result.get('error')}")
    written = int(result.get("written_rows") or 0)
    return {
        "count": written,
        "status": "completed",
        "written": written,
        "report_date": target,
        "message": f"写入 {written} 条 (季度 {target})",
    }


async def _step_build_stage_features(conn) -> int:
    """阶段特征构建"""
    from services.stock_stage_engine import build_stock_stage_features
    return await _run_blocking_market_db_task(build_stock_stage_features)


async def _step_build_turtle_features(conn) -> int:
    """海龟特征构建"""
    from services.stock_turtle_engine import build_stock_turtle_features
    return await _run_blocking_market_db_task(build_stock_turtle_features)


async def _step_calc_inst_scores(conn) -> int:
    """计算机构评分"""
    from services.scoring import calculate_institution_scores
    return await _run_blocking_db_task(calculate_institution_scores)


async def _step_calc_stock_scores(conn) -> int:
    """计算股票评分"""
    from services.scoring import calculate_stock_scores
    return await _run_blocking_db_task(calculate_stock_scores)


# P1.5 (2026-04-28): 5 个妙想独家 capability sync step
async def _step_sync_aif10_capability(conn, capability_name: str) -> dict:
    """通用妙想 capability sync step. 失败不阻塞主流程."""
    from services.aif10_capability_client import sync_capability
    try:
        result = await asyncio.to_thread(sync_capability, capability_name)
        rows = result.get("rows", 0)
        return {
            "count": rows,
            "status": "ok" if rows > 0 else "empty",
            "report_name": result.get("report_name"),
            "raw_table": result.get("raw_table"),
            "elapsed_s": result.get("elapsed_s"),
        }
    except Exception as exc:
        logger.warning(f"[aif10/{capability_name}] 同步失败: {type(exc).__name__}: {str(exc)[:120]}")
        return {"count": 0, "status": "failed", "error": str(exc)[:200]}


async def _step_sync_aif10_holder_count(conn) -> dict:
    return await _step_sync_aif10_capability(conn, "holder_count")


async def _step_sync_aif10_valuation_quantile(conn) -> dict:
    return await _step_sync_aif10_capability(conn, "valuation_quantile")


async def _step_sync_aif10_peer_valuation(conn) -> dict:
    return await _step_sync_aif10_capability(conn, "peer_valuation")


async def _step_sync_aif10_forecast_consensus(conn) -> dict:
    return await _step_sync_aif10_capability(conn, "forecast_consensus")


async def _step_calc_prediction_outcomes(conn) -> dict:
    """P2.8: 算近 90 天预测的 forward return + IC tracking."""
    from services.prediction_outcome import calc_outcomes
    try:
        result = calc_outcomes(conn)
        return {
            "count": result.get("n_written", 0),
            "status": result.get("status", "ok"),
            "n_candidates": result.get("n_candidates"),
            "n_skipped": result.get("n_skipped"),
            "elapsed_s": result.get("elapsed_s"),
        }
    except Exception as exc:
        logger.warning(f"[预测outcome] 失败: {exc}")
        return {"count": 0, "status": "failed", "error": str(exc)[:200]}


async def _step_calc_risk_factors(conn) -> dict:
    """计算全市场风险因子 (P1.6). vol/sharpe/dd/mom/skew/kurt."""
    from services.risk_factors import calc_risk_factors
    try:
        result = calc_risk_factors(conn)
        return {
            "count": result.get("n_written", 0),
            "status": result.get("status", "ok"),
            "calc_date": result.get("calc_date"),
            "elapsed_s": result.get("elapsed_s"),
        }
    except Exception as exc:
        logger.warning(f"[风险因子] 失败: {exc}")
        return {"count": 0, "status": "failed", "error": str(exc)[:200]}


async def _step_sync_aif10_financial_history(conn) -> dict:
    """v0 接口, 按单股拉. 默认 50 只活跃股 (避免一次跑太久)."""
    from services.aif10_capability_client import sync_financial_history_200q
    try:
        result = sync_financial_history_200q(limit=50)
        return {
            "count": result.get("rows", 0),
            "status": "ok" if result.get("rows", 0) > 0 else "empty",
            "secucodes": result.get("secucodes"),
            "elapsed_s": result.get("elapsed_s"),
        }
    except Exception as exc:
        logger.warning(f"[aif10/financial_history] 失败: {exc}")
        return {"count": 0, "status": "failed", "error": str(exc)[:200]}


RUNNERS = {
    "sync_calendar": _step_sync_calendar,
    "sync_raw": _step_sync_raw,
    "match_inst": _step_match_inst,
    "sync_market_data": _step_sync_market_data,
    "sync_financial": _step_sync_financial,
    "gen_events": _step_gen_events,
    "calc_returns": _step_calc_returns,
    "sync_industry": _step_sync_industry,
    "sync_surveys": _step_sync_surveys,
    "sync_qfii": _step_sync_qfii,
    "sync_margin": _step_sync_margin,
    "sync_lhb": _step_sync_lhb,
    # P1.5 妙想独家 capability
    "sync_aif10_holder_count": _step_sync_aif10_holder_count,
    "sync_aif10_valuation_quantile": _step_sync_aif10_valuation_quantile,
    "sync_aif10_peer_valuation": _step_sync_aif10_peer_valuation,
    "sync_aif10_forecast_consensus": _step_sync_aif10_forecast_consensus,
    "sync_aif10_financial_history": _step_sync_aif10_financial_history,
    "calc_financial_derived": _step_calc_financial_derived,
    "build_current_rel": _step_build_current_rel,
    "build_profiles": _step_build_profiles,
    "build_industry_stat": _step_build_industry_stat,
    "build_trends": _step_build_trends,
    "calc_screening": _step_calc_screening,
    "calc_sector_momentum": _step_calc_sector_momentum,
    "build_external_attention": _step_build_external_attention,
    "build_stage_features": _step_build_stage_features,
    "calc_risk_factors": _step_calc_risk_factors,  # P1.6
    "calc_prediction_outcomes": _step_calc_prediction_outcomes,  # P2.8
    "build_turtle_features": _step_build_turtle_features,
    "calc_inst_scores": _step_calc_inst_scores,
    "calc_stock_scores": _step_calc_stock_scores,
}


# ============================================================
# API 端点
# ============================================================

def _calibrate_data_completeness(conn, step_id, skipped, failed):
    """
    Phase 2: data_completeness 校准（基于实际数据覆盖率，不只是步骤状态）。

    判定规则（写死）：
    - build_profiles: calc_returns skipped/failed OR 收益覆盖率 < 50% → partial
    - build_industry_stat: calc_returns 或 sync_industry 缺失 OR 行业覆盖率 < 80% → partial
    - build_trends: 收益或行业覆盖任一不足 → partial
    """
    calc_returns_missing = _is_blocking_upstream_state(conn, "calc_returns")
    sync_industry_missing = _is_blocking_upstream_state(conn, "sync_industry")

    # 查实际覆盖率
    returns_partial = calc_returns_missing
    industry_partial = sync_industry_missing
    if not returns_partial:
        try:
            from services.market_db import get_market_conn
            mkt_conn = get_market_conn()
            latest_market_date = mkt_conn.execute(
                f"SELECT MAX(date) FROM {KLINE_DAILY_QFQ_RELATION} WHERE freq='daily' AND adjust='qfq'"
            ).fetchone()[0]
            mkt_conn.close()
            total_events = conn.execute(
                """
                SELECT COUNT(*)
                FROM fact_institution_event
                WHERE notice_date IS NOT NULL AND notice_date != ''
                  AND tradable_date IS NOT NULL AND tradable_date != ''
                  AND (? IS NOT NULL AND tradable_date <= ?)
                """,
                (latest_market_date, latest_market_date),
            ).fetchone()[0]
            events_with_gain = conn.execute(
                """
                SELECT COUNT(*)
                FROM fact_institution_event
                WHERE return_to_now IS NOT NULL
                  AND tradable_date IS NOT NULL AND tradable_date != ''
                  AND (? IS NOT NULL AND tradable_date <= ?)
                """,
                (latest_market_date, latest_market_date),
            ).fetchone()[0]
            if total_events > 0 and events_with_gain / total_events < 0.5:
                returns_partial = True
                logger.info(f"[data_completeness] 收益覆盖率 {events_with_gain}/{total_events} = "
                           f"{events_with_gain/total_events:.0%} < 50% → partial")
        except Exception as e:
            logger.warning(f"[data_completeness] 收益覆盖率检测异常: {e}")
    if not industry_partial:
        try:
            coverage = summarize_industry_coverage(
                conn,
                "SELECT DISTINCT stock_code FROM inst_holdings WHERE stock_code IS NOT NULL",
            )
            total_holdings = coverage["total_codes"]
            with_industry = coverage["complete_codes"]
            if total_holdings > 0 and with_industry / total_holdings < 0.8:
                industry_partial = True
                logger.info(f"[data_completeness] 行业覆盖率 {with_industry}/{total_holdings} = "
                           f"{with_industry/total_holdings:.0%} < 80% → partial")
        except Exception as e:
            logger.warning(f"[data_completeness] 行业覆盖率检测异常: {e}")

    table_map = {
        "build_profiles": ("mart_institution_profile", returns_partial),
        "build_industry_stat": ("mart_institution_industry_stat",
                                returns_partial or industry_partial),
        "build_trends": ("mart_stock_trend",
                          returns_partial or industry_partial),
    }

    if step_id in table_map:
        table, is_partial = table_map[step_id]
        completeness = "partial" if is_partial else "complete"
        try:
            conn.execute(f"UPDATE {table} SET data_completeness = ?", (completeness,))
            conn.commit()
            if is_partial:
                logger.info(f"[data_completeness] {table} → partial")
        except Exception as e:
            logger.warning(f"[data_completeness] 更新 {table} 完整度标记失败: {e}")


def _update_step(conn, step_id, **kwargs):
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k == "records":
            v = _coerce_step_record_count(v) or 0
        elif k == "error" and isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(step_id)
    conn.execute(f"UPDATE step_status SET {', '.join(sets)} WHERE step_id = ?", vals)
    conn.commit()


def _resolve_step_result(result):
    """规范化 runner 返回值为 (status, count, detail_json_or_skip_text).

    支持三种返回:
    - str  : 旧 skipped 接口, status='skipped', error_text = skip 原因
    - dict : 详细状态. 必含 count; 可含 status (completed/skipped), message, written, skipped, empty, failed
             序列化整体 JSON 写到 error 字段 (作为 detail, 由 _normalize_update_step_detail 解析)
    - int / None : 旧 completed 接口, status='completed', records=int
    """
    if isinstance(result, str):
        return "skipped", 0, result
    if isinstance(result, dict):
        count = int(result.get("count") or 0)
        status = _normalize_step_status(result.get("status") or "completed")
        return status, count, json.dumps(result, ensure_ascii=False)
    return "completed", int(result or 0), None


@router.post("/update/all")
async def update_all():
    """一键更新全部（当前主 DAG）"""
    global _is_running, _stop_requested
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    _reset_ui_logs()
    _is_running = True
    _stop_requested = False
    _set_run_context("all", step_ids=[s["id"] for s in STEPS])

    async def _run():
        global _is_running, _stop_requested
        conn = get_conn(timeout=120)
        try:
            # Reset any stuck "running" steps from previous crashed runs
            conn.execute("""
                UPDATE step_status SET status = 'failed', error = '上次运行异常中断'
                WHERE status = 'running'
                  AND TRY_CAST(started_at AS TIMESTAMP) < CURRENT_TIMESTAMP - INTERVAL 1 HOUR
            """)
            conn.commit()

            # 清除旧 DAG 残留的步骤状态
            valid_ids = {s["id"] for s in STEPS}
            conn.execute(
                "DELETE FROM step_status WHERE step_id NOT IN ({})".format(
                    ",".join("?" * len(valid_ids))
                ), list(valid_ids)
            )

            # 初始化步骤状态
            for s in STEPS:
                conn.execute("""
                    INSERT OR REPLACE INTO step_status (step_id, group_name, step_name, step_order, status)
                    VALUES (?, ?, ?, ?, 'pending')
                """, (s["id"], s["group"], s["name"], s["order"]))
            conn.commit()

            completed = set()
            failed = set()
            skipped = set()
            stopped = set()

            # 预检连通性 —— 仅 K 线源做 precheck（跨服务探测成本高、失败不可恢复）。
            # 行业源放弃 precheck：tdxhub 单点探测易误报，
            # sync_tdx_industry 内部 count==0 分支已能写入 blocked 兜底。
            conn_status = await check_connectivity()
            kline_available = conn_status.get("kline_source", False)
            if not kline_available:
                logger.warning(f"[更新] K线源不可用 — {conn_status.get('message', '')}")

            for step in STEPS:
                if _should_stop():
                    logger.info("[更新] 用户停止")
                    remaining = [s["id"] for s in STEPS if s["id"] not in completed and s["id"] not in failed and s["id"] not in skipped]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    break

                sid = step["id"]
                hard = HARD_DEPS.get(sid, [])
                soft = SOFT_DEPS.get(sid, [])

                selected = {s["id"] for s in STEPS}
                hard_blocked = any(
                    d in failed or d in stopped or (d in skipped and d in selected)
                    for d in hard
                )
                if hard_blocked:
                    _update_step(conn, sid, status="skipped", error="硬依赖步骤未完成")
                    skipped.add(sid)
                    continue

                # 网络依赖检查
                if sid == "sync_market_data" and not kline_available:
                    stock_names = _tracked_stock_names(conn)
                    mark_current_missing_as(
                        conn,
                        "daily_kline",
                        status="blocked",
                        reason="K线源不可用，当前未执行同步",
                        last_error=conn_status.get("message", ""),
                        stock_names=stock_names,
                        commit=False,
                    )
                    mark_current_missing_as(
                        conn,
                        "monthly_kline",
                        status="blocked",
                        reason="K线源不可用，当前未执行同步",
                        last_error=conn_status.get("message", ""),
                        stock_names=stock_names,
                        commit=True,
                    )
                    _update_step(conn, sid, status="skipped", error="K线源不可用")
                    skipped.add(sid)
                    continue
                # 软依赖检查：仅作为日志或提示
                _soft_missing = [d for d in soft if d in failed or d in skipped]

                _update_step(conn, sid, status="running", started_at=datetime.now().isoformat())
                _touch_run_heartbeat(sid)
                logger.info(f"[更新] 开始: {step['name']}")

                try:
                    runner = RUNNERS[sid]
                    step_conn = get_conn(timeout=120)
                    try:
                        result = await runner(step_conn)
                    finally:
                        step_conn.close()

                    status, count, error_text = _resolve_step_result(result)
                    _record_step_source_state(conn, sid, status, error_text)
                    finished_at = datetime.now().isoformat()
                    if status == "skipped":
                        _update_step(conn, sid, status="skipped",
                                     finished_at=finished_at, records=count, error=error_text)
                        skipped.add(sid)
                        outcome = _format_step_result_for_log(status, count, error_text)
                        logger.info(f"[更新] 已最新: {step['name']} ({outcome})")
                        continue

                    update_kwargs = {"status": status, "finished_at": finished_at, "records": count}
                    if error_text is not None:
                        update_kwargs["error"] = error_text
                    _update_step(conn, sid, **update_kwargs)
                    if status in {"failed", "blocked"}:
                        failed.add(sid)
                        outcome = _format_step_result_for_log(status, count, error_text)
                        logger.error(f"[更新] 失败: {step['name']}: {outcome}")
                        continue
                    completed.add(sid)

                    # Phase 1: data_completeness 校准
                    _calibrate_data_completeness(conn, sid, skipped, failed)

                    outcome = _format_step_result_for_log(status, count, error_text)
                    logger.info(f"[更新] 完成: {step['name']} ({outcome})")
                except _RunStopped as e:
                    _record_step_source_state(conn, sid, "blocked", str(e))
                    _update_step(conn, sid, status="stopped",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    stopped.add(sid)
                    remaining = [
                        s["id"] for s in STEPS
                        if s["id"] not in completed and s["id"] not in failed
                        and s["id"] not in skipped and s["id"] not in stopped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    logger.info(f"[更新] 已停止: {step['name']}")
                    break
                except Exception as e:
                    _record_step_source_state(conn, sid, "failed", str(e))
                    _update_step(conn, sid, status="failed",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    failed.add(sid)
                    logger.error(f"[更新] 失败: {step['name']}: {e}")

            logger.info(f"[更新] 全部完成: {len(completed)} 成功, {len(failed)} 失败, {len(skipped)} 跳过, {len(stopped)} 停止")
        except Exception as e:
            _fail_unfinished_steps(conn, [s["id"] for s in STEPS], f"运行异常: {e}")
            logger.error(f"[更新] 异常: {e}")
        finally:
            conn.close()
            _is_running = False
            _stop_requested = False
            _finish_run_context()
            _schedule_holder_audit_snapshot_refresh("full_update")

    asyncio.create_task(_run())
    return {"ok": True, "steps": len(STEPS)}


@router.get("/update/status")
async def update_status():
    """更新状态"""
    conn = get_conn()
    try:
        valid_ids = {s["id"] for s in STEPS}
        if valid_ids:
            conn.execute(
                "DELETE FROM step_status WHERE step_id NOT IN ({})".format(
                    ",".join("?" * len(valid_ids))
                ),
                list(valid_ids),
            )
            conn.commit()
        # M9.5: 若 STEPS 中存在但 step_status 没有 (新增 step 后第一次读), 自动 prime 一行 idle
        existing = {r[0] for r in conn.execute("SELECT step_id FROM step_status").fetchall()}
        missing = [s for s in STEPS if s["id"] not in existing]
        if missing:
            for spec in missing:
                conn.execute(
                    """INSERT OR IGNORE INTO step_status
                       (step_id, group_name, step_name, step_order, status, error, records, started_at, finished_at)
                       VALUES (?, ?, ?, ?, 'idle', NULL, 0, NULL, NULL)""",
                    (spec["id"], spec["group"], spec["name"], spec["order"]),
                )
            conn.commit()

        rows = conn.execute("SELECT * FROM step_status ORDER BY step_order").fetchall()
        steps = []
        for row in rows:
            item = _sanitize_step_status_item(dict(row))
            steps.append(item)
        summary = _build_status_summary(steps, _is_running, _stop_requested, _run_context, _last_run_context)
        return {
            "running": _is_running,
            "stop_requested": _stop_requested,
            "run_context": dict(_run_context) if _run_context else None,
            "last_run_context": dict(_last_run_context) if _last_run_context else None,
            "summary": summary,
            "steps": steps,
            "logs": list(_ui_logs),
            "server_time": datetime.now().isoformat(),
        }
    finally:
        conn.close()


@router.post("/update/stop")
async def update_stop():
    """停止更新"""
    global _stop_requested
    _stop_requested = True
    logger.info("[更新] 已请求停止")
    return {"ok": True, "message": "已请求停止"}


@router.post("/update/reset-derived")
async def reset_derived():
    """清空可重算派生层，保留原始数据、持仓、行业和K线源数据"""
    conn = get_conn(timeout=120)
    try:
        counts, missing_tables = _reset_tables(conn, _DERIVED_RESET_TABLES)

        total = sum(counts.values())
        return {
            "ok": True,
            "message": f"已清空 {total} 条派生数据，请重新执行智能更新",
            "counts": counts,
            "missing_tables": missing_tables,
        }
    finally:
        conn.close()


@router.post("/update/reset-industry-derived")
async def reset_industry_derived(restart_smart: bool = True):
    """清空行业口径切换后需要重算的快照和派生层，并可直接接续智能更新。"""
    global _is_running
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    conn = get_conn(timeout=120)
    try:
        counts, missing_tables = _reset_tables(conn, _INDUSTRY_RESET_TABLES)
    finally:
        conn.close()

    total = sum(counts.values())
    response = {
        "ok": True,
        "message": f"已清空 {total} 条行业相关派生/快照数据，请重新执行智能更新",
        "counts": counts,
        "missing_tables": missing_tables,
        "preserved_tables": [
            "dim_stock_tdx_industry",
            "fact_institution_event",
            "inst_holdings",
            "market_kline_daily",
            "raw_gpcw_financial",
            "fact_financial_derived",
        ],
    }
    if not restart_smart:
        return response

    smart_result = await smart_update()
    response["message"] = f"已清空 {total} 条行业相关派生/快照数据，并启动智能更新"
    response["smart_update"] = smart_result
    response["ok"] = bool(smart_result.get("ok", True))
    return response


@router.get("/update/connectivity")
async def connectivity_check(force: bool = False):
    """测试数据源连通性"""
    return await check_connectivity(force=force)


@router.get("/update/audit")
async def data_audit(force: bool = False):
    """数据质量审计

    默认优先返回最近一次同步后的审计快照；
    force=true 时立即重算并覆盖快照。
    """
    from services.audit import get_quality_audit
    conn = get_conn()
    try:
        payload = get_quality_audit(conn, force=force)
        payload["snapshot_refreshing"] = _is_audit_snapshot_refreshing()
        return payload
    finally:
        conn.close()


@router.get("/update/smart-plan", include_in_schema=False)
async def smart_plan(critical_only: bool = False):
    """内部运维接口：智能更新计划（不执行，只返回建议）。"""
    from services.audit import build_smart_plan
    conn = get_conn()
    try:
        plan = build_smart_plan(conn)
        if critical_only:
            return {"ok": True, "plan": _critical_daily_plan(plan)}
        return {"ok": True, "plan": _plan_with_budgets(plan)}
    finally:
        conn.close()


@router.post("/update/smart")
async def smart_update(critical_only: bool = False):
    """智能更新（先审计再决定跑什么）"""
    global _is_running, _stop_requested
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    from services.audit import build_smart_plan

    _reset_ui_logs()
    _is_running = True
    _stop_requested = False

    conn_plan = get_conn()
    try:
        calendar_preflight = await _step_sync_calendar(conn_plan)
        if calendar_preflight.get("status") == "failed":
            _is_running = False
            return {
                "ok": False,
                "message": calendar_preflight.get("error") or "交易日历不可用",
                "calendar_preflight": calendar_preflight,
            }
        # Production cron must not reuse a stale audit snapshot: a cached raw
        # freshness miss can incorrectly expand the daily plan into a long
        # full-source sync.
        raw_plan = build_smart_plan(conn_plan, use_cache=False)
        plan = _critical_daily_plan(raw_plan) if critical_only else _plan_with_budgets(raw_plan)
    finally:
        conn_plan.close()

    steps_to_run = _ensure_calendar_step_for_data_fetch(list(plan["steps"]))
    if steps_to_run != plan["steps"]:
        plan = dict(plan)
        plan["steps"] = steps_to_run
        plan = _plan_with_budgets(plan)
    if not steps_to_run:
        _is_running = False
        _set_last_noop_context("smart", "数据已是最新，无需更新")
        return {
            "ok": True,
            "message": "数据已是最新，无需更新",
            "plan": plan,
            "steps": 0,
            "step_ids": [],
            "noop": True,
        }
    _set_run_context("smart", step_ids=steps_to_run)
    if critical_only and _run_context is not None:
        _run_context["critical_only"] = True
    conn_init = get_conn(timeout=120)
    try:
        _prime_step_status_rows(
            conn_init,
            steps_to_run,
            inactive_mode="skipped",
            skip_reasons=plan.get("skip_reasons", {}),
        )
    finally:
        conn_init.close()
    logger.info(f"[智能更新] 已请求: {len(steps_to_run)} 个步骤待执行")

    async def _run():
        global _is_running, _stop_requested
        conn = get_conn(timeout=120)
        try:
            # Reset stuck steps
            conn.execute("""
                UPDATE step_status SET status = 'failed', error = '上次运行异常中断'
                WHERE status = 'running'
                  AND TRY_CAST(started_at AS TIMESTAMP) < CURRENT_TIMESTAMP - INTERVAL 1 HOUR
            """)
            conn.commit()

            # 预检连通性 —— 仅 K 线源 precheck，行业源放弃 precheck（sync_industry 自身兜底）
            if "sync_market_data" in steps_to_run:
                conn_status = await check_connectivity()
                kline_available = conn_status.get("kline_source", False)
            else:
                conn_status = {}
                kline_available = True

            completed = set()
            failed = set()
            skipped = set()
            stopped = set()

            # 标记智能计划跳过的步骤
            for s in STEPS:
                if s["id"] not in steps_to_run:
                    skipped.add(s["id"])

            for step in STEPS:
                if _should_stop():
                    logger.info("[智能更新] 用户停止")
                    remaining = [
                        s["id"] for s in STEPS
                        if s["id"] in steps_to_run
                        and s["id"] not in completed and s["id"] not in failed
                        and s["id"] not in skipped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    break
                sid = step["id"]
                if sid not in steps_to_run:
                    continue

                hard = HARD_DEPS.get(sid, [])
                soft = SOFT_DEPS.get(sid, [])

                selected = set(steps_to_run)
                hard_blocked = any(
                    d in failed or d in stopped or (d in skipped and d in selected)
                    for d in hard
                )
                if hard_blocked:
                    _update_step(conn, sid, status="skipped", error="硬依赖步骤未完成")
                    skipped.add(sid)
                    continue

                # 网络依赖检查
                if sid == "sync_market_data" and not kline_available:
                    stock_names = _tracked_stock_names(conn)
                    mark_current_missing_as(
                        conn,
                        "daily_kline",
                        status="blocked",
                        reason="K线源不可用，当前未执行同步",
                        last_error=conn_status.get("message", ""),
                        stock_names=stock_names,
                        commit=False,
                    )
                    mark_current_missing_as(
                        conn,
                        "monthly_kline",
                        status="blocked",
                        reason="K线源不可用，当前未执行同步",
                        last_error=conn_status.get("message", ""),
                        stock_names=stock_names,
                        commit=True,
                    )
                    _update_step(conn, sid, status="skipped", error="K线源不可用")
                    skipped.add(sid)
                    continue
                _update_step(conn, sid, status="running", started_at=datetime.now().isoformat())
                _touch_run_heartbeat(sid)
                logger.info(f"[智能更新] 开始: {step['name']}")

                try:
                    runner = RUNNERS[sid]
                    budget = _step_budget_seconds(sid)
                    step_conn = get_conn(timeout=120)
                    try:
                        coro = runner(step_conn)
                        result = await asyncio.wait_for(coro, timeout=budget) if budget else await coro
                    finally:
                        step_conn.close()

                    status, count, error_text = _resolve_step_result(result)
                    _record_step_source_state(conn, sid, status, error_text)
                    finished_at = datetime.now().isoformat()
                    if status == "skipped":
                        _update_step(conn, sid, status="skipped",
                                     finished_at=finished_at, records=count, error=error_text)
                        skipped.add(sid)
                        outcome = _format_step_result_for_log(status, count, error_text)
                        logger.info(f"[智能更新] 已最新: {step['name']} ({outcome})")
                        continue

                    update_kwargs = {"status": status, "finished_at": finished_at, "records": count}
                    if error_text is not None:
                        update_kwargs["error"] = error_text
                    _update_step(conn, sid, **update_kwargs)
                    if status in {"failed", "blocked"}:
                        failed.add(sid)
                        outcome = _format_step_result_for_log(status, count, error_text)
                        logger.error(f"[智能更新] 失败: {step['name']}: {outcome}")
                        continue
                    completed.add(sid)
                    _calibrate_data_completeness(conn, sid, skipped, failed)
                except _RunStopped as e:
                    _record_step_source_state(conn, sid, "blocked", str(e))
                    _update_step(conn, sid, status="stopped",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    stopped.add(sid)
                    remaining = [
                        s["id"] for s in STEPS
                        if s["id"] in steps_to_run
                        and s["id"] not in completed and s["id"] not in failed
                        and s["id"] not in skipped and s["id"] not in stopped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    logger.info(f"[智能更新] 已停止: {step['name']}")
                    break
                except asyncio.TimeoutError:
                    budget = _step_budget_seconds(sid)
                    error_text = f"step budget timeout after {budget}s"
                    _record_step_source_state(conn, sid, "failed", error_text)
                    _update_step(conn, sid, status="failed",
                                 finished_at=datetime.now().isoformat(), error=error_text)
                    failed.add(sid)
                    logger.error(f"[智能更新] 超时: {step['name']}: {error_text}")
                except Exception as e:
                    _record_step_source_state(conn, sid, "failed", str(e))
                    _update_step(conn, sid, status="failed",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    failed.add(sid)
                    logger.error(f"[智能更新] 失败: {step['name']}: {e}")

            result_counts = {
                "completed": len(completed),
                "failed": len(failed),
                "skipped": len(skipped),
                "stopped": len(stopped),
            }
            logger.info(f"[智能更新] 完成: {len(completed)} 成功, {len(failed)} 失败, {len(skipped)} 跳过, {len(stopped)} 停止")
        except Exception as e:
            _fail_unfinished_steps(conn, steps_to_run, f"运行异常: {e}")
            logger.error(f"[智能更新] 异常: {e}")
        finally:
            conn.close()
            _is_running = False
            _stop_requested = False
            _finish_run_context({"result": locals().get("result_counts")})
            _schedule_holder_audit_snapshot_refresh("smart_update")

    asyncio.create_task(_run())
    return {"ok": True, "steps": len(steps_to_run), "step_ids": steps_to_run, "plan": plan}


@router.post("/update/step/{step_id}")
async def run_single_step(step_id: str):
    """执行单个步骤"""
    global _is_running, _stop_requested
    if step_id not in RUNNERS:
        return {"ok": False, "error": f"未知步骤: {step_id}"}
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    step_meta = next((s for s in STEPS if s["id"] == step_id), None)
    step_name = (step_meta or {}).get("name", step_id)
    step_ids = _ensure_calendar_step_for_data_fetch(_collect_downstream_steps(step_id))
    _reset_ui_logs()
    _is_running = True
    _stop_requested = False
    _set_run_context("single", step_id, step_name, step_ids=step_ids)
    conn_init = get_conn(timeout=120)
    try:
        _prime_step_status_rows(conn_init, step_ids, inactive_mode="idle")
    finally:
        conn_init.close()
    logger.info(f"[单步] 已请求: {step_name}")

    async def _run():
        global _is_running, _stop_requested
        conn = get_conn(timeout=120)
        try:
            selected = set(step_ids)
            kline_available = True
            # 仅 K线源做 precheck —— 多服试探成本低、失败不可恢复。
            # 行业源(sync_industry) 放弃 precheck：tdxhub 单点探测易误报，
            # 且 sync_tdx_industry 内部 count==0 分支已能写入 blocked 兜底。
            conn_status = {}
            if "sync_market_data" in selected:
                conn_status = await check_connectivity()
                kline_available = conn_status.get("kline_source", False)

            completed = set()
            failed = set()
            skipped = set()
            stopped = set()

            for sid in step_ids:
                if _should_stop():
                    logger.info("[单步] 用户停止")
                    remaining = [
                        x for x in step_ids
                        if x not in completed and x not in failed and x not in skipped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    break

                step = next((s for s in STEPS if s["id"] == sid), None)
                step_label = (step or {}).get("name", sid)
                hard = [d for d in HARD_DEPS.get(sid, []) if d in selected]

                hard_blocked = any(
                    d in failed or d in stopped or (d in skipped and d in selected)
                    for d in hard
                )
                if hard_blocked:
                    _update_step(conn, sid, status="skipped",
                                 finished_at=datetime.now().isoformat(),
                                 error="硬依赖步骤未完成")
                    skipped.add(sid)
                    logger.warning(f"[单步] 跳过: {step_label}: 硬依赖步骤未完成")
                    continue

                if sid == "sync_market_data" and not kline_available:
                    stock_names = _tracked_stock_names(conn)
                    mark_current_missing_as(
                        conn,
                        "daily_kline",
                        status="blocked",
                        reason="K线源不可用，当前未执行同步",
                        last_error=conn_status.get("message", ""),
                        stock_names=stock_names,
                        commit=False,
                    )
                    mark_current_missing_as(
                        conn,
                        "monthly_kline",
                        status="blocked",
                        reason="K线源不可用，当前未执行同步",
                        last_error=conn_status.get("message", ""),
                        stock_names=stock_names,
                        commit=True,
                    )
                    _update_step(conn, sid, status="skipped",
                                 started_at=datetime.now().isoformat(),
                                 finished_at=datetime.now().isoformat(),
                                 error="K线源不可用")
                    skipped.add(sid)
                    logger.warning(f"[单步] 跳过: {step_label}: K线源不可用")
                    continue

                _update_step(conn, sid, status="running",
                             started_at=datetime.now().isoformat(),
                             finished_at=None, error=None)
                if sid == step_id:
                    logger.info(f"[单步] 开始: {step_label}")
                else:
                    logger.info(f"[单步续跑] 开始: {step_label}")

                try:
                    result = await RUNNERS[sid](conn)
                    status, count, error_text = _resolve_step_result(result)
                    finished_at = datetime.now().isoformat()
                    if status == "skipped":
                        _update_step(conn, sid, status="skipped",
                                     finished_at=finished_at, records=count, error=error_text)
                        skipped.add(sid)
                        outcome = _format_step_result_for_log(status, count, error_text)
                        logger.info(f"[单步{'续跑' if sid != step_id else ''}] 已最新: {step_label} ({outcome})")
                        continue

                    update_kwargs = {"status": status, "finished_at": finished_at, "records": count}
                    if error_text is not None:
                        update_kwargs["error"] = error_text
                    _update_step(conn, sid, **update_kwargs)
                    if status in {"failed", "blocked"}:
                        failed.add(sid)
                        outcome = _format_step_result_for_log(status, count, error_text)
                        logger.error(f"[单步{'续跑' if sid != step_id else ''}] 失败: {step_label}: {outcome}")
                        continue
                    completed.add(sid)
                    _calibrate_data_completeness(conn, sid, skipped, failed)
                    outcome = _format_step_result_for_log(status, count, error_text)
                    logger.info(f"[单步{'续跑' if sid != step_id else ''}] 完成: {step_label}: {outcome}")
                except _RunStopped as e:
                    _update_step(conn, sid, status="stopped",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    stopped.add(sid)
                    remaining = [
                        x for x in step_ids
                        if x not in completed and x not in failed and x not in skipped and x not in stopped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    logger.info(f"[单步{'续跑' if sid != step_id else ''}] 已停止: {step_label}")
                    break
                except Exception as e:
                    _update_step(conn, sid, status="failed",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    failed.add(sid)
                    logger.error(f"[单步{'续跑' if sid != step_id else ''}] 失败: {step_label}: {e}")

            result_counts = {
                "completed": len(completed),
                "failed": len(failed),
                "skipped": len(skipped),
                "stopped": len(stopped),
            }
            logger.info(f"[单步] 链路完成: {len(completed)} 成功, {len(failed)} 失败, {len(skipped)} 跳过, {len(stopped)} 停止")
        except Exception as e:
            _fail_unfinished_steps(conn, step_ids, f"运行异常: {e}")
            logger.error(f"[单步] {step_name} 失败: {e}")
        finally:
            conn.close()
            _is_running = False
            _stop_requested = False
            _finish_run_context({"result": locals().get("result_counts")})
            _schedule_holder_audit_snapshot_refresh(f"single_step:{step_id}")

    asyncio.create_task(_run())
    return {"ok": True, "step_id": step_id, "name": step_name, "steps": step_ids}


# ============================================================
# 救生艇：独立运行 AKShare 十大股东新进查询
# ============================================================

_lifeboat_running = False
_lifeboat_result = None


@router.post("/lifeboat/run")
async def run_lifeboat():
    """运行救生艇脚本（异步），返回运行状态"""
    global _lifeboat_running, _lifeboat_result
    if _lifeboat_running:
        return {"ok": False, "message": "救生艇正在运行中，请稍候"}

    # 移除未使用的 subprocess 导入
    script_path = Path(__file__).resolve().parent.parent.parent / "lifeboat" / "fetch_and_report.py"
    if not script_path.exists():
        return {"ok": False, "message": f"救生艇脚本不存在: {script_path}"}

    _lifeboat_running = True
    _lifeboat_result = None

    async def _run():
        global _lifeboat_running, _lifeboat_result
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(script_path.parent),
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if proc.returncode == 0:
                _lifeboat_result = {"ok": True, "message": "救生艇报告已生成", "output": output[-500:]}
                logger.info("[救生艇] 运行完成")
            else:
                _lifeboat_result = {"ok": False, "message": f"运行失败 (exit {proc.returncode})", "output": output[-500:]}
                logger.error(f"[救生艇] 失败: {output[-200:]}")
        except Exception as e:
            _lifeboat_result = {"ok": False, "message": str(e)}
            logger.error(f"[救生艇] 异常: {e}")
        finally:
            _lifeboat_running = False

    asyncio.create_task(_run())
    return {"ok": True, "message": "救生艇已启动，请稍候约2分钟"}


@router.get("/lifeboat/status")
async def lifeboat_status():
    """查询救生艇运行状态"""
    if _lifeboat_running:
        return {"running": True, "result": None}
    return {"running": False, "result": _lifeboat_result}


@router.get("/lifeboat/report")
async def lifeboat_report():
    """返回救生艇 HTML 报告内容"""
    report_path = Path(__file__).resolve().parent.parent.parent / "lifeboat" / "report.html"
    if not report_path.exists():
        return Response(content="<h3>尚未生成救生艇报告。请先运行。</h3>", media_type="text/html")
    return FileResponse(str(report_path), media_type="text/html")


@router.post("/update/sync")
async def update_sync():
    """只跑数据获取组"""
    return await _run_group_pipeline("sync", "数据获取组", "data")


@router.post("/update/calc")
async def update_calc():
    """只跑事实计算组"""
    return await _run_group_pipeline("calc", "事实计算组", "calc")


@router.post("/update/mart")
async def update_mart():
    """只跑集市构建组"""
    return await _run_group_pipeline("mart", "集市构建组", "mart")


async def _run_group_pipeline(run_mode: str, run_name: str, group_id: str):
    global _is_running, _stop_requested
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    steps_in_group = [s for s in STEPS if s.get("group") == group_id]
    step_ids = [s["id"] for s in steps_in_group]
    if not step_ids:
        return {"ok": False, "error": f"未知的分组: {group_id}"}
        
    _reset_ui_logs()
    _is_running = True
    _stop_requested = False
    _set_run_context(run_mode, step_ids=step_ids)
    
    conn_init = get_conn(timeout=120)
    try:
        _prime_step_status_rows(conn_init, step_ids, inactive_mode="idle")
    finally:
        conn_init.close()
        
    logger.info(f"[{run_name}] 已请求: {len(step_ids)} 个步骤")

    async def _run():
        global _is_running, _stop_requested
        conn = get_conn(timeout=120)
        try:
            selected = set(step_ids)
            
            conn_status = await check_connectivity()
            kline_available = conn_status.get("kline_source", False)

            completed = set()
            failed = set()
            skipped = set()
            stopped = set()

            for step in steps_in_group:
                sid = step["id"]
                if _should_stop():
                    logger.info(f"[{run_name}] 用户停止")
                    remaining = [
                        x for x in step_ids
                        if x not in completed and x not in failed and x not in skipped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    break

                hard = [d for d in HARD_DEPS.get(sid, []) if d in selected]

                hard_blocked = any(
                    d in failed or d in stopped or (d in skipped and d in selected)
                    for d in hard
                )
                if hard_blocked:
                    _update_step(conn, sid, status="skipped",
                                 finished_at=datetime.now().isoformat(),
                                 error="硬依赖步骤未完成")
                    skipped.add(sid)
                    continue

                if sid == "sync_market_data" and not kline_available:
                    _update_step(conn, sid, status="skipped", error="K线源不可用")
                    skipped.add(sid)
                    continue

                _update_step(conn, sid, status="running", started_at=datetime.now().isoformat())
                logger.info(f"[{run_name}] 开始: {step['name']}")

                try:
                    runner = RUNNERS[sid]
                    step_conn = get_conn(timeout=120)
                    try:
                        result = await runner(step_conn)
                    finally:
                        step_conn.close()

                    status, count, error_text = _resolve_step_result(result)
                    finished_at = datetime.now().isoformat()
                    update_kwargs = {"status": status, "finished_at": finished_at, "records": count}
                    if error_text is not None:
                        update_kwargs["error"] = error_text
                    _update_step(conn, sid, **update_kwargs)
                    outcome = _format_step_result_for_log(status, count, error_text)

                    if status == "skipped":
                        skipped.add(sid)
                        logger.info(f"[{run_name}] 已最新: {step['name']} ({outcome})")
                        continue
                    if status in {"failed", "blocked"}:
                        failed.add(sid)
                        logger.error(f"[{run_name}] 失败: {step['name']}: {outcome}")
                        continue

                    completed.add(sid)

                    _calibrate_data_completeness(conn, sid, skipped, failed)

                    logger.info(f"[{run_name}] 完成: {step['name']} ({outcome})")
                except _RunStopped as e:
                    _update_step(conn, sid, status="stopped",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    stopped.add(sid)
                    remaining = [
                        x for x in step_ids
                        if x not in completed and x not in failed
                        and x not in skipped and x not in stopped
                    ]
                    _mark_steps_status(conn, remaining, "stopped", "用户已停止")
                    stopped.update(remaining)
                    break
                except Exception as e:
                    _update_step(conn, sid, status="failed",
                                 finished_at=datetime.now().isoformat(), error=str(e)[:200])
                    failed.add(sid)
                    logger.error(f"[{run_name}] 失败: {step['name']}: {e}")

            result_counts = {
                "completed": len(completed),
                "failed": len(failed),
                "skipped": len(skipped),
                "stopped": len(stopped),
            }
        except Exception as e:
            _fail_unfinished_steps(conn, step_ids, f"运行异常: {e}")
            logger.error(f"[{run_name}] 异常: {e}")
        finally:
            conn.close()
            _is_running = False
            _stop_requested = False
            _finish_run_context({"result": locals().get("result_counts")})
            _schedule_holder_audit_snapshot_refresh(run_name)

    asyncio.create_task(_run())
    return {"ok": True, "steps": len(step_ids), "step_ids": step_ids}
