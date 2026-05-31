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
 20. refresh_today_signals    — 物化今日信号快照
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter

from services.db import get_conn
from services.gap_queue import load_tracked_stock_names
from services.market_db import get_canonical_kline_qfq_relation
from services.utils import latest_completed_trade_date
from routers.updater_audit import (
    _is_audit_snapshot_refreshing,
    _schedule_holder_audit_snapshot_refresh,
    build_update_audit_payload,
)
from routers.updater_calendar import (
    CALENDAR_DATA_FETCH_STEPS,
    CALENDAR_FUTURE_COVER_DAYS,
    CALENDAR_MIN_ROWS,
    _ensure_calendar_step_for_data_fetch,
    _ensure_trading_calendar_table,
    _refresh_trading_calendar,
    _step_sync_calendar,
    _trading_calendar_status,
)
from routers.updater_calc import (
    _step_build_current_rel,
    _step_build_external_attention,
    _step_build_stage_features,
    _step_build_turtle_features,
    _step_calc_returns,
    _step_calc_financial_derived,
    _step_calc_inst_scores,
    _step_calc_prediction_outcomes,
    _step_calc_risk_factors,
    _step_calc_screening,
    _step_calc_sector_momentum,
    _step_calc_stock_scores,
    _step_gen_events,
    _step_refresh_today_signals,
)
from routers.updater_connectivity import (
    _compute_connectivity,
    check_connectivity,
    get_cached_connectivity,
)
from routers.updater_completeness import calibrate_data_completeness
from routers.updater_infra import (
    _build_daily_sync_batch_summary,
    _format_step_result_for_log,
    _format_sync_source_metrics,
    _normalize_update_step_detail,
    _record_sync_source_metric,
    _reset_ui_logs,
    _snapshot_sync_source_metrics,
    attach_ui_log_handler,
    get_ui_logs,
)
from routers.updater_institution import (
    _build_exclusion_set,
    _step_build_industry_stat_sync,
    _step_match_inst,
    _step_match_inst_sync,
    _step_sync_industry_with_hooks,
)
from routers.updater_launcher import (
    UpdaterExecutionDeps,
    launch_group_update_request,
    run_full_update_background,
    run_single_update_background,
    run_smart_update_background,
)
from routers.updater_lifeboat import router as lifeboat_router
from routers.updater_profiles import (
    _median,
    _parse_notice_date,
    _step_build_profiles as _step_build_profiles_with_hooks,
    _step_build_profiles_sync,
)
from routers.updater_reset import (
    build_reset_derived_response,
    build_reset_industry_response,
    reset_tables as _reset_tables,
)
from routers.updater_steps import (
    _fail_unfinished_steps,
    _mark_stale_running_steps_failed,
    _mark_steps_status,
    _prime_step_status_rows_for_steps,
    _record_step_source_state_for_domains,
    _resolve_step_result,
    _sync_step_status_catalog_for_steps,
    _update_step,
    prime_run_step_status_for_steps,
)
from routers.updater_status import (
    STEP_BUDGET_MODEL,
    STEP_BUDGET_SECONDS,
    STEP_SOURCE_DOMAINS,
    _collect_downstream_steps as _collect_downstream_steps_for_graph,
    _critical_daily_plan,
    _plan_with_budgets,
    _step_budget_seconds,
    _watermark_lag_days,
    build_finished_run_context,
    build_noop_run_context,
    build_run_context,
    build_smart_plan_response,
    build_smart_update_plan,
    build_update_status_response,
    touch_run_context_heartbeat,
)
from routers.updater_runtime import _run_blocking_db_task, _run_blocking_market_db_task
from routers.updater_sync import (
    _step_sync_aif10_capability,
    _step_sync_aif10_financial_history,
    _step_sync_aif10_forecast_consensus,
    _step_sync_aif10_holder_count,
    _step_sync_aif10_peer_valuation,
    _step_sync_aif10_valuation_quantile,
    _step_sync_financial as _step_sync_financial_with_hooks,
    _step_sync_lhb,
    _step_sync_qfii,
    _step_sync_raw,
    _step_sync_surveys,
)
from routers.updater_market_data import _step_sync_market_data as _step_sync_market_data_with_hooks
from routers.updater_plan import (
    HARD_DEPS,
    MANUAL_ONLY_STEPS,
    SOFT_DEPS,
    STEPS,
    step_ids_for,
    step_index_for,
    step_name_for,
    step_specs_for_group,
)
from routers.updater_trends import (
    _step_build_trends as _step_build_trends_with_hooks,
    _step_build_trends_sync,
)

logger = logging.getLogger("cm-api")
router = APIRouter()
router.include_router(lifeboat_router)
KLINE_DAILY_QFQ_RELATION = get_canonical_kline_qfq_relation()


attach_ui_log_handler(logger)

