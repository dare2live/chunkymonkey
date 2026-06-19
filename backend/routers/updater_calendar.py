"""Trading calendar preflight helpers for the updater router."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from services.utils import latest_completed_trade_date


logger = logging.getLogger("cm-api")

CALENDAR_MIN_ROWS = 700
CALENDAR_FUTURE_COVER_DAYS = 30
CALENDAR_DATA_FETCH_STEPS = {
    "sync_raw",
    "sync_market_data",
    "sync_financial",
    "sync_industry",
    "sync_surveys",
    "sync_qfii",
    # sync_margin removed (Phase ψ.5 dead-data cleanup): 写了没人读, UI 一处 + audit 一处而已
    "sync_lhb",
    "sync_aif10_valuation_quantile",
    "sync_aif10_peer_valuation",
    "sync_aif10_forecast_consensus",
}


def _ensure_calendar_step_for_data_fetch(steps: list[str]) -> list[str]:
    """Insert calendar preflight before any source fetch step."""

    if "sync_calendar" in steps:
        return steps
    if not any(step in CALENDAR_DATA_FETCH_STEPS for step in steps):
        return steps
    return ["sync_calendar", *steps]


def _ensure_trading_calendar_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_trading INTEGER DEFAULT 1
        )
        """
    )


def _trading_calendar_status(conn, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    try:
        _ensure_trading_calendar_table(conn)
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt, MIN(trade_date) AS min_date, MAX(trade_date) AS max_date
              FROM dim_trading_calendar
             WHERE is_trading = 1
            """
        ).fetchone()
    except Exception as exc:
        return {
            "exists": False,
            "count": 0,
            "min_date": None,
            "max_date": None,
            "latest_completed_trade_date": None,
            "needs_refresh": True,
            "reason": f"calendar_query_failed: {exc}",
        }

    count = int((row["cnt"] if hasattr(row, "keys") else row[0]) or 0) if row else 0
    min_date = (row["min_date"] if hasattr(row, "keys") else row[1]) if row else None
    max_date = (row["max_date"] if hasattr(row, "keys") else row[2]) if row else None
    cover_target = (now.date() + timedelta(days=CALENDAR_FUTURE_COVER_DAYS)).strftime("%Y-%m-%d")
    latest_trade = latest_completed_trade_date(conn, now=now) if count else None
    reasons = []
    if count < CALENDAR_MIN_ROWS:
        reasons.append(f"rows<{CALENDAR_MIN_ROWS}")
    if not max_date or str(max_date) < cover_target:
        reasons.append(f"max_date<{cover_target}")
    if not latest_trade:
        reasons.append("no_completed_trade_date")
    return {
        "exists": True,
        "count": count,
        "min_date": min_date,
        "max_date": max_date,
        "latest_completed_trade_date": latest_trade,
        "needs_refresh": bool(reasons),
        "reason": ",".join(reasons) if reasons else "fresh",
    }


async def _refresh_trading_calendar(conn) -> int:
    from services.akshare_client import fetch_trading_calendar

    days = await fetch_trading_calendar()
    unique_days = sorted({str(day)[:10] for day in days if day})
    if len(unique_days) < CALENDAR_MIN_ROWS:
        raise RuntimeError(f"交易日历刷新结果过少: {len(unique_days)}")

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO dim_trading_calendar(trade_date, is_trading)
            VALUES (?, 1)
            """,
            [(day,) for day in unique_days],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(unique_days)


async def _step_sync_calendar(conn) -> dict:
    """全局数据获取前置步骤：先确认交易日历可用且覆盖未来窗口。"""

    before = _trading_calendar_status(conn)
    refreshed = 0
    if before["needs_refresh"]:
        logger.info(f"[交易日历] 需要刷新: {before['reason']}")
        refreshed = await _refresh_trading_calendar(conn)
    after = _trading_calendar_status(conn)
    if after["needs_refresh"] or not after["latest_completed_trade_date"]:
        return {
            "count": refreshed,
            "status": "failed",
            "error": f"交易日历不可用: {after['reason']}",
            "calendar": after,
        }

    logger.info(
        "[交易日历] ready: latest=%s range=%s~%s rows=%d refreshed=%d",
        after["latest_completed_trade_date"],
        after["min_date"],
        after["max_date"],
        after["count"],
        refreshed,
    )
    return {
        "count": refreshed,
        "status": "completed",
        "latest_trade_date": after["latest_completed_trade_date"],
        "calendar": after,
        "message": (
            f"latest={after['latest_completed_trade_date']} "
            f"range={after['min_date']}~{after['max_date']} refreshed={refreshed}"
        ),
    }
