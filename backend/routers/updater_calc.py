"""Standalone calculation runners for the updater pipeline."""

import logging

from routers.updater_runtime import _run_blocking_db_task, _run_blocking_market_db_task

logger = logging.getLogger("cm-api")


async def _step_gen_events(conn) -> dict:
    """生成机构事件 (§4.25 #2 幂等化: 输入签名不变就跳过 DELETE+INSERT)."""
    from services.event_engine import (
        compute_gen_events_input_signature,
        generate_events,
        generate_exit_events,
        get_last_step_fingerprint,
        update_step_fingerprint,
    )

    def _worker(worker_conn):
        new_fp, n_holdings = compute_gen_events_input_signature(worker_conn)
        last_fp, last_count = get_last_step_fingerprint(worker_conn, "gen_events")
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

        if last_fp:
            logger.info(f"[事件] 输入签名变化 (旧 {last_fp[:12]}... → 新 {new_fp[:12]}...), 重建事件表")
        else:
            logger.info(f"[事件] 首次记录签名 ({new_fp[:12]}...), 生成事件")
        count = generate_events(worker_conn)
        count += generate_exit_events(worker_conn)
        update_step_fingerprint(worker_conn, "gen_events", new_fp, count)
        try:
            from services.schema_versions import record_actual_version

            record_actual_version(worker_conn, "fact_institution_event")
        except Exception:
            logger.debug("[schema] fact_institution_event version record skipped", exc_info=True)
        return {
            "count": count,
            "status": "completed",
            "written": count,
            "message": f"重建 {count} 条事件 (输入持仓 {n_holdings} 行)",
        }

    return await _run_blocking_db_task(_worker)


async def _step_calc_returns(conn) -> int:
    """计算事件收益"""
    from services.return_engine import calculate_returns

    return await _run_blocking_db_task(calculate_returns)


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
    from services.industry_context_engine import build_stock_industry_context
    from services.sector_momentum import calc_dual_confirm, calc_sector_momentum

    def _worker(worker_conn, worker_mkt_conn):
        sector_count = calc_sector_momentum(worker_conn, worker_mkt_conn)
        dual_count = calc_dual_confirm(worker_conn)
        context_count = build_stock_industry_context(worker_conn)
        return sector_count + dual_count + context_count

    return await _run_blocking_market_db_task(_worker)


async def _step_build_current_rel(conn) -> int:
    """构建 mart_current_relationship 物化表"""
    from services.holdings import build_current_relationship

    return await _run_blocking_db_task(build_current_relationship)


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


async def _step_build_external_attention(conn) -> int:
    """外部关注快照"""
    from services.external_attention import sync_external_attention_snapshot

    return await _run_blocking_db_task(sync_external_attention_snapshot)


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


async def _step_refresh_today_signals(conn) -> dict:
    """物化今日信号快照；页面打开只读这个快照，不隐式重算。"""
    from services.signals_v2 import load_config, materialize_today_signal_cache

    def _worker(worker_conn):
        cfg = load_config(worker_conn)
        payload = materialize_today_signal_cache(
            worker_conn,
            config=cfg,
            freshness_days=cfg.signal_freshness_days,
        )
        cache = payload.get("cache") or {}
        count = int(cache.get("signal_count") or len(payload.get("signals") or []))
        return {
            "count": count,
            "status": "completed",
            "freshness_days": cfg.signal_freshness_days,
            "built_at": cache.get("built_at"),
            "source_max_notice_date": cache.get("source_max_notice_date"),
            "message": f"物化 {count} 条今日信号快照",
        }

    return await _run_blocking_db_task(_worker)
