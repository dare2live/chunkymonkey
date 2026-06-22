"""Phase δ — v3 Paper Engine API。

端点 (供 design/v3-data-live.jsx 第三波 fetch 调用):
  GET /api/v3/paper/nav        — NAV 时间序列 + 当前
  GET /api/v3/paper/holdings   — 当前持仓 (open positions)
  GET /api/v3/paper/kpis       — 聚合 KPI (sharpe / max_dd / excess / monthly_win)
  GET /api/v3/paper/signal-ic  — 每公式最新 IC
  GET /api/v3/paper/pl-attr    — P&L 归因 (by_formula)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Query

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-paper")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(r and r[0])


def _latest_picture_by_code(conn, codes: list[str]) -> dict[str, tuple[Any, Any]]:
    unique_codes = sorted({code for code in codes if code})
    if not unique_codes or not _table_exists(conn, "mart_stock_picture_daily"):
        return {}

    placeholders = ",".join("?" for _ in unique_codes)
    rows = conn.execute(
        f"""
        SELECT stock_code, latest_close, technical_stage
          FROM (
            SELECT stock_code, latest_close, technical_stage,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code
                       ORDER BY snapshot_date DESC
                   ) AS rn
              FROM mart_stock_picture_daily
             WHERE stock_code IN ({placeholders})
          )
         WHERE rn = 1
        """,
        unique_codes,
    ).fetchall()
    return {str(r[0]): (r[1], r[2]) for r in rows}


# ============ NAV ============
@router.get("/nav")
async def get_nav(
    from_date: str | None = Query(None, alias="from"),
    model_id: str = Query("paper_v1"),
):
    """NAV 时间序列 + 最新一日。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_paper_nav"):
            return {"ok": True, "data": [], "latest": None}
        sql = """
            SELECT snapshot_date, nav, nav_value, daily_ret, cum_ret,
                   hs300_nav, hs300_cum_ret, vs_hs300_cum_ret,
                   eqw_nav, eqw_cum_ret, vs_eqw_cum_ret,
                   cash, position_count, drawdown
              FROM mart_paper_nav
             WHERE model_id = ?
        """
        params = [model_id]
        if from_date:
            sql += " AND snapshot_date >= ?"
            params.append(from_date)
        sql += " ORDER BY snapshot_date"
        rows = conn.execute(sql, params).fetchall()
        data = [
            {
                "snapshot_date": r[0], "nav": r[1], "nav_value": r[2],
                "daily_ret": r[3], "cum_ret": r[4],
                "hs300_nav": r[5], "hs300_cum_ret": r[6], "vs_hs300_cum_ret": r[7],
                "eqw_nav": r[8], "eqw_cum_ret": r[9], "vs_eqw_cum_ret": r[10],
                "cash": r[11], "position_count": r[12], "drawdown": r[13],
            }
            for r in rows
        ]
        latest = data[-1] if data else None
        return {"ok": True, "data": data, "latest": latest, "total": len(data)}
    finally:
        conn.close()


# ============ HOLDINGS ============
@router.get("/holdings")
async def get_holdings(model_id: str = Query("paper_v1")):
    """当前持仓 (open positions)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "fact_paper_position"):
            return {"ok": True, "data": []}
        # 每股有 buy 行但无对应 sell 行 = open
        rows = conn.execute(
            """
            SELECT b.stock_code, b.event_date, b.exec_price, b.qty, b.notional, b.reason
              FROM fact_paper_position b
             WHERE b.side = 'buy' AND b.model_id = ?
               AND NOT EXISTS (
                 SELECT 1 FROM fact_paper_position s
                  WHERE s.stock_code = b.stock_code AND s.side='sell'
                    AND s.model_id = b.model_id AND s.entry_date = b.event_date
               )
             ORDER BY b.event_date DESC, b.notional DESC
            """,
            [model_id],
        ).fetchall()
        # 拿最新 NAV 算 weight + 批量拿最新 picture 算 close/stage
        latest_nav_row = conn.execute(
            "SELECT nav_value FROM mart_paper_nav WHERE model_id=? ORDER BY snapshot_date DESC LIMIT 1",
            [model_id],
        ).fetchone()
        total_nav = float(latest_nav_row[0]) if latest_nav_row else 1_000_000.0
        pictures = _latest_picture_by_code(conn, [str(r[0]) for r in rows])

        data = []
        for r in rows:
            code, open_date, open_price, qty, notional, reason = r
            pic = pictures.get(str(code))
            cur_close = float(pic[0]) if pic and pic[0] else float(open_price)
            stage = pic[1] if pic else "—"
            cur_value = qty * cur_close
            ret_pct = (cur_close / float(open_price) - 1) if open_price else 0.0
            from datetime import date as _date
            try:
                hd = (_date.today() - _date.fromisoformat(open_date)).days  # rule-compliance: ok evidence=持仓天数展示(日历天), 非PIT决策锚
            except Exception:
                hd = 0
            data.append({
                "code": code,
                "open_date": open_date,
                "open_price": float(open_price),
                "current_close": cur_close,
                "qty": int(qty),
                "notional": float(notional),
                "current_value": cur_value,
                "ret_pct": ret_pct,
                "holding_days": hd,
                "technical_stage": stage,
                "weight_pct": cur_value / total_nav if total_nav > 0 else 0.0,
                "reason": reason,
            })
        return {"ok": True, "data": data, "total": len(data)}
    finally:
        conn.close()


# ============ KPIS ============
@router.get("/kpis")
async def get_kpis(model_id: str = Query("paper_v1"), window: int = Query(120, ge=1, le=500)):
    """聚合 KPI: nav / nav_chg_pct / max_dd / monthly_win / position_count / cash_pct。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_paper_nav"):
            return {"ok": True, "data": {}}
        rows = conn.execute(
            """
            SELECT snapshot_date, nav, nav_value, daily_ret, cum_ret,
                   hs300_cum_ret, position_count, drawdown, cash
              FROM mart_paper_nav
             WHERE model_id = ?
             ORDER BY snapshot_date DESC LIMIT ?
            """,
            [model_id, window],
        ).fetchall()
        if not rows:
            return {"ok": True, "data": {}}
        rows = list(reversed(rows))  # 升序

        # 调 portfolio.compute_kpis
        from services.paper_engine.portfolio import compute_kpis
        series = [
            {
                "snapshot_date": r[0], "nav_value": float(r[2] or 0.0),
                "hs300_cum_ret": float(r[5]) if r[5] is not None else None,
            }
            for r in rows
        ]
        latest = rows[-1]
        initial = float(rows[0][2]) / float(rows[0][1]) if rows[0][1] else 1_000_000.0
        kpis = compute_kpis(series, starting_nav=initial)
        # 附加当日
        kpis["position_count"] = int(latest[6]) if latest[6] is not None else 0
        kpis["cash"] = float(latest[8]) if latest[8] is not None else 0.0
        kpis["cash_pct"] = float(latest[8]) / float(latest[2]) if latest[2] else 1.0
        kpis["current_drawdown_pct"] = float(latest[7]) if latest[7] is not None else 0.0
        return {"ok": True, "data": kpis}
    finally:
        conn.close()


