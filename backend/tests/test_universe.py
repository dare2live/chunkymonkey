"""Tests for backend/services/universe.py (ST filter added 2026-05-22)."""
import sys
from pathlib import Path
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
    assert "NOT LIKE 'ST%'" in sql
    assert "NOT LIKE '*ST%'" in sql
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

    # 创建模拟 K 线表, 让退市检查用测试数据 (不依赖真实 market.duckdb)
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
