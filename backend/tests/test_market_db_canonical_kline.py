import json

from conftest import duck_mem
from services.market_db import (
    CANONICAL_KLINE_QFQ_VIEW_DDL,
    PRICE_KLINE_TDXHUB_DDL,
    canonical_kline_daily_qfq_sql,
    get_canonical_kline_qfq_relation,
    upsert_price_kline_tdxhub_rows,
)


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


# tushare qfq 主表 (2026-06-22 起 v_price_kline_qfq 唯一来源; 只存 OHLCV, 视图合成其余列)
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
    PRIMARY KEY (code, date)
);
"""


def _setup(conn):
    conn.executescript(PRICE_KLINE_DDL)
    conn.executescript(PRICE_KLINE_TDXHUB_DDL)
    conn.executescript(PRICE_KLINE_QFQ_TUSHARE_DDL)
    conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)


def test_canonical_kline_reads_tushare_only_ignores_tdxhub_and_akshare():
    """tushare-only 契约 (2026-06-22): v_price_kline_qfq 只读 price_kline_qfq_tushare;
    price_kline_tdxhub (qfq 系统性算错) 与 price_kline (akshare) 都不再进视图。"""
    conn = duck_mem()
    try:
        _setup(conn)
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare VALUES "
            "('000001', '2026-05-04', 10, 11, 9, 10.5, 1000, 10500)"
        )
        # tdxhub / akshare 行存在但应被视图忽略 (tushare-only)
        conn.execute(
            "INSERT INTO price_kline_tdxhub "
            "(code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at) "
            "VALUES ('000001', '2026-05-04', 'daily', 'qfq', 99, 99, 99, 99, 9999, 9999, 'tdxhub', 'tdx-1', '2026-05-04')"
        )
        conn.execute(
            "INSERT INTO price_kline "
            "(code, date, freq, adjust, open, high, low, close, volume, amount, source, batch_id, ingested_at) "
            "VALUES ('000001', '2026-05-05', 'daily', 'qfq', 11, 12, 10, 11.5, 1100, 12650, 'eastmoney', 'fb', '2026-05-05')"
        )

        rows = conn.execute(
            "SELECT code, date, close, factor, source_name, source_tier, is_fallback "
            "FROM v_price_kline_qfq ORDER BY date"
        ).fetchall()

        # 只有 tushare 行; tdxhub 同键被忽略 (不取其 99 值); akshare 另一天不进视图
        assert [tuple(r) for r in rows] == [
            ("000001", "2026-05-04", 10.5, 1.0, "tushare", 1, False),
        ]
    finally:
        conn.close()


def test_canonical_kline_excludes_invalid_price_rows_no_fallback():
    """tushare 行 OHLC 非法 → 排除; 且不回退到 akshare (没有备用源)。"""
    conn = duck_mem()
    try:
        _setup(conn)
        # 非法 tushare 行 (close NULL)
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare VALUES "
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


def test_canonical_kline_excludes_invalid_volume_amount():
    """tushare 行 volume/amount 退化 (denormal float) → 排除。"""
    conn = duck_mem()
    try:
        _setup(conn)
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare VALUES "
            "('000001', '2026-05-04', 10, 11, 9, 10.5, 5.877471754111438e-39, 5.877471754111438e-39)"
        )
        n = conn.execute("SELECT COUNT(*) FROM v_price_kline_qfq").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_canonical_kline_excludes_invalid_ohlc_consistency():
    """tushare 行 high<low 类不自洽 → 排除。"""
    conn = duck_mem()
    try:
        _setup(conn)
        # high(8) < low(10) 不自洽
        conn.execute(
            "INSERT INTO price_kline_qfq_tushare VALUES "
            "('000001', '2026-05-04', 9, 8, 10, 9.5, 1000, 9500)"
        )
        n = conn.execute("SELECT COUNT(*) FROM v_price_kline_qfq").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_tdxhub_upsert_rejects_invalid_rows_and_records_monitor_evidence():
    """tdxhub upsert 函数 (退役中, 仍守数据质量门): 拒非法行 + 写 monitor 证据。
    注: 写的是 price_kline_tdxhub 表, 不再进 v_price_kline_qfq (tushare-only)。"""
    conn = duck_mem()
    try:
        _setup(conn)

        written = upsert_price_kline_tdxhub_rows(
            conn,
            [
                {
                    "code": "000001", "date": "2026-05-04", "freq": "daily", "adjust": "qfq",
                    "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 10, "amount": 10500,
                },
                {
                    "code": "000002", "date": "2026-05-04", "freq": "daily", "adjust": "qfq",
                    "open": 20, "high": 21, "low": 19, "close": 20.5,
                    "volume": 5.877471754111438e-39, "amount": 5.877471754111438e-39,
                },
            ],
            source="tdxhub",
            batch_id="unit-clean",
        )

        row = conn.execute("SELECT COUNT(*) FROM price_kline_tdxhub").fetchone()
        monitor = conn.execute(
            "SELECT rejected_rows, reason_counts_json FROM mart_data_processing_tool_run "
            "WHERE output_table = 'price_kline_tdxhub'"
        ).fetchone()
        issues = conn.execute(
            "SELECT reason_code, sample_rows_json FROM mart_data_processing_tool_issue "
            "WHERE affected_table = 'price_kline_tdxhub' ORDER BY reason_code"
        ).fetchall()

        assert written == 1
        assert row[0] == 1
        assert monitor["rejected_rows"] == 1
        reasons = json.loads(monitor["reason_counts_json"])
        assert reasons["invalid_volume"] == 1
        assert reasons["invalid_amount"] == 1
        assert [issue["reason_code"] for issue in issues] == ["invalid_amount", "invalid_volume"]
        assert json.loads(issues[0]["sample_rows_json"])[0]["code"] == "000002"
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
    assert "factor" in sql
    assert "COALESCE(source_name, 'unknown') AS source_name" in sql
    assert "COALESCE(source_tier, 99)::SMALLINT AS source_tier" in sql
    assert "COALESCE(is_fallback, FALSE) AS is_fallback" in sql


def test_tdxhub_upsert_writes_table_but_not_canonical_view():
    """tushare-only 契约 (2026-06-22): tdxhub upsert 仍写 price_kline_tdxhub 表,
    但 v_price_kline_qfq 已切 tushare-only → tdxhub 行不再出现在 canonical 视图。
    旧契约 (tdxhub=canonical primary) 已废 — 因 tdxhub qfq 系统性算错。"""
    conn = duck_mem()
    try:
        _setup(conn)

        written = upsert_price_kline_tdxhub_rows(
            conn,
            [
                {
                    "code": "000001", "date": "2026-05-04", "freq": "daily", "adjust": "qfq",
                    "open": 10, "high": 11, "low": 9, "close": 10.5,
                    "volume": 10, "amount": 10500, "factor": 1.0,
                }
            ],
            source="tdxhub_incremental",
            batch_id="tdx-inc",
        )

        primary = conn.execute(
            "SELECT close, source, batch_id FROM price_kline_tdxhub"
        ).fetchone()
        in_view = conn.execute(
            "SELECT COUNT(*) FROM v_price_kline_qfq WHERE code='000001' AND date='2026-05-04'"
        ).fetchone()[0]

        assert written == 1
        assert tuple(primary) == (10.5, "tdxhub_incremental", "tdx-inc")
        assert in_view == 0  # tushare-only: tdxhub 行不进视图
    finally:
        conn.close()


def test_upsert_price_rows_rejects_non_allowlist_source_governance_v1():
    """governance v1: price_kline 主表 retired except hs300 allowlist.

    from yaml: configs/data_governance.yaml schema_contracts.price_kline.allowed_sources
    """
    import pytest
    from services.market_db import upsert_price_rows

    conn = duck_mem()
    try:
        conn.executescript(PRICE_KLINE_DDL)
        row = {
            "code": "000001", "date": "2026-05-15", "freq": "daily", "adjust": "qfq",
            "open": 10, "high": 11, "low": 9, "close": 10.5,
            "volume": 10, "amount": 10500,
        }
        # 退役 source 一律 reject
        for forbidden in ["akshare_sina", "tdxhub", "mootdx", "eastmoney_direct"]:
            with pytest.raises(ValueError, match="governance v1 reject"):
                upsert_price_rows(conn, [row], source=forbidden)

        # HS300 allowlist accept
        hs300 = {
            "code": "000300", "date": "2026-05-15", "freq": "daily", "adjust": "qfq",
            "open": 3500, "high": 3550, "low": 3480, "close": 3520,
            "volume": 100.0, "amount": 100.0 * 100 * 3520,
        }
        n = upsert_price_rows(conn, [hs300], source="akshare_csindex_hs300")
        assert n == 1
    finally:
        conn.close()
