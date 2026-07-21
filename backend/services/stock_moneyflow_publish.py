"""B2 stock-day moneyflow publication — fact_stock_moneyflow(_dc)_daily strangler.

Owns serve/pulse stock-day moneyflow grain (trade_date × ts_code) with typed
available_at (trade_date 18:00 Asia/Shanghai, matching sync_registry
moneyflow / moneyflow_dc available_after) and lineage (source_table + built_at).

Two vendor planes stay separate:
  - moneyflow = tushare order-size net_mf_amount (万元)
  - moneyflow_dc = eastmoney stock net_amount (万元)

Source = raw landing residual. Not a formal accept plane; publication is
derived/materialized so DataAccess can leave the raw leaf. Not DC membership
PIT — moneyflow_dc is stock-day vendor flow only.
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect

TABLE_MF = "fact_stock_moneyflow_daily"
TABLE_MF_DC = "fact_stock_moneyflow_dc_daily"
SOURCE_MF = "raw_tushare_moneyflow"
SOURCE_MF_DC = "raw_tushare_moneyflow_dc"
_SH = ZoneInfo("Asia/Shanghai")
_PUBLISH_AT = time(18, 0)  # sync_registry moneyflow / moneyflow_dc available_after

# Test hooks (production reads database_manifest via resolver.db_path).
RAW_DB: Path | None = None
SMARTMONEY_DB: Path | None = None

DDL_MF = f"""
CREATE TABLE IF NOT EXISTS {TABLE_MF} (
    trade_date VARCHAR NOT NULL,
    ts_code VARCHAR NOT NULL,
    stock_code VARCHAR NOT NULL,
    net_mf_amount DOUBLE,
    buy_sm_amount DOUBLE,
    buy_md_amount DOUBLE,
    buy_lg_amount DOUBLE,
    buy_elg_amount DOUBLE,
    sell_sm_amount DOUBLE,
    sell_md_amount DOUBLE,
    sell_lg_amount DOUBLE,
    sell_elg_amount DOUBLE,
    available_at TIMESTAMPTZ NOT NULL,
    source_table VARCHAR NOT NULL,
    built_at TIMESTAMPTZ NOT NULL
)
"""

DDL_MF_DC = f"""
CREATE TABLE IF NOT EXISTS {TABLE_MF_DC} (
    trade_date VARCHAR NOT NULL,
    ts_code VARCHAR NOT NULL,
    stock_code VARCHAR NOT NULL,
    net_amount DOUBLE,
    net_amount_rate DOUBLE,
    pct_change DOUBLE,
    available_at TIMESTAMPTZ NOT NULL,
    source_table VARCHAR NOT NULL,
    built_at TIMESTAMPTZ NOT NULL
)
"""


def moneyflow_available_at(trade_date: str) -> datetime:
    """Consumer publication clock for one moneyflow partition day."""
    day = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    if len(day) != 8:
        raise ValueError(f"trade_date must be YYYYMMDD; got {trade_date!r}")
    d = datetime.strptime(day, "%Y%m%d").date()
    return datetime.combine(d, _PUBLISH_AT, tzinfo=_SH)


def ensure_schema_mf(conn) -> None:
    conn.execute(DDL_MF)
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE_MF}_grain
        ON {TABLE_MF} (trade_date, ts_code)
        """
    )


