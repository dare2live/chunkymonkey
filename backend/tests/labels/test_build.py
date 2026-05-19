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


def _seed_trading_calendar(conn: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    import datetime as dt

    dates = sorted({dt.date.fromisoformat(r["date"]) for r in rows})
    if not dates:
        return
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    cur = dates[0]
    calendar_rows = []
    while cur <= dates[-1]:
        calendar_rows.append((cur.isoformat(), 1))
        cur += dt.timedelta(days=1)
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, ?)", calendar_rows)


def _make_conn_with_mock_kline(rows: list[dict]) -> duckdb.DuckDBPyConnection:
    """Create in-memory conn with mkt schema + v_price_kline_qfq view (governance v1).

    governance v1: label build reads mkt.v_price_kline_qfq (tier-1 tdxhub primary).
    Test fixture creates price_kline_tdxhub + canonical view (mock production schema).
    """
    conn = duckdb.connect(":memory:")
    _seed_trading_calendar(conn, rows)
    conn.execute("CREATE SCHEMA IF NOT EXISTS mkt")
    conn.execute("""
        CREATE TABLE mkt.price_kline_tdxhub (
            code TEXT, date TEXT, freq TEXT, adjust TEXT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE,
            factor DOUBLE DEFAULT 1.0,
            source TEXT DEFAULT 'tdxhub',
            batch_id TEXT,
            ingested_at TEXT
        )
    """)
    conn.execute("""
        CREATE OR REPLACE VIEW mkt.v_price_kline_qfq AS
        SELECT code, date, freq, adjust, open, high, low, close, volume, amount,
               COALESCE(factor, 1.0) AS factor,
               COALESCE(source, 'tdxhub') AS source_name,
               1::SMALLINT AS source_tier,
               FALSE AS is_fallback
        FROM mkt.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq'
          AND open > 0 AND high > 0 AND low > 0 AND close > 0
          AND volume >= 1e-6 AND amount >= 1e-6
    """)
    conn.executemany(
        "INSERT INTO mkt.price_kline_tdxhub (code, date, freq, adjust, open, high, low, close, volume, amount) "
        "VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?)",
        [
            (r["code"], r["date"], r["open"], r["high"], r["low"], r["close"],
             r["volume"], r["amount"])
            for r in rows
        ],
    )
    return conn


def _run_build_sql(conn, signal_dates, stock_codes, round_trip, build_as_of_date: str = "2099-12-31"):
    """Helper: stage tmp_pit_stock_signal + run _BUILD_SQL (batch redesign 2026-05-19).

    Old test helper used separate tmp_signal_dates + tmp_stocks (CROSS JOIN).
    New build SQL reads tmp_pit_stock_signal (PIT-clean stock-date pairs) directly.
    For non-PIT tests (CROSS JOIN test fixture stays equivalent), produce cartesian product.
    """
    conn.execute("DROP TABLE IF EXISTS tmp_pit_stock_signal")
    conn.execute("CREATE TEMP TABLE tmp_pit_stock_signal(stock_code TEXT, signal_date DATE)")
    # cartesian product (test helper); production uses PIT-filtered pairs
    pairs = [(c, d) for d in signal_dates for c in stock_codes]
    conn.executemany("INSERT INTO tmp_pit_stock_signal VALUES (?, ?)", pairs)
    return conn.execute(_BUILD_SQL, [build_as_of_date, round_trip]).fetchall()


