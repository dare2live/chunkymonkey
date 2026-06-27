"""DB health check 回归测试 (redundant index drop / index consistency / startup checks).

2026-06-27 通达信全删: raw_gpcw_financial 物删 → 原 idx_rgf 冗余索引 + _cleanup_snapshot_stub
(gpcw snapshot rowid DELETE) 相关测试随之移除; 通用 db_health 函数改在仍受监控的
fact_top10_holder_period 上验证 (它仍在 REDUNDANT_INDEXES/WATCHED_TABLES)。
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
    """In-memory DuckDB with fact_top10_holder_period + canonical indexes (受监控表)。"""
    conn = connect(":memory:")
    conn.execute(
        """
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT,
            report_date TEXT,
            holder_name TEXT,
            holder_name_norm TEXT,
            effective_date TEXT,
            holder_set TEXT,
            share_class TEXT
        )
        """
    )
    conn.execute("CREATE INDEX idx_t10_stock ON fact_top10_holder_period(stock_code, report_date DESC)")
    conn.execute("CREATE INDEX idx_t10_holder ON fact_top10_holder_period(holder_name)")
    conn.execute("CREATE INDEX idx_t10_holder_norm ON fact_top10_holder_period(holder_name_norm)")
    conn.execute("CREATE INDEX idx_t10_effective ON fact_top10_holder_period(effective_date)")
    conn.execute("CREATE INDEX idx_t10_set_class ON fact_top10_holder_period(holder_set, share_class)")
    yield conn
    conn.close()


def test_drop_redundant_indexes_idempotent(idx_conn):
    """没有冗余索引时, drop 操作返空列表 — idempotent."""
    dropped = drop_redundant_indexes(idx_conn)
    assert dropped == [], f"该表已无冗余索引, 不应删任何: {dropped}"


def test_drop_redundant_indexes_removes_legacy_fact_top10_holder_indexes(idx_conn):
    """fact_top10_holder_period 的 legacy idx_fact_hp_* 应在启动前被清掉."""
    legacy_indexes = [
        ("idx_fact_hp_stock_date", "stock_code, report_date"),
        ("idx_fact_hp_name", "holder_name"),
        ("idx_fact_hp_name_norm", "holder_name_norm"),
        ("idx_fact_hp_eff_date", "effective_date"),
        ("idx_fact_hp_set_class", "holder_set, share_class"),
    ]
    for idx_name, cols in legacy_indexes:
        idx_conn.execute(f"CREATE INDEX {idx_name} ON fact_top10_holder_period({cols})")

    dropped = drop_redundant_indexes(idx_conn)
    assert all(
        f"fact_top10_holder_period.{idx_name}" in dropped for idx_name, _ in legacy_indexes
    )
    rows = idx_conn.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name='fact_top10_holder_period'"
    ).fetchall()
    remaining = {r[0] for r in rows}
    assert all(idx_name not in remaining for idx_name, _ in legacy_indexes)
    assert {"idx_t10_stock", "idx_t10_holder", "idx_t10_holder_norm", "idx_t10_effective", "idx_t10_set_class"} <= remaining


def test_check_table_index_consistency_passes_on_clean_table(idx_conn):
    """干净表索引一致性检查应 OK."""
    idx_conn.execute(
        "INSERT INTO fact_top10_holder_period (stock_code, report_date) VALUES ('600519', '2024-12-31')"
    )
    chk = check_table_index_consistency(idx_conn, "fact_top10_holder_period")
    assert chk["ok"] is True
    assert chk["rows"] == 1


def test_run_startup_checks_clean(idx_conn):
    """干净 DB 上 run_startup_checks 不抛, 返摘要 (含受监控的 fact_top10_holder_period)."""
    summary = run_startup_checks(idx_conn)
    assert summary["still_broken"] == []
    assert all(c.get("ok") for c in summary["checks"])
    assert any(c.get("table") == "fact_top10_holder_period" for c in summary["checks"])
