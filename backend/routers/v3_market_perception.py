"""市场感知 (Market Perception) router."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter

from services.db import get_conn
from services.market_perception.regime_engine import get_regime_config

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
    }


def _latest_snapshot_lag_trading_days(conn, latest_snapshot: str | None) -> int | None:
    if not latest_snapshot:
        return None
    latest_expected = conn.execute(
        """
        SELECT CAST(MAX(trade_date) AS VARCHAR) AS trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) < ?
        """,
        [date.today().isoformat()],
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


def _serialize_emotion_row(row) -> dict:
    return {
        "snapshot_date": str(row[0]) if row[0] is not None else None,
        "emotion_score": float(row[1]) if row[1] is not None else None,
        "emotion_state": row[2],
        "action_bias": row[3],
        "cycle_phase": row[4],
        "market_breadth": float(row[5]) if row[5] is not None else None,
        "up_count": int(row[6]) if row[6] is not None else None,
        "down_count": int(row[7]) if row[7] is not None else None,
        "limit_up_count": int(row[8]) if row[8] is not None else None,
        "limit_down_count": int(row[9]) if row[9] is not None else None,
        "first_board_count": int(row[10]) if row[10] is not None else None,
        "second_board_count": int(row[11]) if row[11] is not None else None,
        "third_plus_count": int(row[12]) if row[12] is not None else None,
        "promotion_rate_1_to_2": float(row[13]) if row[13] is not None else None,
        "promotion_rate_2_to_3": float(row[14]) if row[14] is not None else None,
        "open_board_rate": float(row[15]) if row[15] is not None else None,
        "next_day_premium": float(row[16]) if row[16] is not None else None,
        "turnover_concentration": float(row[17]) if row[17] is not None else None,
        "lhb_event_count": int(row[18]) if row[18] is not None else None,
        "n_stocks": int(row[19]) if row[19] is not None else None,
        "unknown_metrics": row[20],
        "source_engines": row[21],
        "pit_cutoff_date": str(row[22]) if row[22] is not None else None,
        "built_at": str(row[23]) if row[23] is not None else None,
    }


EMOTION_SELECT = """
    snapshot_date, emotion_score, emotion_state, action_bias, cycle_phase,
    market_breadth, up_count, down_count, limit_up_count, limit_down_count,
    first_board_count, second_board_count, third_plus_count,
    promotion_rate_1_to_2, promotion_rate_2_to_3, open_board_rate,
    next_day_premium, turnover_concentration, lhb_event_count, n_stocks,
    unknown_metrics, source_engines, pit_cutoff_date, built_at
"""


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
        "StockContextEngine": "spec_only",
    }
    with get_conn() as conn:
        health = _market_perception_health(conn)
    if health["mart_rows"] > 0:
        engines["MarketRegimeEngine"] = "live"
    if health.get("emotion_rows", 0) > 0:
        engines["MarketEmotionCycle"] = "live"
    return {
        "ok": True,
        "engines": engines,
        **health,
        "handoff_doc": "docs/market_perception_codex_handoff.md",
    }
