"""v3 BestChoice tab API endpoints — read-only challenger data for UI tab.

Mounts at prefix /api/v3/bestchoice. Read mart tables imported via
backend/scripts/import_bestchoice_phase{1,2,3}_*.py. Champion untouched.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from services.duck_adapter import connect
from services.db_connection import DB_PATH
from services.bestchoice_read import (
    DEFAULT_RUN_ID,
    get_complementarity,
    get_daily_picks,
    get_overview,
    get_top_candidates,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/bestchoice/overview")
async def overview(run_id: str = DEFAULT_RUN_ID):
    """High-level BC summary: counts + paper_sim KPI compare rows."""
    try:
        with connect(str(DB_PATH), read_only=True) as conn:
            return get_overview(conn, run_id)
    except Exception as exc:
        logger.warning("bestchoice/overview failed: %s", exc)
        return {"error": str(exc)[:200], "run_id": run_id}


@router.get("/bestchoice/candidates")
async def candidates(run_id: str = DEFAULT_RUN_ID, limit: int = Query(50, ge=1, le=500)):
    """Top BC candidates ranked by score."""
    try:
        with connect(str(DB_PATH), read_only=True) as conn:
            return {"candidates": get_top_candidates(conn, run_id, limit)}
    except Exception as exc:
        logger.warning("bestchoice/candidates failed: %s", exc)
        return {"error": str(exc)[:200]}


@router.get("/bestchoice/daily_picks")
async def daily_picks(
    run_id: str = DEFAULT_RUN_ID,
    signal_date: str | None = None,
    limit: int = Query(20, ge=1, le=200),
):
    """Daily candidate picks for a given signal_date (defaults to latest)."""
    try:
        with connect(str(DB_PATH), read_only=True) as conn:
            return get_daily_picks(conn, run_id, signal_date, limit)
    except Exception as exc:
        logger.warning("bestchoice/daily_picks failed: %s", exc)
        return {"error": str(exc)[:200]}


@router.get("/bestchoice/complementarity")
async def complementarity():
    """Phase 4 complementarity: BC vs baseline picks overlap."""
    try:
        with connect(str(DB_PATH), read_only=True) as conn:
            return get_complementarity(conn)
    except Exception as exc:
        logger.warning("bestchoice/complementarity failed: %s", exc)
        return {"error": str(exc)[:200]}
