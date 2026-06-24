"""kline_completeness 门 clean-vs-source 口径单测 (2026-06-24 cry-wolf 修复).

验证: 门验的是"clean 无损保住 source 行", 与交易日历/停牌无关。
- clean == source → PASS (即便相对全交易日历有"缺口"=停牌/退市, 也不误报)
- clean 丢了 source 有的行 → FAIL (真正的 M2 变换丢行 bug)
red→green: 旧口径(clean-vs-calendar)会对停牌股误报 FAIL; 新口径对同样数据 PASS。
"""
from __future__ import annotations

import duckdb

from services.data_audit import _check_kline_completeness


def _make_conn() -> duckdb.DuckDBPyConnection:
    """构造带 tushare_raw + market 两个 attached 库的 conn (镜像 _open_conn 的别名)。"""
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS tushare_raw")
    conn.execute("ATTACH ':memory:' AS market")
    conn.execute(
        "CREATE TABLE tushare_raw.raw_tushare_daily (ts_code VARCHAR, trade_date VARCHAR, close DOUBLE)"
    )
    conn.execute(
        "CREATE TABLE market.v_price_kline_qfq (code VARCHAR, date VARCHAR, freq VARCHAR, adjust VARCHAR, close DOUBLE)"
    )
    return conn


def _seed_source(conn: duckdb.DuckDBPyConnection) -> None:
    # 000001.SZ 源有 3 个交易日 (20240102/03/04); 注意 0103 故意"缺" 20240103 在日历上(模拟停牌)
    conn.execute(
        "INSERT INTO tushare_raw.raw_tushare_daily VALUES "
        "('000001.SZ','20240102',10.0), ('000001.SZ','20240104',10.2)"
    )


def test_clean_lossless_vs_source_passes_even_with_calendar_gap() -> None:
    """clean 完整保住 source 的 2 行 (中间 20240103 源就没有=停牌) → PASS, 不因日历缺口误报。"""
    conn = _make_conn()
    _seed_source(conn)
    conn.execute(
        "INSERT INTO market.v_price_kline_qfq VALUES "
        "('000001','2024-01-02','daily','qfq',10.0), ('000001','2024-01-04','daily','qfq',10.2)"
    )
    result = _check_kline_completeness(conn)
    assert result.status == "PASS", result.detail
    assert "lossless" in result.detail


def test_clean_drops_source_row_fails() -> None:
    """clean 丢了 source 有的 20240104 行 → FAIL (真正的 M2 非无损 bug)。"""
    conn = _make_conn()
    _seed_source(conn)
    conn.execute(
        "INSERT INTO market.v_price_kline_qfq VALUES "
        "('000001','2024-01-02','daily','qfq',10.0)"  # 缺 20240104
    )
    result = _check_kline_completeness(conn)
    assert result.status == "FAIL", result.detail
    assert "000001" in result.detail and "lost" in result.detail


def test_source_only_code_not_in_clean_universe_ignored() -> None:
    """源有但 clean 宇宙里根本没有的股 (如北交所未建 clean) 不算 clean-loss → 仍 PASS。"""
    conn = _make_conn()
    _seed_source(conn)
    conn.execute(  # clean 完整保住 000001 的两行
        "INSERT INTO market.v_price_kline_qfq VALUES "
        "('000001','2024-01-02','daily','qfq',10.0), ('000001','2024-01-04','daily','qfq',10.2)"
    )
    conn.execute(  # 源里另有 830001.BJ (北交所), clean 宇宙没有它
        "INSERT INTO tushare_raw.raw_tushare_daily VALUES ('830001.BJ','20240102',5.0)"
    )
    result = _check_kline_completeness(conn)
    assert result.status == "PASS", result.detail
