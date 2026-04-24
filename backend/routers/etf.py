from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
import asyncio
import logging
from datetime import datetime

from services.etf_engine import sync_etf_universe
from services.etf_mining_engine import analyze_etf_deep
from services.etf_db import get_etf_conn
from services.etf_snapshot_manager import get_latest_etf_snapshot_bundle, persist_latest_etf_snapshot

router = APIRouter(tags=["ETF_Quant"])
# 使用 cm-api logger，让 ETF 日志走入 routers/updater.py 的 _UILogHandler
logger = logging.getLogger("cm-api")


# ============================================================
# 后台运行状态 + 进度
# 与 routers/updater.py 的 _is_running / _ui_logs 走同一套基础设施，
# 但 ETF 自有进度状态，避免和主流水线互相覆盖
# ============================================================

_etf_state: Dict[str, Any] = {
    "running": False,
    "stage": "idle",       # idle | fetch_list | write_universe | sync_kline | build_snapshot | done | error
    "current": 0,
    "total": 0,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
    "log_seq_start": 0,    # 本次任务开始时的 _ui_log_seq，用于切片日志
}


def _get_log_seq_now() -> int:
    """读取 routers/updater.py 当前的 UI log 序号，用于划定本次 ETF 任务的日志窗口。"""
    try:
        from routers import updater as _u
        return getattr(_u, "_ui_log_seq", 0) or 0
    except Exception:
        return 0


def _get_ui_logs_after(seq: int) -> List[dict]:
    try:
        from routers import updater as _u
        return [r for r in getattr(_u, "_ui_logs", []) if r.get("id", 0) > seq]
    except Exception:
        return []


def _progress_cb(stage: str, current: int, total: int, message: str) -> None:
    _etf_state["stage"] = stage
    _etf_state["current"] = current
    _etf_state["total"] = total
    if message:
        _etf_state["message"] = message