def test_batch_redesign_pit_temporal_conflict_no_leak():
    """Codex review 2026-05-19 a748f11e PIT temporal conflict 单测.

    场景: stock A 在 signal_date 2024-01-03 上市. signal_dates 含 [2024-01-02, 2024-01-03].
    tmp_pit_stock_signal 只含 (A, 2024-01-03) (universe.py 已 filter listed_date<=signal_date).
    panel 输出应只含 signal_date=2024-01-03 行, 2024-01-02 stock A 不应出现.

    防御 batch redesign 引入 PIT leakage: 旧 CROSS JOIN tmp_stocks × tmp_signal_dates 会产 (A, 2024-01-02)
    可能 leakage 未上市行; 新 JOIN tmp_pit_stock_signal 已 PIT-filter, 不引入 leakage row.

    rule-compliance: ok evidence=PIT-temporal-conflict-defense
    """
    rows = [
        # 2024-01-02: A 未上市 (无 K 线), B 已上市
        {"code": "B", "date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 10000},
        # 2024-01-03: A 上市 entry, B 继续
        {"code": "A", "date": "2024-01-03", "open": 20, "high": 21, "low": 19, "close": 20, "volume": 500, "amount": 10000},
        {"code": "B", "date": "2024-01-03", "open": 10.5, "high": 11.5, "low": 10, "close": 11, "volume": 1100, "amount": 11550},
        # forward fills for label calc
        {"code": "A", "date": "2024-01-04", "open": 20.5, "high": 21.5, "low": 20, "close": 21, "volume": 500, "amount": 10500},
        {"code": "B", "date": "2024-01-04", "open": 11, "high": 12, "low": 10.5, "close": 11.5, "volume": 1100, "amount": 12100},
        {"code": "A", "date": "2024-01-05", "open": 21, "high": 22, "low": 20.5, "close": 21.5, "volume": 500, "amount": 10750},
        {"code": "B", "date": "2024-01-05", "open": 11.5, "high": 12.5, "low": 11, "close": 12, "volume": 1100, "amount": 12650},
    ]
    conn = _make_conn_with_mock_kline(rows)
    try:
        # tmp_pit_stock_signal 显式只含 (A, 2024-01-03) + B in both — simulate PIT universe filter result
        conn.execute("DROP TABLE IF EXISTS tmp_pit_stock_signal")
        conn.execute("CREATE TEMP TABLE tmp_pit_stock_signal(stock_code TEXT, signal_date DATE)")
        conn.executemany(
            "INSERT INTO tmp_pit_stock_signal VALUES (?, ?)",
            [("B", "2024-01-02"), ("A", "2024-01-03"), ("B", "2024-01-03")],
        )
        out_rows = conn.execute(_BUILD_SQL, ["2099-12-31", compute_round_trip_cost_pct(_TX)]).fetchall()
        # Extract (stock_code, signal_date) pairs from output
        pairs = {(r[0], str(r[1])) for r in out_rows}
        # A on 2024-01-02 must NOT appear (未上市)
        assert ("A", "2024-01-02") not in pairs, "PIT leakage: A 在 2024-01-02 未上市却出现在 panel"
        # B on both dates + A only on 2024-01-03 should appear
        assert ("B", "2024-01-02") in pairs
        assert ("B", "2024-01-03") in pairs
        assert ("A", "2024-01-03") in pairs
    finally:
        conn.close()


def test_ddl_creates_table():
    conn = duckdb.connect(":memory:")
    conn.execute(LABEL_PANEL_DDL)
    n_cols = conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='mart_p0a_label_panel'"
    ).fetchone()[0]
    assert n_cols == 28  # 28 fields per ddl


