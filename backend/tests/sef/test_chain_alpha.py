"""SEF chain_alpha 单元测试：closed/open 链 PnL 计算 + 幂等写入."""

from __future__ import annotations

import sqlite3

import pytest

from services.sef.chain_alpha import (
    _compute_follow_metrics,
    _compute_inst_pnl,
    backfill_chain_alpha,
    link_events_to_chains,
)
from services.sef.schema import migrate_phase1


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
            notice_date TEXT,
            event_type TEXT NOT NULL,
            PRIMARY KEY (institution_id, stock_code, report_date)
        );
        CREATE TABLE research_holding_chains (
            institution_id TEXT,
            stock_code TEXT,
            chain_id INTEGER,
            chain_start_date TEXT,
            chain_end_date TEXT,
            chain_status TEXT,
            chain_days INTEGER,
            event_sequence TEXT,
            event_count INTEGER,
            entry_inst_cost REAL,
            exit_inst_cost REAL,
            chain_inst_gain_pct REAL,
            entry_follow_price REAL,
            entry_premium_pct REAL,
            follow_gain_30d REAL,
            follow_gain_60d REAL,
            follow_gain_120d REAL,
            max_drawdown_30d REAL,
            industry_l1 TEXT,
            industry_l2 TEXT,
            industry_l3 TEXT,
            PRIMARY KEY (institution_id, stock_code, chain_id)
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


def _seed_kline(mkt, code, rows):
    """rows = [(date, open, high, low, close)]"""
    mkt.executemany(
        "INSERT INTO price_kline(code, date, freq, adjust, open, high, low, close, volume, amount) "
        "VALUES(?,?,'daily','qfq',?,?,?,?,0,0)",
        [(code, d, o, h, l, c) for d, o, h, l, c in rows],
    )
    mkt.commit()


def test_compute_follow_metrics_basic():
    series = [
        ("2024-01-02", 10.0, 10.5, 9.8),
        ("2024-01-03", 10.8, 11.0, 10.5),
        ("2024-01-04", 10.2, 10.4, 9.6),  # 回撤
        ("2024-01-05", 12.0, 12.3, 11.8),  # 新高
    ]
    pnl, dd, eval_p, eval_d = _compute_follow_metrics(10.0, series)
    assert pnl == pytest.approx((12.0 / 10.0 - 1) * 100, rel=1e-6)
    # 回撤: peak=11.0, trough=9.6 -> (9.6/11.0 - 1)*100 ≈ -12.73
    assert dd is not None and dd < 0
    assert eval_p == 12.0
    assert eval_d == "2024-01-05"


def test_compute_follow_metrics_empty_series():
    pnl, dd, ep, ed = _compute_follow_metrics(10.0, [])
    assert pnl is None and dd is None and ep is None and ed is None


def test_compute_follow_metrics_zero_entry():
    pnl, dd, ep, ed = _compute_follow_metrics(0, [("2024-01-02", 10, 10, 10, 10)])
    assert pnl is None


def test_compute_inst_pnl():
    assert _compute_inst_pnl(10.0, 12.0) == pytest.approx(20.0)
    assert _compute_inst_pnl(None, 12.0) is None
    assert _compute_inst_pnl(10.0, None) is None
    assert _compute_inst_pnl(0, 12.0) is None


def test_backfill_closed_and_open(conn, mkt_conn):
    # closed 链: 2024-01-02 ~ 2024-02-02
    conn.execute(
        """
        INSERT INTO research_holding_chains(
            institution_id, stock_code, chain_id, chain_start_date, chain_end_date,
            chain_status, chain_days, event_sequence, event_count,
            entry_inst_cost, exit_inst_cost, chain_inst_gain_pct,
            entry_follow_price, entry_premium_pct,
            follow_gain_30d, follow_gain_60d, follow_gain_120d,
            max_drawdown_30d, industry_l1, industry_l2, industry_l3
        ) VALUES(
            'i1','000001',1,'2024-01-02','2024-02-02','closed',31,
            'new_entry→exit',2, 8.0, 10.0, 25.0, 8.0, 0.0,
            10.0, 15.0, 20.0, -3.0, 'T01','T0101','T010101'
        )
        """
    )
    # open 链: 2024-03-01 ~ 还在持有
    conn.execute(
        """
        INSERT INTO research_holding_chains(
            institution_id, stock_code, chain_id, chain_start_date, chain_end_date,
            chain_status, chain_days, event_sequence, event_count,
            entry_inst_cost, exit_inst_cost, chain_inst_gain_pct,
            entry_follow_price, entry_premium_pct,
            follow_gain_30d, follow_gain_60d, follow_gain_120d,
            max_drawdown_30d, industry_l1, industry_l2, industry_l3
        ) VALUES(
            'i1','000001',2,'2024-03-01',NULL,'open',30,
            'new_entry',1, 9.0, NULL, NULL, 9.0, 0.0,
            NULL, NULL, NULL, NULL, 'T01','T0101','T010101'
        )
        """
    )
    _seed_kline(
        mkt_conn,
        "000001",
        [
            ("2024-01-02", 8.0, 8.2, 7.9, 8.0),
            ("2024-01-15", 9.5, 9.8, 9.2, 9.5),
            ("2024-02-02", 10.5, 10.8, 10.0, 10.5),
            ("2024-03-01", 9.0, 9.2, 8.8, 9.0),
            ("2024-03-15", 11.0, 11.5, 10.5, 11.0),
            ("2024-04-20", 12.6, 12.9, 12.3, 12.6),
        ],
    )

    stats = backfill_chain_alpha(conn, mkt_conn, as_of_date="2024-04-20")
    assert stats["total_chains"] == 2
    assert stats["closed"] == 1
    assert stats["open"] == 1
    rows = conn.execute(
        "SELECT research_chain_id, status, chain_follow_pnl, chain_inst_pnl FROM fact_chain_alpha_truth"
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    # closed PnL = (10.5/8.0 - 1)*100 = 31.25
    assert by_id[1][1] == "closed"
    assert by_id[1][2] == pytest.approx(31.25, rel=1e-3)
    # inst pnl = (10/8 - 1)*100 = 25
    assert by_id[1][3] == pytest.approx(25.0, rel=1e-3)
    # open PnL = (12.6/9.0 - 1)*100 = 40
    assert by_id[2][1] == "open"
    assert by_id[2][2] == pytest.approx(40.0, rel=1e-3)
    # open chain 无 exit cost -> inst_pnl 为 None
    assert by_id[2][3] is None


def test_backfill_is_idempotent(conn, mkt_conn):
    conn.execute(
        """
        INSERT INTO research_holding_chains(
            institution_id, stock_code, chain_id, chain_start_date, chain_end_date,
            chain_status, chain_days, event_sequence, event_count,
            entry_inst_cost, exit_inst_cost, chain_inst_gain_pct,
            entry_follow_price, entry_premium_pct,
            follow_gain_30d, follow_gain_60d, follow_gain_120d,
            max_drawdown_30d, industry_l1, industry_l2, industry_l3
        ) VALUES(
            'i1','000001',1,'2024-01-02','2024-01-15','closed',13,'new_entry→exit',2,
            8.0, 9.5, 18.75, 8.0, 0.0, NULL, NULL, NULL, NULL, 'T01','T0101','T010101'
        )
        """
    )
    _seed_kline(
        mkt_conn,
        "000001",
        [
            ("2024-01-02", 8.0, 8.2, 7.9, 8.0),
            ("2024-01-15", 9.5, 9.8, 9.2, 9.5),
        ],
    )
    s1 = backfill_chain_alpha(conn, mkt_conn, as_of_date="2024-01-15")
    s2 = backfill_chain_alpha(conn, mkt_conn, as_of_date="2024-01-15")
    assert s1["written"] == s2["written"] == 1
    n = conn.execute("SELECT COUNT(*) FROM fact_chain_alpha_truth").fetchone()[0]
    assert n == 1  # UPSERT 不重复插入


def test_link_events_to_chains(conn, mkt_conn):
    conn.execute(
        """
        INSERT INTO research_holding_chains(
            institution_id, stock_code, chain_id, chain_start_date, chain_end_date,
            chain_status, chain_days, event_sequence, event_count,
            entry_inst_cost, exit_inst_cost, chain_inst_gain_pct,
            entry_follow_price, entry_premium_pct,
            follow_gain_30d, follow_gain_60d, follow_gain_120d,
            max_drawdown_30d, industry_l1, industry_l2, industry_l3
        ) VALUES(
            'i1','000001',7,'2024-01-01','2024-03-01','closed',60,'seq',2,
            10,12,20, 10, 0, NULL, NULL, NULL, NULL, 'T01','T0101','T010101'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO fact_institution_event(institution_id, stock_code, report_date, notice_date, event_type)
        VALUES('i1','000001','2024-02-15','2024-02-15','increase')
        """
    )
    n = link_events_to_chains(conn)
    assert n >= 1
    row = conn.execute(
        "SELECT chain_id FROM fact_institution_event WHERE institution_id='i1'"
    ).fetchone()
    assert row[0] == 7
