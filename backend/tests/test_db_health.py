"""Phase ψ.5 根因 2 修复 — DB health check + financial_client._cleanup_snapshot_stub 回归测试.

测试目标:
  1. drop_redundant_indexes 删 raw_gpcw / fact_top10 legacy indexes
  2. check_table_index_consistency 正常表返 ok
  3. run_startup_checks 在干净表上不抛
  4. _cleanup_snapshot_stub 用 rowid 路径 — 即便给的 (notice_date, report_date)
     没匹配, 也 graceful 返回 (不抛 FATAL)
"""
from __future__ import annotations

import pytest

from services.duck_adapter import connect
from services.db_health import (
    drop_redundant_indexes,
    check_table_index_consistency,
    run_startup_checks,
)
from services.financial_client import _cleanup_snapshot_stub, ensure_tables


@pytest.fixture()
def fin_conn():
    """In-memory DuckDB with full raw_gpcw_financial schema."""
    conn = connect(":memory:")
    ensure_tables(conn)
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
    conn.execute(
        "CREATE INDEX idx_t10_stock ON fact_top10_holder_period(stock_code, report_date DESC)"
    )
    conn.execute(
        "CREATE INDEX idx_t10_holder ON fact_top10_holder_period(holder_name)"
    )
    conn.execute(
        "CREATE INDEX idx_t10_holder_norm ON fact_top10_holder_period(holder_name_norm)"
    )
    conn.execute(
        "CREATE INDEX idx_t10_effective ON fact_top10_holder_period(effective_date)"
    )
    conn.execute(
        "CREATE INDEX idx_t10_set_class ON fact_top10_holder_period(holder_set, share_class)"
    )
    yield conn
    conn.close()


def test_ensure_tables_no_longer_creates_redundant_index(fin_conn):
    """ensure_tables 应该只建 idx_rgf_report, 不再建 idx_rgf_stock_report."""
    rows = fin_conn.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name='raw_gpcw_financial'"
    ).fetchall()
    names = {r[0] for r in rows}
    # PK 隐式 index 名在 DuckDB 通常是 类似 'raw_gpcw_financial_pkey' — 我们关心的是冗余
    assert "idx_rgf_report" in names, f"idx_rgf_report 该建却没建: {names}"
    assert "idx_rgf_stock_report" not in names, (
        f"idx_rgf_stock_report 跟 PK 重复, 不该再建: {names}"
    )


def test_drop_redundant_indexes_idempotent(fin_conn):
    """没有冗余索引时, drop 操作返空列表 — idempotent."""
    dropped = drop_redundant_indexes(fin_conn)
    assert dropped == [], f"该表已无冗余索引, 不应删任何: {dropped}"


def test_drop_redundant_indexes_removes_legacy(fin_conn):
    """模拟老 DB 文件有 legacy 冗余索引 — drop 应清掉."""
    fin_conn.execute(
        "CREATE INDEX idx_rgf_stock_report ON raw_gpcw_financial(stock_code, report_date)"
    )
    dropped = drop_redundant_indexes(fin_conn)
    assert "raw_gpcw_financial.idx_rgf_stock_report" in dropped
    # verify removed
    rows = fin_conn.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name='raw_gpcw_financial'"
    ).fetchall()
    assert "idx_rgf_stock_report" not in {r[0] for r in rows}


def test_drop_redundant_indexes_removes_legacy_fact_top10_holder_indexes(fin_conn):
    """fact_top10_holder_period 的 legacy idx_fact_hp_* 应在启动前被清掉."""
    legacy_indexes = [
        ("idx_fact_hp_stock_date", "stock_code, report_date"),
        ("idx_fact_hp_name", "holder_name"),
        ("idx_fact_hp_name_norm", "holder_name_norm"),
        ("idx_fact_hp_eff_date", "effective_date"),
        ("idx_fact_hp_set_class", "holder_set, share_class"),
    ]
    for idx_name, cols in legacy_indexes:
        fin_conn.execute(
            f"CREATE INDEX {idx_name} ON fact_top10_holder_period({cols})"
        )

    dropped = drop_redundant_indexes(fin_conn)
    assert all(
        f"fact_top10_holder_period.{idx_name}" in dropped for idx_name, _ in legacy_indexes
    )
    rows = fin_conn.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name='fact_top10_holder_period'"
    ).fetchall()
    remaining = {r[0] for r in rows}
    assert all(idx_name not in remaining for idx_name, _ in legacy_indexes)
    assert {"idx_t10_stock", "idx_t10_holder", "idx_t10_holder_norm", "idx_t10_effective", "idx_t10_set_class"} <= remaining


