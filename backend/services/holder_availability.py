"""PIT availability helpers for top holder period facts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any


def normalize_yyyymmdd(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return None


def regulatory_notice_date_for_report_date(report_date: Any) -> str | None:
    """Return conservative statutory disclosure deadline for a report period."""

    yyyymmdd = normalize_yyyymmdd(report_date)
    if not yyyymmdd:
        return None
    year = int(yyyymmdd[:4])
    mmdd = yyyymmdd[4:8]
    if mmdd == "1231":
        return f"{year + 1}0430"
    if mmdd == "0331":
        return f"{year}0430"
    if mmdd == "0630":
        return f"{year}0831"
    if mmdd == "0930":
        return f"{year}1031"
    dt = datetime.strptime(yyyymmdd, "%Y%m%d").date() + timedelta(days=90)
    return dt.strftime("%Y%m%d")


def _plus_days(yyyymmdd: str, days: int) -> str:
    dt = datetime.strptime(yyyymmdd, "%Y%m%d").date() + timedelta(days=days)
    return dt.strftime("%Y%m%d")


def _day_gap(start_yyyymmdd: str, end_yyyymmdd: str) -> int:
    start = datetime.strptime(start_yyyymmdd, "%Y%m%d").date()
    end = datetime.strptime(end_yyyymmdd, "%Y%m%d").date()
    return (end - start).days


def next_trading_day_after(conn, yyyymmdd: str) -> str | None:
    """Return the next trading day strictly after yyyymmdd, falling back to calendar day."""

    target = normalize_yyyymmdd(yyyymmdd)
    if not target:
        return None
    if conn is None:
        return _plus_days(target, 1)
    try:
        bounds = conn.execute(
            """
            SELECT MIN(REPLACE(CAST(trade_date AS VARCHAR), '-', '')) AS min_date,
                   MAX(REPLACE(CAST(trade_date AS VARCHAR), '-', '')) AS max_date
            FROM dim_trading_calendar
            WHERE is_trading = 1
            """
        ).fetchone()
        min_date = bounds["min_date"] if hasattr(bounds, "keys") else bounds[0]
        max_date = bounds["max_date"] if hasattr(bounds, "keys") else bounds[1]
        if not min_date or not max_date or target >= max_date:
            return _plus_days(target, 1)
        if target < min_date and _day_gap(target, min_date) > 10:
            return _plus_days(target, 1)
        row = conn.execute(
            """
            SELECT REPLACE(CAST(trade_date AS VARCHAR), '-', '') AS trade_date
            FROM dim_trading_calendar
            WHERE is_trading = 1
              AND REPLACE(CAST(trade_date AS VARCHAR), '-', '') > ?
            ORDER BY REPLACE(CAST(trade_date AS VARCHAR), '-', '')
            LIMIT 1
            """,
            (target,),
        ).fetchone()
    except Exception:
        return _plus_days(target, 1)
    if not row:
        return _plus_days(target, 1)
    return row["trade_date"] if hasattr(row, "keys") else row[0]


def derive_holder_availability_dates(
    conn,
    *,
    report_date: Any,
    notice_date: Any = None,
    effective_date: Any = None,
) -> tuple[str | None, str | None, str | None]:
    """Return notice, effective, and source for holder period availability."""

    normalized_notice = normalize_yyyymmdd(notice_date)
    source = "source_notice" if normalized_notice else None
    if normalized_notice is None:
        normalized_notice = regulatory_notice_date_for_report_date(report_date)
        source = "regulatory_deadline" if normalized_notice else None
    normalized_effective = normalize_yyyymmdd(effective_date)
    if normalized_notice and normalized_effective is None:
        normalized_effective = next_trading_day_after(conn, normalized_notice)
    return normalized_notice, normalized_effective, source


def enrich_holder_rows_with_availability(conn, rows: Iterable[dict]) -> list[dict]:
    enriched = []
    for row in rows:
        item = dict(row)
        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date=item.get("report_date"),
            notice_date=item.get("notice_date"),
            effective_date=item.get("effective_date"),
        )
        item["notice_date"] = notice
        item["effective_date"] = effective
        item["availability_source"] = source
        enriched.append(item)
    return enriched


def backfill_holder_period_availability(conn) -> dict:
    """Backfill missing PIT availability dates on fact_top10_holder_period."""

    return backfill_holder_period_availability_rows(conn, overwrite_regulatory=False)


def backfill_holder_period_availability_rows(conn, *, overwrite_regulatory: bool = False) -> dict:
    where = """
        report_date IS NOT NULL
        AND (
            notice_date IS NULL OR notice_date = ''
            OR effective_date IS NULL OR effective_date = ''
        )
    """
    if overwrite_regulatory:
        where = """
            report_date IS NOT NULL
            AND (
                notice_date IS NULL OR notice_date = ''
                OR effective_date IS NULL OR effective_date = ''
                OR availability_source = 'regulatory_deadline'
            )
        """
    rows = conn.execute(
        f"""
        SELECT DISTINCT report_date
        FROM fact_top10_holder_period
        WHERE {where}
        ORDER BY report_date
        """
    ).fetchall()
    updates = 0
    for row in rows:
        report_date = row["report_date"] if hasattr(row, "keys") else row[0]
        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date=report_date,
        )
        if not notice:
            continue
        conn.execute(
            f"""
            UPDATE fact_top10_holder_period
            SET notice_date = {('?' if overwrite_regulatory else "COALESCE(NULLIF(notice_date, ''), ?)")},
                effective_date = {('?' if overwrite_regulatory else "COALESCE(NULLIF(effective_date, ''), ?)")}
            WHERE report_date = ?
              AND (
                  notice_date IS NULL OR notice_date = ''
                  OR effective_date IS NULL OR effective_date = ''
                  { "OR availability_source = 'regulatory_deadline'" if overwrite_regulatory else "" }
              )
            """,
            (notice, effective, report_date),
        )
        updates += 1
        _backfill_availability_source(conn, report_date=report_date, source=source)
    remaining = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM fact_top10_holder_period
        WHERE notice_date IS NULL OR notice_date = ''
           OR effective_date IS NULL OR effective_date = ''
        """
    ).fetchone()
    return {
        "updated_report_dates": updates,
        "remaining_missing_rows": int((remaining["n"] if hasattr(remaining, "keys") else remaining[0]) or 0),
    }


def _backfill_availability_source(conn, *, report_date: str, source: str | None) -> None:
    if not source:
        return
    try:
        conn.execute(
            """
            UPDATE fact_top10_holder_period
            SET availability_source = COALESCE(NULLIF(availability_source, ''), ?)
            WHERE report_date = ?
            """,
            (source, report_date),
        )
    except Exception:
        return
