"""Trading calendar 统一 API — A 股 15:00 收盘.

Codex review 2026-05-19 a7ffbdb2 HIGH: calendar gate 双源 (utils.py + market_db.py)
抽 services/calendar.py 统一 close policy.

抽象层:
- `latest_completed_trade_date(conn, ...)`: 通用 PIT cutoff (default close_hour=16 conservative)
- `latest_completed_for_kline_write()`: K-line write-side lint (close_hour=15 close_minute=5)
- `latest_closed_or_raise(...)`: fail-closed wrapper (raise on calendar miss)

backward compat: services/utils.py 保留 import shim (避免大范围 caller refactor).

rule-compliance: ok evidence=Codex-a7ffbdb2-HIGH-calendar-gate-unified
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


_MARKET_TZ = ZoneInfo("Asia/Shanghai")

# Default close policy thresholds
DEFAULT_CLOSE_HOUR = 16          # conservative buffer (15:00 close + 1h)
DEFAULT_CLOSE_MINUTE = 0

# K-line write-side policy (Codex review 2026-05-19 + 用户 push back 15:11 抓不到 5/19)
KLINE_WRITE_CLOSE_HOUR = 15      # A 股 15:00 close
KLINE_WRITE_CLOSE_MINUTE = 5     # +5 min tdxhub settlement publish buffer


class CalendarMissError(RuntimeError):
    """dim_trading_calendar 不可访问或为空."""


def latest_completed_trade_date(
    conn,
    now: Optional[datetime] = None,
    close_hour: int = DEFAULT_CLOSE_HOUR,
    close_minute: int = DEFAULT_CLOSE_MINUTE,
) -> Optional[str]:
    """返回最近一个已完成收盘的交易日（北京时间口径）.

    default close_hour=16 保守 (A 股 15:00 close + 1h buffer). K-line write site 用
    `latest_completed_for_kline_write()` 显式 15:05 阈值.

    Returns: 'YYYY-MM-DD' string, or None if calendar query fails.
    """
    if now is None:
        now_local = datetime.now(_MARKET_TZ)
    elif now.tzinfo is None:
        now_local = now.replace(tzinfo=_MARKET_TZ)
    else:
        now_local = now.astimezone(_MARKET_TZ)

    anchor_date = now_local.date()
    # rule-compliance: ok evidence=A-share-close-policy
    if (now_local.hour, now_local.minute) < (close_hour, close_minute):
        anchor_date -= timedelta(days=1)

    row = conn.execute(
        "SELECT MAX(trade_date) AS d FROM dim_trading_calendar "
        "WHERE is_trading=1 AND trade_date <= ?",
        (anchor_date.strftime("%Y-%m-%d"),)
    ).fetchone()
    if not row:
        return None
    if hasattr(row, "keys") and "d" in row.keys():
        return row["d"]
    return row[0]


def latest_completed_for_kline_write(
    now: Optional[datetime] = None,
    *,
    raise_on_miss: bool = True,
) -> Optional[str]:
    """K-line write-side calendar lint — 15:05 buffer.

    A 股 15:00 close + 5min tdxhub settlement publish buffer. 让 daily_update 在
    15:05 之后能跑 sync 抓当日 K-line.

    Codex review 2026-05-19 HIGH 1: fail-closed. calendar 不可访问 raise
    CalendarMissError. emergency bypass: env KLINE_WRITE_LINT_BYPASS=1.

    rule-compliance: ok evidence=A-share-close-15:00-plus-5min-tdxhub-buffer
    """
    import os
    if os.environ.get("KLINE_WRITE_LINT_BYPASS") == "1":
        import logging
        logging.getLogger(__name__).warning(
            "kline write lint: BYPASS via KLINE_WRITE_LINT_BYPASS=1 (audit this bypass!)"
        )
        return None
    try:
        from services.db import get_conn as _get_smart_conn
        smart_conn = _get_smart_conn()
        try:
            return latest_completed_trade_date(
                smart_conn, now=now,
                close_hour=KLINE_WRITE_CLOSE_HOUR,
                close_minute=KLINE_WRITE_CLOSE_MINUTE,
            )
        finally:
            smart_conn.close()
    except Exception as e:
        if raise_on_miss:
            raise CalendarMissError(
                f"latest_completed_trade_date lookup failed: {e}. "
                "fail-closed. Set KLINE_WRITE_LINT_BYPASS=1 to bypass (audit any uses)."
            ) from e
        return None


def latest_closed_or_raise(
    now: Optional[datetime] = None,
    close_hour: int = DEFAULT_CLOSE_HOUR,
    close_minute: int = DEFAULT_CLOSE_MINUTE,
) -> str:
    """便利 wrapper — caller 不传 conn, 内部自取 + raise on miss.

    适合 deep-call sites (return_engine / scoring / screening 等), 一行替换原 wall-clock.
    """
    from services.db import get_conn

    conn = get_conn()
    try:
        d = latest_completed_trade_date(conn, now=now, close_hour=close_hour, close_minute=close_minute)
    finally:
        conn.close()
    if not d:
        raise CalendarMissError(
            "dim_trading_calendar 未 seed 或表损坏; 拒绝 fallback to wall-clock now."
        )
    return d


__all__ = [
    "CalendarMissError",
    "DEFAULT_CLOSE_HOUR",
    "DEFAULT_CLOSE_MINUTE",
    "KLINE_WRITE_CLOSE_HOUR",
    "KLINE_WRITE_CLOSE_MINUTE",
    "latest_completed_trade_date",
    "latest_completed_for_kline_write",
    "latest_closed_or_raise",
]
