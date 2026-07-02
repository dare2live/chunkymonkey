"""paper_portfolio router — 实盘模拟 (手动版) API (2026-07-02)

前端契约 (用户前端原则: 卡片↔entity/API 一一对应, widget 独立取数):
  POST   /api/v3/paper/positions        入池 {stock_code, amount|shares, strategy_tag?, note?}
  DELETE /api/v3/paper/positions/{id}   出池
  GET    /api/v3/paper/portfolio        组合状态 (positions + KPI 胜率/收益/超额)
  GET    /api/v3/paper/nav              nav 曲线 (前端画 vs HS300)
  POST   /api/v3/paper/mark             手动 mark-to-market (daily_update 也会自动跑)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import paper_portfolio as pp
from services.db import get_conn

router = APIRouter()


class AddPositionReq(BaseModel):
    stock_code: str = Field(min_length=6, max_length=6)
    amount: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    strategy_tag: str = "manual"
    note: str = ""


@router.post("/positions")
def add_position(req: AddPositionReq):
    try:
        return {"status": "ok", "data": pp.add_position(
            req.stock_code, amount=req.amount, shares=req.shares,
            strategy_tag=req.strategy_tag, note=req.note)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/positions/{position_id}")
def close_position(position_id: str):
    try:
        return {"status": "ok", "data": pp.close_position(position_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/portfolio")
def portfolio():
    conn = get_conn()
    try:
        pp.ensure_tables(conn)
        positions = [dict(zip(
            ["position_id", "strategy_tag", "stock_code", "shares", "entry_date", "entry_price",
             "status", "exit_date", "exit_price", "note"], r)) for r in conn.execute(
            "SELECT position_id, strategy_tag, stock_code, shares, entry_date, entry_price, "
            "status, exit_date, exit_price, note FROM paper_position ORDER BY created_at DESC"
        ).fetchall()]
        return {"status": "ok", "positions": positions, "kpi": pp.portfolio_kpi(conn=conn)}
    finally:
        conn.close()


@router.get("/nav")
def nav():
    conn = get_conn()
    try:
        pp.ensure_tables(conn)
        rows = [dict(zip(["nav_date", "nav", "cash", "position_value", "n_open", "bench_close"], r))
                for r in conn.execute(
                    "SELECT nav_date, nav, cash, position_value, n_open, bench_close "
                    "FROM paper_nav_daily ORDER BY nav_date").fetchall()]
        return {"status": "ok", "nav": rows}
    finally:
        conn.close()


@router.post("/mark")
def mark():
    try:
        return {"status": "ok", "data": pp.mark_to_market()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
