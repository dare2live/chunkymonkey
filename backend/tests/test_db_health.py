"""DB health check 回归测试 (redundant index drop / index consistency / startup checks).

Holders fact plane retired 2026-07-26 — watch list empty. Tests exercise the
generic helpers on a stand-in table (not a production watched table).
"""
from __future__ import annotations

import pytest

from services.duck_adapter import connect
from services.db_health import (
    drop_redundant_indexes,
    check_table_index_consistency,
    run_startup_checks,
)


@pytest.fixture()
def idx_conn():
    """In-memory DuckDB with a stand-in table for helper coverage."""
    conn = connect(":memory:")
    conn.execute(
        """
        CREATE TABLE _db_health_probe (
            stock_code TEXT,
            report_date TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_probe_stock ON _db_health_probe(stock_code, report_date DESC)"
    )
    yield conn
    conn.close()


def test_drop_redundant_indexes_idempotent(idx_conn):
    """Empty REDUNDANT_INDEXES → drop 操作返空列表 — idempotent."""
    dropped = drop_redundant_indexes(idx_conn)
    assert dropped == [], f"无冗余清单时不应删任何: {dropped}"


def test_check_table_index_consistency_passes_on_clean_table(idx_conn):
    """干净表索引一致性检查应 OK."""
    idx_conn.execute(
        "INSERT INTO _db_health_probe (stock_code, report_date) VALUES ('600519', '20241231')"
    )
    chk = check_table_index_consistency(idx_conn, "_db_health_probe")
    assert chk["ok"] is True
    assert chk["rows"] == 1


def test_run_startup_checks_clean(idx_conn):
    """干净 DB 上 run_startup_checks 不抛; watched list empty after fact retire."""
    summary = run_startup_checks(idx_conn)
    assert summary["still_broken"] == []
    assert summary["checks"] == []
    assert summary["dropped"] == []
