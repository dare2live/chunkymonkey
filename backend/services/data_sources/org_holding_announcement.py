"""Org-holding PIT axis = the listed company's periodic-report announcement.

``RPT_MAIN_ORGHOLDDETAIL`` has only ``REPORT_DATE``. The statutory disclosure
deadline is a completeness clock, not known-at. Backtests join the same
company's first ``income.f_ann_date`` (else holders ``notice_date``).
NULL announcement → not decision-visible (same contract as holders).
First-seen land date is a live fallback only, never a historical known-at.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def compact_yyyymmdd(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def iso_date(value: Any) -> str | None:
    compact = compact_yyyymmdd(value)
    if compact is None:
        return None
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def normalize_stock_code(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:] if len(digits) > 6 else digits
    return text


def land_calendar_date(today: date | datetime | None = None) -> str:
    """A-share calendar day for first-seen stamps (Asia/Shanghai)."""
    if today is None:
        today = datetime.now(_SHANGHAI).date()  # rule-compliance: ok evidence=first-seen land calendar day (Shanghai), not trade_date
    elif isinstance(today, datetime):
        today = today.astimezone(_SHANGHAI).date()
    return today.isoformat()


def merge_announcement_maps(
    *,
    income_first: Mapping[str, str] | None = None,
    holders_first: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Income ``f_ann_date`` wins; holders ``notice_date`` fills gaps."""
    out: dict[str, str] = {}
    for raw_code, raw_day in (holders_first or {}).items():
        code = normalize_stock_code(raw_code)
        day = compact_yyyymmdd(raw_day)
        if code and day is not None:
            out[code] = day
    for raw_code, raw_day in (income_first or {}).items():
        code = normalize_stock_code(raw_code)
        day = compact_yyyymmdd(raw_day)
        if code and day is not None:
            out[code] = day
    return out


def resolve_available_iso(
    *,
    stock_code: str,
    report_date: str,
    announcement_by_stock: Mapping[str, str] | None,
    land_date: str,
    today: str | None = None,
) -> str:
    """Stamp one grain. Announcement if valid and already public; else land_date.

    Never returns the statutory disclosure deadline.
    """
    report = compact_yyyymmdd(report_date)
    land = compact_yyyymmdd(land_date)
    asof = compact_yyyymmdd(today) or land
    if report is None:
        raise ValueError("report_date required")
    if land is None:
        raise ValueError("land_date required")
    code = normalize_stock_code(stock_code)
    announced = None
    if announcement_by_stock:
        announced = compact_yyyymmdd(
            announcement_by_stock.get(code)
            or announcement_by_stock.get(stock_code)
        )
    chosen = land
    if (
        announced is not None
        and announced >= report
        and announced <= (asof or announced)
    ):
        chosen = announced
    elif land < report:
        chosen = report
    if asof is not None and chosen > asof:
        chosen = asof if asof >= report else report
    iso = iso_date(chosen)
    if iso is None:
        raise ValueError("available_date compact failed")
    return iso


def stamp_available_dates(
    rows: Sequence[Mapping[str, Any]],
    *,
    announcement_by_stock: Mapping[str, str] | None,
    land_date: str,
    today: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["available_date"] = resolve_available_iso(
            stock_code=str(item.get("stock_code") or ""),
            report_date=str(item.get("report_date") or ""),
            announcement_by_stock=announcement_by_stock,
            land_date=land_date,
            today=today,
        )
        out.append(item)
    return out


def is_decision_visible(
    *,
    asof: str,
    announcement: str | None,
    first_seen: str | None = None,
    allow_first_seen: bool = False,
) -> bool:
    """Backtest default: only a real announcement date is known-at.

    ``allow_first_seen`` is live-only (we observed the row today, no join yet).
    Historical first-seen is crawl time, not market-known time.
    """
    cutoff = compact_yyyymmdd(asof)
    if cutoff is None:
        return False
    announced = compact_yyyymmdd(announcement)
    if announced is not None:
        return announced <= cutoff
    if allow_first_seen:
        seen = compact_yyyymmdd(first_seen)
        return seen is not None and seen <= cutoff
    return False


def pit_visible_rows(
    rows: Sequence[Mapping[str, Any]],
    asof: str,
    *,
    allow_first_seen: bool = False,
) -> list[dict[str, Any]]:
    """Keep grains whose announcement (or live first-seen) is already public."""
    visible: list[dict[str, Any]] = []
    for row in rows:
        if is_decision_visible(
            asof=asof,
            announcement=row.get("announcement") or row.get("announcement_date"),
            first_seen=row.get("first_seen") or row.get("available_date"),
            allow_first_seen=allow_first_seen,
        ):
            visible.append(dict(row))
    return visible


def _query_first_dates(conn, sql: str, period_compact: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        rows = conn.execute(sql, [period_compact]).fetchall()
    except Exception:  # noqa: BLE001 — missing table/db is empty map, not a second truth
        return out
    for row in rows:
        code = normalize_stock_code(row[0])
        day = compact_yyyymmdd(row[1])
        if code and day is not None:
            out[code] = day
    return out


def load_period_announcement_map(
    report_date: str,
    *,
    income_conn=None,
    holders_conn=None,
) -> dict[str, str]:
    """First public day of this report period per stock. Empty if DBs unavailable."""
    period = compact_yyyymmdd(report_date)
    if period is None:
        return {}
    income_map: dict[str, str] = {}
    holders_map: dict[str, str] = {}
    close_income = False
    close_holders = False
    if income_conn is None:
        income_conn = _open_alias("tushare_raw")
        close_income = income_conn is not None
    if holders_conn is None:
        holders_conn = _open_alias("smartmoney")
        close_holders = holders_conn is not None
    try:
        if income_conn is not None:
            income_map = _query_first_dates(
                income_conn,
                """
                SELECT split_part(CAST(ts_code AS VARCHAR), '.', 1),
                       MIN(replace(CAST(f_ann_date AS VARCHAR), '-', ''))
                  FROM raw_tushare_income
                 WHERE replace(CAST(end_date AS VARCHAR), '-', '') = ?
                   AND f_ann_date IS NOT NULL
                   AND CAST(f_ann_date AS VARCHAR) NOT IN ('', 'None', 'none')
                 GROUP BY 1
                """,
                period,
            )
        if holders_conn is not None:
            holders_map = _query_first_dates(
                holders_conn,
                """
                SELECT stock_code,
                       MIN(replace(CAST(notice_date AS VARCHAR), '-', ''))
                  FROM canonical_top10_float_holders_period
                 WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
                 GROUP BY 1
                """,
                period,
            )
    finally:
        if close_income and income_conn is not None:
            income_conn.close()
        if close_holders and holders_conn is not None:
            holders_conn.close()
    return merge_announcement_maps(
        income_first=income_map, holders_first=holders_map
    )


def _open_alias(alias: str):
    try:
        from services.database_manifest import get_database_manifest
        from services.duck_adapter import connect

        path = get_database_manifest().path_for(alias)
        if path is None or not path.exists():
            return None
        return connect(str(path), read_only=True)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "compact_yyyymmdd",
    "is_decision_visible",
    "iso_date",
    "land_calendar_date",
    "load_period_announcement_map",
    "merge_announcement_maps",
    "normalize_stock_code",
    "pit_visible_rows",
    "resolve_available_iso",
    "stamp_available_dates",
]
