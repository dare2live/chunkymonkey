"""市场感知 (Market Perception) router.

2026-05-21: 拆 god-module 807→~500 LOC, serialize 11 个函数 + 6 SELECT 常量已抽到
`services.market_perception.router_serialize`. Endpoint 行为不变.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from services.db import get_conn
from services.market_perception.regime_engine import get_regime_config
from services.market_perception.router_serialize import (
    EMOTION_SELECT,
    LEADER_FOLLOWER_SELECT,
    STOCK_CONTEXT_SELECT,
    STYLE_SELECT,
    THEME_SELECT,
    UNDER_REACTION_SELECT,
    _clean_text,
    _finite_float,
    _serialize_emotion_row,
    _serialize_leader_follower_row,
    _serialize_row,
    _serialize_stock_context_row,
    _serialize_style_row,
    _serialize_theme_row,
    _serialize_under_reaction_row,
)
from services.utils import latest_completed_trade_date

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


def _table_row_count(conn, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row[0] if row else 0)


def _market_perception_health(conn) -> dict:
    if not _table_exists(conn, "mart_market_perception_daily"):
        return {
            "mart_table_exists": False,
            "mart_rows": 0,
            "latest_snapshot_date": None,
            "latest_built_at": None,
            "latest_snapshot_lag_trading_days": None,
            "score_guard_status": "unknown",
            "score_guard_violations": None,
            "latest_audit": None,
            "emotion_rows": 0,
            "theme_rows": 0,
            "under_reaction_rows": 0,
            "leader_follower_rows": 0,
            "style_rows": 0,
            "stock_context_rows": 0,
        }

    row = conn.execute(
        """
        SELECT COUNT(*) AS n,
               CAST(MAX(snapshot_date) AS VARCHAR) AS latest_snapshot_date,
               CAST(MAX(built_at) AS VARCHAR) AS latest_built_at
          FROM mart_market_perception_daily
        """,
    ).fetchone()
    mart_rows = int(row[0] if row else 0)
    latest_snapshot = row[1] if row and row[1] is not None else None
    latest_built_at = row[2] if row and row[2] is not None else None
    guard_abs_max = get_regime_config().regime_score_abs_max
    guard_row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM mart_market_perception_daily
         WHERE regime_score IS NOT NULL
           AND ABS(regime_score) > ?
        """,
        [guard_abs_max],
    ).fetchone()
    guard_violations = int(guard_row[0] if guard_row else 0)
    latest_lag = _latest_snapshot_lag_trading_days(conn, latest_snapshot)
    latest_audit = _latest_market_perception_audit(conn)
    latest_snapshot_audit_status = _latest_snapshot_audit_status(latest_snapshot, latest_audit)
    emotion_rows = _table_row_count(conn, "mart_market_perception_emotion_daily")
    theme_rows = _table_row_count(conn, "mart_market_perception_theme_daily")
    under_reaction_rows = _table_row_count(conn, "mart_market_perception_under_reaction_daily")
    leader_follower_rows = _table_row_count(conn, "mart_market_perception_leader_follower_daily")
    style_rows = _table_row_count(conn, "mart_market_perception_style_daily")
    stock_context_rows = _table_row_count(conn, "mart_market_perception_stock_context_daily")
    return {
        "mart_table_exists": True,
        "mart_rows": mart_rows,
        "latest_snapshot_date": latest_snapshot,
        "latest_built_at": latest_built_at,
        "latest_snapshot_lag_trading_days": latest_lag,
        "latest_snapshot_audit_status": latest_snapshot_audit_status,
        "score_guard_status": "ok" if guard_violations == 0 else "alert",
        "score_guard_violations": guard_violations,
        "score_guard_abs_max": guard_abs_max,
        "latest_audit": latest_audit,
        "emotion_rows": emotion_rows,
        "theme_rows": theme_rows,
        "under_reaction_rows": under_reaction_rows,
        "leader_follower_rows": leader_follower_rows,
        "style_rows": style_rows,
        "stock_context_rows": stock_context_rows,
    }


