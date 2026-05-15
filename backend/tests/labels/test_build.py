"""P0a label panel build script 单测.

用 in-memory DuckDB + mock 数据验证:
- 正常路径: entry + 5/10/20 都有 K 线 → label 算对
- 停牌 entry → unable_at_entry=True, 所有 horizon label=None
- 一字板 entry → unable_at_entry=True
- 停牌 5d exit → 仅 5d label=None
"""
from __future__ import annotations

import duckdb
import pytest

from services.labels.build import _BUILD_SQL, LABEL_VERSION
from services.labels.cost_after import compute_round_trip_cost_pct
from services.labels.ddl import LABEL_PANEL_DDL
from services.paper_sim.config import TxCostConfig


_TX = TxCostConfig(
    commission_pct=0.00025,
    commission_min_cny=5,
    stamp_duty_sell_pct=0.0005,
    transfer_fee_pct=0.00001,
    exchange_fee_pct=0.0000341,
    regulatory_fee_pct=0.00002,
    slippage_pct=0.0008,
    large_order_surcharge_pct=0.0015,
    large_order_adv_threshold_pct=0.03,
)


def _make_conn_with_mock_kline(rows: list[dict]) -> duckdb.DuckDBPyConnection:
    """Create in-memory conn with mkt schema + price_kline table populated."""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SCHEMA IF NOT EXISTS mkt")
    conn.execute("""
        CREATE TABLE mkt.price_kline (
            code TEXT, date TEXT, freq TEXT, adjust TEXT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE
        )
    """)
    conn.executemany(
        "INSERT INTO mkt.price_kline (code, date, freq, adjust, open, high, low, close, volume, amount) "
        "VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?)",
        [
            (r["code"], r["date"], r["open"], r["high"], r["low"], r["close"],
             r["volume"], r["amount"])
            for r in rows
        ],
    )
    return conn


def _run_build_sql(conn, signal_dates, stock_codes, round_trip):
    """Helper: stage tmp tables + run _BUILD_SQL."""
    conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
    conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
    conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
    conn.execute("DROP TABLE IF EXISTS tmp_stocks")
    conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
    conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])
    return conn.execute(_BUILD_SQL, [round_trip, round_trip, round_trip]).fetchall()


def test_ddl_creates_table():
    conn = duckdb.connect(":memory:")
    conn.execute(LABEL_PANEL_DDL)
    n_cols = conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='mart_p0a_label_panel'"
    ).fetchone()[0]
    assert n_cols == 20  # 20 fields per ddl


def test_normal_path_all_horizons_have_label():
    """Entry + 5d + 10d + 20d 全部正常成交 → label 算对."""
    # 22 个 trade day: D0..D21
    kline = []
    for i in range(22):
        date_str = f"2024-01-{(i % 28) + 1:02d}" if i < 28 else f"2024-02-{(i - 27):02d}"
        kline.append({
            "code": "600000", "date": date_str,
            "open": 10.0 + i * 0.1, "high": 10.0 + i * 0.1, "low": 10.0 + i * 0.1,
            "close": 10.0 + i * 0.1,
            "volume": 1000.0,
            "amount": 10000.0 + i * 100.0,  # VWAP = 10 + i*0.1
        })
    # 让 high>low 避免一字板误判
    for k in kline:
        k["high"] = k["open"] + 0.05
        k["low"] = k["open"] - 0.05

    conn = _make_conn_with_mock_kline(kline)
    rt = compute_round_trip_cost_pct(_TX)
    rows = _run_build_sql(conn, [kline[0]["date"]], ["600000"], rt)
    assert len(rows) == 1
    r = rows[0]
    # entry_vwap = kline[1].amount / kline[1].volume
    expected_entry = kline[1]["amount"] / kline[1]["volume"]
    expected_5d = kline[1 + 5]["amount"] / kline[1 + 5]["volume"]
    assert abs(r[3] - expected_entry) < 1e-9
    assert r[4] is False  # unable_at_entry
    assert abs(r[6] - expected_5d) < 1e-9
    assert r[7] is False
    assert abs(r[8] - ((expected_5d / expected_entry - 1.0) - rt)) < 1e-9


