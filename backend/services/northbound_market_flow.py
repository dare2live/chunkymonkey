"""Market-day northbound flow overlay. Not a holder identity."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

TABLE = "raw_tushare_moneyflow_hsgt"
GRAIN = "market_day"
NOTE = (
    "tushare moneyflow_hsgt north_money is one row per trade_date for the "
    "whole market; it is not a named holder and must not be mixed into "
    "national_team or seat tags"
)


def latest_northbound_market_flow(
    *,
    raw_db: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(raw_db) if raw_db is not None else Path(
        get_database_manifest().path_for("tushare_raw")
    )
    if not path.is_file():
        return {
            "status": "unavailable",
            "reason": "tushare_raw_missing",
            "grain": GRAIN,
            "note": NOTE,
            "trade_date": None,
            "north_money": None,
            "south_money": None,
            "hgt": None,
            "sgt": None,
        }
    con = duck_connect(str(path), read_only=True)
    try:
        present = con.execute(
            "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
            [TABLE],
        ).fetchone()
        if present is None:
            return {
                "status": "unavailable",
                "reason": "table_absent",
                "grain": GRAIN,
                "note": NOTE,
                "trade_date": None,
                "north_money": None,
                "south_money": None,
                "hgt": None,
                "sgt": None,
            }
        row = con.execute(
            f"""
            SELECT trade_date, north_money, south_money, hgt, sgt
            FROM {TABLE}
            WHERE trade_date IS NOT NULL AND north_money IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return {
            "status": "empty",
            "reason": "no_rows",
            "grain": GRAIN,
            "note": NOTE,
            "trade_date": None,
            "north_money": None,
            "south_money": None,
            "hgt": None,
            "sgt": None,
        }
    return {
        "status": "ok",
        "reason": None,
        "grain": GRAIN,
        "note": NOTE,
        "source_table": TABLE,
        "unit": "tushare_moneyflow_hsgt_vendor_raw",
        "trade_date": str(row[0]),
        "north_money": _as_float(row[1]),
        "south_money": _as_float(row[2]),
        "hgt": _as_float(row[3]),
        "sgt": _as_float(row[4]),
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["latest_northbound_market_flow"]
