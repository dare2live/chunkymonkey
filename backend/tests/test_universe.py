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