def test_check_table_index_consistency_passes_on_clean_table(fin_conn):
    """干净表索引一致性检查应 OK."""
    fin_conn.execute(
        "INSERT INTO raw_gpcw_financial (stock_code, report_date) VALUES ('600519', '2024-12-31')"
    )
    chk = check_table_index_consistency(fin_conn, "raw_gpcw_financial")
    assert chk["ok"] is True
    assert chk["rows"] == 1


def test_run_startup_checks_clean(fin_conn):
    """干净 DB 上 run_startup_checks 不抛, 返摘要."""
    summary = run_startup_checks(fin_conn)
    assert summary["still_broken"] == []
    assert all(c.get("ok") for c in summary["checks"])
    assert any(c.get("table") == "fact_top10_holder_period" for c in summary["checks"])


def test_cleanup_snapshot_stub_rowid_path_no_match(fin_conn):
    """_cleanup_snapshot_stub 用 rowid 路径 — 没匹配的 stub 不抛, 直接返."""
    # 表为空, 调用应静默成功
    _cleanup_snapshot_stub(fin_conn, "600519", "2024-12-31", "2024-12-31")
    # 加些跟过滤条件不匹配的 row
    fin_conn.execute(
        "INSERT INTO raw_gpcw_financial (stock_code, report_date, report_type, notice_date) "
        "VALUES ('600519', '2024-09-30', '合并期末', '2024-10-30')"
    )
    _cleanup_snapshot_stub(fin_conn, "600519", "2024-12-31", "2024-12-31")
    # 数据不应被误删
    n = fin_conn.execute(
        "SELECT COUNT(*) FROM raw_gpcw_financial WHERE stock_code='600519'"
    ).fetchone()[0]
    assert n == 1


def test_cleanup_snapshot_stub_rowid_path_deletes_only_matching(fin_conn):
    """_cleanup_snapshot_stub 真删 matching stub 不动其它."""
    rows = [
        # 该删: latest_snapshot + notice 匹配 + report_date 不同
        ("600519", "2024-12-31", "latest_snapshot", "2024-12-31"),
        ("600519", "2024-09-30", "latest_snapshot", "2024-12-31"),  # 这条 stub 该删 (notice=2024-12-31, report 不是 2024-12-31)
        # 不该删: report_type 不是 latest_snapshot
        ("600519", "2024-06-30", "合并期末", "2024-12-31"),
        # 不该删: notice 不匹配
        ("600519", "2024-03-31", "latest_snapshot", "2024-09-30"),
    ]
    fin_conn.executemany(
        "INSERT INTO raw_gpcw_financial (stock_code, report_date, report_type, notice_date) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    # 调用 cleanup: 真 report_date=2024-12-31, notice=2024-12-31
    # 该删: report_type='latest_snapshot' AND notice='2024-12-31' AND report_date != '2024-12-31'
    # → 只有第 2 条匹配 (report_date=2024-09-30, notice=2024-12-31)
    _cleanup_snapshot_stub(fin_conn, "600519", "2024-12-31", "2024-12-31")

    remaining = fin_conn.execute(
        "SELECT report_date FROM raw_gpcw_financial WHERE stock_code='600519' ORDER BY report_date"
    ).fetchall()
    dates = [r[0] for r in remaining]
    assert "2024-09-30" not in dates, "stub 该删却还在"
    assert "2024-12-31" in dates, "latest_snapshot real 不该被删"
    assert "2024-06-30" in dates, "合并期末 不该被删"
    assert "2024-03-31" in dates, "notice 不匹配的不该被删"
