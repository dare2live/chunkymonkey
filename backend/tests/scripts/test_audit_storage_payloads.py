from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_storage_payloads.py"
SPEC = importlib.util.spec_from_file_location("audit_storage_payloads", SCRIPT_PATH)
audit_storage_payloads = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_storage_payloads
SPEC.loader.exec_module(audit_storage_payloads)


def _policy(**overrides):
    policy = dict(audit_storage_payloads.DEFAULT_POLICY)
    policy.update(
        {
            "max_value_warn_bytes": 10,
            "max_value_fail_bytes": 50,
            "total_value_warn_bytes": 40,
            "total_value_fail_bytes": 200,
            "top_sample_rows": 5,
            "duplicate_sample_warn_count": 2,
            "recursive_keywords": ("latest_dispatch", "queue_items"),
            "path_markers": ("data/reports/",),
        }
    )
    policy.update(overrides)
    return policy


def test_storage_payload_audit_fails_large_payload() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_today_signal_cache (signals_json TEXT)")
        conn.execute("INSERT INTO mart_today_signal_cache VALUES (?)", ("x" * 60,))

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(),
            tables=["mart_today_signal_cache"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "FAIL"
    finding = report["findings"][0]
    assert finding["table"] == "mart_today_signal_cache"
    assert finding["column"] == "signals_json"
    assert finding["max_value_bytes"] == 60
    assert "max_value_bytes exceeds fail threshold" in finding["reasons"]


def test_storage_payload_audit_fails_recursive_keyword() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_audit_snapshot_state (audit_json TEXT)")
        conn.execute("INSERT INTO mart_audit_snapshot_state VALUES (?)", ('{"latest_dispatch": {"queue_items": []}}',))

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(max_value_fail_bytes=500),
            tables=["mart_audit_snapshot_state"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "FAIL"
    finding = report["findings"][0]
    assert finding["recursive_keyword_hits"] == 1
    assert "recursive keyword detected" in finding["reasons"]


def test_storage_payload_audit_ignores_scalar_varchar_columns() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fact_feature_panel (stock_code TEXT, built_at TEXT, payload_json TEXT)")
        conn.executemany(
            "INSERT INTO fact_feature_panel VALUES (?, ?, ?)",
            [("000001", "2026-05-31T00:00:00", "{}") for _ in range(12)],
        )

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(max_value_fail_bytes=500, total_value_fail_bytes=50),
            tables=["fact_feature_panel"],
        )
    finally:
        conn.close()

    scanned_columns = {finding["column"] for finding in report["findings"]}
    assert scanned_columns == {"payload_json"}
    assert report["verdict"] == "PASS"


def test_storage_payload_audit_requires_json_key_shape_for_recursive_hits() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_pipeline_run_manifest (command TEXT, perf_summary_json TEXT)")
        conn.execute(
            "INSERT INTO mart_pipeline_run_manifest VALUES (?, ?)",
            (
                'codegraph context "latest_dispatch queue_items"',
                '{"command": "mentions latest_dispatch and queue_items as values only"}',
            ),
        )

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                max_value_warn_bytes=500,
                max_value_fail_bytes=1000,
                total_value_warn_bytes=1000,
                total_value_fail_bytes=2000,
            ),
            tables=["mart_pipeline_run_manifest"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "PASS"
    assert {finding["recursive_keyword_hits"] for finding in report["findings"]} == {0}


def test_storage_payload_audit_warns_path_marker_and_duplicate_samples() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_report_cache (payload_json TEXT)")
        payload = "data/reports/" + ("a" * 20)
        conn.executemany("INSERT INTO mart_report_cache VALUES (?)", [(payload,), (payload,)])

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(max_value_fail_bytes=500, total_value_fail_bytes=500),
            tables=["mart_report_cache"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "WARN"
    finding = report["findings"][0]
    assert finding["path_marker_hits"] == 2
    assert finding["top_sample_duplicate_count"] == 2
    assert "path marker detected in payload" in finding["reasons"]
    assert "duplicate large top-sample payloads detected" in finding["reasons"]


def test_storage_payload_reviewed_bounded_column_passes_total_warn() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fact_technical_trigger (reason_codes_json TEXT)")
        conn.executemany("INSERT INTO fact_technical_trigger VALUES (?)", [("[\"a\"]",) for _ in range(12)])

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                total_value_warn_bytes=20,
                reviewed_columns=[
                    {
                        "table": "fact_technical_trigger",
                        "column": "reason_codes_json",
                        "classification": "compact_reason_codes",
                        "max_value_bytes": 20,
                        "max_total_value_bytes": 100,
                    }
                ],
            ),
            tables=["fact_technical_trigger"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "PASS"
    assert report["summary"]["reviewed"] == 1
    finding = report["findings"][0]
    assert finding["severity"] == "PASS"
    assert finding["review"]["status"] == "accepted"
    assert finding["reasons"] == ["reviewed payload: compact_reason_codes"]


def test_storage_payload_reviewed_macd_state_history_reason_codes_passes_total_warn() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_macd_state_history (reason_codes_json TEXT)")
        conn.executemany("INSERT INTO mart_macd_state_history VALUES (?)", [("[\"macd_state:holding\"]",) for _ in range(12)])

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                total_value_warn_bytes=20,
                total_value_fail_bytes=1000,
                reviewed_columns=[
                    {
                        "table": "mart_macd_state_history",
                        "column": "reason_codes_json",
                        "classification": "diagnostic_state_history_evidence",
                        "max_value_bytes": 32,
                        "max_total_value_bytes": 1000,
                    }
                ],
            ),
            tables=["mart_macd_state_history"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "PASS"
    assert report["summary"]["reviewed"] == 1
    finding = report["findings"][0]
    assert finding["severity"] == "PASS"
    assert finding["review"]["status"] == "accepted"
    assert finding["reasons"] == ["reviewed payload: diagnostic_state_history_evidence"]


def test_storage_payload_reviewed_picture_institution_summary_passes_total_warn() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_stock_picture_daily (institution_top_json TEXT)")
        payload = '[{"name":"inst","score":1}]'
        conn.executemany("INSERT INTO mart_stock_picture_daily VALUES (?)", [(payload,) for _ in range(12)])

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                total_value_warn_bytes=20,
                total_value_fail_bytes=1000,
                reviewed_columns=[
                    {
                        "table": "mart_stock_picture_daily",
                        "column": "institution_top_json",
                        "classification": "bounded_picture_daily_institution_summary",
                        "max_value_bytes": 128,
                        "max_total_value_bytes": 1000,
                    }
                ],
            ),
            tables=["mart_stock_picture_daily"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "PASS"
    assert report["summary"]["reviewed"] == 1
    finding = report["findings"][0]
    assert finding["severity"] == "PASS"
    assert finding["review"]["status"] == "accepted"
    assert finding["reasons"] == ["reviewed payload: bounded_picture_daily_institution_summary"]


def test_default_payload_policy_contains_current_capacity_review_rules() -> None:
    policy = audit_storage_payloads.load_payload_policy()
    by_column = {
        (rule["table"], rule["column"]): rule
        for rule in policy["reviewed_columns"]
    }

    assert by_column[("fact_technical_trigger", "reason_codes_json")]["max_total_value_bytes"] == 536870912
    assert by_column[("mart_macd_state_history", "reason_codes_json")]["max_total_value_bytes"] == 536870912
    assert (
        by_column[("mart_stock_picture_daily", "institution_top_json")]["classification"]
        == "bounded_picture_daily_institution_summary"
    )
    assert by_column[("mart_stock_picture_daily", "institution_top_json")]["owner"] == "picture_daily"
    assert by_column[("mart_stock_picture_daily", "institution_top_json")]["max_value_bytes"] == 2048
    assert by_column[("mart_stock_picture_daily", "institution_top_json")]["max_total_value_bytes"] == 67108864


def test_storage_payload_reviewed_column_never_downgrades_recursive_fail() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_audit_snapshot_state (audit_json TEXT)")
        conn.execute("INSERT INTO mart_audit_snapshot_state VALUES (?)", ('{"latest_dispatch": {}}',))

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                max_value_fail_bytes=500,
                reviewed_columns=[
                    {
                        "table": "mart_audit_snapshot_state",
                        "column": "audit_json",
                        "classification": "bad_review_rule",
                        "max_value_bytes": 500,
                    }
                ],
            ),
            tables=["mart_audit_snapshot_state"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "FAIL"
    finding = report["findings"][0]
    assert finding["review"]["status"] == "blocked"
    assert "recursive keyword hits" in finding["review"]["blockers"]
    assert "recursive keyword detected" in finding["reasons"]


def test_storage_payload_reviewed_path_pointer_passes_when_allowed() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_paper_sim_kpi (lineage_url TEXT)")
        conn.execute("INSERT INTO mart_paper_sim_kpi VALUES (?)", ("data/reports/lineage/run.md",))

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                reviewed_columns=[
                    {
                        "table": "mart_paper_sim_kpi",
                        "column": "lineage_url",
                        "classification": "lineage_pointer",
                        "allow_path_marker": True,
                        "max_value_bytes": 100,
                    }
                ],
            ),
            tables=["mart_paper_sim_kpi"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "PASS"
    finding = report["findings"][0]
    assert finding["path_marker_hits"] == 1
    assert finding["review"]["status"] == "accepted"
    assert finding["reasons"] == ["reviewed payload: lineage_pointer"]


def test_storage_payload_reviewed_cap_breach_stays_warn() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE mart_today_signal_cache_signal (signal_json TEXT)")
        conn.execute("INSERT INTO mart_today_signal_cache_signal VALUES (?)", ("x" * 12,))

        report = audit_storage_payloads.build_storage_payload_report(
            conn,
            policy=_policy(
                max_value_warn_bytes=10,
                max_value_fail_bytes=50,
                reviewed_columns=[
                    {
                        "table": "mart_today_signal_cache_signal",
                        "column": "signal_json",
                        "classification": "bounded_cache_detail_row",
                        "max_value_bytes": 10,
                    }
                ],
            ),
            tables=["mart_today_signal_cache_signal"],
        )
    finally:
        conn.close()

    assert report["verdict"] == "WARN"
    finding = report["findings"][0]
    assert finding["review"]["status"] == "blocked"
    assert "max_value_bytes exceeds reviewed cap" in finding["review"]["blockers"]
    assert "max_value_bytes exceeds warn threshold" in finding["reasons"]


def test_storage_payload_markdown_renders_findings() -> None:
    report = {
        "verdict": "FAIL",
        "summary": {"columns_scanned": 1, "fail": 1, "warn": 0, "reviewed": 0, "pass": 0},
        "findings": [
            {
                "severity": "FAIL",
                "table": "mart_today_signal_cache",
                "column": "signals_json",
                "max_value_bytes": 60,
                "total_value_bytes": 60,
                "recursive_keyword_hits": 0,
                "path_marker_hits": 0,
                "reasons": ["max_value_bytes exceeds fail threshold"],
            }
        ],
    }

    markdown = audit_storage_payloads.render_markdown(report)

    assert "Storage Payload Audit" in markdown
    assert "`mart_today_signal_cache`" in markdown
    assert "max_value_bytes exceeds fail threshold" in markdown
