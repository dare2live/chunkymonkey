from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_universe_coverage.py"
SPEC = importlib.util.spec_from_file_location("audit_universe_coverage", SCRIPT_PATH)
audit_universe_coverage = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_universe_coverage
SPEC.loader.exec_module(audit_universe_coverage)


def test_ashare_universe_by_date_batches_dates_and_filters_non_daily_qfq(monkeypatch) -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE kline (date TEXT, code TEXT, freq TEXT, adjust TEXT)")
        conn.execute(
            """
            INSERT INTO kline VALUES
            ('2026-05-01', '000001', 'daily', 'qfq'),
            ('2026-05-01', '300001', 'daily', 'qfq'),
            ('2026-05-01', '510300', 'daily', 'qfq'),
            ('2026-05-01', '000002', 'weekly', 'qfq'),
            ('2026-05-02', '688001', 'daily', 'qfq'),
            ('2026-05-02', '830001', 'daily', 'hfq')
            """
        )

        monkeypatch.setattr(audit_universe_coverage, "KLINE_RELATION", "kline")
        by_date = audit_universe_coverage._ashare_universe_by_date(
            conn,
            ["2026-05-01", "2026-05-02", "2026-05-03"],
        )
    finally:
        conn.close()

    assert by_date == {
        "2026-05-01": {"000001", "300001"},
        "2026-05-02": {"688001"},
        "2026-05-03": set(),
    }


def test_query_biz_codes_by_date_handles_regular_and_alpha_date_columns() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fact_feature_panel (date TEXT, stock_code TEXT)")
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('2026-05-01', '000001'),
            ('2026-05-01', '300001'),
            ('2026-05-02', '688001')
            """
        )
        conn.execute("CREATE SCHEMA a158")
        conn.execute("CREATE TABLE a158.fact_alpha158_panel (date DATE, stock_code TEXT)")
        conn.execute(
            """
            INSERT INTO a158.fact_alpha158_panel VALUES
            (DATE '2026-05-01', '000001'),
            (DATE '2026-05-02', '600001')
            """
        )

        regular = audit_universe_coverage._query_biz_codes_by_date(
            conn,
            "fact_feature_panel",
            "date",
            "stock_code",
            "main",
            ["2026-05-01", "2026-05-02", "2026-05-03"],
        )
        alpha = audit_universe_coverage._query_biz_codes_by_date(
            conn,
            "fact_alpha158_panel",
            "date",
            "stock_code",
            "a158",
            ["2026-05-01", "2026-05-02", "2026-05-03"],
        )
    finally:
        conn.close()

    assert regular == {
        "2026-05-01": {"000001", "300001"},
        "2026-05-02": {"688001"},
        "2026-05-03": set(),
    }
    assert alpha == {
        "2026-05-01": {"000001"},
        "2026-05-02": {"600001"},
        "2026-05-03": set(),
    }


def test_check_business_table_coverage_preserves_panel_and_event_semantics(monkeypatch) -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE kline (date TEXT, code TEXT, freq TEXT, adjust TEXT)")
        conn.execute(
            """
            INSERT INTO kline VALUES
            ('2024-01-02', '000001', 'daily', 'qfq'),
            ('2024-01-02', '300001', 'daily', 'qfq'),
            ('2025-01-02', '000001', 'daily', 'qfq'),
            ('2025-01-02', '300001', 'daily', 'qfq'),
            ('2026-05-27', '000001', 'daily', 'qfq'),
            ('2026-05-27', '300001', 'daily', 'qfq')
            """
        )
        conn.execute("CREATE TABLE fact_feature_panel (date TEXT, stock_code TEXT)")
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('2024-01-02', '000001'),
            ('2024-01-02', '300001'),
            ('2025-01-02', '000001'),
            ('2026-05-27', '000001'),
            ('2026-05-27', '300001')
            """
        )
        conn.execute("CREATE TABLE fact_technical_trigger (date TEXT, stock_code TEXT)")
        conn.execute(
            """
            INSERT INTO fact_technical_trigger VALUES
            ('2024-01-02', '000001'),
            ('2026-05-27', '300001')
            """
        )

        monkeypatch.setattr(audit_universe_coverage, "KLINE_RELATION", "kline")
        monkeypatch.setattr(audit_universe_coverage, "_attach_alpha158", lambda _conn: None)
        monkeypatch.setattr(
            audit_universe_coverage,
            "PANEL_TABLES",
            (("fact_feature_panel", "date", "stock_code", "main"),),
        )
        monkeypatch.setattr(
            audit_universe_coverage,
            "EVENT_TABLES",
            (("fact_technical_trigger", "date", "stock_code", "main"),),
        )

        results = audit_universe_coverage.check_business_table_coverage(conn)
    finally:
        conn.close()

    statuses = {result.name: result.status for result in results}
    assert statuses["fact_feature_panel@2024-01-02"] == "PASS"
    assert statuses["fact_feature_panel@2025-01-02"] == "FAIL"
    assert statuses["fact_technical_trigger@2025-01-02[event-table-info]"] == "PASS"


def test_sample_codes_returns_stable_sorted_prefix() -> None:
    assert audit_universe_coverage._sample_codes({"300001", "000001", "600001"}, limit=2) == [
        "000001",
        "300001",
    ]
