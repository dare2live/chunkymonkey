from __future__ import annotations

import duckdb

from scripts import audit_end_to_end as audit


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
