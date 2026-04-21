"""SEF Triple Barrier 单元测试：upper/lower/time 三种 label 各自能被触发."""

from __future__ import annotations

import sqlite3

import pytest

from services.sef.schema import migrate_phase1
from services.sef.triple_barrier import _label_chain, apply_triple_barrier


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE fact_institution_event (
            institution_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            report_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            PRIMARY KEY (institution_id, stock_code, report_date)
        );
        CREATE TABLE research_holding_chains (
            institution_id TEXT, stock_code TEXT, chain_id INTEGER,
            PRIMARY KEY(institution_id, stock_code, chain_id)
        );
        """
    )
    migrate_phase1(c)
    yield c
    c.close()


@pytest.fixture
def mkt_conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE price_kline (
            code TEXT, date TEXT, freq TEXT, adjust TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL
        );
        """
    )
    yield c
    c.close()


def _mk_series(dates_prices):
    """helper: [(date, hi, lo, close)]"""
    return [(d, c, hi, lo) for d, hi, lo, c in dates_prices]


def test_label_chain_upper_hit():
    # ATR 10%; upper at 12.0 (k_up=2 × 0.1 → +20%)
    series = _mk_series([
        ("2024-01-02", 10.0, 9.8, 9.9),
        ("2024-01-10", 12.5, 11.8, 12.0),  # hi>=12.0
    ])
    res = _label_chain(10.0, series, k_up=2.0, k_dn=1.0, atr_pct=0.1, horizon_days=60)
    assert res["label"] == "upper"
    assert res["upper"] == 1


def test_label_chain_lower_hit():
    # ATR 10% → lower = 10 * (1 - 1*0.1) = 9.0
    series = _mk_series([
        ("2024-01-02", 10.0, 9.95, 9.98),
        ("2024-01-05", 9.5, 8.9, 9.1),  # lo <= 9.0
    ])
    res = _label_chain(10.0, series, k_up=2.0, k_dn=1.0, atr_pct=0.1, horizon_days=60)
    assert res["label"] == "lower"
    assert res["lower"] == 1


def test_label_chain_time_hit():
    series = _mk_series([
        ("2024-01-02", 10.0, 9.9, 10.0),
        ("2024-01-03", 10.1, 9.9, 10.0),
    ])
    res = _label_chain(10.0, series, k_up=2.0, k_dn=1.0, atr_pct=0.1, horizon_days=60)
    assert res["label"] == "time"
    assert res["time"] == 1


def test_apply_triple_barrier_end_to_end(conn, mkt_conn):
    # 插入一条 chain truth
    conn.execute(
        """
        INSERT INTO fact_chain_alpha_truth(institution_id, stock_code, research_chain_id,
            entry_date, eval_date, status, entry_price
        ) VALUES('i1','600519',1,'2024-01-10','2024-02-10','closed',100.0)
        """
    )
    # entry 前 14 天 ATR 约 2% (hi-lo)/close
    for i in range(15):
        d = f"2023-12-{15+i:02d}"
        if i < 10:
            mkt_conn.execute(
                "INSERT INTO price_kline(code,date,freq,adjust,open,high,low,close,volume,amount) "
                "VALUES('600519',?,?,?,?,?,?,?,0,0)",
                (d, "daily", "qfq", 100, 101.0, 99.0, 100.0),
            )
    # entry 当日起上涨触发 upper (ATR 2% × k_up 2 = 4% → upper=104)
    fwd = [
        ("2024-01-10", 100.0, 100.5, 99.5, 100.0),
        ("2024-01-11", 101.0, 103.0, 100.0, 102.5),
        ("2024-01-12", 103.0, 105.0, 102.5, 104.8),  # hi>=104 触发 upper
    ]
    for d, o, hi, lo, cl in fwd:
        mkt_conn.execute(
            "INSERT INTO price_kline(code,date,freq,adjust,open,high,low,close,volume,amount) "
            "VALUES('600519',?,?,?,?,?,?,?,0,0)",
            (d, "daily", "qfq", o, hi, lo, cl),
        )
    mkt_conn.commit()

    stats = apply_triple_barrier(conn, mkt_conn, horizon_days=60, atr_window=14)
    assert stats["labeled"] == 1
    row = conn.execute(
        "SELECT tb_label, tb_upper_hit, tb_time_horizon_days FROM fact_chain_alpha_truth"
    ).fetchone()
    assert row[0] == "upper"
    assert row[1] == 1
    assert row[2] == 60
