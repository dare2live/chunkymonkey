from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_tdx_data_need_coverage.py"
SPEC = importlib.util.spec_from_file_location("audit_tdx_data_need_coverage", SCRIPT_PATH)
audit_tdx_data_need_coverage = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_tdx_data_need_coverage
SPEC.loader.exec_module(audit_tdx_data_need_coverage)

from services import db as db_facade  # noqa: E402


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


@pytest.mark.parametrize(
    ("source_name", "family"),
    [
        ("tdxhub_quote", "tdxhub"),
        ("tdxhub_f10", "tdxhub"),
        ("miaoxiang", "aif10"),
        ("miaoxiang/aif10_scraper/registry.py", "aif10"),
        ("akshare", "akshare"),
    ],
)
def test_canonical_source_family_maps_family_aliases(source_name: str, family: str) -> None:
    assert audit_tdx_data_need_coverage._canonical_source_family(source_name) == family


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


def test_summarize_need_gaps_identifies_only_blocked_need() -> None:
    config = audit_tdx_data_need_coverage.load_tdx_data_need_config()
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_data_source_failure_queue (
                failure_id VARCHAR,
                data_domain VARCHAR,
                source_name VARCHAR,
                source_tier SMALLINT,
                stock_code VARCHAR,
                error_type VARCHAR,
                last_error VARCHAR,
                status VARCHAR,
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                retry_after TIMESTAMP,
                occurrence_count INTEGER,
                resolved_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_data_source_failure_queue VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "2df0b27c9086b45e",
                "order_flow_fund_flow",
                "akshare",
                3,
                "600519",
                "RuntimeError",
                "ConnectionError: remote disconnect",
                "open",
                "2026-06-01 01:05:18",
                "2026-06-01 03:57:25",
                None,
                3,
                None,
            ],
        )
        conn.execute(
            """
            INSERT INTO mart_data_source_failure_queue VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "a906f9ddbdd284f1",
                "stock_fund_flow_rank_snapshot",
                "akshare",
                3,
                None,
                "watermark_failure",
                "research-side rank snapshot only; exact need_027 flow remains blocked/unknown",
                "resolved",
                "2026-06-01 06:37:29",
                "2026-06-01 14:35:33",
                None,
                8,
                "2026-06-01 14:35:33",
            ],
        )
        summary = audit_tdx_data_need_coverage._summarize_need_gaps(conn, config["needs"])
    finally:
        conn.close()

    assert summary["need_count"] == 27
    assert summary["registered_source_names"] == ["aif10", "akshare", "tdxhub"]
    assert summary["eligibility_counts"]["eligible"] == 4
    assert summary["eligibility_counts"]["research_only"] == 22
    assert summary["eligibility_counts"]["blocked"] == 1
    assert summary["blocked_need_count"] == 1
    blocked = summary["blocked_needs"][0]
    assert blocked["need_id"] == "need_027"
    assert blocked["need_name"] == "主力/超大/大/中/小单资金流向"
    assert blocked["consumer"] == "cyq_position_monitor, institution_score, sniper_score"
    assert blocked["current_source"] == "raw_fund_flow_daily deprecated/stale"
    assert blocked["tdxhub_capability"] == "not in current TDX path"
    assert blocked["pit_key"] == "unknown"
    assert blocked["evidence_status"] == "unknown"
    assert blocked["production_eligibility"] == "blocked"
    assert blocked["preferred_source"] == "akshare"
    assert blocked["fallback_source"] == "miaoxiang"
    assert blocked["source_registration"]["registered_source_names"] == ["aif10", "akshare", "tdxhub"]
    assert blocked["source_registration"]["preferred_source_registered"] is True
    assert blocked["source_registration"]["fallback_source_registered"] is False
    assert blocked["source_registration"]["preferred_source_family"] == "akshare"
    assert blocked["source_registration"]["preferred_source_family_registered"] is True
    assert blocked["source_registration"]["fallback_source_family"] == "aif10"
    assert blocked["source_registration"]["fallback_source_family_registered"] is True
    assert "individual_fund_flow" in blocked["source_registration"]["preferred_source_capabilities"]
    assert "individual_fund_flow_rank_snapshot" in blocked["source_registration"]["preferred_source_capabilities"]
    assert "individual_fund_flow" not in blocked["source_registration"]["fallback_source_capabilities"]
    assert "individual_fund_flow_rank_snapshot" not in blocked["source_registration"]["fallback_source_capabilities"]
    assert blocked["source_registration"]["preferred_source_supports_individual_fund_flow"] is True
    assert blocked["source_registration"]["fallback_source_supports_individual_fund_flow"] is False
    assert blocked["failure_queue_snapshot"]["row_count"] == 2
    assert blocked["failure_queue_snapshot"]["status_counts"] == {"open": 1, "resolved": 1}
    assert blocked["failure_queue_snapshot"]["latest_open_row"]["data_domain"] == "order_flow_fund_flow"
    assert blocked["failure_queue_snapshot"]["latest_open_row"]["stock_code"] == "600519"
    assert blocked["failure_queue_snapshot"]["latest_resolved_row"]["data_domain"] == "stock_fund_flow_rank_snapshot"
    assert blocked["action"] == "probe_restore_or_keep_unknown"
    assert "CYQ 主力画像需要真实订单流" in blocked["notes"]


def test_source_registration_summary_reports_registered_families_and_capabilities() -> None:
    registered_sources = {
        "akshare": SimpleNamespace(
            capabilities=[
                SimpleNamespace(name="individual_fund_flow"),
                SimpleNamespace(name="individual_fund_flow_rank_snapshot"),
            ]
        ),
        "aif10": SimpleNamespace(capabilities=[SimpleNamespace(name="other_capability")]),
    }

    summary = audit_tdx_data_need_coverage._source_registration_summary(
        "akshare",
        "miaoxiang",
        registered_source_names=["aif10", "akshare", "tdxhub"],
        registered_source_name_set={"aif10", "akshare", "tdxhub"},
        registered_sources=registered_sources,
    )

    assert summary["registered_source_names"] == ["aif10", "akshare", "tdxhub"]
    assert summary["preferred_source_registered"] is True
    assert summary["preferred_source_family"] == "akshare"
    assert summary["preferred_source_family_registered"] is True
    assert summary["fallback_source_registered"] is False
    assert summary["fallback_source_family"] == "aif10"
    assert summary["fallback_source_family_registered"] is True
    assert summary["preferred_source_capabilities"] == [
        "individual_fund_flow",
        "individual_fund_flow_rank_snapshot",
    ]
    assert summary["fallback_source_capabilities"] == ["other_capability"]
    assert summary["preferred_source_supports_individual_fund_flow"] is True
    assert summary["fallback_source_supports_individual_fund_flow"] is False


def test_blocked_need_summary_preserves_need_entry_fields() -> None:
    source_registration = {
        "registered_source_names": ["aif10", "akshare", "tdxhub"],
        "preferred_source_registered": True,
        "preferred_source_family": "akshare",
        "preferred_source_family_registered": True,
        "fallback_source_registered": False,
        "fallback_source_family": "aif10",
        "fallback_source_family_registered": True,
        "preferred_source_capabilities": ["individual_fund_flow"],
        "fallback_source_capabilities": [],
        "preferred_source_supports_individual_fund_flow": True,
        "fallback_source_supports_individual_fund_flow": False,
    }
    failure_queue_snapshot = {
        "row_count": 2,
        "status_counts": {"open": 1, "resolved": 1},
        "latest_open_row": {"data_domain": "order_flow_fund_flow"},
        "latest_resolved_row": {"data_domain": "stock_fund_flow_rank_snapshot"},
    }
    record = {
        "need_id": "need_027",
        "need_name": "主力/超大/大/中/小单资金流向",
        "consumer": "cyq_position_monitor, institution_score, sniper_score",
        "current_source": "raw_fund_flow_daily deprecated/stale",
        "tdxhub_capability": "not in current TDX path",
        "pit_key": "unknown",
        "preferred_source": "akshare",
        "fallback_source": "miaoxiang",
        "action": "probe_restore_or_keep_unknown",
        "notes": "CYQ 主力画像需要真实订单流",
    }

    blocked = audit_tdx_data_need_coverage._blocked_need_summary(
        record,
        evidence_status="unknown",
        eligibility="blocked",
        source_registration=source_registration,
        failure_queue_snapshot=failure_queue_snapshot,
    )

    assert blocked["need_id"] == "need_027"
    assert blocked["need_name"] == "主力/超大/大/中/小单资金流向"
    assert blocked["consumer"] == "cyq_position_monitor, institution_score, sniper_score"
    assert blocked["current_source"] == "raw_fund_flow_daily deprecated/stale"
    assert blocked["tdxhub_capability"] == "not in current TDX path"
    assert blocked["pit_key"] == "unknown"
    assert blocked["evidence_status"] == "unknown"
    assert blocked["production_eligibility"] == "blocked"
    assert blocked["preferred_source"] == "akshare"
    assert blocked["fallback_source"] == "miaoxiang"
    assert blocked["source_registration"] == source_registration
    assert blocked["failure_queue_snapshot"] == failure_queue_snapshot
    assert blocked["action"] == "probe_restore_or_keep_unknown"
    assert blocked["notes"] == "CYQ 主力画像需要真实订单流"


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
    assert result["need_gap_summary"]["need_count"] == 1
    assert result["need_gap_summary"]["blocked_need_count"] == 0
    assert counts == {"coverage": 1, "priority": 1, "reassignment": 1}
    assert contract_row == (
        "stock_code x trade_date",
        "trade_date",
        "daily_after_close",
        "production",
        "eligible",
    )
    assert result["materialized"] is True


def test_summarize_tdx_data_need_coverage_does_not_materialize_tables(tmp_path: Path) -> None:
    root = tmp_path / "root"
    evidence = root / "evidence.md"
    config = root / "tdx_data_need_coverage.yaml"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("source evidence\n", encoding="utf-8")
    _write_config(config, evidence)

    conn = duckdb.connect(":memory:")
    try:
        result = audit_tdx_data_need_coverage.summarize_tdx_data_need_coverage(conn, config)
        table_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM information_schema.tables
             WHERE table_name IN (
                'mart_tdx_data_need_coverage',
                'dim_data_source_priority',
                'mart_data_source_reassignment_proposal'
             )
            """
        ).fetchone()[0]
    finally:
        conn.close()

    assert result["coverage_rows"] == 1
    assert result["priority_rows"] == 1
    assert result["reassignment_rows"] == 1
    assert result["need_gap_summary"]["blocked_need_count"] == 0
    assert result["materialized"] is False
    assert table_count == 0


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


