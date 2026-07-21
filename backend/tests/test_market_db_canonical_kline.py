import json

from conftest import duck_mem
from services.market_db import (
    ANALYSIS_KLINE_QFQ_VIEW_DDL,
    analysis_kline_daily_qfq_sql,
    get_analysis_kline_qfq_relation,
)
# PRICE_KLINE_TDXHUB_DDL/upsert_price_kline_tdxhub_rows import 已删 (M3+单元6 退役 tdxhub K线写入路径+adjustment_event物删)


# akshare/旧 price_kline 表 (2026-06-22 起不再进 v_price_kline_qfq, 仅作 HS300 指数 allowlist)
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


# tushare qfq 主表: OHLCV + physical lineage (batch_id/ingested_at/factor_as_of)
PRICE_KLINE_QFQ_TUSHARE_DDL = """
CREATE TABLE price_kline_qfq_tushare (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    amount REAL,
    batch_id TEXT,
    ingested_at TIMESTAMP,
    factor_as_of TEXT,
    PRIMARY KEY (code, date)
);
"""

_LINEAGE = (
    "batch_id, ingested_at, factor_as_of"
)


def _setup(conn):
    conn.executescript(PRICE_KLINE_DDL)
    conn.executescript(PRICE_KLINE_QFQ_TUSHARE_DDL)
    conn.executescript(ANALYSIS_KLINE_QFQ_VIEW_DDL)


def test_analysis_kline_reads_tushare_only_ignores_tdxhub_and_akshare():
    """tushare-only 契约 (2026-06-22): v_price_kline_qfq 只读 price_kline_qfq_tushare;
    akshare (price_kline) 不再进视图。(price_kline_tdxhub 已 M3 物删, 2026-06-23)。"""
    conn = duck_mem()
    try:
        _setup(conn)
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare "
            "(code, date, open, high, low, close, volume, amount, "
            f"{_LINEAGE}) VALUES "
            "('000001', '2026-05-04', 10, 11, 9, 10.5, 1000, 10500, "
            "'qfq:test:from_accepted', TIMESTAMP '2026-07-21 10:00:00', '2026-05-05')"
        )
        # akshare 行存在但应被视图忽略 (tushare-only)
        conn.execute(
            "INSERT INTO price_kline "
            "(code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at) "
            "VALUES ('000001', '2026-05-05', 'daily', 'qfq', 11, 12, 10, 11.5, 1100, 12650, 'eastmoney', 'fb', '2026-05-05')"
        )

        rows = conn.execute(
            "SELECT code, date, close, factor, source_name, source_tier, is_fallback, "
            "batch_id, factor_as_of "
            "FROM v_price_kline_qfq ORDER BY date"
        ).fetchall()

        # 只有 tushare 行; physical lineage passthrough; akshare 另一天不进视图
        assert [tuple(r) for r in rows] == [
            (
                "000001",
                "2026-05-04",
                10.5,
                1.0,
                "tushare",
                1,
                False,
                "qfq:test:from_accepted",
                "2026-05-05",
            ),
        ]
    finally:
        conn.close()


def test_analysis_kline_excludes_invalid_price_rows_no_fallback():
    """tushare 行 OHLC 非法 → 排除; 且不回退到 akshare (没有备用源)。"""
    conn = duck_mem()
    try:
        _setup(conn)
        # 非法 tushare 行 (close NULL)
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare "
            "(code, date, open, high, low, close, volume, amount) VALUES "
            "('000001', '2026-05-04', NULL, NULL, NULL, NULL, NULL, NULL)"
        )
        # akshare 有合法行但不应被当兜底
        conn.execute(
            "INSERT INTO price_kline "
            "(code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at) "
            "VALUES ('000001', '2026-05-04', 'daily', 'qfq', 11, 12, 10, 11.5, 1100, 12650, 'eastmoney', 'fb', '2026-05-04')"
        )

        n = conn.execute(
            "SELECT COUNT(*) FROM v_price_kline_qfq WHERE code='000001' AND date='2026-05-04'"
        ).fetchone()[0]
        assert n == 0  # 非法 tushare 排除 + 无 akshare 兜底 = 该键缺失 (诚实缺口)
    finally:
        conn.close()


def test_analysis_kline_excludes_invalid_volume_amount():
    """tushare 行 volume/amount 退化 (denormal float) → 排除。"""
    conn = duck_mem()
    try:
        _setup(conn)
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare "
            "(code, date, open, high, low, close, volume, amount) VALUES "
            "('000001', '2026-05-04', 10, 11, 9, 10.5, 5.877471754111438e-39, 5.877471754111438e-39)"
        )
        n = conn.execute("SELECT COUNT(*) FROM v_price_kline_qfq").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_analysis_kline_excludes_invalid_ohlc_consistency():
    """tushare 行 high<low 类不自洽 → 排除。"""
    conn = duck_mem()
    try:
        _setup(conn)
        # high(8) < low(10) 不自洽
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare "
            "(code, date, open, high, low, close, volume, amount) VALUES "
            "('000001', '2026-05-04', 9, 8, 10, 9.5, 1000, 9500)"
        )
        n = conn.execute("SELECT COUNT(*) FROM v_price_kline_qfq").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_analysis_relation_resolves_for_direct_and_attached_connections():
    assert get_analysis_kline_qfq_relation() == "v_price_kline_qfq"
    assert get_analysis_kline_qfq_relation("market") == "market.v_price_kline_qfq"


def test_analysis_daily_qfq_sql_uses_single_policy_relation_and_optional_lineage():
    assert analysis_kline_daily_qfq_sql(columns=("code", "date", "amount")) == (
        "SELECT code, date, amount\n"
        "FROM market.v_price_kline_qfq\n"
        "WHERE freq='daily' AND adjust='qfq'"
    )

    sql = analysis_kline_daily_qfq_sql(include_source_lineage=True)

    assert "FROM market.v_price_kline_qfq" in sql
    assert "factor" in sql
    # No COALESCE placeholders — physical/view columns selected honestly.
    assert "COALESCE(" not in sql
    assert "source_name" in sql
    assert "source_tier" in sql
    assert "is_fallback" in sql
    assert "batch_id" in sql
    assert "ingested_at" in sql
    assert "factor_as_of" in sql


# test_upsert_price_rows_rejects_non_allowlist_source_governance_v1 已删 (2026-06-29 批3a 回归清:
#   upsert_price_rows + price_kline 表已物删/函数已删, governance v1 hs300 allowlist 写路径不再存在)