# ============ SIGNAL IC ============
@router.get("/signal-ic")
async def get_signal_ic(window: int = Query(60, ge=1, le=365), horizon: int = Query(10)):
    """每公式最近 window 日的滚动 IC 均值。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_signal_ic"):
            return {"ok": True, "data": []}
        # 取 latest_date
        latest = conn.execute("SELECT MAX(snapshot_date) FROM mart_signal_ic").fetchone()
        if not latest or not latest[0]:
            return {"ok": True, "data": []}
        col = f"ic_{horizon}d" if horizon in (5, 10, 30) else "ic_10d"
        # window 日内每公式平均
        from datetime import date as _date, timedelta
        cutoff = (_date.fromisoformat(latest[0]) - timedelta(days=window)).isoformat()
        rows = conn.execute(
            f"""
            SELECT formula_id, AVG({col}) AS ic, AVG(n_signals) AS avg_n, COUNT(*) AS n_dates
              FROM mart_signal_ic
             WHERE snapshot_date >= ? AND {col} IS NOT NULL
             GROUP BY formula_id ORDER BY ic DESC NULLS LAST
            """,
            [cutoff],
        ).fetchall()
        data = [
            {
                "signal": r[0],
                "formula_id": r[0],
                "ic": float(r[1]) if r[1] is not None else None,
                "avg_n_signals": float(r[2]) if r[2] is not None else 0,
                "n_dates": int(r[3]),
            }
            for r in rows
        ]
        return {"ok": True, "data": data, "latest_date": latest[0], "horizon": horizon}
    finally:
        conn.close()


# ============ PL ATTRIBUTION ============
@router.get("/pl-attr")
async def get_pl_attr(window: int = Query(30, ge=1, le=365), model_id: str = Query("paper_v1")):
    """P&L 归因 (by_formula): sell 行的 gross_return × notional 按 reason 分组。

    Phase δ 简化: 只输出 by_formula (用 reason 字段 surrogate 公式), 行业/风格 留 Phase ε。
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "fact_paper_position"):
            return {"ok": True, "data": {"by_formula": []}}
        from datetime import date as _date, timedelta
        cutoff = (_date.today() - timedelta(days=window)).isoformat()  # rule-compliance: ok evidence=PnL归因展示窗(日历天), 非PIT锚
        rows = conn.execute(
            """
            SELECT COALESCE(reason, 'unknown') AS k, SUM(notional * gross_return) AS pnl
              FROM fact_paper_position
             WHERE side='sell' AND model_id = ? AND event_date >= ?
             GROUP BY 1 ORDER BY pnl DESC
            """,
            [model_id, cutoff],
        ).fetchall()
        total = sum(float(r[1] or 0.0) for r in rows)
        by_formula = [
            {
                "k": r[0],
                "v": round(float(r[1] or 0.0), 2),
                "pct": (float(r[1] or 0.0) / total) if total else 0.0,
            }
            for r in rows
        ]
        return {"ok": True, "data": {"by_formula": by_formula}, "total_pnl": round(total, 2)}
    finally:
        conn.close()
