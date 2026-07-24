"""Tests for org truncation audit helper."""
from __future__ import annotations

import duckdb

from services.org_holding_aif10 import ensure_tables
from services.org_holding_truncation_audit import list_truncated_org_periods


def test_list_truncated_flags_page_cap_signature(monkeypatch):
    con = duckdb.connect(":memory:")
    ensure_tables(con)
    for i in range(200_000):
        con.execute(
            "INSERT INTO raw_org_holding_aif10 "
            "(report_date, stock_code, holder_code, fund_derivecode) "
            "VALUES ('2025-12-31', ?, ?, '')",
            [f"{i % 500:06d}", f"H{i}"],
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
