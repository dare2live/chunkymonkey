"""External data sync runners for the updater pipeline."""

import asyncio
import json as _json
import logging
from datetime import datetime, timedelta

from routers.updater_runtime import _run_blocking_db_task
from services.update_tasks import ingest_holders_tdxhub_raw_parse, sync_gpcw_files_and_auto_features
from services.utils import latest_completed_trade_date

logger = logging.getLogger("cm-api")


async def _step_sync_raw(conn) -> dict:
    """十大流通股东 — 调 tdxhub raw->parse ingest.

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

    loop = asyncio.get_event_loop()

    def _do() -> dict:
        return ingest_holders_tdxhub_raw_parse(workers=4, con=raw_con)

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


async def _step_sync_financial(
    conn,
    *,
    should_stop,
    update_step,
    daily_critical: bool,
) -> dict:
    """同步财务数据（tdxhub finance）.

    §4.25 #4: 返回 dict 含 partial 语义 — 当 5 个子阶段
    (history/snapshot/capital/indicator/gpcw) 中部分失败但部分成功时,
    status='partial', 让 UI 显示有缺口而非误报 completed.
    """
    from services.financial_client import sync_financial_data

    progress_records = 0
    last_progress = {}

    def _on_progress(progress: dict):
        nonlocal progress_records, last_progress
        last_progress = progress or {}
        progress_records = ((last_progress.get("summary") or {}).get("records") or 0)
        update_step(
            conn,
            "sync_financial",
            error=_json.dumps(last_progress, ensure_ascii=False),
            records=progress_records,
        )

    total = await sync_financial_data(
        conn,
        progress_callback=_on_progress,
        should_stop=should_stop,
        include_history=not daily_critical,
        include_capital=not daily_critical,
        include_indicator=not daily_critical,
    )

    def _sync_gpcw_and_features(worker_conn):
        return sync_gpcw_files_and_auto_features(worker_conn, quarters=12)

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

    # 子阶段状态聚合: 任一 failed/error -> partial (除非全失败)
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
    update_step(
        conn,
        "sync_financial",
        error=_json.dumps(detail_payload, ensure_ascii=False),
        records=progress_records,
    )
    return detail_payload