_is_running = False
_stop_requested = False
_run_context = None
_last_run_context = None
# 2026-05-21 P0-2 fix: 全局 exception 渠道, 异步 _run() 失败时记录, /update/status 返回给前端.
# 否则前端看到 running=false 但不知错误, 假以为成功.
_last_exception = None  # {"ts": str, "trigger": "smart|sync|step", "message": str, "type": str}


def _calibrate_data_completeness(conn, step_id: str) -> None:
    calibrate_data_completeness(
        conn,
        step_id,
        is_blocking_upstream_state=_is_blocking_upstream_state,
        kline_relation=KLINE_DAILY_QFQ_RELATION,
        logger=logger,
    )


def _record_last_exception(trigger: str, exc: BaseException) -> None:
    """P0-2: 记录最新异常 (供 /update/status 返回前端). 守护型, 任何失败都不 raise."""
    global _last_exception
    try:
        _last_exception = {
            "ts": datetime.now().isoformat(),
            "trigger": str(trigger),
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        }
    except Exception:  # rule-compliance: ok evidence=safety-net-no-raise-from-error-recorder
        pass


def _safe_finally_cleanup(trigger: str, conn=None) -> None:
    """P1-1: 嵌套守护 finally 块, 防 _is_running 锁泄漏.
    任一 cleanup 异常都被吞并写 log, 但 _is_running 必定 reset.
    """
    global _is_running, _stop_requested
    if conn is not None:
        try:
            conn.close()
        except Exception as exc:  # rule-compliance: ok evidence=defensive-finally-conn-close
            logger.warning(f"[{trigger}] conn.close exception: {exc}")
    # _is_running / _stop_requested 必定 reset, 不管下面 cleanup 死活
    _is_running = False
    _stop_requested = False
    try:
        _finish_run_context({"result": None})
    except Exception as exc:  # rule-compliance: ok evidence=defensive-finally-finish-run-context
        logger.warning(f"[{trigger}] _finish_run_context exception: {exc}")
    try:
        _schedule_holder_audit_snapshot_refresh(trigger)
    except Exception as exc:  # rule-compliance: ok evidence=defensive-finally-audit-refresh
        logger.warning(f"[{trigger}] _schedule_holder_audit_snapshot_refresh exception: {exc}")


class _RunStopped(Exception):
    """用户主动停止当前更新链路。"""


def _raise_if_stop():
    if _stop_requested:
        raise _RunStopped("用户已停止")


def _touch_run_heartbeat(step_id: Optional[str] = None):
    touch_run_context_heartbeat(_run_context, step_id)


def _record_step_source_state(conn, step_id: str, status: str, error_text: Optional[str] = None) -> None:
    return _record_step_source_state_for_domains(
        conn,
        STEP_SOURCE_DOMAINS,
        step_id,
        status,
        error_text,
        logger=logger,
    )


def _execution_deps() -> UpdaterExecutionDeps:
    return UpdaterExecutionDeps(
        get_conn=get_conn,
        fail_unfinished_steps=_fail_unfinished_steps,
        safe_finally_cleanup=_safe_finally_cleanup,
        record_last_exception=_record_last_exception,
        logger=logger,
        stopped_exception_type=_RunStopped,
        should_stop=_should_stop,
        check_connectivity=check_connectivity,
        update_step=_update_step,
        update_steps=_mark_steps_status,
        record_step_source_state=_record_step_source_state,
        resolve_step_result=_resolve_step_result,
        format_step_result_for_log=_format_step_result_for_log,
        calibrate_data_completeness=_calibrate_data_completeness,
        touch_heartbeat=_touch_run_heartbeat,
        mark_stale_running_steps_failed=_mark_stale_running_steps_failed,
        step_budget_seconds=_step_budget_seconds,
        prime_step_status_rows=_prime_step_status_rows,
    )


def _set_run_context(mode: str, step_id: Optional[str] = None, step_name: Optional[str] = None, step_ids=None):
    global _run_context
    _run_context = build_run_context(mode, step_id, step_name, step_ids)


def _begin_run(
    mode: Optional[str] = None,
    step_id: Optional[str] = None,
    step_name: Optional[str] = None,
    step_ids=None,
):
    global _is_running, _stop_requested
    _reset_ui_logs()
    _is_running = True
    _stop_requested = False
    if mode is not None:
        _set_run_context(mode, step_id, step_name, step_ids=step_ids)


def _is_daily_critical_context() -> bool:
    return bool(_run_context and _run_context.get("critical_only"))


def _set_last_noop_context(mode: str, message: str):
    global _last_run_context
    _last_run_context = build_noop_run_context(mode, message)


