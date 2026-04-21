"""SEF survivorship 单元测试：active / delisted 判定 + 多源合并."""

from __future__ import annotations

import sqlite3

import pytest

from services.sef.schema import migrate_phase1
from services.sef.survivorship import build_dim_all_ever_listed


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE dim_active_a_stock(
            stock_code TEXT PRIMARY KEY, stock_name TEXT
        );
        CREATE TABLE fact_institution_event(
            institution_id TEXT, stock_code TEXT, stock_name TEXT, report_date TEXT,
            notice_date TEXT, event_type TEXT,
            PRIMARY KEY(institution_id, stock_code, report_date)
        );
        CREATE TABLE inst_holdings(
            institution_id TEXT, stock_code TEXT, stock_name TEXT, report_date TEXT,
            PRIMARY KEY(institution_id, stock_code, report_date)
        );
        CREATE TABLE research_holding_chains(
            institution_id TEXT, stock_code TEXT, chain_id INTEGER,
            PRIMARY KEY(institution_id, stock_code, chain_id)
        );
        CREATE TABLE raw_gpcw_detail(
            stock_code TEXT, report_date TEXT
        );
        """
    )
    migrate_phase1(c)
    yield c
    c.close()


@pytest.fixture
def mkt():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE price_kline(code TEXT, date TEXT, freq TEXT, adjust TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL)"
    )
    yield c
    c.close()


def test_survivorship_active_vs_delisted(conn, mkt):
    # 股票 A：当前活跃
    mkt.executemany(
        "INSERT INTO price_kline VALUES(?,?,'daily','qfq',0,0,0,10,0,0)",
        [
            ("600000", "2023-01-05"),
            ("600000", "2024-01-05"),
            ("600000", "2026-04-15"),  # 最近仍在交易
        ],
    )
    # 股票 B：2023 年之后停牌（模拟退市）
    mkt.executemany(
        "INSERT INTO price_kline VALUES(?,?,'daily','qfq',0,0,0,5,0,0)",
        [
            ("000500", "2022-01-05"),
            ("000500", "2023-06-05"),  # 最后一次交易
        ],
    )
    mkt.commit()

    conn.execute("INSERT INTO dim_active_a_stock VALUES('600000','浦发银行')")
    conn.execute(
        "INSERT INTO fact_institution_event(institution_id,stock_code,stock_name,report_date,event_type) "
        "VALUES('i1','000500','退市股','2022-12-31','new_entry')"
    )
    conn.commit()

    stats = build_dim_all_ever_listed(conn, mkt, active_window_days=30)
    assert stats["total"] >= 2

    rows = {r[0]: r for r in conn.execute(
        "SELECT stock_code, is_active, delisted_date FROM dim_all_ever_listed"
    ).fetchall()}
    assert rows["600000"][1] == 1
    assert rows["000500"][1] == 0
    assert rows["000500"][2] == "2023-06-05"


def test_survivorship_multi_source_merge(conn, mkt):
    mkt.execute(
        "INSERT INTO price_kline VALUES('600036','2024-01-10','daily','qfq',0,0,0,40,0,0)"
    )
    mkt.commit()

    conn.execute("INSERT INTO dim_active_a_stock VALUES('600036','招商银行')")
    conn.execute(
        "INSERT INTO fact_institution_event(institution_id,stock_code,stock_name,report_date,event_type) "
        "VALUES('i2','600036','招商银行','2024-03-30','increase')"
    )
    conn.execute(
        "INSERT INTO inst_holdings(institution_id,stock_code,stock_name,report_date) "
        "VALUES('i2','600036','招商银行','2024-03-30')"
    )
    conn.commit()

    stats = build_dim_all_ever_listed(conn, mkt, active_window_days=9999)
    assert stats["active"] >= 1
    row = conn.execute(
        "SELECT stock_name, first_seen_date, last_seen_date FROM dim_all_ever_listed WHERE stock_code='600036'"
    ).fetchone()
    assert row[0] == "招商银行"
    # first_seen 应该 <= 2024-01-10，last_seen 应该 >= 2024-03-30
    assert row[1] is not None and row[2] is not None
