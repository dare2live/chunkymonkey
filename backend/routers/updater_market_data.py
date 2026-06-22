"""Market data sync runner for the updater pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from routers.updater_infra import (
    _build_daily_sync_batch_summary,
    _format_sync_source_metrics,
    _record_sync_source_metric,
    _snapshot_sync_source_metrics,
)
from routers.updater_status import _parse_sync_time
from services.gap_queue import (
    load_tracked_stock_names,
    mark_gap_failed,
    mark_gap_resolved,
    mark_gap_retrying,
    reconcile_gap_queue_snapshot,
    summarize_gap_queue,
)
from services.market_db import get_canonical_kline_qfq_relation
from services.source_policy import normalize_kline_write_source
from services.tdx_source import iter_tdx_servers
from services.utils import latest_completed_trade_date

logger = logging.getLogger("cm-api")
KLINE_DAILY_QFQ_RELATION = get_canonical_kline_qfq_relation()


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


async def _step_sync_market_data(conn, *, should_stop, update_step, stopped_exception_type) -> int:
    """同步行情数据：合并原 kline_monthly + kline_daily，写入 market.duckdb

    Phase ψ.5: monthly + daily 都走 calendar-gated end_date (统一调用 latest_
    completed_trade_date), 不允许 fallback to wall-clock now.
    """
    import json as _json
    from services.market_db import (
        get_market_conn,
        upsert_price_kline_tdxhub_rows,
        update_sync_state,
        get_all_sync_states,
    )
    from services.akshare_client import (
        fetch_stock_kline_monthly,
        fetch_stock_kline_daily,
        probe_stock_kline_fallback_preference,
    )
    from services.xdxr_client import sync_xdxr_for_codes

    # Phase ψ.5: 统一 end_date — 在 step 入口算一次, 后续 monthly + daily 都用.
    monthly_end_iso = latest_completed_trade_date(conn)
    if not monthly_end_iso:
        raise RuntimeError(
            "[行情同步] dim_trading_calendar 未 seed, 拒绝 fallback to wall-clock"
        )
    monthly_end_date = monthly_end_iso.replace("-", "")
    logger.info(f"[行情同步] 月K + 日K end_date (calendar-gated): {monthly_end_iso}")

    mkt_conn = get_market_conn()
    sub_status = {}
    stock_names = load_tracked_stock_names(conn)
    codes = list(stock_names.keys())
    total_rows = 0

    def _dataset_gap_summary(dataset: str, limit: int = 6) -> dict:
        return summarize_gap_queue(conn, datasets=(dataset,), limit_per_dataset=limit)["datasets"][0]

    def _push_progress():
        update_step(
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
                "SELECT DISTINCT code FROM price_kline WHERE freq='monthly' AND adjust='qfq'"  # rule-compliance: ok evidence=writer覆盖检查(查已有monthly码集与入参codes做missing diff), 非派生策略宇宙; 真宇宙=入参codes(调用方已过universe)
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
            should_stop()
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
                kline_records, source = await fetch_stock_kline_monthly(
                    code, limit=36, start_date="20230101", end_date=monthly_end_date
                )
                if kline_records:
                    rows_data = [
                        {"code": code, "date": str(r["date"])[:10], "freq": "monthly",
                         "adjust": "qfq", "open": r["open"], "high": r["high"],
                         "low": r["low"], "close": r["close"],
                         "volume": r.get("volume"), "amount": r.get("amount")}
                        for r in kline_records
                    ]
                    write_source = normalize_kline_write_source(source)
                    # governance v1: price_kline 主表 stock 入库一律 reject (yaml retired_except_hs300_benchmark_allowlist)
                    # monthly stock K 线无 tdxhub native 表 → 跳过 + 记 deprecation
                    logger.info(
                        f"[governance v1] monthly stock K-line skipped (主表 retired): "
                        f"code={code} source={write_source} rows={len(rows_data)} "
                        f"reason=no_tdxhub_monthly_table"
                    )
                    rows_written = 0
                    if rows_written <= 0:
                        raise ValueError("monthly_kline_cleaner_rejected_all_rows")
                    dates = [r["date"] for r in rows_data]
                    update_sync_state(mkt_conn, code, "monthly", source=write_source,
                                      min_date=min(dates), max_date=max(dates),
                                      row_count=rows_written)
                    success_m += 1
                    total_rows += rows_written
                    monthly_rows_total += rows_written
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
            except stopped_exception_type:
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
            "status": "stopped" if isinstance(e, stopped_exception_type) else "failed",
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
        if isinstance(e, stopped_exception_type):
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
        # Phase ψ.5: 拒绝 wall-clock fallback, 日历不可用直接 raise (而不是落入盘中数据)
        latest_trade_date = latest_completed_trade_date(conn)
        if not latest_trade_date:
            raise RuntimeError(
                "[行情同步 reconcile] latest_completed_trade_date 返 None — "
                "dim_trading_calendar 未 seed, 拒绝用 wall-clock fallback"
            )
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

        # Phase ψ.5: 复用 step 入口算的 monthly_end_iso (monthly + daily 同一日期)
        daily_end_iso = monthly_end_iso
        daily_end_date = monthly_end_date
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
                should_stop()
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
                        # 双重防御 (Phase ψ.5): 即便上游 ignore end_date 仍返盘中 tick,
                        # 本地强制按 daily_end_iso 上界过滤, 不让盘中半成品落库.
                        rows_data = [
                            {"code": code, "date": str(r["date"])[:10], "freq": "daily",
                             "adjust": "qfq", "open": r["open"], "high": r["high"],
                             "low": r["low"], "close": r["close"],
                             "volume": r.get("volume"), "amount": r.get("amount")}
                            for r in kline_records
                            if str(r["date"])[:10] <= daily_end_iso
                        ]
                        if not rows_data:
                            # 上游全部返回的都是 daily_end_iso 之后的盘中数据, 跳过本股.
                            failed_codes.append(code)
                            update_sync_state(
                                mkt_conn, code, "daily", row_count=0,
                                error=f"all_records_after_{daily_end_iso}",
                            )
                            return
                        rows_written = len(rows_data)
                        write_source = normalize_kline_write_source(source)
                        if write_source.startswith("tdxhub"):
                            rows_written = upsert_price_kline_tdxhub_rows(mkt_conn, rows_data, source=write_source)
                        else:
                            # governance v1: stock daily 主表 retired, 只 tdxhub native
                            # from yaml: configs/data_governance.yaml schema_contracts.price_kline.forbidden_sources
                            logger.info(
                                f"[governance v1] non-tdxhub stock daily K-line skipped: "
                                f"code={code} source={write_source} rows={len(rows_data)} "
                                f"reason=主表_retired_只tdxhub_native"
                            )
                            rows_written = 0
                        if rows_written <= 0:
                            raise ValueError("daily_kline_cleaner_rejected_all_rows")
                        dates = [r["date"] for r in rows_data]
                        update_sync_state(mkt_conn, code, "daily", source=write_source,
                                          min_date=min(dates), max_date=max(dates),
                                          row_count=rows_written)
                        d_count += 1
                        daily_rows_total += rows_written
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
                except stopped_exception_type:
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
                should_stop()
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
            "status": "stopped" if isinstance(e, stopped_exception_type) else "failed",
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
        if isinstance(e, stopped_exception_type):
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
            should_stop=should_stop,
            progress_callback=_on_xdxr_progress,
        )
        total_rows += xdxr_status.get("rows", 0)
        sub_status["xdxr_sync"] = xdxr_status
        _push_progress()
    except Exception as e:
        sub_status["xdxr_sync"] = {
            "status": "stopped" if isinstance(e, stopped_exception_type) else "failed",
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
        if isinstance(e, stopped_exception_type):
            raise
        logger.error(f"[行情同步] xdxr失败: {e}")

    sub_status["sync_state_refresh"] = {"status": "success"}
    mkt_conn.close()

    # 把子阶段详情写入 step_status.error（JSON 格式）
    _push_progress()

    logger.info(f"[行情同步] 完成: {total_rows} 行")
    return total_rows
