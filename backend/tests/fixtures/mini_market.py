from __future__ import annotations

from services.duck_adapter import DuckConn, connect
from services.market_db import CANONICAL_KLINE_QFQ_VIEW_DDL, PRICE_KLINE_TDXHUB_DDL


PRICE_KLINE_DDL = """
CREATE TABLE price_kline (
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    freq        TEXT NOT NULL DEFAULT 'daily',
    adjust      TEXT NOT NULL DEFAULT 'qfq',
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    source      TEXT,
    batch_id    TEXT,
    ingested_at TEXT,
    PRIMARY KEY (code, date, freq, adjust)
);
"""


CALENDAR_DDL = """
CREATE TABLE dim_trading_calendar (
    trade_date TEXT PRIMARY KEY,
    is_trading INTEGER DEFAULT 1
);
"""


def mini_market_conn() -> DuckConn:
    """Return a market-shaped in-memory DuckDB connection."""

    conn = connect(":memory:")
    conn.executescript(PRICE_KLINE_DDL)
    conn.executescript(PRICE_KLINE_TDXHUB_DDL)
    conn.executescript(CALENDAR_DDL)
    conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
    return conn


def insert_primary_kline(conn: DuckConn, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO price_kline_tdxhub
        (code, date, freq, adjust, open, high, low, close, volume, amount, factor, source, batch_id, ingested_at)
        VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?)
        """,
        rows,
    )


def insert_fallback_kline(conn: DuckConn, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO price_kline
        (code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at)
        VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