def _finish_run_context(extra: Optional[dict] = None):
    global _run_context, _last_run_context
    finished = build_finished_run_context(_run_context, extra)
    if finished:
        _last_run_context = finished
    _run_context = None
    # 跑完任何更新后立即让 audit 缓存失效，下一次 /update/audit 走最新数据
    try:
        from services.audit import invalidate_audit_cache
        from services.etf_snapshot_manager import invalidate_etf_snapshot_cache

        invalidate_audit_cache()
        invalidate_etf_snapshot_cache()
    except Exception as e:
        logger.warning(f"[更新结束] 缓存失效操作异常: {e}")


def _prime_step_status_rows(conn, active_step_ids, *, inactive_mode: str = "idle",
                            skip_reasons: Optional[dict] = None):
    return _prime_step_status_rows_for_steps(
        conn,
        STEPS,
        active_step_ids,
        inactive_mode=inactive_mode,
        skip_reasons=skip_reasons,
    )


def _sync_step_status_catalog(conn):
    return _sync_step_status_catalog_for_steps(conn, STEPS)


def _prime_run_step_status(active_step_ids, *, inactive_mode: str = "idle",
                           skip_reasons: Optional[dict] = None):
    return prime_run_step_status_for_steps(
        get_conn,
        STEPS,
        active_step_ids,
        inactive_mode=inactive_mode,
        skip_reasons=skip_reasons,
    )


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


def _tracked_stock_names(conn) -> dict[str, Optional[str]]:
    return load_tracked_stock_names(conn)


def _collect_downstream_steps(start_step_id):
    return _collect_downstream_steps_for_graph(
        start_step_id,
        STEPS,
        HARD_DEPS,
        SOFT_DEPS,
        MANUAL_ONLY_STEPS,
    )

def _should_stop():
    return _stop_requested


# ============================================================
# 步骤执行函数
# ============================================================

# [Phase 5 已删除] _step_kline_monthly 和 _step_kline_daily 已被 _step_sync_market_data 替代


# Phase 3b-3: fact_institution_event_industry_snapshot 已退役。
# _capture_missing_event_industry_snapshots + snapshot 表本身均已删除,
# _step_build_industry_stat_sync / backtest_engine / scoring 统一走 dim_stock_tdx_industry 直 JOIN。


async def _step_build_profiles(conn) -> int:
    """计算机构画像 mart_institution_profile"""
    return await _step_build_profiles_with_hooks(conn, should_stop=_raise_if_stop)


async def _step_build_trends(conn) -> int:
    """计算股票趋势 mart_stock_trend"""
    return await _step_build_trends_with_hooks(conn, should_stop=_raise_if_stop)


async def _step_sync_industry(conn) -> int:
    """通达信行业同步 — 拉取 tdxhy.cfg 并全量 upsert 到 dim_stock_tdx_industry"""
    return await _step_sync_industry_with_hooks(
        conn,
        tracked_stock_names=_tracked_stock_names,
        should_stop=_raise_if_stop,
        update_step=_update_step,
        open_conn=get_conn,
    )


async def _step_build_industry_stat(conn) -> int:
    """计算机构在各行业的表现统计"""
    return await _run_blocking_db_task(
        lambda worker_conn: _step_build_industry_stat_sync(
            worker_conn,
            should_stop=_raise_if_stop,
        )
    )


async def _step_sync_market_data(conn) -> int:
    """同步行情数据：合并原 kline_monthly + kline_daily，写入 market.duckdb"""
    return await _step_sync_market_data_with_hooks(
        conn,
        should_stop=_raise_if_stop,
        update_step=_update_step,
        stopped_exception_type=_RunStopped,
    )


async def _step_sync_financial(conn) -> dict:
    """同步财务数据（tdxhub finance）.

    §4.25 #4: 返回 dict 含 partial 语义 — 当 5 个子阶段
    (history/snapshot/capital/indicator/gpcw) 中部分失败但部分成功时,
    status='partial', 让 UI 显示有缺口而非误报 completed.
    """
    return await _step_sync_financial_with_hooks(
        conn,
        should_stop=_raise_if_stop,
        update_step=_update_step,
        daily_critical=_is_daily_critical_context(),
    )


# _step_sync_margin removed Phase ψ.5: raw_margin_daily was written daily but
# never consumed by any recommendation / backtest / signal pipeline.
# Only two leftover consumers were a UI score in routers/institution.py (also
# removed) and a smart_plan trigger in services/audit.py (also removed).


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
    "refresh_today_signals": _step_refresh_today_signals,
}


# ============================================================
# API 端点
# ============================================================

@router.post("/update/all")
async def update_all():
    """一键更新全部（当前主 DAG）"""
    global _is_running, _stop_requested
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    step_ids = step_ids_for(STEPS)
    _begin_run("all", step_ids=step_ids)

    asyncio.create_task(
        run_full_update_background(
            steps=STEPS,
            step_ids=step_ids,
            hard_deps=HARD_DEPS,
            runners=RUNNERS,
            deps=_execution_deps(),
        )
    )
    return {"ok": True, "steps": len(STEPS)}