def ensure_schema_mf_dc(conn) -> None:
    conn.execute(DDL_MF_DC)
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE_MF_DC}_grain
        ON {TABLE_MF_DC} (trade_date, ts_code)
        """
    )


def _raw_db_path() -> Path:
    return Path(RAW_DB) if RAW_DB is not None else Path(resolver.db_path("tushare_raw"))


def _smartmoney_db_path() -> Path:
    return (
        Path(SMARTMONEY_DB)
        if SMARTMONEY_DB is not None
        else Path(resolver.db_path("smartmoney"))
    )


def _compact(value: str) -> str:
    day = "".join(ch for ch in str(value) if ch.isdigit())[:8]
    if len(day) != 8:
        raise ValueError(f"expected YYYYMMDD; got {value!r}")
    datetime.strptime(day, "%Y%m%d")  # validate
    return day


def _window_args(
    start: str | None, end: str | None
) -> tuple[str | None, str | None]:
    start_d = _compact(start) if start else None
    end_d = _compact(end) if end else None
    if (start_d is None) ^ (end_d is None):
        raise ValueError("start and end must both be set or both omitted")
    if start_d and end_d and start_d > end_d:
        raise ValueError(f"start {start_d} > end {end_d}")
    return start_d, end_d


def publish_fact_stock_moneyflow_daily(
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Materialize tushare stock-day moneyflow from landing raw into smartmoney."""
    start_d, end_d = _window_args(start, end)
    raw_path = _raw_db_path().resolve()
    sm_path = _smartmoney_db_path().resolve()
    if not raw_path.is_file():
        raise FileNotFoundError(f"missing raw db: {raw_path}")
    sm_path.parent.mkdir(parents=True, exist_ok=True)

    built_at = datetime.now(tz=_SH)
    con = duck_connect(str(sm_path), read_only=False)
    try:
        raw_esc = str(raw_path).replace("'", "''")
        con.execute(f"ATTACH '{raw_esc}' AS tr (READ_ONLY)")
        if start_d is None:
            con.execute(f"DROP TABLE IF EXISTS {TABLE_MF}")
            ensure_schema_mf(con)
            where = "TRUE"
            params: list[Any] = []
        else:
            ensure_schema_mf(con)
            con.execute(
                f"DELETE FROM {TABLE_MF} WHERE trade_date >= ? AND trade_date <= ?",
                [start_d, end_d],
            )
            where = "trade_date >= ? AND trade_date <= ?"
            params = [start_d, end_d]

        con.execute(
            f"""
            INSERT INTO {TABLE_MF} (
                trade_date, ts_code, stock_code, net_mf_amount,
                buy_sm_amount, buy_md_amount, buy_lg_amount, buy_elg_amount,
                sell_sm_amount, sell_md_amount, sell_lg_amount, sell_elg_amount,
                available_at, source_table, built_at
            )
            SELECT
                r.trade_date,
                r.ts_code,
                SPLIT_PART(r.ts_code, '.', 1) AS stock_code,
                r.net_mf_amount,
                r.buy_sm_amount,
                r.buy_md_amount,
                r.buy_lg_amount,
                r.buy_elg_amount,
                r.sell_sm_amount,
                r.sell_md_amount,
                r.sell_lg_amount,
                r.sell_elg_amount,
                timezone(
                    'Asia/Shanghai',
                    CAST(strptime(r.trade_date, '%Y%m%d') AS TIMESTAMP)
                    + INTERVAL 18 HOUR
                ) AS available_at,
                ? AS source_table,
                ? AS built_at
            FROM tr.{SOURCE_MF} r
            WHERE {where}
              AND r.ts_code IS NOT NULL
              AND r.trade_date IS NOT NULL
            """,
            [SOURCE_MF, built_at, *params],
        )
        rows = con.execute(
            f"SELECT COUNT(*) FROM {TABLE_MF}"
            + (
                " WHERE trade_date >= ? AND trade_date <= ?"
                if start_d
                else ""
            ),
            [start_d, end_d] if start_d else [],
        ).fetchone()[0]
        return {
            "table": TABLE_MF,
            "source_table": SOURCE_MF,
            "rows": int(rows),
            "start": start_d,
            "end": end_d,
            "built_at": built_at.isoformat(),
            "grain": ["trade_date", "ts_code"],
            "mode": "window" if start_d else "full_rebuild",
        }
    finally:
        con.close()


def publish_fact_stock_moneyflow_dc_daily(
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Materialize eastmoney stock-day moneyflow from landing raw into smartmoney."""
    start_d, end_d = _window_args(start, end)
    raw_path = _raw_db_path().resolve()
    sm_path = _smartmoney_db_path().resolve()
    if not raw_path.is_file():
        raise FileNotFoundError(f"missing raw db: {raw_path}")
    sm_path.parent.mkdir(parents=True, exist_ok=True)

    built_at = datetime.now(tz=_SH)
    con = duck_connect(str(sm_path), read_only=False)
    try:
        raw_esc = str(raw_path).replace("'", "''")
        con.execute(f"ATTACH '{raw_esc}' AS tr (READ_ONLY)")
        if start_d is None:
            con.execute(f"DROP TABLE IF EXISTS {TABLE_MF_DC}")
            ensure_schema_mf_dc(con)
            where = "TRUE"
            params: list[Any] = []
        else:
            ensure_schema_mf_dc(con)
            con.execute(
                f"DELETE FROM {TABLE_MF_DC} WHERE trade_date >= ? AND trade_date <= ?",
                [start_d, end_d],
            )
            where = "trade_date >= ? AND trade_date <= ?"
            params = [start_d, end_d]

        con.execute(
            f"""
            INSERT INTO {TABLE_MF_DC} (
                trade_date, ts_code, stock_code,
                net_amount, net_amount_rate, pct_change,
                available_at, source_table, built_at
            )
            SELECT
                r.trade_date,
                r.ts_code,
                SPLIT_PART(r.ts_code, '.', 1) AS stock_code,
                r.net_amount,
                r.net_amount_rate,
                r.pct_change,
                timezone(
                    'Asia/Shanghai',
                    CAST(strptime(r.trade_date, '%Y%m%d') AS TIMESTAMP)
                    + INTERVAL 18 HOUR
                ) AS available_at,
                ? AS source_table,
                ? AS built_at
            FROM tr.{SOURCE_MF_DC} r
            WHERE {where}
              AND r.ts_code IS NOT NULL
              AND r.trade_date IS NOT NULL
            """,
            [SOURCE_MF_DC, built_at, *params],
        )
        rows = con.execute(
            f"SELECT COUNT(*) FROM {TABLE_MF_DC}"
            + (
                " WHERE trade_date >= ? AND trade_date <= ?"
                if start_d
                else ""
            ),
            [start_d, end_d] if start_d else [],
        ).fetchone()[0]
        return {
            "table": TABLE_MF_DC,
            "source_table": SOURCE_MF_DC,
            "rows": int(rows),
            "start": start_d,
            "end": end_d,
            "built_at": built_at.isoformat(),
            "grain": ["trade_date", "ts_code"],
            "mode": "window" if start_d else "full_rebuild",
        }
    finally:
        con.close()


__all__ = [
    "TABLE_MF",
    "TABLE_MF_DC",
    "SOURCE_MF",
    "SOURCE_MF_DC",
    "moneyflow_available_at",
    "ensure_schema_mf",
    "ensure_schema_mf_dc",
    "publish_fact_stock_moneyflow_daily",
    "publish_fact_stock_moneyflow_dc_daily",
]
