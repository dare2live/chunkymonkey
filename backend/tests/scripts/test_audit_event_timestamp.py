from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_event_timestamp.py"
SPEC = importlib.util.spec_from_file_location("audit_event_timestamp", SCRIPT_PATH)
audit_event_timestamp = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_event_timestamp
SPEC.loader.exec_module(audit_event_timestamp)


def _by_name(results):
    return {result.name: result for result in results}


def test_timestamp_non_null_rate_preserves_primary_fail_and_secondary_warn(monkeypatch) -> None:
    monkeypatch.setattr(
        audit_event_timestamp,
        "EVENT_TABLES",
        [
            {
                "table": "event_ts_sample",
                "primary_ts": "notice_date",
                "secondary_ts": "built_at",
                "critical": True,
            }
        ],
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE event_ts_sample (notice_date DATE, built_at DATE)")
        conn.execute(
            """
            INSERT INTO event_ts_sample VALUES
            (DATE '2026-05-01', DATE '2026-05-02'),
            (NULL, NULL)
            """
        )

        results = _by_name(audit_event_timestamp.check_timestamp_non_null_rate(conn))
    finally:
        conn.close()

    assert results["event_ts_sample.notice_date[primary]"].status == "FAIL"
    assert results["event_ts_sample.notice_date[primary]"].rows == 2
    assert results["event_ts_sample.built_at[secondary]"].status == "PASS"


def test_pit_lag_distribution_batches_valid_tables_and_warns_missing_table(monkeypatch) -> None:
    monkeypatch.setattr(
        audit_event_timestamp,
        "EVENT_TABLES",
        [
            {
                "table": "event_lag_sample",
                "primary_ts": "notice_date",
                "secondary_ts": "built_at",
                "critical": True,
            },
            {
                "table": "event_lag_missing",
                "primary_ts": "notice_date",
                "secondary_ts": "built_at",
                "critical": False,
            },
        ],
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE event_lag_sample (notice_date DATE, built_at DATE)")
        conn.execute(
            """
            INSERT INTO event_lag_sample VALUES
            (DATE '2026-05-03', DATE '2026-05-01'),
            (DATE '2026-05-05', DATE '2026-05-02')
            """
        )

        results = _by_name(audit_event_timestamp.check_pit_lag_distribution(conn))
    finally:
        conn.close()

    assert results["event_lag_sample(notice_date-built_at)"].status == "PASS"
    assert results["event_lag_sample(notice_date-built_at)"].rows == 2
    assert results["event_lag_missing(notice_date-built_at)"].status == "WARN"
    assert "table not found" in results["event_lag_missing(notice_date-built_at)"].detail


def test_recent_30d_sanity_batches_future_fail_and_recent_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        audit_event_timestamp,
        "EVENT_TABLES",
        [
            {
                "table": "event_recent_sample",
                "primary_ts": "notice_date",
                "secondary_ts": None,
                "critical": True,
            }
        ],
    )
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE event_recent_sample (notice_date DATE)")
        conn.execute(
            """
            INSERT INTO event_recent_sample VALUES
            (CURRENT_DATE - INTERVAL 5 DAY),
            (CURRENT_DATE + INTERVAL 1 DAY)
            """
        )

        results = _by_name(audit_event_timestamp.check_recent_30d_sanity(conn))
    finally:
        conn.close()

    assert results["event_recent_sample.notice_date.future"].status == "FAIL"
    assert results["event_recent_sample.notice_date.future"].rows == 1
    assert results["event_recent_sample.notice_date.recent_30d"].status == "PASS"
