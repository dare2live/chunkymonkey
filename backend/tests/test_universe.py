"""Tests for backend/services/universe.py (ST filter added 2026-05-22)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.universe import (
    is_active_a_share, is_st_stock, sql_where_active_a_share, sql_where_no_st,
    ACTIVE_A_SHARE_PREFIXES,
)


def test_active_a_share_prefixes_match_spec():
    assert ACTIVE_A_SHARE_PREFIXES == ("60", "00", "30", "68")


def test_is_active_a_share_keep():
    assert is_active_a_share("600000")  # SSE 沪主板
    assert is_active_a_share("000001")  # SZSE 深主板
    assert is_active_a_share("300001")  # SZSE 创业板
    assert is_active_a_share("688001")  # SSE 科创板


def test_is_active_a_share_exclude():
    assert not is_active_a_share("830001")  # 北交所
    assert not is_active_a_share("400001")  # 老三板/新三板
    assert not is_active_a_share("510300")  # ETF
    assert not is_active_a_share("")
    assert not is_active_a_share("X")


def test_is_st_stock():
    assert is_st_stock("ST 股份")
    assert is_st_stock("*ST 退市风险")
    assert not is_st_stock("正常股")
    assert not is_st_stock(None)
    assert not is_st_stock("")
    assert not is_st_stock("XD 除息")


def test_sql_where_active_a_share():
    sql = sql_where_active_a_share("code")
    assert "SUBSTR(code, 1, 2) IN" in sql
    for p in ACTIVE_A_SHARE_PREFIXES:
        assert f"'{p}'" in sql


def test_sql_where_no_st():
    sql = sql_where_no_st("d.stock_name")
    assert "NOT (" in sql
    assert "d.stock_name LIKE 'ST%'" in sql
    assert "d.stock_name LIKE '*ST%'" in sql
    assert "IS NULL" in sql  # tolerate missing JOIN


def test_get_active_universe(tmp_path, monkeypatch):
    """get_active_universe returns set, exclude ST/退市 by default."""
    import duckdb
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    conn.execute("CREATE TABLE dim_all_ever_listed (stock_code VARCHAR, is_active INTEGER)")
    # Insert test stocks
    conn.execute("""
        INSERT INTO dim_active_a_stock VALUES
            ('600001', '正常A'), ('600002', '正常B'),
            ('ST600003', 'ST 测试'), ('600003', 'ST 测试'),
            ('*ST600004', '*ST 测试'), ('600004', '*ST 测试'),
            ('830001', '北交所A'),
            ('000001', '深主板A'), ('300001', '创业板A'), ('688001', '科创A')
    """)
    conn.execute("INSERT INTO dim_all_ever_listed VALUES ('600002', 0)")  # 600002 已退市

    # 创建模拟 K 线关系, 让退市检查用测试数据 (不依赖真实 market.duckdb)
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, freq VARCHAR, date DATE)")
    conn.execute("""
        INSERT INTO price_kline_tdxhub VALUES
            ('600001', 'daily', CURRENT_DATE),
            ('000001', 'daily', CURRENT_DATE),
            ('300001', 'daily', CURRENT_DATE),
            ('688001', 'daily', CURRENT_DATE),
            ('600003', 'daily', CURRENT_DATE),
            ('600004', 'daily', CURRENT_DATE)
    """)
    conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")
    # 600002 没有 K 线 = 真退市

    from services.universe import get_active_universe
    universe = get_active_universe(conn, market_conn=conn)
    # Should keep: 600001, 000001, 300001, 688001 (4 normal stocks)
    # Excludes: 600002 (no recent K-line), 600003+600004 (ST/*ST names), 830001 (prefix)
    assert "600001" in universe
    assert "000001" in universe
    assert "300001" in universe
    assert "688001" in universe
    assert "600002" not in universe  # delisted
    assert "600003" not in universe  # ST
    assert "600004" not in universe  # *ST
    assert "830001" not in universe  # 北交所
    conn.close()


def test_get_active_universe_excludes_index_not_in_dim_active(tmp_path):
    """2026-06-19 身份真相源交集防回退: K线含指数 benchmark (000300 沪深300, 00 前缀过前缀门)
    但不在 dim_active_a_stock (tushare stock_basic 真股清单) → 必被 universe 剔除。
    旧逻辑 K线∩前缀−ST 会让 000300 漏入 universe (根因; red→green)。"""
    import duckdb
    conn = duckdb.connect(str(tmp_path / "test.duckdb"))
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    conn.execute("CREATE TABLE dim_all_ever_listed (stock_code VARCHAR, is_active INTEGER)")
    conn.execute("INSERT INTO dim_active_a_stock VALUES ('600001', '正常A')")  # 真股清单无 000300 指数
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, freq VARCHAR, date DATE)")
    conn.execute("""
        INSERT INTO price_kline_tdxhub VALUES
            ('600001', 'daily', CURRENT_DATE),
            ('000300', 'daily', CURRENT_DATE)
    """)  # K线含真股 + 指数 benchmark (00 前缀)
    conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")
    from services.universe import get_active_universe
    universe = get_active_universe(conn, market_conn=conn)
    assert "600001" in universe
    assert "000300" not in universe  # 指数不在真股清单 → 身份交集剔除 (修复点)
    conn.close()


def test_get_active_universe_requires_market_truth_source(monkeypatch):
    from services.universe import UniverseDataError, get_active_universe

    def fail_market_conn():
        raise RuntimeError("missing market db")

    monkeypatch.setattr("services.market_db.get_market_conn", fail_market_conn)

    with pytest.raises(UniverseDataError, match="K-line market DB"):
        get_active_universe(include_st=True)


def test_get_active_universe_reads_st_mapping_from_reference(tmp_path, monkeypatch):
    """§9 拆库 (2026-06-27): ST/identity mapping 源从 conn(smartmoney) 迁 reference 库 dim_active_a_stock。

    旧契约 "conn 缺 dim → raise" 已变: 现经 security_master.active_codes/active_stock_name_map
    auto-fallback 读 reference (always 可用)。传缺 dim 的 conn 不再 raise — 落 reference 读 ST + identity
    交集 (test code '600001' 不在 reference 真股清单 → 被 identity 交集剔除, 返过滤集非异常)。

    hermetic: 自建 tmp reference 并 monkeypatch resolver.connect_ro, 不依赖真实 data/reference.duckdb
    (CI offline / 空 data 目录下该文件不存在 → 旧版直读真库 IOException)。
    """
    import duckdb
    from services.data_access import resolver
    from services.universe import get_active_universe

    # tmp reference: 真股清单 (含一只真股, 无 600001) → fallback 落它做 identity 交集
    ref_path = tmp_path / "reference.duckdb"
    rc = duckdb.connect(str(ref_path))
    rc.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    rc.execute("INSERT INTO dim_active_a_stock VALUES ('600519', '贵州茅台')")
    rc.close()

    def fake_connect_ro(alias):
        assert alias == "reference"  # 本测试只该 fallback reference 库
        return duckdb.connect(str(ref_path), read_only=True)

    monkeypatch.setattr(resolver, "connect_ro", fake_connect_ro)

    conn = duckdb.connect(str(tmp_path / "test.duckdb"))
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, freq VARCHAR, date DATE)")
    conn.execute("INSERT INTO price_kline_tdxhub VALUES ('600001', 'daily', CURRENT_DATE)")
    conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")

    # §9: 不再 raise (ST mapping 落 reference); 返 set, 600001 非真股经 identity 交集剔除
    result = get_active_universe(conn, market_conn=conn)
    assert isinstance(result, set)
    assert "600001" not in result

    conn.close()


def test_audit_contamination(tmp_path):
    """audit_strategy_universe_contamination detects ST/退市/BSE/ETF in picks."""
    import duckdb
    db = duckdb.connect(":memory:")
    db.execute("CREATE TABLE dim_active_a_stock (stock_code VARCHAR, stock_name VARCHAR)")
    db.execute("CREATE TABLE dim_all_ever_listed (stock_code VARCHAR, is_active INTEGER)")
    db.execute("""
        INSERT INTO dim_active_a_stock VALUES
            ('600001', '正常'), ('600002', 'ST 测试'), ('830001', '北交所')
    """)
    db.execute("INSERT INTO dim_all_ever_listed VALUES ('600003', 0)")
    db.execute("CREATE TABLE picks (model_id VARCHAR, stock_code VARCHAR)")
    db.execute("""
        INSERT INTO picks VALUES
            ('M1', '600001'), ('M1', '600002'), ('M1', '830001'), ('M1', '600003'),
            ('M1', '510300')
    """)
    from services.universe import audit_strategy_universe_contamination
    r = audit_strategy_universe_contamination(db, table='picks', model_id_filter='M1')
    assert r['total_picks'] == 5
    assert r['st_picks'] == 1  # 600002
    assert r['delisted_picks'] == 1  # 600003
    assert r['neeq_picks'] == 1  # 830001
    assert r['etf_picks'] == 1  # 510300


# === 2026-06-17 universe 升交易日历级真相源: 硬验证器 + PIT ST 防回归 ===

def test_classify_exclusion_whitelist_passes():
    from services.universe import classify_exclusion
    for code in ("600000", "000001", "300274", "688981"):
        assert classify_exclusion(code) is None


def test_classify_exclusion_flags_excluded_boards():
    from services.universe import classify_exclusion
    assert classify_exclusion("920819") is not None   # 北交所
    assert classify_exclusion("832000") is not None   # 北交所/三板
    assert classify_exclusion("430139") is not None   # 新三板
    assert classify_exclusion("159915") is not None   # ETF
    assert classify_exclusion("510300") is not None   # ETF


def test_assert_universe_clean_passes_whitelist():
    from services.universe import assert_universe_clean
    assert assert_universe_clean(["600000", "000001", "300274", "688981"]) is True


def test_assert_universe_clean_raises_on_contamination():
    from services.universe import assert_universe_clean, UniverseContaminationError
    with pytest.raises(UniverseContaminationError):
        assert_universe_clean(["600000", "920819"], context="test")
    # 报错应列出污染只数 + 板块
    try:
        assert_universe_clean(["600000", "920819", "159915"])
    except UniverseContaminationError as e:
        msg = str(e)
        assert "2" in msg  # 2 只排除股


def test_is_st_on_pit():
    from services.universe import is_st_on
    cal = {"600519": {"20240101", "20240102"}}
    assert is_st_on("600519", "20240101", cal) is True
    assert is_st_on("600519", "20240601", cal) is False  # PIT: 当日未 ST
    assert is_st_on("000001", "20240101", cal) is False  # 未在日历
