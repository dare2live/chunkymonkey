"""选股筛选 API 路由。"""

from fastapi import APIRouter, Query
from services.db import get_conn
from services.industry_overview_read import get_industry_overview_payload
from services.screening_read import (
    get_screening_detail,
    get_screening_summary,
    list_dual_confirm_rows,
    list_screening_results,
)

router = APIRouter()


@router.get("/sector-momentum", include_in_schema=False)
async def get_sector_momentum():
    """兼容查询：板块动量状态。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mart_sector_momentum ORDER BY momentum_score DESC"
        ).fetchall()
        return {"ok": True, "count": len(rows), "data": [dict(r) for r in rows]}
    except Exception:
        return {"ok": True, "count": 0, "data": []}
    finally:
        conn.close()


@router.get("/dual-confirm", include_in_schema=False)
async def get_dual_confirm(hits_only: bool = Query(True)):
    """兼容查询：双重确认信号。"""
    conn = get_conn()
    try:
        rows = list_dual_confirm_rows(conn, hits_only=hits_only, limit=500)
        return {"ok": True, "count": len(rows), "data": rows}
    except Exception:
        return {"ok": True, "count": 0, "data": []}
    finally:
        conn.close()


@router.get("/results", include_in_schema=False)
async def get_results(
    formula: str = Query(None, description="按公式过滤: f1/f3/f5"),
    hits_only: bool = Query(False, description="只返回命中的"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """兼容查询：选股结果列表。"""
    conn = get_conn()
    try:
        rows, total = list_screening_results(
            conn,
            formula=formula,
            hits_only=hits_only,
            limit=limit,
            offset=offset,
        )

        return {
            "ok": True,
            "total": total,
            "count": len(rows),
            "data": rows,
        }
    finally:
        conn.close()


@router.get("/detail/{stock_code}", include_in_schema=False)
async def get_detail(stock_code: str):
    """兼容查询：单股选股详细分解。"""
    conn = get_conn()
    try:
        row = get_screening_detail(conn, stock_code)
        if not row:
            return {"ok": False, "message": "无数据"}
        return {"ok": True, "data": row}
    finally:
        conn.close()


@router.get("/industry-overview")
async def get_industry_overview(topn: int = Query(3, ge=1, le=10)):
    """行业研究背景视图。

    面向前端行业页，统一输出：
    - 行业动量 / 相对强弱
    - 当前机构活跃度
    - 最近新进入与买入信号
    - 候选股票质量 / 阶段 / 综合分分布
    - 每个行业前排候选股票
    """
    conn = get_conn()
    try:
        return get_industry_overview_payload(conn, topn=topn)
    finally:
        conn.close()


@router.get("/summary", include_in_schema=False)
async def get_summary():
    """兼容查询：命中统计汇总。"""
    conn = get_conn()
    try:
        return {"ok": True, **get_screening_summary(conn)}
    finally:
        conn.close()
