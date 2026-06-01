from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_data_completeness.py"
SPEC = importlib.util.spec_from_file_location("audit_data_completeness", SCRIPT_PATH)
audit_data_completeness = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_data_completeness
SPEC.loader.exec_module(audit_data_completeness)


def test_load_table_summaries_batches_tables_for_one_db(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE prices (code TEXT, date DATE)")
        con.execute(
            "INSERT INTO prices VALUES ('000001', DATE '2026-05-26'), ('000001', DATE '2026-05-27'), ('000002', DATE '2026-05-27')"
        )
        con.execute("CREATE TABLE sectors (date DATE)")
        con.execute("INSERT INTO sectors VALUES (DATE '2026-05-20'), (DATE '2026-05-22')")

        summaries = audit_data_completeness._load_table_summaries(
            con,
            [
                ("sample.duckdb", "prices", "date", True, "code"),
                ("sample.duckdb", "sectors", "date", False, None),
            ],
        )
    finally:
        con.close()

    assert summaries == {
        "prices": ("2026-05-27", 2),
        "sectors": ("2026-05-22", None),
    }


def test_table_verdict_flags_partial_current_coverage() -> None:
    verdict, issue = audit_data_completeness._table_verdict(
        "prices",
        "2026-05-27",
        "2,000",
        cal_max="2026-05-27",
        has_codes=True,
        code_col="code",
    )

    assert verdict == "PARTIAL_WARN"
    assert issue == "only 2,000 codes (38%)"


def test_table_verdict_keeps_short_staleness_non_blocking() -> None:
    verdict, issue = audit_data_completeness._table_verdict(
        "prices",
        "2026-05-25",
        "5,200",
        cal_max="2026-05-27",
        has_codes=True,
        code_col="code",
    )

    assert verdict == "STALE_2d"
    assert issue is None


def test_issue_severity_treats_sparse_event_partial_as_warn() -> None:
    assert audit_data_completeness._issue_severity("PARTIAL_WARN", "sparse_event_presence_only") == "WARN"
    assert audit_data_completeness._issue_severity("PARTIAL_WARN", None) == "FAIL"


def test_load_coverage_policies_reads_dim_data_asset(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE dim_data_asset (table_name TEXT, coverage_policy TEXT)")
        con.execute(
            "INSERT INTO dim_data_asset VALUES ('fact_technical_trigger', 'sparse_event_presence_only')"
        )

        policies = audit_data_completeness._load_coverage_policies(
            con,
            [("sample.duckdb", "fact_technical_trigger", "date", True, "stock_code")],
        )
    finally:
        con.close()

    assert policies == {"fact_technical_trigger": "sparse_event_presence_only"}