def test_main_emits_json_when_requested(monkeypatch, capsys) -> None:
    class DummyConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        audit_tdx_data_need_coverage,
        "get_conn",
        lambda: DummyConn(),
    )
    monkeypatch.setattr(
        audit_tdx_data_need_coverage,
        "audit_tdx_data_need_coverage",
        lambda conn, config_path=None: {
            "coverage_rows": 1,
            "priority_rows": 1,
            "reassignment_rows": 1,
            "need_gap_summary": {"need_count": 1, "blocked_need_count": 0},
            "config_path": "config.yaml",
            "input_files_read": [],
            "built_at": "2026-06-01T00:00:00+00:00",
        },
    )

    rc = audit_tdx_data_need_coverage.main(["--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["coverage_rows"] == 1
    assert payload["need_gap_summary"] == {"need_count": 1, "blocked_need_count": 0}


def test_main_summary_only_uses_read_only_summary_path(monkeypatch, capsys) -> None:
    class DummyConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        audit_tdx_data_need_coverage,
        "get_conn",
        lambda: pytest.fail("summary-only must not open the write/materialize connection"),
    )
    monkeypatch.setattr(audit_tdx_data_need_coverage, "get_read_only_conn", lambda: DummyConn())
    monkeypatch.setattr(
        audit_tdx_data_need_coverage,
        "summarize_tdx_data_need_coverage",
        lambda conn, config_path=None: {
            "coverage_rows": 1,
            "priority_rows": 1,
            "reassignment_rows": 1,
            "need_gap_summary": {"need_count": 1, "blocked_need_count": 0},
            "config_path": "config.yaml",
            "input_files_read": [],
            "built_at": "2026-06-01T00:00:00+00:00",
            "materialized": False,
        },
    )

    rc = audit_tdx_data_need_coverage.main(["--summary-only", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["materialized"] is False
    assert payload["need_gap_summary"] == {"need_count": 1, "blocked_need_count": 0}


def test_get_read_only_conn_uses_runtime_db_path(monkeypatch, tmp_path: Path) -> None:
    runtime_db_dir = tmp_path / "runtime-db"
    runtime_db_path = runtime_db_dir / "smartmoney.duckdb"
    captured: dict[str, object] = {}

    class DummyConn:
        pass

    def fake_duck_connect(path: str, *, read_only: bool, timeout: int) -> DummyConn:
        captured.update({"path": path, "read_only": read_only, "timeout": timeout})
        return DummyConn()

    monkeypatch.setattr(db_facade, "DB_DIR", runtime_db_dir)
    monkeypatch.setattr(db_facade, "DB_PATH", runtime_db_path)
    monkeypatch.setattr(audit_tdx_data_need_coverage, "duck_connect", fake_duck_connect)

    conn = audit_tdx_data_need_coverage.get_read_only_conn(timeout=7)

    assert isinstance(conn, DummyConn)
    assert captured == {"path": str(runtime_db_path), "read_only": True, "timeout": 7}
    assert runtime_db_dir.exists()
