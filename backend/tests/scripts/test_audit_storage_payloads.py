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


def test_storage_payload_markdown_renders_findings() -> None:
    report = {
        "verdict": "FAIL",
        "summary": {"columns_scanned": 1, "fail": 1, "warn": 0, "pass": 0},
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
