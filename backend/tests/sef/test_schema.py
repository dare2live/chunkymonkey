"""SEF schema.migrate_phase1 单元测试：幂等 + 扩列 + 建表."""

from __future__ import annotations

import sqlite3

import pytest

from services.sef.schema import (
    SCHEMA_VERSION,
    _EVENT_EXTRA_COLUMNS,
    _CHAIN_EXTRA_COLUMNS,
    migrate_phase1,
)


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
            institution_id TEXT,
            stock_code TEXT,
            chain_id INTEGER,
            chain_start_date TEXT,
            chain_end_date TEXT,
            chain_status TEXT,
            PRIMARY KEY (institution_id, stock_code, chain_id)
        );
        """
    )
    yield c
    c.close()


def _table_cols(c, name):
    return {r[1] for r in c.execute(f"PRAGMA table_info({name})").fetchall()}


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_migrate_adds_expected_columns(conn):
    report = migrate_phase1(conn)
    cols = _table_cols(conn, "fact_institution_event")
    for name, _ in _EVENT_EXTRA_COLUMNS:
        assert name in cols
    cols2 = _table_cols(conn, "research_holding_chains")
    for name, _ in _CHAIN_EXTRA_COLUMNS:
        assert name in cols2
    assert report["version"] == SCHEMA_VERSION


def test_migrate_creates_expected_tables(conn):
    migrate_phase1(conn)
    tables = _tables(conn)
    expected = {
        "fact_chain_alpha_truth",
        "mart_institution_capability",
        "mart_institution_style",
        "fact_stock_character",
        "model_signals_log",
        "model_signals_realized",
        "model_state",
        "fact_regime_state",
        "institution_drift_log",
        "backtest_walk_forward",
        "portfolio_recommendation_daily",
        "dim_all_ever_listed",
        "sef_schema_version",
    }
    missing = expected - tables
    assert not missing, f"missing tables: {missing}"


def test_migrate_is_idempotent(conn):
    r1 = migrate_phase1(conn)
    r2 = migrate_phase1(conn)
    # 二次执行不应报错，且不再新增列
    assert r2.get("altered", {}) == {}
    assert r1["version"] == r2["version"]


def test_schema_version_recorded(conn):
    migrate_phase1(conn)
    row = conn.execute("SELECT version_id FROM sef_schema_version").fetchone()
    assert row[0] == SCHEMA_VERSION


def test_chain_truth_unique_constraint(conn):
    migrate_phase1(conn)
    conn.execute(
        """
        INSERT INTO fact_chain_alpha_truth(
            institution_id, stock_code, research_chain_id,
            entry_date, eval_date, status
        ) VALUES('inst1','000001',1,'2024-01-01','2024-02-01','closed')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth(
                institution_id, stock_code, research_chain_id,
                entry_date, eval_date, status
            ) VALUES('inst1','000001',1,'2024-01-01','2024-02-01','closed')
            """
        )
