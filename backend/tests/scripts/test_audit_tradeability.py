from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_tradeability.py"
SPEC = importlib.util.spec_from_file_location("audit_tradeability", SCRIPT_PATH)
audit_tradeability = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_tradeability
SPEC.loader.exec_module(audit_tradeability)


def test_grep_dir_counts_line_once_when_multiple_patterns_match(tmp_path: Path) -> None:
    source = tmp_path / "backend" / "services" / "paper_sim" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def f(row):\n"
        "    return row.volume <= 0 or require_today_traded(row)\n",
        encoding="utf-8",
    )

    hits = audit_tradeability._grep_dir(
        tmp_path,
        "backend/services/paper_sim/",
        audit_tradeability.SUSPENSION_PATTERNS,
    )

    assert len(hits) == 1
    assert hits[0][0] == source
    assert hits[0][1] == 2


def test_fetch_spot_check_counts_batches_raw_and_view_counts() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE price_kline (date TEXT, adjust TEXT, freq TEXT, volume DOUBLE)")
        conn.execute("CREATE TABLE v_price_kline_qfq (date TEXT, adjust TEXT, freq TEXT, volume DOUBLE)")
        conn.execute(
            """
            INSERT INTO price_kline VALUES
            ('2026-05-01', 'qfq', 'daily', 10),
            ('2026-05-01', 'qfq', 'daily', 0),
            ('2026-05-02', 'qfq', 'daily', NULL),
            ('2026-05-02', 'qfq', 'daily', 5)
            """
        )
        conn.execute(
            """
            INSERT INTO v_price_kline_qfq VALUES
            ('2026-05-01', 'qfq', 'daily', 10),
            ('2026-05-02', 'qfq', 'daily', 0),
            ('2026-05-02', 'qfq', 'daily', 5)
            """
        )

        counts = audit_tradeability._fetch_spot_check_counts(conn, ["2026-05-01", "2026-05-02"])
    finally:
        conn.close()

    assert counts["2026-05-01"]["raw"] == (2, 1)
    assert counts["2026-05-01"]["view"] == (1, 0)
    assert counts["2026-05-02"]["raw"] == (2, 1)
    assert counts["2026-05-02"]["view"] == (2, 1)


def test_spot_check_result_preserves_pass_fail_warn_semantics() -> None:
    passed = audit_tradeability._spot_check_result_for_counts("2026-05-01", 2, 1, 1, 0)
    failed = audit_tradeability._spot_check_result_for_counts("2026-05-02", 2, 1, 2, 1)
    warned = audit_tradeability._spot_check_result_for_counts("2026-05-03", 2, 0, 2, 0)

    assert passed.status == "PASS"
    assert passed.extras["view_drop"] == 1
    assert failed.status == "FAIL"
    assert failed.rows == 1
    assert warned.status == "WARN"
