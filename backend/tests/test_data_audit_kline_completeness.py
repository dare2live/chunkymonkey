"""kline_completeness 门 clean-vs-source 口径单测 (2026-06-24 cry-wolf 修复;
2026-09-08 source 改判 raw_tushare_daily -> canonical_nominal_ohlcv_daily)。

验证: 门验的是"clean 无损保住 source 行", 与交易日历/停牌无关。
- clean == source → PASS (即便相对全交易日历有"缺口"=停牌/退市, 也不误报)
- clean 丢了 source 有的行 → FAIL (真正的 M2 变换丢行 bug)
red→green: 旧口径(clean-vs-calendar)会对停牌股误报 FAIL; 新口径对同样数据 PASS。

2026-09-08 新增: source_raw_table 改判前后的回归测. 实测 raw_tushare_daily 已冻结在
20260716, 之后任何 clean 丢行旧配置都看不见 (source 本身缺那几天的行, EXCEPT 无从比起)。
本文件的 fixture 现在建 canonical_nominal_ohlcv_daily (trade_date 为原生 DATE 类型,
对齐生产 schema), 且新增一条测试直接复现生产事故的形状: 某天多只股在 source 都有行,
clean 整天丢掉其中一部分 (不是 0 行, 是"有但不全"——比 COUNT(*)>0 更隐蔽), 门必须转红。
"""
from __future__ import annotations

import duckdb

from services.data_audit import _check_kline_completeness


def _make_conn() -> duckdb.DuckDBPyConnection:
    """构造带 tushare_raw + market 两个 attached 库的 conn (镜像 _open_conn 的别名)。

    canonical_nominal_ohlcv_daily.trade_date 是原生 DATE 类型 (对齐生产 schema; 已冻结的
    raw_tushare_daily 才是 YYYYMMDD 字符串), 门内部用 source_raw_date_kind=date_type 显式
    strftime 对齐, 不靠隐式转换。
    """
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS tushare_raw")
    conn.execute("ATTACH ':memory:' AS market")
    conn.execute(
        "CREATE TABLE tushare_raw.canonical_nominal_ohlcv_daily "
        "(ts_code VARCHAR, trade_date DATE, close DOUBLE)"
    )
    conn.execute(
        "CREATE TABLE market.v_price_kline_qfq (code VARCHAR, date VARCHAR, freq VARCHAR, adjust VARCHAR, close DOUBLE)"
    )
    return conn


def _seed_source(conn: duckdb.DuckDBPyConnection) -> None:
    # 000001.SZ 源有 3 个交易日 (2024-01-02/03/04); 注意源故意"缺" 2024-01-03 (模拟停牌)
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('000001.SZ', DATE '2024-01-02', 10.0), ('000001.SZ', DATE '2024-01-04', 10.2)"
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
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES ('830001.BJ', DATE '2024-01-02', 5.0)"
    )
    result = _check_kline_completeness(conn)
    assert result.status == "PASS", result.detail


def test_partial_day_drop_across_multiple_stocks_fails() -> None:
    """复现生产事故形状 (2026-08-12/08-13 整缺两天/10,257行/5,128只股): 某天 source 对多只股都有
    行, clean 那天只留了一部分 (不是 0 行——如果拿 COUNT(*)>0 或 MAX(date) 判断会误判为"有数据/
    覆盖到了这天", 必须逐 (code,date) 集合比对才抓得住)。三只股都在前一天(01-01)出现过, 确保它们
    都在 clean 宇宙内 (不会被"源有但 clean 宇宙没有的股"规则误排除)。"""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO tushare_raw.canonical_nominal_ohlcv_daily VALUES "
        "('000001.SZ', DATE '2024-01-01', 9.0), ('000002.SZ', DATE '2024-01-01', 19.0), "
        "('000003.SZ', DATE '2024-01-01', 29.0), "
        "('000001.SZ', DATE '2024-01-02', 10.0), ('000002.SZ', DATE '2024-01-02', 20.0), "
        "('000003.SZ', DATE '2024-01-02', 30.0)"
    )
    conn.execute(
        # 01-01 三只股齐全 (建立 clean 宇宙成员资格); 01-02 clean 只留了 000001 一只
        # (COUNT(*)>0, MAX(date) 依旧覆盖到 01-02; 但 000002/000003 那两行被丢——
        # 比 COUNT/MAX 会放行, 比集合才抓得住)
        "INSERT INTO market.v_price_kline_qfq VALUES "
        "('000001','2024-01-01','daily','qfq',9.0), ('000002','2024-01-01','daily','qfq',19.0), "
        "('000003','2024-01-01','daily','qfq',29.0), "
        "('000001','2024-01-02','daily','qfq',10.0)"
    )
    result = _check_kline_completeness(conn)
    assert result.status == "FAIL", result.detail
    assert "000002" in result.detail and "000003" in result.detail
