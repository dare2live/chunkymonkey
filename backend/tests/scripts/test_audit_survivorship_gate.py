from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_survivorship_gate.py"
SPEC = importlib.util.spec_from_file_location("audit_survivorship_gate", SCRIPT_PATH)
audit_survivorship_gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_survivorship_gate
SPEC.loader.exec_module(audit_survivorship_gate)


def test_fetch_survivorship_counts_batches_universe_and_panel_counts() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_all_ever_listed (stock_code TEXT, is_active INTEGER)")
        conn.execute("CREATE TABLE mart_p0a_label_panel (stock_code TEXT, label_version TEXT)")
        conn.execute(
            """
            INSERT INTO dim_all_ever_listed VALUES
            ('600001', 1),
            ('600002', 0),
            ('300001', 1),
            ('830001', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO mart_p0a_label_panel VALUES
            ('600001', 'v1'),
            ('600002', 'v1'),
            ('830001', 'v1'),
            ('300001', 'other')
            """
        )

        counts = audit_survivorship_gate._fetch_survivorship_counts(conn, "v1", ("60", "30"))
    finally:
        conn.close()

    # Preserve existing gate semantics: label panel count is by label_version,
    # while ever/active counts are filtered by KEEP prefixes.
    assert counts == (3, 2, 3)


def test_scan_is_active_issues_skips_comments_and_allowed_lines() -> None:
    pattern = re.compile(r"is_active\s*=\s*1", re.IGNORECASE)
    text = """
    # WHERE is_active=1 should not count in comments
    sql = "SELECT * FROM t WHERE stock_code LIKE '60%' AND is_active=1"
    live_sql = "SELECT * FROM live_universe WHERE is_active=1"
    combo = "WHERE is_active=1 AND stock_code LIKE '60%'"
    """

    issues = audit_survivorship_gate._scan_is_active_issues("sample.py", text, pattern)

    assert issues == ['sample.py:3: sql = "SELECT * FROM t WHERE stock_code LIKE \'60%\' AND is_active=1"']


def test_matched_line_reports_last_line_without_trailing_newline() -> None:
    pattern = re.compile(r"is_active\s*=\s*1", re.IGNORECASE)
    text = "first = 1\nsql = 'WHERE is_active=1'"
    match = next(pattern.finditer(text))

    line_num, line = audit_survivorship_gate._matched_line(text, match)

    assert line_num == 2
    assert line == "sql = 'WHERE is_active=1'"
