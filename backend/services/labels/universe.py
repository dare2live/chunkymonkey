"""Point-in-time stock universe helpers for label builds.

This module is intentionally scoped to the label/panel pipeline.  It uses
listing-date history instead of ``dim_active_a_stock`` so historical delisted (rule-compliance: ok evidence=docstring-reference)
stocks stay in training data while future listings are excluded per signal date.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Any


ACTIVE_A_SHARE_PREFIXES: tuple[str, ...] = ("60", "00", "30", "68")


def _date_literal(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(r[0]) for r in rows}


def has_pit_listing_source(conn: Any) -> bool:
    """Return True when a listing-history source is available."""
    if _table_exists(conn, "dim_listing_status"):
        row = conn.execute("SELECT COUNT(*) FROM dim_listing_status").fetchone()
        if row and row[0] > 0:
            cols = _table_columns(conn, "dim_listing_status")
            if "listed_date" in cols or "flag_from_date" in cols:
                return True
    return _table_exists(conn, "dim_all_ever_listed")


def pit_active_ever(
    conn: Any,
    signal_date: str | date,
    *,
    prefixes: Iterable[str] = ACTIVE_A_SHARE_PREFIXES,
) -> list[str]:
    """Return stocks listed by ``signal_date`` without current-active bias.

    Rules:
    - include stocks whose listed date is on/before ``signal_date``;
    - include currently delisted stocks for historical dates before delisting;
    - exclude stocks whose listed date is after ``signal_date``;
    - keep the existing A-share prefix universe.
    """
    signal = _date_literal(signal_date)
    prefix_list = tuple(prefixes)
    if not prefix_list:
        return []
    placeholders = ",".join("?" for _ in prefix_list)

    if _table_exists(conn, "dim_listing_status"):
        cols = _table_columns(conn, "dim_listing_status")
        count_row = conn.execute("SELECT COUNT(*) FROM dim_listing_status").fetchone()
        if count_row and count_row[0] > 0 and ("listed_date" in cols or "flag_from_date" in cols):
            code_col = "stock_code" if "stock_code" in cols else "ts_code"
            listed_expr = (
                "TRY_CAST(listed_date AS DATE)"
                if "listed_date" in cols
                else "TRY_CAST(flag_from_date AS DATE)"
            )
            delisted_expr = "TRY_CAST(delisted_date AS DATE)" if "delisted_date" in cols else "NULL::DATE"
            rows = conn.execute(
                f"""
                SELECT {code_col}
                FROM dim_listing_status
                WHERE {code_col} IS NOT NULL
                  AND SUBSTR({code_col}, 1, 2) IN ({placeholders})
                  AND COALESCE({listed_expr}, DATE '1900-01-01') <= TRY_CAST(? AS DATE)
                  AND ({delisted_expr} IS NULL OR {delisted_expr} >= TRY_CAST(? AS DATE))
                ORDER BY {code_col}
                """,
                [*prefix_list, signal, signal],
            ).fetchall()
            return [str(r[0]) for r in rows]

    if _table_exists(conn, "dim_all_ever_listed"):
        rows = conn.execute(
            f"""
            SELECT stock_code
            FROM dim_all_ever_listed
            WHERE stock_code IS NOT NULL
              AND SUBSTR(stock_code, 1, 2) IN ({placeholders})
              AND COALESCE(TRY_CAST(first_seen_date AS DATE), DATE '1900-01-01') <= TRY_CAST(? AS DATE)
              AND (
                  TRY_CAST(delisted_date AS DATE) IS NULL
                  OR TRY_CAST(delisted_date AS DATE) >= TRY_CAST(? AS DATE)
              )
            ORDER BY stock_code
            """,
            [*prefix_list, signal, signal],
        ).fetchall()
        return [str(r[0]) for r in rows]

    raise RuntimeError("No PIT listing source found: dim_listing_status or dim_all_ever_listed required")


def pit_universe_by_signal_date(
    conn: Any,
    signal_dates: Iterable[str | date],
    *,
    candidate_stock_codes: Iterable[str] | None = None,
    prefixes: Iterable[str] = ACTIVE_A_SHARE_PREFIXES,
) -> dict[str, list[str]]:
    """Resolve PIT stock lists per signal date, optionally intersecting candidates."""
    candidate_set = None
    if candidate_stock_codes is not None:
        candidate_set = {str(code) for code in candidate_stock_codes if code}

    out: dict[str, list[str]] = {}
    for signal_date in signal_dates:
        key = _date_literal(signal_date)
        stocks = pit_active_ever(conn, key, prefixes=prefixes)
        if candidate_set is not None:
            stocks = [stock for stock in stocks if stock in candidate_set]
        out[key] = stocks
    return out
