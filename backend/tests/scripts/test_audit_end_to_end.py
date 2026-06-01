from __future__ import annotations

import duckdb

from scripts import audit_end_to_end as audit


def _freshness_conn(signal_context_date: str) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    for table, col in [
        ("fact_technical_trigger", "date"),
        ("fact_signal_context", "date"),
        ("mart_stock_picture_daily", "snapshot_date"),
        ("mart_stock_survey_features", "as_of_date"),
    ]:
        conn.execute(f"CREATE TABLE {table} ({col} DATE)")
        conn.execute(f"INSERT INTO {table} VALUES ('2026-05-29')")
    conn.execute("DELETE FROM fact_signal_context")
    conn.execute("INSERT INTO fact_signal_context VALUES (?)", [signal_context_date])
    return conn


def test_table_completeness_uses_batched_stats_without_changing_verdicts():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fact_technical_trigger (date DATE)")
        conn.execute(
            """
            INSERT INTO fact_technical_trigger VALUES
                ('2026-05-18'),
                ('2026-05-20')
            """
        )

        issues = audit.audit_table_completeness(conn)
    finally:
        conn.close()

    by_table = {issue["table"]: issue for issue in issues}

    assert by_table["fact_technical_trigger"] == {
        "category": "table_completeness",
        "table": "fact_technical_trigger",
        "n": 2,
        "expected_n": 100_000,
        "severity": "FAIL",
        "note": "行数严重不足 2 < expect 100,000/10",
        "latest": "2026-05-20",
    }
    assert by_table["fact_signal_context"] == {
        "category": "table_completeness",
        "table": "fact_signal_context",
        "severity": "FAIL",
        "note": "table missing",
    }


def test_issues_by_severity_keeps_fail_and_warn_only():
    issues = [
        {"severity": "OK", "table": "ok_table"},
        {"severity": "FAIL", "table": "bad_table"},
        {"severity": "WARN", "check": "soft_check"},
    ]

    grouped = audit._issues_by_severity(issues)

    assert grouped["FAIL"] == [{"severity": "FAIL", "table": "bad_table"}]
    assert grouped["WARN"] == [{"severity": "WARN", "check": "soft_check"}]


def test_issue_render_helpers_skip_metadata_fields():
    issue = {
        "category": "freshness",
        "table": "fact_signal_context",
        "severity": "WARN",
        "latest": "2026-05-19",
        "days_behind": 2,
    }

    assert audit._issue_header(issue) == "  [freshness] fact_signal_context"
    assert audit._issue_detail_lines(issue) == [
        "      latest: 2026-05-19",
        "      days_behind: 2",
    ]


def test_audit_data_freshness_uses_latest_completed_trade_date(monkeypatch):
    conn = _freshness_conn("2026-05-29")
    monkeypatch.setattr(audit, "_latest_completed_trade_date_for_write", lambda raise_on_miss=False: "2026-05-29")
    try:
        issues = audit.audit_data_freshness(conn)
    finally:
        conn.close()

    by_table = {issue["table"]: issue for issue in issues}
    assert by_table["fact_technical_trigger"]["severity"] == "OK"
    assert by_table["fact_technical_trigger"]["days_behind"] == 0
    assert by_table["fact_signal_context"]["severity"] == "OK"
    assert by_table["fact_signal_context"]["days_behind"] == 0
    assert by_table["mart_stock_picture_daily"]["severity"] == "OK"
    assert by_table["mart_stock_survey_features"]["severity"] == "OK"


def test_audit_data_freshness_warns_when_one_completed_trade_day_behind(monkeypatch):
    conn = _freshness_conn("2026-05-28")
    monkeypatch.setattr(audit, "_latest_completed_trade_date_for_write", lambda raise_on_miss=False: "2026-05-29")
    try:
        issues = audit.audit_data_freshness(conn)
    finally:
        conn.close()

    by_table = {issue["table"]: issue for issue in issues}
    assert by_table["fact_signal_context"]["severity"] == "WARN"
    assert by_table["fact_signal_context"]["days_behind"] == 1


def test_audit_data_freshness_fails_closed_when_calendar_lookup_missing(monkeypatch):
    conn = _freshness_conn("2026-05-29")
    monkeypatch.setattr(audit, "_latest_completed_trade_date_for_write", lambda raise_on_miss=False: (_ for _ in ()).throw(RuntimeError("calendar missing")))
    try:
        issues = audit.audit_data_freshness(conn)
    finally:
        conn.close()

    assert {issue["severity"] for issue in issues} == {"FAIL"}
    assert all("latest completed trade date lookup failed" in issue["note"] for issue in issues)
