"""M2 Stage C: build_etf_kline_qfq_tushare 复权公式合成数据单测。

为何合成不用真实 510300: 真实分红除息日 qfq 收益 = 当日**真实总收益**(市场涨跌叠加分红调整),
非恒 0 (实测 510300 20240118 当日市场真涨 +1.46%, qfq 收益=+1.46% 而非 0)。
只有"纯分红除息(raw 跌幅=分红, 无真实涨跌)"时 qfq 收益才≈0 → 合成此场景隔离验证公式:
  qfq = close × adj_factor / latest_adj_per_code (rebase to latest, 同 A股约定)
  除息日 adj 跳变把原始除息跌调回总收益 (mootdx 不调=显原始跌=bug, tushare 对)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb  # noqa: rule-compliance: ok evidence=单测内存库合成数据, 不碰真实库 (中央adapter仅生产路径)
import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "build_etf_kline_qfq_tushare", REPO / "backend" / "scripts" / "build_etf_kline_qfq_tushare.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _seed(conn):
    """注合成 tr.raw_tushare_fund_daily + fund_adj (build attach=False 时 tr=schema)。
    场景: 159999.SZ 三日, 20240102=纯分红除息日 (raw close 跌 3%, adj 跳 +3%, 无真实涨跌);
          510999.SH adj 恒 2.0 (验 latest 按 code 分区, 非全局)。"""
    conn.execute("CREATE SCHEMA tr")
    conn.execute("""
        CREATE TABLE tr.raw_tushare_fund_daily (
            ts_code VARCHAR, trade_date VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, vol DOUBLE, amount DOUBLE)
    """)
    conn.execute("CREATE TABLE tr.raw_tushare_fund_adj (ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE)")
    daily = [
        # 159999.SZ: 纯分红除息场景 (无真实涨跌, raw 跌=分红)
        ("159999.SZ", "20240101", 1.0, 1.01, 0.99, 1.000, 100.0, 100.0),
        ("159999.SZ", "20240102", 0.97, 0.98, 0.96, 0.970, 200.0, 194.0),  # 除息日 raw -3%
        ("159999.SZ", "20240103", 0.975, 0.985, 0.97, 0.980, 150.0, 147.0),  # 真实 +1.03%, adj 不变
        # 510999.SH: adj 恒 2.0 (验 latest per-code)
        ("510999.SH", "20240101", 5.0, 5.1, 4.9, 5.000, 300.0, 1500.0),
        ("510999.SH", "20240102", 5.05, 5.15, 4.95, 5.050, 320.0, 1616.0),
        ("510999.SH", "20240103", 5.10, 5.20, 5.00, 5.100, 310.0, 1581.0),
    ]
    conn.executemany("INSERT INTO tr.raw_tushare_fund_daily VALUES (?,?,?,?,?,?,?,?)", daily)
    adj = [
        ("159999.SZ", "20240101", 1.00), ("159999.SZ", "20240102", 1.03), ("159999.SZ", "20240103", 1.03),
        ("510999.SH", "20240101", 2.00), ("510999.SH", "20240102", 2.00), ("510999.SH", "20240103", 2.00),
    ]
    conn.executemany("INSERT INTO tr.raw_tushare_fund_adj VALUES (?,?,?)", adj)


@pytest.fixture()
def built():
    conn = duckdb.connect(":memory:")
    _seed(conn)
    n = _mod.build(conn, attach=False)
    rows = conn.execute(
        f"SELECT code, date, open, high, low, close, volume, amount FROM {_mod.TARGET} ORDER BY code, date"
    ).fetchall()
    by = {(r[0], r[1]): r for r in rows}
    yield n, by
    conn.close()


def test_row_count(built):
    n, _ = built
    assert n == 6  # 2 ETF × 3 日


def test_rebase_to_latest_equals_raw(built):
    """最新日 adj==latest → qfq close == raw close (rebase 约定)。"""
    _, by = built
    assert by[("159999", "2024-01-03")][5] == pytest.approx(0.980, abs=1e-9)  # raw close
    assert by[("510999", "2024-01-03")][5] == pytest.approx(5.100, abs=1e-9)


def test_dividend_ex_day_return_near_zero(built):
    """纯分红除息日 (raw -3%, adj +3%, 无真实涨跌): qfq 收益 ≈ 0 (分红调回总收益)。
    对照 raw 收益 = -3% (mootdx 未复权所显, =bug)。"""
    _, by = built
    c0 = by[("159999", "2024-01-01")][5]  # qfq close
    c1 = by[("159999", "2024-01-02")][5]
    qfq_ret = c1 / c0 - 1
    assert qfq_ret == pytest.approx(0.0, abs=5e-3)  # 总收益≈0 (远离 raw -3%)
    # 证 raw 会显 -3% (mootdx bug): raw_ret = 0.970/1.000 - 1
    assert (0.970 / 1.000 - 1) == pytest.approx(-0.03, abs=1e-9)


def test_non_dividend_day_return_equals_raw(built):
    """无 adj 变化日 (20240103): qfq 收益 == raw 收益 (复权不动真实涨跌)。"""
    _, by = built
    c1 = by[("159999", "2024-01-02")][5]
    c2 = by[("159999", "2024-01-03")][5]
    qfq_ret = c2 / c1 - 1
    raw_ret = 0.980 / 0.970 - 1
    assert qfq_ret == pytest.approx(raw_ret, abs=1e-9)


def test_latest_adj_per_code_not_global(built):
    """latest 按 code 分区: 510999 adj 恒 2.0 → qfq close == raw close (若 latest 全局取 159999 的 1.03 则错)。"""
    _, by = built
    for d, raw_close in [("2024-01-01", 5.000), ("2024-01-02", 5.050), ("2024-01-03", 5.100)]:
        assert by[("510999", d)][5] == pytest.approx(raw_close, abs=1e-9)


def test_units_volume_no_x100_amount_x1000(built):
    """单位对齐 etf_price_kline (mootdx): volume=vol(手, 不×100), amount=amount×1000(千元→元)。"""
    _, by = built
    row = by[("159999", "2024-01-02")]
    assert row[6] == pytest.approx(200.0, abs=1e-9)        # volume == vol (非 200×100)
    assert row[7] == pytest.approx(194.0 * 1000, abs=1e-6)  # amount == 千元×1000


def test_ohlc_all_adjusted_consistently(built):
    """open/high/low/close 同乘 adj/latest (除息日 4 价齐调, 不只 close)。"""
    _, by = built
    r = by[("159999", "2024-01-01")]
    factor = 1.00 / 1.03  # adj[20240101]/latest
    assert r[2] == pytest.approx(1.0 * factor, abs=1e-9)    # open
    assert r[3] == pytest.approx(1.01 * factor, abs=1e-9)   # high
    assert r[4] == pytest.approx(0.99 * factor, abs=1e-9)   # low
    assert r[5] == pytest.approx(1.0 * factor, abs=1e-9)    # close
