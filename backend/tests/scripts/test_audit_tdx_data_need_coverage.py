from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_tdx_data_need_coverage.py"
SPEC = importlib.util.spec_from_file_location("audit_tdx_data_need_coverage", SCRIPT_PATH)
audit_tdx_data_need_coverage = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_tdx_data_need_coverage
SPEC.loader.exec_module(audit_tdx_data_need_coverage)


class ExecuteOnlyConn:
    def __init__(self, conn):
        self.conn = conn
        self.calls: list[str] = []

    def execute(self, sql: str):
        self.calls.append(sql)
        return self.conn.execute(sql)


def test_ensure_tables_uses_single_execute_without_executescript() -> None:
    conn = duckdb.connect(":memory:")
    wrapper = ExecuteOnlyConn(conn)
    try:
        audit_tdx_data_need_coverage.ensure_tables(wrapper)
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_name IN (
                    'mart_tdx_data_need_coverage',
                    'dim_data_source_priority',
                    'mart_data_source_reassignment_proposal'
                 )
                """
            ).fetchall()
        }
    finally:
        conn.close()

    assert len(wrapper.calls) == 1
    assert tables == {
        "mart_tdx_data_need_coverage",
        "dim_data_source_priority",
        "mart_data_source_reassignment_proposal",
    }


def test_read_input_inventory_reports_sizes_and_relative_paths(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    first = root / "one.txt"
    second = root / "nested" / "two.txt"
    second.parent.mkdir(parents=True)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("alpha\nbeta\n", encoding="utf-8")
    second.write_text("三\n", encoding="utf-8")

    monkeypatch.setattr(audit_tdx_data_need_coverage, "ROOT", root)

    assert audit_tdx_data_need_coverage._read_input_inventory([first, second]) == [
        {"path": "one.txt", "bytes": 11, "lines": 3},
        {"path": "nested/two.txt", "bytes": 4, "lines": 2},
    ]


def test_read_input_inventory_fails_when_evidence_file_is_missing(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    missing = root / "missing.md"
    monkeypatch.setattr(audit_tdx_data_need_coverage, "ROOT", root)

    with pytest.raises(FileNotFoundError):
        audit_tdx_data_need_coverage._read_input_inventory([missing])


def _write_config(
    path: Path,
    evidence_path: Path,
    *,
    omit_need_field: str | None = None,
    evidence_status: str = "production",
    production_eligibility: str = "eligible",
    pit_key: str = "trade_date",
) -> None:
    need_name_line = "" if omit_need_field == "need_name" else '    need_name: "sample need"\n'
    grain_line = "" if omit_need_field == "grain" else '    grain: "stock_code x trade_date"\n'
    pit_key_line = "" if omit_need_field == "pit_key" else f'    pit_key: "{pit_key}"\n'
    freshness_line = "" if omit_need_field == "freshness_sla" else '    freshness_sla: "daily_after_close"\n'
    evidence_line = "" if omit_need_field == "evidence_status" else f'    evidence_status: "{evidence_status}"\n'
    eligibility_line = (
        ""
        if omit_need_field == "production_eligibility"
        else f'    production_eligibility: "{production_eligibility}"\n'
    )
    path.write_text(
        f"""
input_paths:
  - "{evidence_path}"
needs:
  - need_id: "need_test"
{need_name_line}    consumer: "test"
{grain_line}{pit_key_line}{freshness_line}{evidence_line}{eligibility_line}    current_source: "old"
    tdxhub_capability: "tdxhub.test"
    tdx_coverage_level: "full"
    preferred_source: "tdxhub_quote"
    fallback_source: "akshare"
    action: "keep"
    notes: "test row"
priorities:
  - data_domain: "test_domain"
    preferred_source: "tdxhub_quote"
    fallback_1: "akshare"
    fallback_2: null
    reason: "test reason"
reassignments:
  - table_name: "test_table"
    current_source: "old"
    proposed_primary_source: "tdxhub_quote"
    fallback_source: "akshare"
    migration_required: true
    risk: "low"
    reason: "test reason"
