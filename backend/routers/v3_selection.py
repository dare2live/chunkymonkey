"""Phase ε — 优选追踪 + 反馈闭环 API。

端点 (供 design/v3-data-live.jsx 第四波 fetch 调用):
  GET /api/v3/selection/log           — 最近 selection 事件
  GET /api/v3/selection/history/{code} — 单股 outcome 时间线
  GET /api/v3/selection/summary       — 单股 rolling 统计
  GET /api/v3/selection/board         — 跨股 board (替代旧 /api/v3/selections)
  GET /api/v3/selection/weights       — 公式权重历史 (反馈环结果)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-selection")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(r and r[0])


# ============ 1. LOG ============
@router.get("/log")
async def get_log(
    from_date: str | None = Query(None, alias="from"),
    limit: int = Query(200, ge=1, le=1000),
    source: str | None = Query(None, description="filter: daily_topk | formula"),
):
    """最近 selection 事件 (默认 200 行)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "fact_stock_selection_log"):
            return {"ok": True, "data": [], "total": 0}
        where = []
        params: list[Any] = []
        if from_date:
            where.append("select_date >= ?")
            params.append(from_date)
        if source:
            where.append("select_source = ?")
            params.append(source)
        where_clause = "WHERE " + " AND ".join(where) if where else ""
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT select_date, stock_code, select_source, source_id,
                   rank_in_date, pred_score, strength, state, horizon_days
              FROM fact_stock_selection_log
              {where_clause}
             ORDER BY select_date DESC, stock_code
             LIMIT ?
            """,
            params,
        ).fetchall()
        data = [
            {
                "select_date": r[0], "stock_code": r[1],
                "select_source": r[2], "source_id": r[3],
                "rank_in_date": r[4], "pred_score": r[5],
                "strength": r[6], "state": r[7], "horizon_days": r[8],
            } for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


# ============ 2. HISTORY {code} ============
@router.get("/history/{stock_code}")
async def get_history(
    stock_code: str,
    limit: int = Query(50, ge=1, le=200),
):
    """单股 selection 历史 + outcome (mock SELECTION_HISTORY 形状)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "fact_stock_selection_log"):
            return {"ok": True, "data": [], "total": 0}
        rows = conn.execute(
            """
            SELECT l.select_date AS selectDate,
                   l.source_id AS formula,
                   COALESCE(o.horizon_days, 30) AS horizon,
                   o.fwd_ret_30d AS retPct,
                   o.fwd_max_dd_30d AS ddPct,
                   o.days_to_t1 AS daysToT1,
                   COALESCE(o.outcome_30d, 'active') AS outcome,
                   l.select_source
              FROM fact_stock_selection_log l
              LEFT JOIN mart_stock_selection_outcome o
                ON o.select_date    = l.select_date
               AND o.stock_code     = l.stock_code
               AND o.select_source  = l.select_source
               AND o.source_id      = l.source_id
             WHERE l.stock_code = ?
             ORDER BY l.select_date DESC LIMIT ?
            """,
            [stock_code, limit],
        ).fetchall()
        data = [
            {
                "selectDate": r[0],
                "formula": r[1],
                "horizon": int(r[2]) if r[2] is not None else 30,
                "retPct": float(r[3]) if r[3] is not None else None,
                "ddPct":  float(r[4]) if r[4] is not None else None,
                "daysToT1": int(r[5]) if r[5] is not None else None,
                "outcome": r[6],
                "select_source": r[7],
            } for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data), "stock_code": stock_code}
    finally:
        conn.close()