def test_normal_path_all_horizons_have_label():
    """Entry + 5d + 10d + 20d 全部正常成交 → label 算对."""
    # 22 个 trade day: D0..D21
    kline = []
    for i in range(22):
        date_str = f"2024-01-{(i % 28) + 1:02d}" if i < 28 else f"2024-02-{(i - 27):02d}"
        # governance v1: volume unit=lots, vwap = amount / (volume * 100)
        # 设计: vwap = 10 + i*0.1, volume=10 lots, → amount = vwap * volume * 100
        kline.append({
            "code": "600000", "date": date_str,
            "open": 10.0 + i * 0.1, "high": 10.0 + i * 0.1, "low": 10.0 + i * 0.1,
            "close": 10.0 + i * 0.1,
            "volume": 10.0,
            "amount": (10.0 + i * 0.1) * 10.0 * 100.0,  # vwap=10+i*0.1
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
    # entry_vwap = kline[1].amount / (kline[1].volume * 100) (governance v1)
    expected_entry = kline[1]["amount"] / (kline[1]["volume"] * 100.0)
    expected_5d = kline[1 + 5]["amount"] / (kline[1 + 5]["volume"] * 100.0)
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
        "INSERT INTO mkt.price_kline_tdxhub (code, date, freq, adjust, open, high, low, close, volume, amount) "
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
    assert LABEL_VERSION == "p0a_v3_horizon_governance"


def _make_long_horizon_kline(n_days: int = 100) -> list[dict]:
    import datetime as dt

    out = []
    start = dt.date(2024, 1, 2)
    for i in range(n_days):
        d = start + dt.timedelta(days=i)
        vwap = 10.0 + i * 0.1
        out.append({
            "code": "600000", "date": d.isoformat(),
            "open": vwap, "high": vwap + 0.05, "low": vwap - 0.05, "close": vwap,
            "volume": 10.0,
            "amount": vwap * 10.0 * 100.0,
        })
    return out


def test_label_60d_uses_long_horizon_exit_and_has_coverage():
    """60d label 使用 trading-calendar offset, 且 mock subset 有非空覆盖."""
    import datetime as dt

    kline = _make_long_horizon_kline()
    conn = _make_conn_with_mock_kline(kline)
    rt = compute_round_trip_cost_pct(_TX)
    rows = _run_build_sql(
        conn,
        [kline[i]["date"] for i in range(5)],
        ["600000"],
        rt,
        build_as_of_date="2024-12-31",
    )
    assert len(rows) == 5
    trade_date = dt.date.fromisoformat(kline[0]["date"])
    exit_date_60d = rows[0][17]
    assert exit_date_60d > trade_date + dt.timedelta(days=55)
    not_null_ratio = sum(1 for r in rows if r[20] is not None) / len(rows)
    assert not_null_ratio >= 0.8


def test_label_90d_uses_long_horizon_exit_and_respects_pit_visibility():
    """90d label 在 build_as_of 早于 exit_date+1 时必须为 NULL."""
    kline = _make_long_horizon_kline()
    conn = _make_conn_with_mock_kline(kline)
    rt = compute_round_trip_cost_pct(_TX)
    rows = _run_build_sql(
        conn,
        [kline[0]["date"]],
        ["600000"],
        rt,
        build_as_of_date="2024-12-31",
    )
    assert len(rows) == 1
    r = rows[0]
    expected_entry = kline[1]["amount"] / (kline[1]["volume"] * 100.0)
    expected_90d = kline[91]["amount"] / (kline[91]["volume"] * 100.0)
    assert r[21] is not None
    assert abs(r[24] - ((expected_90d / expected_entry - 1.0) - rt)) < 1e-9

    hidden = _run_build_sql(
        conn,
        [kline[0]["date"]],
        ["600000"],
        rt,
        build_as_of_date=str(r[21]),
    )
    assert hidden[0][24] is None


def _make_conn_with_fallback_view(rows_primary: list[dict], rows_fallback: list[dict]) -> duckdb.DuckDBPyConnection:
    """Codex Q4 FIX: fixture mock 完整 v_price_kline_qfq view (primary UNION fallback NOT EXISTS).

    governance v1 prod view design:
    - primary_rows: price_kline_tdxhub (tier-1 tdxhub, source_tier=1, is_fallback=FALSE)
    - fallback_rows: price_kline (tier-3 allowlist hs300_only, source_tier=3, is_fallback=TRUE)
                     NOT EXISTS in primary (避免重复)
    """
    conn = duckdb.connect(":memory:")
    _seed_trading_calendar(conn, rows_primary + rows_fallback)
    conn.execute("CREATE SCHEMA IF NOT EXISTS mkt")
    conn.execute("""
        CREATE TABLE mkt.price_kline_tdxhub (
            code TEXT, date TEXT, freq TEXT, adjust TEXT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE,
            factor DOUBLE DEFAULT 1.0, source TEXT DEFAULT 'tdxhub',
            batch_id TEXT, ingested_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE mkt.price_kline (
            code TEXT, date TEXT, freq TEXT, adjust TEXT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE,
            factor DOUBLE DEFAULT 1.0, source TEXT,
            batch_id TEXT, ingested_at TEXT
        )
    """)
    conn.execute("""
        CREATE OR REPLACE VIEW mkt.v_price_kline_qfq AS
        WITH primary_rows AS (
            SELECT code, date, freq, adjust, open, high, low, close, volume, amount,
                   COALESCE(factor, 1.0) AS factor,
                   COALESCE(source, 'tdxhub') AS source_name,
                   1::SMALLINT AS source_tier, FALSE AS is_fallback
            FROM mkt.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq'
              AND open > 0 AND high > 0 AND low > 0 AND close > 0
              AND volume >= 1e-6 AND amount >= 1e-6
        ),
        fallback_rows AS (
            SELECT f.code, f.date, f.freq, f.adjust, f.open, f.high, f.low, f.close, f.volume, f.amount,
                   1.0 AS factor,
                   COALESCE(f.source, 'akshare_csindex_hs300') AS source_name,
                   3::SMALLINT AS source_tier, TRUE AS is_fallback
            FROM mkt.price_kline f
            WHERE f.freq='daily' AND f.adjust='qfq'
              AND f.open > 0 AND f.high > 0 AND f.low > 0 AND f.close > 0
              AND f.volume >= 1e-6 AND f.amount >= 1e-6
              AND NOT EXISTS (
                  SELECT 1 FROM mkt.price_kline_tdxhub p
                  WHERE p.code = f.code AND p.date = f.date
                    AND p.freq = f.freq AND p.adjust = f.adjust
              )
        )
        SELECT * FROM primary_rows
        UNION ALL
        SELECT * FROM fallback_rows
    """)
    if rows_primary:
        conn.executemany(
            "INSERT INTO mkt.price_kline_tdxhub (code, date, freq, adjust, open, high, low, close, volume, amount) "
            "VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?)",
            [(r["code"], r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], r["amount"])
             for r in rows_primary],
        )
    if rows_fallback:
        conn.executemany(
            "INSERT INTO mkt.price_kline (code, date, freq, adjust, open, high, low, close, volume, amount, source) "
            "VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?, 'akshare_csindex_hs300')",
            [(r["code"], r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], r["amount"])
             for r in rows_fallback],
        )
    return conn


