"""市场感知 (Market Perception) router."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-market-perception")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _mart_row_count(conn) -> int:
    if not _table_exists(conn, "mart_market_perception_daily"):
        return 0
    row = conn.execute("SELECT COUNT(*) AS n FROM mart_market_perception_daily").fetchone()
    return int(row[0] if row else 0)


def _serialize_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "regime_score": float(row[1]) if row[1] is not None else None,
        "breadth_state": row[2],
        "volatility_state": row[3],
        "sentiment_phase": row[4],
        "hs300_ret_60d": float(row[5]) if row[5] is not None else None,
        "hs300_vol_20d": float(row[6]) if row[6] is not None else None,
        "breadth_ratio": float(row[7]) if row[7] is not None else None,
        "breadth_p75_90d": float(row[8]) if row[8] is not None else None,
        "limit_up_count": int(row[9]) if row[9] is not None else None,
        "lhb_event_count": int(row[10]) if row[10] is not None else None,
        "n_obs_days": int(row[11]) if row[11] is not None else None,
        "source_engines": row[12],
        "pit_cutoff_date": str(row[13]) if row[13] is not None else None,
        "built_at": str(row[14]) if row[14] is not None else None,
    }


@router.get("/snapshot")
async def get_snapshot():
    """Return the latest market perception snapshot."""
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_daily"):
                return {"ok": True, "stub": True, "data": None, "built_at": datetime.now(timezone.utc).isoformat()}
            row = conn.execute(
                """
                SELECT snapshot_date, regime_score, breadth_state,
                       volatility_state, sentiment_phase, hs300_ret_60d,
                       hs300_vol_20d, breadth_ratio, breadth_p75_90d,
                       limit_up_count, lhb_event_count, n_obs_days,
                       source_engines, pit_cutoff_date, built_at
                  FROM mart_market_perception_daily
                 ORDER BY snapshot_date DESC LIMIT 1
                """,
            ).fetchone()
            if not row:
                return {"ok": True, "stub": True, "data": None, "built_at": datetime.now(timezone.utc).isoformat()}
            data = _serialize_row(row)
            return {"ok": True, "stub": False, "data": data, "built_at": data["built_at"]}
    except Exception as exc:
        logger.warning("market_perception snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": None, "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/history")
async def get_history(days: int = 90):
    """Return last N trading-day market perception rows."""
    days = max(1, min(int(days), 500))
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_daily"):
                return {"ok": True, "stub": True, "data": [], "days_requested": days}
            rows = conn.execute(
                """
                WITH recent_days AS (
                    SELECT trade_date
                      FROM dim_trading_calendar
                     WHERE is_trading = 1
                       AND CAST(trade_date AS DATE) <= (
                           SELECT MAX(snapshot_date) FROM mart_market_perception_daily
                       )
                     ORDER BY CAST(trade_date AS DATE) DESC
                     LIMIT ?
                )
                SELECT m.snapshot_date, m.regime_score, m.breadth_state,
                       m.volatility_state, m.sentiment_phase, m.hs300_ret_60d,
                       m.hs300_vol_20d, m.breadth_ratio, m.breadth_p75_90d,
                       m.limit_up_count, m.lhb_event_count, m.n_obs_days,
                       m.source_engines, m.pit_cutoff_date, m.built_at
                  FROM mart_market_perception_daily m
                  JOIN recent_days d
                    ON CAST(d.trade_date AS DATE) = m.snapshot_date
                 ORDER BY m.snapshot_date
                """,
                (days,),
            ).fetchall()
            return {
                "ok": True,
                "stub": False,
                "data": [_serialize_row(row) for row in rows],
                "days_requested": days,
                "rows": len(rows),
            }
    except Exception as exc:
        logger.warning("market_perception history query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "days_requested": days}


@router.get("/health")
async def get_health():
    """模块健康检查 — 列出哪些 engine 已实施."""
    engines = {
        "MarketRegimeEngine": "stub",
        "ThemeLifecycleEngine": "spec_only",
        "ChainDiffusionEngine": "spec_only",
        "FundFlowEngine": "spec_only",
        "LeaderFollowerEngine": "spec_only",
        "CrowdingRiskEngine": "spec_only",
        "StockContextEngine": "spec_only",
    }
    with get_conn() as conn:
        row_count = _mart_row_count(conn)
        has_mart = row_count > 0
    if has_mart:
        engines["MarketRegimeEngine"] = "live"
    return {
        "ok": True,
        "engines": engines,
        "mart_table_exists": has_mart,
        "mart_rows": row_count,
        "handoff_doc": "docs/market_perception_codex_handoff.md",
    }