def _latest_snapshot_lag_trading_days(conn, latest_snapshot: str | None) -> int | None:
    if not latest_snapshot:
        return None
    latest_expected = conn.execute(
        """
        SELECT CAST(MAX(trade_date) AS VARCHAR) AS trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) <= ?
        """,
        [latest_completed_trade_date(conn)],
    ).fetchone()
    if not latest_expected or latest_expected[0] is None:
        return None
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) > ?
           AND CAST(trade_date AS DATE) <= ?
        """,
        [latest_snapshot, latest_expected[0]],
    ).fetchone()
    return int(row[0] if row else 0)


def _latest_market_perception_audit(conn) -> dict | None:
    if not _table_exists(conn, "mart_market_perception_audit_log"):
        return None
    row = conn.execute(
        """
        SELECT run_id, CAST(started_at AS VARCHAR) AS started_at,
               CAST(ended_at AS VARCHAR) AS ended_at, status,
               CAST(start_date AS VARCHAR) AS start_date,
               CAST(end_date AS VARCHAR) AS end_date,
               trading_days_requested, rows_written, missing_days,
               score_min, score_max, guard_status, input_row_counts_json, notes,
               CAST(built_at AS VARCHAR) AS built_at
          FROM mart_market_perception_audit_log
         ORDER BY started_at DESC
         LIMIT 1
        """,
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row[0],
        "started_at": row[1],
        "ended_at": row[2],
        "status": row[3],
        "start_date": row[4],
        "end_date": row[5],
        "trading_days_requested": int(row[6]) if row[6] is not None else None,
        "rows_written": int(row[7]) if row[7] is not None else None,
        "missing_days": int(row[8]) if row[8] is not None else None,
        "score_min": float(row[9]) if row[9] is not None else None,
        "score_max": float(row[10]) if row[10] is not None else None,
        "guard_status": row[11],
        "input_row_counts_json": row[12],
        "notes": row[13],
        "built_at": row[14],
    }


def _latest_snapshot_audit_status(latest_snapshot: str | None, latest_audit: dict | None) -> str:
    if not latest_snapshot:
        return "no_snapshot"
    if not latest_audit:
        return "no_audit"
    if latest_audit.get("status") != "success":
        return "latest_audit_not_success"
    audit_end = latest_audit.get("end_date")
    if audit_end is None:
        return "audit_end_missing"
    if str(audit_end) < str(latest_snapshot):
        return "snapshot_newer_than_latest_success_audit"
    return "ok"


# serialize 函数 + SELECT 常量 + _finite_float / _clean_text 已抽到
# `services.market_perception.router_serialize` (2026-05-21 拆分).
# 此 router 仅保留 endpoints, 通过上方 import 引入.


# 全部 serialize 函数 + SELECT 常量 已搬到 services.market_perception.router_serialize


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


@router.get("/emotion/snapshot")
async def get_emotion_snapshot():
    """Return the latest short-term market emotion snapshot."""
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_emotion_daily"):
                return {"ok": True, "stub": True, "data": None, "built_at": datetime.now(timezone.utc).isoformat()}
            row = conn.execute(
                f"""
                SELECT {EMOTION_SELECT}
                  FROM mart_market_perception_emotion_daily
                 ORDER BY snapshot_date DESC LIMIT 1
                """,
            ).fetchone()
            if not row:
                return {"ok": True, "stub": True, "data": None, "built_at": datetime.now(timezone.utc).isoformat()}
            data = _serialize_emotion_row(row)
            return {"ok": True, "stub": False, "data": data, "built_at": data["built_at"]}
    except Exception as exc:
        logger.warning("market_perception emotion snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": None, "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/emotion/history")
async def get_emotion_history(days: int = 90):
    """Return last N trading-day short-term market emotion rows."""
    days = max(1, min(int(days), 500))
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_emotion_daily"):
                return {"ok": True, "stub": True, "data": [], "days_requested": days}
            rows = conn.execute(
                f"""
                WITH recent_days AS (
                    SELECT trade_date
                      FROM dim_trading_calendar
                     WHERE is_trading = 1
                       AND CAST(trade_date AS DATE) <= (
                           SELECT MAX(snapshot_date) FROM mart_market_perception_emotion_daily
                       )
                     ORDER BY CAST(trade_date AS DATE) DESC
                     LIMIT ?
                )
                SELECT {EMOTION_SELECT}
                  FROM mart_market_perception_emotion_daily m
                  JOIN recent_days d
                    ON CAST(d.trade_date AS DATE) = m.snapshot_date
                 ORDER BY m.snapshot_date
                """,
                (days,),
            ).fetchall()
            return {
                "ok": True,
                "stub": False,
                "data": [_serialize_emotion_row(row) for row in rows],
                "days_requested": days,
                "rows": len(rows),
            }
    except Exception as exc:
        logger.warning("market_perception emotion history query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "days_requested": days}


@router.get("/theme/snapshot")
async def get_theme_snapshot():
    """Return latest PIT theme lifecycle rows."""
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_theme_daily"):
                return {"ok": True, "stub": True, "data": [], "built_at": datetime.now(timezone.utc).isoformat()}
            latest = conn.execute(
                "SELECT MAX(snapshot_date) FROM mart_market_perception_theme_daily",
            ).fetchone()
            if not latest or latest[0] is None:
                return {"ok": True, "stub": True, "data": [], "built_at": datetime.now(timezone.utc).isoformat()}
            rows = conn.execute(
                f"""
                SELECT {THEME_SELECT}
                  FROM mart_market_perception_theme_daily
                 WHERE snapshot_date = ?
                 ORDER BY mainline_rank, theme_name
                """,
                [latest[0]],
            ).fetchall()
            data = [_serialize_theme_row(row) for row in rows]
            built_at = data[0]["built_at"] if data else datetime.now(timezone.utc).isoformat()
            return {"ok": True, "stub": False, "data": data, "rows": len(data), "built_at": built_at}
    except Exception as exc:
        logger.warning("market_perception theme snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/theme/history")
async def get_theme_history(days: int = 20, top_n: int = 5):
    """Return top-N theme lifecycle rows over the last N trading days."""
    days = max(1, min(int(days), 120))
    top_n = max(1, min(int(top_n), 20))
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_theme_daily"):
                return {"ok": True, "stub": True, "data": [], "days_requested": days, "top_n": top_n}
            rows = conn.execute(
                f"""
                WITH recent_days AS (
                    SELECT trade_date
                      FROM dim_trading_calendar
                     WHERE is_trading = 1
                       AND CAST(trade_date AS DATE) <= (
                           SELECT MAX(snapshot_date) FROM mart_market_perception_theme_daily
                       )
                     ORDER BY CAST(trade_date AS DATE) DESC
                     LIMIT ?
                )
                SELECT {THEME_SELECT}
                  FROM mart_market_perception_theme_daily m
                  JOIN recent_days d
                    ON CAST(d.trade_date AS DATE) = m.snapshot_date
                 WHERE m.mainline_rank <= ?
                 ORDER BY m.snapshot_date, m.mainline_rank, m.theme_name
                """,
                (days, top_n),
            ).fetchall()
            return {
                "ok": True,
                "stub": False,
                "data": [_serialize_theme_row(row) for row in rows],
                "days_requested": days,
                "top_n": top_n,
                "rows": len(rows),
            }
    except Exception as exc:
        logger.warning("market_perception theme history query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "days_requested": days, "top_n": top_n}


@router.get("/under_reaction/snapshot")
async def get_under_reaction_snapshot(limit: int = 50):
    """Return latest fund-anomaly but price-under-reacted candidates."""
    limit = max(1, min(int(limit), 200))
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_under_reaction_daily"):
                return {"ok": True, "stub": True, "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}
            latest = conn.execute(
                "SELECT MAX(snapshot_date) FROM mart_market_perception_under_reaction_daily",
            ).fetchone()
            if not latest or latest[0] is None:
                return {"ok": True, "stub": True, "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}
            rows = conn.execute(
                f"""
                SELECT {UNDER_REACTION_SELECT}
                  FROM mart_market_perception_under_reaction_daily
                 WHERE snapshot_date = ?
                 ORDER BY under_reaction_score DESC
                 LIMIT ?
                """,
                [latest[0], limit],
            ).fetchall()
            data = [_serialize_under_reaction_row(row) for row in rows]
            built_at = data[0]["built_at"] if data else datetime.now(timezone.utc).isoformat()
            return {"ok": True, "stub": False, "data": data, "rows": len(data), "limit": limit, "built_at": built_at}
    except Exception as exc:
        logger.warning("market_perception under_reaction snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/leader_follower/snapshot")
async def get_leader_follower_snapshot(limit: int = 50):
    """Return latest PIT leader/follower diffusion edges."""
    limit = max(1, min(int(limit), 200))
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_leader_follower_daily"):
                return {"ok": True, "stub": True, "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}
            latest = conn.execute(
                "SELECT MAX(snapshot_date) FROM mart_market_perception_leader_follower_daily",
            ).fetchone()
            if not latest or latest[0] is None:
                return {"ok": True, "stub": True, "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}
            rows = conn.execute(
                f"""
                SELECT {LEADER_FOLLOWER_SELECT}
                  FROM mart_market_perception_leader_follower_daily
                 WHERE snapshot_date = ?
                 ORDER BY diffusion_score DESC
                 LIMIT ?
                """,
                [latest[0], limit],
            ).fetchall()
            data = [_serialize_leader_follower_row(row) for row in rows]
            built_at = data[0]["built_at"] if data else datetime.now(timezone.utc).isoformat()
            return {"ok": True, "stub": False, "data": data, "rows": len(data), "limit": limit, "built_at": built_at}
    except Exception as exc:
        logger.warning("market_perception leader_follower snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/style/snapshot")
async def get_style_snapshot():
    """Return latest style rotation and crowding snapshot."""
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_style_daily"):
                return {"ok": True, "stub": True, "data": None, "built_at": datetime.now(timezone.utc).isoformat()}
            row = conn.execute(
                f"""
                SELECT {STYLE_SELECT}
                  FROM mart_market_perception_style_daily
                 ORDER BY snapshot_date DESC LIMIT 1
                """,
            ).fetchone()
            if not row:
                return {"ok": True, "stub": True, "data": None, "built_at": datetime.now(timezone.utc).isoformat()}
            data = _serialize_style_row(row)
            return {"ok": True, "stub": False, "data": data, "built_at": data["built_at"]}
    except Exception as exc:
        logger.warning("market_perception style snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": None, "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/stock_context/snapshot")
async def get_stock_context_snapshot(limit: int = 50):
    """Return latest stock-level market context rows."""
    limit = max(1, min(int(limit), 200))
    try:
        with get_conn() as conn:
            if not _table_exists(conn, "mart_market_perception_stock_context_daily"):
                return {"ok": True, "stub": True, "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}
            latest = conn.execute(
                "SELECT MAX(snapshot_date) FROM mart_market_perception_stock_context_daily",
            ).fetchone()
            if not latest or latest[0] is None:
                return {"ok": True, "stub": True, "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}
            rows = conn.execute(
                f"""
                SELECT {STOCK_CONTEXT_SELECT}
                  FROM mart_market_perception_stock_context_daily
                 WHERE snapshot_date = ?
                 ORDER BY context_score DESC
                 LIMIT ?
                """,
                [latest[0], limit],
            ).fetchall()
            data = [_serialize_stock_context_row(row) for row in rows]
            built_at = data[0]["built_at"] if data else datetime.now(timezone.utc).isoformat()
            return {"ok": True, "stub": False, "data": data, "rows": len(data), "limit": limit, "built_at": built_at}
    except Exception as exc:
        logger.warning("market_perception stock_context snapshot query failed: %s", exc)
        return {"ok": False, "error": str(exc), "data": [], "limit": limit, "built_at": datetime.now(timezone.utc).isoformat()}


@router.get("/health")
async def get_health():
    """模块健康检查 — 列出哪些 engine 已实施."""
    engines = {
        "MarketRegimeEngine": "stub",
        "MarketEmotionCycle": "spec_only",
        "ThemeLifecycleEngine": "spec_only",
        "ChainDiffusionEngine": "spec_only",
        "FundFlowEngine": "spec_only",
        "LeaderFollowerEngine": "spec_only",
        "CrowdingRiskEngine": "spec_only",
        "StyleRotationEngine": "spec_only",
        "StockContextEngine": "spec_only",
    }
    with get_conn() as conn:
        health = _market_perception_health(conn)
    if health["mart_rows"] > 0:
        engines["MarketRegimeEngine"] = "live"
    if health.get("emotion_rows", 0) > 0:
        engines["MarketEmotionCycle"] = "live"
    if health.get("theme_rows", 0) > 0:
        engines["ThemeLifecycleEngine"] = "live"
    if health.get("under_reaction_rows", 0) > 0:
        engines["FundFlowEngine"] = "live"
    if health.get("leader_follower_rows", 0) > 0:
        engines["LeaderFollowerEngine"] = "live"
        engines["ChainDiffusionEngine"] = "research_mvp"
    if health.get("style_rows", 0) > 0:
        engines["CrowdingRiskEngine"] = "research_mvp"
        engines["StyleRotationEngine"] = "research_mvp"
    if health.get("stock_context_rows", 0) > 0:
        engines["StockContextEngine"] = "research_mvp"
    return {
        "ok": True,
        "engines": engines,
        **health,
        "handoff_doc": "docs/market_perception_codex_handoff.md",
    }
