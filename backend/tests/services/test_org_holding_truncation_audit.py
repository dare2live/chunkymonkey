"""Tests for org truncation audit helper."""
from __future__ import annotations

import duckdb

from services.org_holding_aif10 import ensure_tables
from services.org_holding_truncation_audit import list_truncated_org_periods


def test_list_truncated_flags_page_cap_signature(monkeypatch):
    con = duckdb.connect(":memory:")
    ensure_tables(con)
    # 2026-09-07: 原为 Python 逐行 INSERT 200,000 次 (本文件 47.9s)。数据逐字等价 ——
    # range(N) 给 0..N-1, printf('%06d', i % 500) 等价于 f"{i % 500:06d}"。
    # 慢的是插入方式, 不是这个测试该被删掉。
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, stock_code, holder_code, fund_derivecode) "
        "SELECT '2025-12-31', printf('%06d', i % 500), 'H' || i::VARCHAR, '' "
        "FROM range(200000) t(i)"
    )
    monkeypatch.setattr(
        "services.org_holding_population.max_accepted_stocks_across_partitions",
        lambda _c: 5520,
    )
    monkeypatch.setattr(
        "services.org_holding_aif10.latest_plannable_report_date",
        lambda today=None: "2025-12-31",
    )
    out = list_truncated_org_periods(con, start_period="2025-12-31", end_period="2025-12-31")
    assert len(out) == 1
    assert out[0]["report_date"] == "2025-12-31"
    con.close()


def test_list_truncated_skips_under_modern_baseline_only(monkeypatch):
    """Honest thin historical land (no page-cap) must not enter repair queue."""
    con = duckdb.connect(":memory:")
    ensure_tables(con)
    con.execute(
        "INSERT INTO raw_org_holding_aif10 "
        "(report_date, stock_code, holder_code, fund_derivecode) "
        "SELECT '2019-03-31', printf('%06d', i % 400), 'H' || i::VARCHAR, '' "
        "FROM range(1000) t(i)"
    )
    monkeypatch.setattr(
        "services.org_holding_population.max_accepted_stocks_across_partitions",
        lambda _c: 5562,
    )
    monkeypatch.setattr(
        "services.org_holding_aif10.latest_plannable_report_date",
        lambda today=None: "2019-03-31",
    )
    out = list_truncated_org_periods(con, start_period="2019-03-31", end_period="2019-03-31")
    assert out == []
    con.close()