@router.get("/list")
async def get_etf_list(force_refresh: bool = Query(False, description="是否强制重算最新 ETF 快照")) -> Dict[str, Any]:
    """返回 ETF 列表与最新缓存快照。"""
    conn = get_etf_conn()
    try:
        bundle = get_latest_etf_snapshot_bundle(conn, conn, force_refresh=force_refresh)
        return {
            "status": "ok",
            "data": bundle["rows"],
            "count": len(bundle["rows"]),
            "overview": bundle["overview"],
            "snapshot": {
                "snapshot_id": bundle.get("snapshot_id"),
                "computed_at": bundle.get("computed_at"),
                "etf_count": bundle.get("etf_count"),
                "is_stale": bundle.get("is_stale", False),
            },
            "source_status": bundle.get("source_status") or {},
        }
    except Exception as e:
        logger.error(f"[ETF] 获取列表失败: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


@router.get("/workbench")
async def get_etf_workbench(force_refresh: bool = Query(False, description="是否强制重算最新 ETF 快照")) -> Dict[str, Any]:
    from routers.updater import check_connectivity, get_cached_connectivity

    conn = get_etf_conn()
    try:
        connectivity = await check_connectivity(force=True) if force_refresh else get_cached_connectivity()
        bundle = get_latest_etf_snapshot_bundle(
            conn,
            conn,
            force_refresh=force_refresh,
            connectivity=connectivity,
        )
        mining = bundle.get("mining_snapshot") or {}
        factor_snapshot = bundle.get("factor_snapshot") or {}
        return {
            "status": "ok",
            "data": {
                "snapshot": {
                    "snapshot_id": bundle.get("snapshot_id"),
                    "computed_at": bundle.get("computed_at"),
                    "etf_count": bundle.get("etf_count"),
                    "is_stale": bundle.get("is_stale", False),
                },
                "source_status": bundle.get("source_status") or {},
                "overview": bundle.get("overview") or {},
                "mining": {
                    "grid_candidates": (mining.get("grid_candidates") or [])[:5],
                    "trend_candidates": (mining.get("trend_candidates") or [])[:5],
                    "next_rotation_watchlist": (mining.get("next_rotation_watchlist") or [])[:5],
                    "factor_snapshot_id": mining.get("factor_snapshot_id"),
                },
                "factor_snapshot": {
                    "model": factor_snapshot.get("model") or {},
                    "leaders": (factor_snapshot.get("leaders") or [])[:6],
                    "categories": (factor_snapshot.get("categories") or [])[:6],
                },
                "sync_state": {
                    "running": _etf_state.get("running"),
                    "stage": _etf_state.get("stage"),
                    "message": _etf_state.get("message"),
                    "started_at": _etf_state.get("started_at"),
                    "finished_at": _etf_state.get("finished_at"),
                    "result": _etf_state.get("result"),
                    "error": _etf_state.get("error"),
                },
            },
        }
    except Exception as e:
        logger.error(f"[ETF] ETF 工作台失败: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


@router.get("/status")
async def etf_status(log_limit: int = Query(60, ge=0, le=400)) -> Dict[str, Any]:
    """ETF 同步任务状态 + 实时日志窗口（前端轮询用）"""
    state = dict(_etf_state)
    if log_limit > 0 and state.get("started_at"):
        logs = _get_ui_logs_after(state["log_seq_start"])
        # ETF 相关行优先；保留最后 N 条
        etf_logs = [r for r in logs if "[ETF]" in (r.get("message") or "")]
        state["logs"] = etf_logs[-log_limit:]
    else:
        state["logs"] = []
    return {"status": "ok", "data": state}


@router.post("/sync")
async def api_sync_etf(
    sync_kline: bool = Query(True, description="是否同时同步 K 线"),
    kline_start_date: str = Query("20230101", description="同步 K 线起始日期，格式 YYYYMMDD"),
    kline_days: int = Query(120, description="旧参数：仅当未提供起始日期时回退使用"),
    max_etfs: int = Query(None, description="限制 ETF 数量（调试用）"),
) -> Dict[str, Any]:
    """触发 ETF 资产池 + K 线同步（mootdx）

    异步执行：立即返回，前端通过 GET /api/etf/status 轮询进度与日志。
    """
    if _etf_state.get("running"):
        return {
            "status": "ok",
            "message": "ETF 同步正在进行中",
            "data": dict(_etf_state),
        }

    # 重置状态
    _etf_state.update({
        "running": True,
        "stage": "starting",
        "current": 0,
        "total": 0,
        "message": "ETF 同步启动中…",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "result": None,
        "error": None,
        "log_seq_start": _get_log_seq_now(),
    })
    logger.info(
        f"[ETF] 同步任务启动 sync_kline={sync_kline} start={kline_start_date or '-'} days={kline_days}"
    )

    async def _run():
        conn = get_etf_conn()
        try:
            result = await sync_etf_universe(
                conn, conn,
                sync_kline=sync_kline,
                kline_days=kline_days,
                kline_start_date=kline_start_date,
                max_etfs=max_etfs,
                progress_cb=_progress_cb,
            )
            _progress_cb("build_snapshot", result.get("etf_count") or 0, result.get("etf_count") or 0, "重建 ETF 最新快照")
            snapshot = persist_latest_etf_snapshot(conn, conn)
            result["snapshot_id"] = snapshot.get("snapshot_id")
            result["snapshot_computed_at"] = snapshot.get("computed_at")
            _etf_state.update({
                "stage": "done",
                "result": result,
                "message": (
                    f"完成：ETF {result['etf_count']} / "
                    f"K 线 {result['kline_etf_count']} / 行 {result['kline_rows']} / 快照 {result['snapshot_id']}"
                ),
            })
            logger.info(f"[ETF] 同步完成 {result}")
        except Exception as e:
            _etf_state.update({
                "stage": "error",
                "error": str(e),
                "message": f"同步失败：{e}",
            })
            logger.error(f"[ETF] 同步失败: {e}")
        finally:
            _etf_state["running"] = False
            _etf_state["finished_at"] = datetime.now().isoformat()
            try:
                conn.close()
            except Exception:
                pass

    asyncio.create_task(_run())
    return {
        "status": "ok",
        "message": "ETF 同步已启动，请在状态面板观察进度",
        "data": dict(_etf_state),
    }


@router.get("/mining")
async def get_etf_mining(
    grid_topn: int = Query(6, ge=1, le=12),
    trend_topn: int = Query(6, ge=1, le=12),
    rotation_topn: int = Query(5, ge=1, le=10),
    force_refresh: bool = Query(False, description="是否强制重算最新 ETF 快照"),
) -> Dict[str, Any]:
    """ETF 挖掘建议。

    输出三块：
    - 网格交易：回测验证后仍具超额的标的与建议步长
    - 买入持有：趋势和因子同时占优的标的
    - 下一轮动行业：基于 ETF 原生因子聚合的类别观察名单
    """
    conn = get_etf_conn()
    try:
        bundle = get_latest_etf_snapshot_bundle(conn, conn, force_refresh=force_refresh)
        data = dict(bundle.get("mining_snapshot") or {})
        data["grid_candidates"] = (data.get("grid_candidates") or [])[:grid_topn]
        data["trend_candidates"] = (data.get("trend_candidates") or [])[:trend_topn]
        data["next_rotation_watchlist"] = (data.get("next_rotation_watchlist") or [])[:rotation_topn]
        return {
            "status": "ok",
            "data": data,
            "snapshot": {
                "snapshot_id": bundle.get("snapshot_id"),
                "computed_at": bundle.get("computed_at"),
                "is_stale": bundle.get("is_stale", False),
            },
        }
    except Exception as e:
        logger.error(f"[ETF] ETF 挖掘建议生成失败: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


@router.get("/grid/optimize")
async def optimize_grid(
    code: str = Query(..., description="ETF 代码, 6 位数字"),
    lookback_days: int = Query(240, ge=60, le=1200, description="回测窗口 (交易日)"),
) -> Dict[str, Any]:
    """ETF 网格自寻优: 在参数空间遍历 step_pct, 返回每个候选的回测结果

    参数空间: 0.5% → 4.5% 每 0.1% 扫描 (约 40 个 step)
    回测窗口: 默认近 240 个交易日
    返回:
      best: 最优参数 + 回测完整报告
      candidates: 所有候选的评分表 (前端可视化 step-return 曲线)
      buy_hold: 同窗口 buy-and-hold 基准
    """
    import re
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=400, detail="ETF 代码必须为 6 位数字")

    from services.etf_grid_engine import (
        _run_grid_backtest, _score_grid_backtest, _buy_hold_stats,
        assess_etf_tradeability, is_supported_exchange_etf_code,
    )

    if not is_supported_exchange_etf_code(code):
        raise HTTPException(status_code=400, detail=f"ETF {code} 不在支持的交易所列表")

    conn = get_etf_conn()
    try:
        # 取基础信息
        row = conn.execute(
            "SELECT code, name, category FROM etf_asset_universe WHERE code = ?",
            [code],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"ETF {code} 不存在")
        base_info = {"code": row[0], "name": row[1], "category": row[2]}

        # 取价格序列 (最近 N 个交易日)
        price_rows_raw = conn.execute(
            """
            SELECT date, open, high, low, close, volume, amount
            FROM etf_price_kline
            WHERE code = ? AND freq='daily' AND adjust='qfq'
            ORDER BY date DESC LIMIT ?
            """,
            [code, lookback_days],
        ).fetchall()
        if len(price_rows_raw) < 60:
            return {"status": "empty", "message": f"ETF {code} 价格数据不足 60 天"}

        price_rows = [
            {"date": str(r[0]), "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": r[5], "amount": r[6]}
            for r in reversed(price_rows_raw)
        ]

        # 可交易性判定
        tradeability = assess_etf_tradeability(code, row[1] or "", row[2], price_rows)
        if not tradeability.get("supported"):
            return {
                "status": "unsupported",
                "message": tradeability.get("reason", "不支持网格"),
                "tradeability": tradeability,
                "info": base_info,
            }

        # 参数空间: 0.5 → 4.5, step 0.1
        step_candidates = [round(0.5 + i * 0.1, 1) for i in range(41)]
        buy_hold = _buy_hold_stats(price_rows)
        all_results = []
        for step_pct in step_candidates:
            backtest = _run_grid_backtest(price_rows, step_pct)
            if not backtest:
                continue
            scored = _score_grid_backtest(backtest, buy_hold, row={**base_info})
            all_results.append(scored)

        feasible = [r for r in all_results if r.get("hard_gate_passed")]
        feasible.sort(
            key=lambda it: (
                -(it.get("candidate_score") or 0.0),
                -(it.get("backtest_excess_pct") or -999.0),
                -(it.get("return_pct") or -999.0),
            )
        )

        candidates_view = [
            {
                "step_pct": r.get("step_pct"),
                "return_pct": r.get("return_pct"),
                "buy_hold_return_pct": buy_hold.get("return_pct") if buy_hold else None,
                "excess_pct": r.get("backtest_excess_pct"),
                "max_drawdown_pct": r.get("max_drawdown_pct"),
                "sharpe": r.get("sharpe"),
                "trade_count": r.get("trade_count"),
                "sell_count": r.get("sell_count"),
                "win_rate": r.get("win_rate"),
                "candidate_score": r.get("candidate_score"),
                "hard_gate_passed": bool(r.get("hard_gate_passed")),
            }
            for r in all_results
        ]
        best = feasible[0] if feasible else None

        return {
            "status": "ok",
            "info": base_info,
            "lookback_days": lookback_days,
            "price_window": {"from": price_rows[0]["date"], "to": price_rows[-1]["date"]},
            "buy_hold": buy_hold,
            "candidates": candidates_view,
            "best": best,
            "feasible_count": len(feasible),
            "total_count": len(all_results),
            "tradeability": tradeability,
        }
    finally:
        conn.close()


@router.get("/sector-rotation")
async def get_sector_rotation(
    limit: int = Query(20, ge=1, le=100, description="返回板块数"),
) -> Dict[str, Any]:
    """ETF 板块轮动快照 (基于 mart_etf_sector_rotation)

    供前端机会发现页的"板块轮动"模块使用。
    按 rotation_score DESC 排序, 返回完整明细给前端自行展示.
    """
    conn = get_etf_conn()
    try:
        # 取最新 snapshot_date
        row = conn.execute(
            "SELECT MAX(snapshot_date) FROM mart_etf_sector_rotation"
        ).fetchone()
        snapshot = str(row[0]) if row and row[0] else None
        if not snapshot:
            return {
                "status": "empty",
                "message": "尚未生成板块轮动快照, 请运行 scripts/build_etf_sector_rotation.py",
                "items": [],
            }

        rows = conn.execute(
            """
            SELECT sector, etf_count,
                   avg_ret_20d, avg_ret_60d, amount_chg_20d,
                   rel_strength_4w, rel_strength_12w,
                   rotation_score, rotation_rank, rotation_label,
                   leading_etf_code, leading_etf_name
            FROM mart_etf_sector_rotation
            WHERE snapshot_date = ?
            ORDER BY rotation_rank
            LIMIT ?
            """,
            [snapshot, limit],
        ).fetchall()

        items = [
            {
                "sector": r[0],
                "etf_count": r[1],
                "avg_ret_20d": float(r[2]) if r[2] is not None else None,
                "avg_ret_60d": float(r[3]) if r[3] is not None else None,
                "amount_chg_20d": float(r[4]) if r[4] is not None else None,
                "rel_strength_4w": float(r[5]) if r[5] is not None else None,
                "rel_strength_12w": float(r[6]) if r[6] is not None else None,
                "rotation_score": float(r[7]) if r[7] is not None else None,
                "rotation_rank": int(r[8]) if r[8] is not None else None,
                "rotation_label": r[9],
                "leading_etf_code": r[10],
                "leading_etf_name": r[11],
            }
            for r in rows
        ]
        return {
            "status": "ok",
            "snapshot_date": snapshot,
            "count": len(items),
            "items": items,
        }
    finally:
        conn.close()


@router.get("/analysis/{code}")
async def get_etf_analysis(code: str) -> Dict[str, Any]:
    """单只 ETF 深度量化分析。

    返回多步长回测对比、买入持有基准、多周期稳定性检验、量化结论。
    前端用于展示详细分析面板。
    """
    import re
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=400, detail="ETF 代码格式错误")

    conn = get_etf_conn()
    try:
        result = analyze_etf_deep(conn, conn, code)
        if result is None:
            raise HTTPException(status_code=404, detail=f"ETF {code} 不存在或数据不足")
        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ETF] 深度分析 {code} 失败: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()