def test_label_build_uses_hs300_fallback_when_no_tdxhub_primary():
    """Codex Q4 FIX: HS300 fallback path 进入 entry/exit kline 时 vwap=amount/(volume*100) 公式正确."""
    import datetime
    # primary 给个 dummy 跑 trading_days CTE
    rows_primary = [
        {"code": "600000", "date": d.strftime("%Y-%m-%d"),
         "open": 10.0, "high": 10.05, "low": 9.95, "close": 10.0,
         "volume": 10.0, "amount": 10.0*10.0*100.0}
        for d in (datetime.date(2024,1,2) + datetime.timedelta(days=i) for i in range(30))
    ]
    # fallback: HS300 000300 数据 (只 primary 没有的 (code,date) 才用 fallback)
    rows_fallback = [
        {"code": "000300", "date": (datetime.date(2024,1,2)+datetime.timedelta(days=i)).strftime("%Y-%m-%d"),
         "open": 3500.0, "high": 3520.0, "low": 3480.0, "close": 3510.0,
         "volume": 100.0, "amount": 100.0*100.0*3510.0}  # vwap = 3510
        for i in range(30)
    ]
    conn = _make_conn_with_fallback_view(rows_primary, rows_fallback)
    rt = compute_round_trip_cost_pct(_TX)
    rows = _run_build_sql(conn, ["2024-01-02"], ["000300"], rt)
    # 000300 走 fallback 路径 (HS300 allowlist), entry_vwap = 3510 (governance v1 公式)
    assert len(rows) == 1
    r = rows[0]
    # entry_vwap (col index 3) ≈ 3510
    assert r[3] is not None
    assert abs(r[3] - 3510.0) < 1.0, f"entry_vwap={r[3]} should be ~3510 (HS300 fallback)"
