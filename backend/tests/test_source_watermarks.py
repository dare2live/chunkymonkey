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
        conn.execute(
            """
            CREATE TABLE market.price_kline_tdxhub (
                date TEXT,
                freq TEXT,
                adjust TEXT
            )
            """
        )
        conn.execute("INSERT INTO market.price_kline_tdxhub VALUES ('2026-05-05', 'daily', 'qfq')")
        items = refresh_known_source_watermarks(conn)
        stored = conn.execute(
            """
            SELECT row_count
              FROM mart_data_source_watermark
             WHERE data_domain = 'kline_daily'
               AND source_name = 'tdxhub_quote'
            """
        ).fetchone()

        assert len(items) > 0
        assert stored["row_count"] == 1
    finally:
        conn.close()


def test_tier_two_rows_do_not_imply_fallback_active_without_fallback_mode():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE raw_lhb_daily (
                trade_date TEXT
            );
            INSERT INTO raw_lhb_daily VALUES ('2026-05-05');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "lhb_daily",
            "source_name": "aif10_lhb",
            "source_tier": 2,
            "table": "raw_lhb_daily",
            "date_col": "trade_date",
            "parser_version": "aif10",
        })

        assert item["row_count"] == 1
        assert item["fallback_active"] is False
    finally:
        conn.close()


def test_kline_fallback_active_only_when_it_fills_primary_gap():
    conn = duck_mem()
    try:
        conn.execute("CREATE SCHEMA market")
        conn.executescript(
            """
            CREATE TABLE market.price_kline_tdxhub (date TEXT);
            CREATE TABLE market.price_kline (date TEXT);
            INSERT INTO market.price_kline_tdxhub VALUES ('2026-05-04');
            INSERT INTO market.price_kline VALUES ('2026-05-04'), ('2026-05-05');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "kline_daily",
            "source_name": "akshare_multi_source",
            "source_tier": 3,
            "table": "market.price_kline",
            "date_col": "date",
            "parser_version": "fallback",
            "fallback_mode": "fills_primary_gap",
            "primary_table": "market.price_kline_tdxhub",
            "primary_date_col": "date",
        })

        assert item["row_count"] == 2
        assert item["last_data_date"] == "2026-05-05"
        assert item["fallback_active"] is True
    finally:
        conn.close()


def test_kline_fallback_active_when_it_fills_missing_primary_keys_same_date():
    conn = duck_mem()
    try:
        conn.execute("CREATE SCHEMA market")
        conn.executescript(
            """
            CREATE TABLE market.price_kline_tdxhub (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT
            );
            CREATE TABLE market.price_kline (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT
            );
            INSERT INTO market.price_kline_tdxhub VALUES
                ('000001', '2026-05-05', 'daily', 'qfq');
            INSERT INTO market.price_kline VALUES
                ('000001', '2026-05-05', 'daily', 'qfq'),
                ('000002', '2026-05-05', 'daily', 'qfq');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "kline_daily",
            "source_name": "akshare_multi_source",
            "source_tier": 3,
            "table": "market.price_kline",
            "date_col": "date",
            "where": "freq = 'daily' AND adjust = 'qfq'",
            "parser_version": "fallback",
            "fallback_mode": "fills_primary_gap",
            "primary_table": "market.price_kline_tdxhub",
            "primary_date_col": "date",
            "primary_where": "freq = 'daily' AND adjust = 'qfq'",
            "gap_key_cols": ["code", "date", "freq", "adjust"],
        })

        assert item["last_data_date"] == "2026-05-05"
        assert item["fallback_active"] is True
    finally:
        conn.close()


def test_derive_watermark_applies_where_clause_to_row_count():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE price_kline (
                date TEXT,
                freq TEXT,
                adjust TEXT
            );
            INSERT INTO price_kline VALUES
                ('2026-05-04', 'daily', 'qfq'),
                ('2026-05-05', 'daily', 'qfq'),
                ('2026-05-31', 'monthly', 'qfq');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "kline_daily",
            "source_name": "akshare_multi_source",
            "source_tier": 3,
            "table": "price_kline",
            "date_col": "date",
            "where": "freq = 'daily' AND adjust = 'qfq'",
            "parser_version": "fallback",
        })

        assert item["row_count"] == 2
        assert item["last_data_date"] == "2026-05-05"
        assert item["fallback_active"] is False
    finally:
        conn.close()