def test_entry_suspended_all_labels_none():
    """T+1 停牌 (无 K 线) → unable_at_entry=True, 所有 horizon label=None."""
    kline = []
    # Trading day index: 0 (signal), skip 1 (停牌), 2..21 都正常
    for i in range(22):
        if i == 1:
            continue  # T+1 停牌, 不写 K 线
        date_str = f"2024-01-{(i % 28) + 1:02d}" if i < 28 else f"2024-02-{(i - 27):02d}"
        kline.append({
            "code": "600000", "date": date_str,
            "open": 10.0 + i * 0.1, "high": 10.0 + i * 0.1 + 0.05,
            "low": 10.0 + i * 0.1 - 0.05, "close": 10.0 + i * 0.1,
            "volume": 1000.0,
            "amount": 10000.0 + i * 100.0,
        })

    conn = _make_conn_with_mock_kline(kline)
    rt = compute_round_trip_cost_pct(_TX)
    # 注意: trading_day_rank 是基于实际有 K 线的日子, signal_rk=1 (D0), entry_rk=2
    # 但 entry_rk=2 对应原 D2 (因为停牌日 D1 没进 trading_days), 所以 entry_date=D2
    # 这里的语义是: trading_day_rank 是 K 线 distinct date 的 rank.
    # mask 检测: entry_date=D2 应该有 K 线 → unable_at_entry=False (但 label 不准)
    # 改为更可控测试: 在 D2 也不写 K 线模拟连续停牌
    # 或者验证 D1 unable 路径需要让 trading_days 含 D1 但 K 线缺该股
    # 此处先验证: 当 entry_date 没该股 K 线时, unable_at_entry=True

    # 增加另一只股 在 D1 有 K 线, 让 trading_days 包含 D1
    conn.execute(
        "INSERT INTO mkt.price_kline (code, date, freq, adjust, open, high, low, close, volume, amount) "
        "VALUES ('000001', '2024-01-02', 'daily', 'qfq', 1, 1.05, 0.95, 1, 100, 1000)"
    )

    rows = _run_build_sql(conn, ["2024-01-01"], ["600000"], rt)
    assert len(rows) == 1
    r = rows[0]
    assert r[4] is True  # unable_at_entry
    assert r[8] is None  # fwd_cost_after_5d
    assert r[12] is None  # fwd_cost_after_10d
    assert r[16] is None  # fwd_cost_after_20d


def test_one_word_limit_up_blocks_entry():
    """T+1 一字板 (open=high=low=close & volume>0) → unable_at_entry=True."""
    kline = []
    for i in range(22):
        date_str = f"2024-01-{(i % 28) + 1:02d}" if i < 28 else f"2024-02-{(i - 27):02d}"
        if i == 1:  # 一字涨停
            kline.append({
                "code": "600000", "date": date_str,
                "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
                "volume": 100.0, "amount": 1100.0,
            })
        else:
            kline.append({
                "code": "600000", "date": date_str,
                "open": 10.0 + i * 0.1, "high": 10.0 + i * 0.1 + 0.05,
                "low": 10.0 + i * 0.1 - 0.05, "close": 10.0 + i * 0.1,
                "volume": 1000.0, "amount": 10000.0 + i * 100.0,
            })
    conn = _make_conn_with_mock_kline(kline)
    rt = compute_round_trip_cost_pct(_TX)
    rows = _run_build_sql(conn, [kline[0]["date"]], ["600000"], rt)
    assert len(rows) == 1
    r = rows[0]
    assert r[4] is True   # unable_at_entry (一字板)
    assert r[8] is None   # 5d label=None
    assert r[12] is None
    assert r[16] is None


def test_only_exit_5d_unable_other_horizons_ok():
    """T+1 + 5 日 exit 一字板, 10/20 正常 → 仅 5d=None."""
    kline = []
    for i in range(22):
        date_str = f"2024-01-{(i % 28) + 1:02d}" if i < 28 else f"2024-02-{(i - 27):02d}"
        if i == 6:  # T+1 + 5d (entry@rk=1 + 5 = rk=6)
            kline.append({
                "code": "600000", "date": date_str,
                "open": 11.5, "high": 11.5, "low": 11.5, "close": 11.5,
                "volume": 100.0, "amount": 1150.0,
            })
        else:
            kline.append({
                "code": "600000", "date": date_str,
                "open": 10.0 + i * 0.1, "high": 10.0 + i * 0.1 + 0.05,
                "low": 10.0 + i * 0.1 - 0.05, "close": 10.0 + i * 0.1,
                "volume": 1000.0, "amount": 10000.0 + i * 100.0,
            })
    conn = _make_conn_with_mock_kline(kline)
    rt = compute_round_trip_cost_pct(_TX)
    rows = _run_build_sql(conn, [kline[0]["date"]], ["600000"], rt)
    assert len(rows) == 1
    r = rows[0]
    assert r[4] is False   # entry ok
    assert r[7] is True    # 5d exit unable
    assert r[8] is None    # 5d label
    assert r[11] is False  # 10d exit ok
    assert r[12] is not None
    assert r[15] is False  # 20d exit ok
    assert r[16] is not None


def test_label_version_constant():
    assert LABEL_VERSION == "p0a_v1"