""",
        encoding="utf-8",
    )


def test_load_config_rejects_missing_required_field(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("source evidence\n", encoding="utf-8")
    config = tmp_path / "tdx_data_need_coverage.yaml"
    _write_config(config, evidence, omit_need_field="need_name")

    with pytest.raises(ValueError, match="missing required fields: need_name"):
        audit_tdx_data_need_coverage.load_tdx_data_need_config(config)


@pytest.mark.parametrize(
    "field",
    ["grain", "pit_key", "freshness_sla", "evidence_status", "production_eligibility"],
)
def test_load_config_rejects_missing_contract_fields(tmp_path: Path, field: str) -> None:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("source evidence\n", encoding="utf-8")
    config = tmp_path / "tdx_data_need_coverage.yaml"
    _write_config(config, evidence, omit_need_field=field)

    with pytest.raises(ValueError, match=f"missing required fields: {field}"):
        audit_tdx_data_need_coverage.load_tdx_data_need_config(config)


def test_load_config_rejects_invalid_evidence_status(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("source evidence\n", encoding="utf-8")
    config = tmp_path / "tdx_data_need_coverage.yaml"
    _write_config(config, evidence, evidence_status="warn_only")

    with pytest.raises(ValueError, match="invalid evidence_status"):
        audit_tdx_data_need_coverage.load_tdx_data_need_config(config)


def test_load_config_rejects_eligible_unknown_pit_key(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("source evidence\n", encoding="utf-8")
    config = tmp_path / "tdx_data_need_coverage.yaml"
    _write_config(config, evidence, pit_key="unknown")

    with pytest.raises(ValueError, match="eligible need cannot use unknown pit_key"):
        audit_tdx_data_need_coverage.load_tdx_data_need_config(config)


def test_load_config_rejects_non_production_eligible_need(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("source evidence\n", encoding="utf-8")
    config = tmp_path / "tdx_data_need_coverage.yaml"
    _write_config(config, evidence, evidence_status="research", production_eligibility="eligible")

    with pytest.raises(ValueError, match="eligible production need requires evidence_status=production"):
        audit_tdx_data_need_coverage.load_tdx_data_need_config(config)


def test_default_config_points_to_existing_evidence_files() -> None:
    config = audit_tdx_data_need_coverage.load_tdx_data_need_config()
    input_paths = config["input_paths"]

    assert any(str(path).endswith("tdxhub/tdxhub/capabilities.py") for path in input_paths)
    assert all(path.exists() for path in input_paths)
    assert len(config["needs"]) == 27
    assert len(config["priorities"]) == 10
    assert len(config["reassignments"]) == 14
    needs_by_id = {row[0]: row for row in config["needs"]}
    assert needs_by_id["need_027"][1] == "主力/超大/大/中/小单资金流向"
    assert needs_by_id["need_027"][4] == "unknown"
    assert needs_by_id["need_027"][6] == "unknown"
    assert needs_by_id["need_027"][7] == "blocked"
    assert needs_by_id["need_027"][10] == "none"


def test_audit_tdx_data_need_coverage_writes_expected_rows(tmp_path: Path) -> None:
    root = tmp_path / "root"
    evidence = root / "evidence.md"
    config = root / "tdx_data_need_coverage.yaml"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("source evidence\n", encoding="utf-8")
    _write_config(config, evidence)

    conn = duckdb.connect(":memory:")
    try:
        result = audit_tdx_data_need_coverage.audit_tdx_data_need_coverage(conn, config)
        counts = {
            "coverage": conn.execute("SELECT COUNT(*) FROM mart_tdx_data_need_coverage").fetchone()[0],
            "priority": conn.execute("SELECT COUNT(*) FROM dim_data_source_priority").fetchone()[0],
            "reassignment": conn.execute(
                "SELECT COUNT(*) FROM mart_data_source_reassignment_proposal"
            ).fetchone()[0],
        }
        contract_row = conn.execute(
            """
            SELECT grain, pit_key, freshness_sla, evidence_status, production_eligibility
              FROM mart_tdx_data_need_coverage
             WHERE need_id = 'need_test'
            """
        ).fetchone()
    finally:
        conn.close()

    assert result["coverage_rows"] == 1
    assert result["priority_rows"] == 1
    assert result["reassignment_rows"] == 1
    assert counts == {"coverage": 1, "priority": 1, "reassignment": 1}
    assert contract_row == (
        "stock_code x trade_date",
        "trade_date",
        "daily_after_close",
        "production",
        "eligible",
    )


def test_audit_tdx_data_need_coverage_exact_sync_removes_obsolete_rows(tmp_path: Path) -> None:
    root = tmp_path / "root"
    evidence = root / "evidence.md"
    config = root / "tdx_data_need_coverage.yaml"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("source evidence\n", encoding="utf-8")
    _write_config(config, evidence)

    conn = duckdb.connect(":memory:")
    try:
        audit_tdx_data_need_coverage.ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_tdx_data_need_coverage
            (need_id, need_name, consumer, grain, pit_key, freshness_sla,
             evidence_status, production_eligibility, current_source, tdxhub_capability,
             tdx_coverage_level, preferred_source, fallback_source, action, notes, built_at)
            VALUES (
                'obsolete_need', 'obsolete need', 'old consumer', 'old grain', 'old pit',
                'old sla', 'unknown', 'blocked', 'old', 'old cap', 'none',
                'old source', NULL, 'remove', 'obsolete row', 'old built_at'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dim_data_source_priority
            VALUES ('obsolete_domain', 'old source', NULL, NULL, 'obsolete row', 'old updated_at')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_data_source_reassignment_proposal
            VALUES ('obsolete_table', 'old', 'old source', NULL, false, 'high', 'obsolete row', 'old built_at')
            """
        )

        result = audit_tdx_data_need_coverage.audit_tdx_data_need_coverage(conn, config)
        keys = {
            "coverage": [
                row[0]
                for row in conn.execute(
                    "SELECT need_id FROM mart_tdx_data_need_coverage ORDER BY need_id"
                ).fetchall()
            ],
            "priority": [
                row[0]
                for row in conn.execute(
                    "SELECT data_domain FROM dim_data_source_priority ORDER BY data_domain"
                ).fetchall()
            ],
            "reassignment": [
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM mart_data_source_reassignment_proposal ORDER BY table_name"
                ).fetchall()
            ],
        }
    finally:
        conn.close()

    assert result["coverage_rows"] == 1
    assert result["priority_rows"] == 1
    assert result["reassignment_rows"] == 1
    assert keys == {
        "coverage": ["need_test"],
        "priority": ["test_domain"],
        "reassignment": ["test_table"],
    }