@router.get("/update/status")
async def update_status():
    """更新状态"""
    return build_update_status_response(
        get_conn=get_conn,
        sync_step_status_catalog=_sync_step_status_catalog,
        running=_is_running,
        stop_requested=_stop_requested,
        run_context=_run_context,
        last_run_context=_last_run_context,
        ui_logs=get_ui_logs(),
        last_exception=_last_exception,  # P0-2: 异常渠道
    )


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
    return build_reset_derived_response(get_conn)


@router.post("/update/reset-industry-derived")
async def reset_industry_derived(restart_smart: bool = True):
    """清空行业口径切换后需要重算的快照和派生层，并可直接接续智能更新。"""
    global _is_running
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    response = build_reset_industry_response(get_conn)
    if not restart_smart:
        return response

    smart_result = await smart_update()
    total = sum(response.get("counts", {}).values())
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
    return build_update_audit_payload(force=force)


@router.get("/update/smart-plan", include_in_schema=False)
async def smart_plan(critical_only: bool = False):
    """内部运维接口：智能更新计划（不执行，只返回建议）。"""
    from services.audit import build_smart_plan

    return build_smart_plan_response(
        get_conn=get_conn,
        build_smart_plan=build_smart_plan,
        critical_only=critical_only,
    )


@router.post("/update/smart")
async def smart_update(critical_only: bool = False):
    """智能更新（先审计再决定跑什么）"""
    global _is_running, _stop_requested
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    from services.audit import build_smart_plan

    _begin_run()

    plan_result = await build_smart_update_plan(
        get_conn=get_conn,
        critical_only=critical_only,
        sync_calendar=_step_sync_calendar,
        build_smart_plan=build_smart_plan,
        ensure_calendar_step_for_data_fetch=_ensure_calendar_step_for_data_fetch,
    )

    if not plan_result["ok"]:
        _is_running = False
        return {
            "ok": False,
            "message": plan_result["message"],
            "calendar_preflight": plan_result["calendar_preflight"],
        }
    if plan_result.get("noop"):
        _is_running = False
        _set_last_noop_context("smart", plan_result["message"])
        return {
            "ok": True,
            "message": plan_result["message"],
            "plan": plan_result["plan"],
            "steps": 0,
            "step_ids": [],
            "noop": True,
        }

    plan = plan_result["plan"]
    steps_to_run = plan_result["steps_to_run"]
    _set_run_context("smart", step_ids=steps_to_run)
    if critical_only and _run_context is not None:
        _run_context["critical_only"] = True
    _prime_run_step_status(
        steps_to_run,
        inactive_mode="skipped",
        skip_reasons=plan.get("skip_reasons", {}),
    )
    logger.info(f"[智能更新] 已请求: {len(steps_to_run)} 个步骤待执行")

    asyncio.create_task(
        run_smart_update_background(
            steps=STEPS,
            steps_to_run=steps_to_run,
            hard_deps=HARD_DEPS,
            runners=RUNNERS,
            deps=_execution_deps(),
        )
    )
    return {"ok": True, "steps": len(steps_to_run), "step_ids": steps_to_run, "plan": plan}


@router.post("/update/step/{step_id}")
async def run_single_step(step_id: str):
    """执行单个步骤"""
    global _is_running, _stop_requested
    if step_id not in RUNNERS:
        return {"ok": False, "error": f"未知步骤: {step_id}"}
    if _is_running:
        return {"ok": False, "message": "更新正在进行中"}

    step_index = step_index_for(STEPS)
    step_name = step_name_for(step_index, step_id)
    step_ids = _ensure_calendar_step_for_data_fetch(_collect_downstream_steps(step_id))
    _begin_run("single", step_id, step_name, step_ids=step_ids)
    _prime_run_step_status(step_ids, inactive_mode="idle")
    logger.info(f"[单步] 已请求: {step_name}")

    asyncio.create_task(
        run_single_update_background(
            requested_step_id=step_id,
            step_name=step_name,
            step_ids=step_ids,
            step_index=step_index,
            hard_deps=HARD_DEPS,
            runners=RUNNERS,
            deps=_execution_deps(),
        )
    )
    return {"ok": True, "step_id": step_id, "name": step_name, "steps": step_ids}


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
    return launch_group_update_request(
        run_mode=run_mode,
        run_name=run_name,
        group_id=group_id,
        running=_is_running,
        steps=STEPS,
        hard_deps=HARD_DEPS,
        runners=RUNNERS,
        step_specs_for_group=step_specs_for_group,
        step_ids_for=step_ids_for,
        begin_run=_begin_run,
        prime_run_step_status=_prime_run_step_status,
        execution_deps=_execution_deps,
        create_task=asyncio.create_task,
        logger=logger,
    )
