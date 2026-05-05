from conftest import duck_mem
from services.source_watermarks import (
    derive_watermark,
    refresh_known_source_watermarks,
)


def test_derive_watermark_from_existing_table():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                report_date TEXT,
                raw_hash TEXT
            );
            INSERT INTO fact_top10_holder_period VALUES
                ('000001', '2026-03-31', 'hash_a'),
                ('000002', '2026-04-30', 'hash_b');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "holders_top10_float",
            "source_name": "tdxhub_holders",
            "source_tier": 1,
            "table": "fact_top10_holder_period",
            "date_col": "report_date",
            "raw_hash_col": "raw_hash",
            "parser_version": "v1",
        })

        assert item["row_count"] == 2
        assert item["last_data_date"] == "2026-04-30"
        assert item["last_raw_hash"] == "hash_b"
        assert item["fallback_active"] is False
    finally:
        conn.close()


def test_refresh_known_source_watermarks_creates_domain_rows():
    conn = duck_mem()
    try:
        conn.execute("CREATE SCHEMA market")
        conn.execute("CREATE TABLE market.price_kline_tdxhub (date TEXT)")
        conn.execute("INSERT INTO market.price_kline_tdxhub VALUES ('2026-05-05')")
        items = refresh_known_source_watermarks(conn)
        stored = conn.execute(
            """
            SELECT row_count
              FROM mart_data_source_watermark
             WHERE data_domain = 'kline_daily'
            """
        ).fetchone()

        assert len(items) > 0
        assert stored["row_count"] == 1
    finally:
        conn.close()
