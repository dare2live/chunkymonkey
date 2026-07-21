"""B2 top_inst seat publication — fact_top_inst_seat_daily strangler.

Owns pulse/institution seat grain
(trade_date × ts_code × exalter × side) with typed available_at
(trade_date 18:00 Asia/Shanghai, matching sync_registry top_inst
available_after) and lineage (source_table + built_at).

Source = raw_tushare_top_inst (landing residual). Not a formal accept plane;
publication is derived/materialized so DataAccess can leave the raw leaf.
Does not invent DC membership PIT. Episode/mart aggregates are not this grain.
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect

TABLE = "fact_top_inst_seat_daily"
SOURCE_TABLE = "raw_tushare_top_inst"
_SH = ZoneInfo("Asia/Shanghai")
_PUBLISH_AT = time(18, 0)  # sync_registry top_inst available_after

# Test hooks (production reads database_manifest via resolver.db_path).
RAW_DB: Path | None = None
SMARTMONEY_DB: Path | None = None

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    trade_date VARCHAR NOT NULL,
    ts_code VARCHAR NOT NULL,
    exalter VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    net_buy DOUBLE,
    available_at TIMESTAMPTZ NOT NULL,
    source_table VARCHAR NOT NULL,
    built_at TIMESTAMPTZ NOT NULL
)
"""


def top_inst_seat_available_at(trade_date: str) -> datetime:
    """Consumer publication clock for one top_inst seat partition day."""
    day = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    if len(day) != 8:
        raise ValueError(f"trade_date must be YYYYMMDD; got {trade_date!r}")
    d = datetime.strptime(day, "%Y%m%d").date()
    return datetime.combine(d, _PUBLISH_AT, tzinfo=_SH)


def ensure_schema(conn) -> None:
    conn.execute(DDL)
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE}_grain
        ON {TABLE} (trade_date, ts_code, exalter, side)
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


def publish_fact_top_inst_seat_daily(
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Materialize seat-day LHB rows from landing raw into smartmoney.

    When start/end are set, replace only that closed partition window.
    When omitted, full rebuild (DROP+CREATE) from all raw rows.
    """
    start_d = _compact(start) if start else None
    end_d = _compact(end) if end else None
    if (start_d is None) ^ (end_d is None):
        raise ValueError("start and end must both be set or both omitted")
    if start_d and end_d and start_d > end_d:
        raise ValueError(f"start {start_d} > end {end_d}")

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
            con.execute(f"DROP TABLE IF EXISTS {TABLE}")
            ensure_schema(con)
            where = "TRUE"
            params: list[Any] = []
        else:
            ensure_schema(con)
            con.execute(
                f"DELETE FROM {TABLE} WHERE trade_date >= ? AND trade_date <= ?",
                [start_d, end_d],
            )
            where = "trade_date >= ? AND trade_date <= ?"
            params = [start_d, end_d]

        con.execute(
            f"""
            INSERT INTO {TABLE} (
                trade_date, ts_code, exalter, side, net_buy,
                available_at, source_table, built_at
            )
            SELECT
                r.trade_date,
                r.ts_code,
                r.exalter,
                r.side,
                r.net_buy,
                timezone(
                    'Asia/Shanghai',
                    CAST(strptime(r.trade_date, '%Y%m%d') AS TIMESTAMP)
                    + INTERVAL 18 HOUR
                ) AS available_at,
                ? AS source_table,
                ? AS built_at
            FROM tr.{SOURCE_TABLE} r
            WHERE {where}
              AND r.ts_code IS NOT NULL
              AND r.trade_date IS NOT NULL
              AND r.exalter IS NOT NULL
              AND r.side IS NOT NULL
            """,
            [SOURCE_TABLE, built_at, *params],
        )
        rows = con.execute(
            f"SELECT COUNT(*) FROM {TABLE}"
            + (
                " WHERE trade_date >= ? AND trade_date <= ?"
                if start_d
                else ""
            ),
            [start_d, end_d] if start_d else [],
        ).fetchone()[0]
        return {
            "table": TABLE,
            "source_table": SOURCE_TABLE,
            "rows": int(rows),
            "start": start_d,
            "end": end_d,
            "built_at": built_at.isoformat(),
            "grain": ["trade_date", "ts_code", "exalter", "side"],
            "mode": "window" if start_d else "full_rebuild",
        }
    finally:
        con.close()


__all__ = [
    "TABLE",
    "SOURCE_TABLE",
    "top_inst_seat_available_at",
    "ensure_schema",
    "publish_fact_top_inst_seat_daily",
]
