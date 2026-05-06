from conftest import duck_mem
from services.market_db import (
    CANONICAL_KLINE_QFQ_VIEW_DDL,
    PRICE_KLINE_TDXHUB_DDL,
    canonical_kline_daily_qfq_sql,
    get_canonical_kline_qfq_relation,
    upsert_price_kline_tdxhub_rows,
)


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


def test_canonical_kline_prefers_tdxhub_and_fills_fallback_gap():
    conn = duck_mem()
    try:
        conn.executescript(PRICE_KLINE_DDL)
        conn.executescript(PRICE_KLINE_TDXHUB_DDL)
        conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
        conn.executemany(
            """
            INSERT INTO price_kline_tdxhub
            (code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at)
            VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "2026-05-04", 10, 11, 9, 10.5, 1000, 10500, "tdxhub", "tdx-1", "2026-05-04"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO price_kline
            (code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at)
            VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "2026-05-04", 99, 99, 99, 99.0, 9999, 9999, "eastmoney", "fb-1", "2026-05-04"),
                ("000001", "2026-05-05", 11, 12, 10, 11.5, 1100, 12650, "eastmoney", "fb-2", "2026-05-05"),
            ],
        )

        rows = conn.execute(
            """
            SELECT code, date, close, source_name, source_tier, is_fallback
              FROM v_price_kline_qfq
             ORDER BY date
            """
        ).fetchall()

        assert [tuple(row) for row in rows] == [
            ("000001", "2026-05-04", 10.5, "tdxhub", 1, False),
            ("000001", "2026-05-05", 11.5, "eastmoney", 3, True),
        ]
    finally:
        conn.close()


def test_canonical_relation_resolves_for_direct_and_attached_connections():
    assert get_canonical_kline_qfq_relation() == "v_price_kline_qfq"
    assert get_canonical_kline_qfq_relation("market") == "market.v_price_kline_qfq"


def test_canonical_daily_qfq_sql_uses_single_policy_relation_and_optional_lineage():
    assert canonical_kline_daily_qfq_sql(columns=("code", "date", "amount")) == (
        "SELECT code, date, amount\n"
        "FROM market.v_price_kline_qfq\n"
        "WHERE freq='daily' AND adjust='qfq'"
    )

    sql = canonical_kline_daily_qfq_sql(include_source_lineage=True)

    assert "FROM market.v_price_kline_qfq" in sql
    assert "COALESCE(source_name, 'unknown') AS source_name" in sql
    assert "COALESCE(source_tier, 99)::SMALLINT AS source_tier" in sql
    assert "COALESCE(is_fallback, FALSE) AS is_fallback" in sql


def test_tdxhub_upsert_writes_primary_table_for_canonical_reads():
    conn = duck_mem()
    try:
        conn.executescript(PRICE_KLINE_DDL)
        conn.executescript(PRICE_KLINE_TDXHUB_DDL)
        conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
        conn.execute(
            """
            INSERT INTO price_kline
            (code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at)
            VALUES ('000001', '2026-05-04', 'daily', 'qfq', 1, 1, 1, 1, 1, 1, 'eastmoney', 'fb', '2026-05-04')
            """
        )

        written = upsert_price_kline_tdxhub_rows(
            conn,
            [
                {
                    "code": "000001",
                    "date": "2026-05-04",
                    "freq": "daily",
                    "adjust": "qfq",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1000,
                    "amount": 10500,
                    "factor": 1.0,
                }
            ],
            source="tdxhub_incremental",
            batch_id="tdx-inc",
        )

        row = conn.execute(
            """
            SELECT close, source_name, source_tier, is_fallback
              FROM v_price_kline_qfq
             WHERE code = '000001' AND date = '2026-05-04'
            """
        ).fetchone()
        primary = conn.execute(
            "SELECT close, source, batch_id FROM price_kline_tdxhub"
        ).fetchone()

        assert written == 1
        assert tuple(row) == (10.5, "tdxhub_incremental", 1, False)
        assert tuple(primary) == (10.5, "tdxhub_incremental", "tdx-inc")
    finally:
        conn.close()