# ============ 3. SUMMARY (per stock) ============
@router.get("/summary")
async def get_summary(
    stock_code: str | None = Query(None),
    codes: str | None = Query(None, description="逗号分隔 codes, 上限 200"),
):
    """单股或多股 rolling 统计 (v3-data.jsx STOCKS 用)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_selection_summary"):
            return {"ok": True, "data": []}
        latest = conn.execute("SELECT MAX(snapshot_date) FROM mart_stock_selection_summary").fetchone()
        if not latest or not latest[0]:
            return {"ok": True, "data": []}
        code_list = []
        if codes:
            code_list = [c.strip() for c in codes.split(",") if c.strip()][:200]
        elif stock_code:
            code_list = [stock_code]
        if code_list:
            placeholders = ",".join(["?"] * len(code_list))
            sql = f"""
                SELECT stock_code, n_total, n_30d, n_90d,
                       win_rate, win_rate_30d, win_rate_90d,
                       avg_ret, avg_ret_30d, avg_dd,
                       last_select_date, last_formula, last_outcome
                  FROM mart_stock_selection_summary
                 WHERE snapshot_date = ? AND stock_code IN ({placeholders})
            """
            params = [latest[0]] + code_list
        else:
            sql = """
                SELECT stock_code, n_total, n_30d, n_90d,
                       win_rate, win_rate_30d, win_rate_90d,
                       avg_ret, avg_ret_30d, avg_dd,
                       last_select_date, last_formula, last_outcome
                  FROM mart_stock_selection_summary
                 WHERE snapshot_date = ?
            """
            params = [latest[0]]
        rows = conn.execute(sql, params).fetchall()
        data = [
            {
                "stock_code": r[0],
                "n_total": int(r[1]) if r[1] is not None else 0,
                "n_30d":   int(r[2]) if r[2] is not None else 0,
                "n_90d":   int(r[3]) if r[3] is not None else 0,
                "win_rate":      float(r[4]) if r[4] is not None else None,
                "win_rate_30d":  float(r[5]) if r[5] is not None else None,
                "win_rate_90d":  float(r[6]) if r[6] is not None else None,
                "avg_ret":       float(r[7]) if r[7] is not None else None,
                "avg_ret_30d":   float(r[8]) if r[8] is not None else None,
                "avg_dd":        float(r[9]) if r[9] is not None else None,
                "last_select_date": r[10],
                "last_formula":     r[11],
                "last_outcome":     r[12] or "active",
            } for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data), "snapshot_date": latest[0]}
    finally:
        conn.close()


# ============ 4. BOARD (cross-stock) ============
@router.get("/board")
async def get_board(limit: int = Query(50, ge=1, le=500)):
    """跨股 board (mock SELECTION_BOARD 形状)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_selection_summary"):
            return {"ok": True, "data": [], "total": 0}
        latest = conn.execute("SELECT MAX(snapshot_date) FROM mart_stock_selection_summary").fetchone()
        if not latest or not latest[0]:
            return {"ok": True, "data": [], "total": 0}
        rows = conn.execute(
            """
            SELECT s.stock_code, COALESCE(d.stock_name, s.stock_code) AS name,
                   s.n_30d, s.n_total, s.win_rate, s.avg_ret,
                   s.last_outcome, s.last_select_date, s.last_formula
              FROM mart_stock_selection_summary s
              LEFT JOIN dim_active_a_stock d ON d.stock_code = s.stock_code
             WHERE s.snapshot_date = ?
             ORDER BY s.n_30d DESC, s.win_rate DESC NULLS LAST
             LIMIT ?
            """,
            [latest[0], limit],
        ).fetchall()
        data = [
            {
                "code": r[0], "name": r[1],
                "n30": int(r[2]) if r[2] is not None else 0,
                "n_total": int(r[3]) if r[3] is not None else 0,
                "win": float(r[4]) if r[4] is not None else None,
                "avg_ret": float(r[5]) if r[5] is not None else None,
                "last_outcome": r[6] or "active",
                "last_date": r[7],
                "last_formula": r[8],
            } for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data), "snapshot_date": latest[0]}
    finally:
        conn.close()


# ============ 6. BLENDED RECOMMENDATION (Phase ε+ 反馈闭环) ============
@router.get("/blended")
async def get_blended(
    snapshot_date: str | None = Query(None, description="default: 最新"),
    limit: int = Query(50, ge=1, le=200),
):
    """反馈环融合后的 daily-topk: base ML pred_score × (1 + Σ formula_weight × sign × strength)。

    sign 由 mart_formula_weight_history.rolling_ic_60d 决定: ≤0 → -1 (sign-flip)。
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_daily_blended_recommendation"):
            return {"ok": True, "data": [], "total": 0}
        if snapshot_date:
            target = snapshot_date
        else:
            r = conn.execute("SELECT MAX(snapshot_date) FROM mart_daily_blended_recommendation").fetchone()
            target = r[0] if r else None
        if not target:
            return {"ok": True, "data": [], "total": 0}
        rows = conn.execute(
            """
            SELECT stock_code, rank_in_date, base_rank_in_date,
                   base_pred_score, formula_bonus, blended_score,
                   formula_breakdown_json
              FROM mart_daily_blended_recommendation
             WHERE snapshot_date = ?
             ORDER BY rank_in_date LIMIT ?
            """,
            [target, limit],
        ).fetchall()
        import json as _json
        data = [
            {
                "stock_code": r[0],
                "rank_in_date": int(r[1]) if r[1] is not None else None,
                "base_rank_in_date": int(r[2]) if r[2] is not None else None,
                "rank_delta": (int(r[2]) - int(r[1])) if (r[1] and r[2]) else None,
                "base_pred_score": float(r[3]) if r[3] is not None else None,
                "formula_bonus": float(r[4]) if r[4] is not None else None,
                "blended_score": float(r[5]) if r[5] is not None else None,
                "formula_breakdown": _json.loads(r[6]) if r[6] else [],
            } for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data), "snapshot_date": target}
    finally:
        conn.close()


# ============ 5. WEIGHTS (反馈环) ============
@router.get("/weights")
async def get_weights(
    asof: str | None = Query(None, description="default: 最新"),
):
    """公式权重 (Phase ε 反馈环结果)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_formula_weight_history"):
            return {"ok": True, "data": [], "total": 0}
        if asof:
            target = asof
        else:
            r = conn.execute("SELECT MAX(snapshot_date) FROM mart_formula_weight_history").fetchone()
            target = r[0] if r else None
        if not target:
            return {"ok": True, "data": [], "total": 0}
        rows = conn.execute(
            """
            SELECT formula_id, formula_variant, weight,
                   rolling_ic_30d, rolling_ic_60d, n_obs, is_active
              FROM mart_formula_weight_history
             WHERE snapshot_date = ?
             ORDER BY weight DESC
            """,
            [target],
        ).fetchall()
        data = [
            {
                "formula_id": r[0],
                "formula_variant": r[1],
                "weight": float(r[2]) if r[2] is not None else 0.0,
                "rolling_ic_30d": float(r[3]) if r[3] is not None else None,
                "rolling_ic_60d": float(r[4]) if r[4] is not None else None,
                "n_obs": int(r[5]) if r[5] is not None else 0,
                "is_active": bool(r[6]),
            } for r in rows
        ]
        return {"ok": True, "data": data, "total": len(data), "snapshot_date": target}
    finally:
        conn.close()
