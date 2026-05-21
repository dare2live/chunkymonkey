"""股票图谱 router (Project D MVP).

2026-05-22 用户新加: 给主项目股票列表 UI 加 multi-tag + 关联弹窗.
基于 Perception 7 mart 输出, 仅 UI 查询层, 不接 ranker / panel / paper_sim.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from services.db import get_conn
from services.stock_graph_read import get_stock_graph, get_stock_related, get_stock_tags

logger = logging.getLogger("cm-api.stock-graph")
router = APIRouter()


@router.get("/stock_graph/{stock_code}")
async def get_stock_graph_endpoint(stock_code: str, snapshot_date: str | None = None):
    """Return unified stock graph (tags + related) for a given stock_code."""
    try:
        with get_conn() as conn:
            return get_stock_graph(conn, stock_code, snapshot_date)
    except Exception as exc:
        logger.warning("stock_graph query failed for %s: %s", stock_code, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stock_graph/{stock_code}/tags")
async def get_stock_tags_endpoint(stock_code: str, snapshot_date: str | None = None):
    """Return just the tags (lighter response for UI chip rendering)."""
    try:
        with get_conn() as conn:
            return get_stock_tags(conn, stock_code, snapshot_date)
    except Exception as exc:
        logger.warning("stock_graph tags query failed for %s: %s", stock_code, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stock_graph/{stock_code}/related")
async def get_stock_related_endpoint(
    stock_code: str,
    snapshot_date: str | None = None,
    limit: int = 20,
):
    """Return related stocks (same industry + leader/follower edges)."""
    try:
        with get_conn() as conn:
            return get_stock_related(conn, stock_code, snapshot_date, limit)
    except Exception as exc:
        logger.warning("stock_graph related query failed for %s: %s", stock_code, exc)
        raise HTTPException(status_code=500, detail=str(exc))
