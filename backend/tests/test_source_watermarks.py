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
            CREATE TABLE canonical_top10_float_holders_period (
                stock_code TEXT,
                notice_date TEXT,
                raw_hash TEXT
            );
            INSERT INTO canonical_top10_float_holders_period VALUES
                ('000001', '20260331', 'hash_a'),
                ('000002', '20260430', 'hash_b');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "holders_top10_float",
            "source_name": "tdxhub_holders",
            "source_tier": 1,
            "table": "canonical_top10_float_holders_period",
            "date_col": "notice_date",
            "raw_hash_col": "raw_hash",
            "parser_version": "v1",
        })

        assert item["row_count"] == 2
        assert item["last_data_date"] == "20260430"
        assert item["last_raw_hash"] == "hash_b"
        assert item["fallback_active"] is False
    finally:
        conn.close()


def test_refresh_known_source_watermarks_creates_domain_rows():
    # 2026-06-28: kline_daily watermark repoint tushare (price_kline_tdxhub 物删 → price_kline_qfq_tushare)
    conn = duck_mem()
    try:
        conn.execute("CREATE SCHEMA market")
        conn.execute(
            """
            CREATE TABLE market.price_kline_qfq_tushare (
                code TEXT,
                date TEXT
            )
            """
        )
        conn.execute("INSERT INTO market.price_kline_qfq_tushare VALUES ('600519', '2026-05-05')")
        items = refresh_known_source_watermarks(conn)
        stored = conn.execute(
            """
            SELECT row_count
              FROM mart_data_source_watermark
             WHERE data_domain = 'kline_daily'
               AND source_name = 'tushare'
            """
        ).fetchone()

        assert len(items) > 0
        assert stored["row_count"] == 1
    finally:
        conn.close()


def test_tier_two_rows_do_not_imply_fallback_active_without_fallback_mode():
    conn = duck_mem()
    try:
        # 中性 tier-2 探针表 (原用 raw_lhb_daily/lhb_daily; 2026-06-29 批2b LHB 切 tushare 退役后改通用名;
        #   本测试验 source_watermarks 通用 tier-2 逻辑[tier-2 行≠fallback active], 不绑具体域)
        conn.executescript(
            """
            CREATE TABLE raw_probe_tier2 (
                trade_date TEXT
            );
            INSERT INTO raw_probe_tier2 VALUES ('2026-05-05');
            """
        )
        item = derive_watermark(conn, {
            "data_domain": "probe_tier2",
            "source_name": "probe_tier2_src",
            "source_tier": 2,
            "table": "raw_probe_tier2",
            "date_col": "trade_date",
            "parser_version": "probe",
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


def test_watermark_day_is_normalized_at_the_single_writer():
    """last_data_date 的格式契约由唯一写入口强制, 不靠每个读者自己 replace("-","")。

    2026-08-17 实测: 这张表混着三种格式(compact8×41 / dashed10×2 / timestamp×1)。
    因为 '-'(0x2D) < '0'(0x30):
      - ORDER BY last_data_date 把全表最新的 industry_dc(2026-08-16 23:33) 排到第 3
      - 字符串比较 '2026-08-14' < '20260810' 为真 —— 更新的域被判成落后
    受害的是 source_watermarks 的 fallback/primary 新旧比较与 project_status 的排序。
    """
    from services.source_watermarks import normalize_watermark_day

    # 三种实际出现过的格式都归一到 compact8
    assert normalize_watermark_day("20260813") == "20260813"
    assert normalize_watermark_day("2026-08-14") == "20260814"
    assert normalize_watermark_day("2026-08-16 23:33:46.497247+08") == "20260816"

    # 读不懂就返回 None, 不猜 —— 写一个错的日期比留空更危险
    for unknown in (None, "", "garbage", "2026/08/14", "08-14-2026"):
        assert normalize_watermark_day(unknown) is None, unknown

    # 归一后字符串比较才与真实时间序一致
    assert normalize_watermark_day("2026-08-14") > normalize_watermark_day("20260810")


def test_stored_watermarks_are_all_compact8():
    """写进去什么格式, 读出来就必须是 compact8 —— 覆盖真正的写入口而不只是纯函数。"""
    from services.source_watermarks import ensure_source_watermark_schema, upsert_watermark

    conn = duck_mem()
    ensure_source_watermark_schema(conn)
    for i, raw in enumerate(["20260813", "2026-08-14", "2026-08-16 23:33:46.497247+08"]):
        upsert_watermark(conn, {
            "data_domain": f"d{i}", "source_name": "s", "source_tier": 1,
            "last_data_date": raw,
        })
    stored = [r[0] for r in conn.execute(
        "SELECT last_data_date FROM mart_data_source_watermark ORDER BY data_domain"
    ).fetchall()]

    assert stored == ["20260813", "20260814", "20260816"], stored
