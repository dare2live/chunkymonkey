from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import evaluate_champion_candidate as subject


pytestmark = pytest.mark.pipeline


def test_evaluate_champion_candidate_runs_pit_then_evidence_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = duck_mem()
    captured: dict[str, object] = {}

    def fake_pit_audit(conn_arg, **kwargs):
        captured["pit_kwargs"] = kwargs
        assert conn_arg is conn
        return {
            "audit_run_id": kwargs["audit_run_id"],
            "model_id": kwargs["model_id"],
            "feature_table": kwargs["feature_table"],
            "feature_set_id": kwargs.get("feature_set_id") or "production_registry",
            "features": 2,
            "violation_rows": 0,
            "status": "passed",
            "audited_at": "2026-05-06T00:00:00",
        }

    def fake_evidence_bundle(**kwargs):
        captured["evidence_kwargs"] = kwargs
        return {
            "evidence_run_id": "evidence_unit",
            "model_id": kwargs["model_id"],
            "status": "success",
            "gate_status": "PASS",
            "failed_steps": [],
            "duration_s": 1.0,
        }

    monkeypatch.setattr(subject, "audit_registry_feature_pit", fake_pit_audit)
    monkeypatch.setattr(subject, "build_evidence_bundle", fake_evidence_bundle)

    try:
        result = subject.evaluate_champion_candidate(
            conn,
            model_id="candidate_1",
            feature_table="fact_feature_panel",
            feature_set_id="production_registry",
            top_k=20,
            pit_audit_run_id="pit_unit",
        )
        stored = conn.execute(
            """
            SELECT model_id, status, pit_audit_run_id, pit_status,
                   evidence_run_id, evidence_status, gate_status, config_json
              FROM mart_champion_candidate_evaluation
             WHERE evaluation_run_id = ?
            """,
            [result["evaluation_run_id"]],
        ).fetchone()
        manifest = conn.execute(
            """
            SELECT pipeline_name, status, gate_result, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = ?
            """,
            [result["evaluation_run_id"]],
        ).fetchone()

        assert result["status"] == "passed"
        assert result["pit_audit_run_id"] == "pit_unit"
        assert result["evidence_run_id"] == "evidence_unit"
        assert captured["pit_kwargs"]["audit_run_id"] == "pit_unit"
        assert captured["evidence_kwargs"]["pit_audit_run_id"] == "pit_unit"
        assert captured["evidence_kwargs"]["top_k"] == 20
        assert stored["model_id"] == "candidate_1"
        assert stored["status"] == "passed"
        assert stored["pit_status"] == "passed"
        assert stored["evidence_status"] == "success"
        assert stored["gate_status"] == "PASS"
        assert json.loads(stored["config_json"])["top_k"] == 20
        assert manifest["pipeline_name"] == "evaluate_champion_candidate"
        assert manifest["status"] == "passed"
        assert manifest["gate_result"] == "PASS"
        assert json.loads(manifest["perf_summary_json"])["evidence_result"]["gate_status"] == "PASS"
    finally:
        conn.close()


def test_evaluate_champion_candidate_releases_db_before_evidence_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    opened = {"count": 0}

    class LoggedConn:
        def __init__(self, name: str):
            self.name = name
            self.conn = duck_mem()

        def __enter__(self):
            events.append(f"enter:{self.name}")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append(f"exit:{self.name}")
            self.conn.close()

        def __getattr__(self, item):
            return getattr(self.conn, item)

    def connection_factory():
        opened["count"] += 1
        return LoggedConn("audit" if opened["count"] == 1 else "write")

    def fake_pit_audit(conn_arg, **kwargs):
        events.append("audit")
        return {
            "audit_run_id": kwargs["audit_run_id"],
            "model_id": kwargs["model_id"],
            "feature_table": kwargs["feature_table"],
            "feature_set_id": "production_registry",
            "features": 2,
            "violation_rows": 0,
            "status": "passed",
            "audited_at": "2026-05-06T00:00:00",
        }

    def fake_evidence_bundle(**kwargs):
        assert events == ["enter:audit", "audit", "exit:audit"]
        events.append("evidence")
        return {
            "evidence_run_id": "evidence_no_lock",
            "model_id": kwargs["model_id"],
            "status": "success",
            "gate_status": "PASS",
            "failed_steps": [],
            "duration_s": 1.0,
        }

    monkeypatch.setattr(subject, "audit_registry_feature_pit", fake_pit_audit)
    monkeypatch.setattr(subject, "build_evidence_bundle", fake_evidence_bundle)

    result = subject.evaluate_champion_candidate(
        model_id="candidate_no_lock",
        pit_audit_run_id="pit_no_lock",
        connection_factory=connection_factory,
    )

    assert result["status"] == "passed"
    assert events == [
        "enter:audit",
        "audit",
        "exit:audit",
        "evidence",
        "enter:write",
        "exit:write",
    ]


def test_evaluate_champion_candidate_records_failed_pit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = duck_mem()

    monkeypatch.setattr(
        subject,
        "audit_registry_feature_pit",
        lambda conn_arg, **kwargs: {
            "audit_run_id": kwargs["audit_run_id"],
            "model_id": kwargs["model_id"],
            "feature_table": kwargs["feature_table"],
            "feature_set_id": "production_registry",
            "features": 2,
            "violation_rows": 3,
            "status": "failed",
            "audited_at": "2026-05-06T00:00:00",
        },
    )
    monkeypatch.setattr(
        subject,
        "build_evidence_bundle",
        lambda **kwargs: {
            "evidence_run_id": "evidence_failed",
            "model_id": kwargs["model_id"],
            "status": "success",
            "gate_status": "FAIL",
            "failed_steps": [],
            "duration_s": 1.0,
        },
    )

    try:
        result = subject.evaluate_champion_candidate(
            conn,
            model_id="candidate_bad",
            pit_audit_run_id="pit_failed",
        )
        row = conn.execute(
            """
            SELECT status, pit_status, pit_violation_rows, gate_status
              FROM mart_champion_candidate_evaluation
             WHERE evaluation_run_id = ?
            """,
            [result["evaluation_run_id"]],
        ).fetchone()

        assert result["status"] == "failed"
        assert result["pit_status"] == "failed"
        assert result["pit_violation_rows"] == 3
        assert row["status"] == "failed"
        assert row["pit_violation_rows"] == 3
        assert row["gate_status"] == "FAIL"
    finally:
        conn.close()


def test_evaluate_champion_candidate_fails_when_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = duck_mem()

    monkeypatch.setattr(
        subject,
        "audit_registry_feature_pit",
        lambda conn_arg, **kwargs: {
            "audit_run_id": kwargs["audit_run_id"],
            "model_id": kwargs["model_id"],
            "feature_table": kwargs["feature_table"],
            "feature_set_id": "production_registry",
            "features": 2,
            "violation_rows": 0,
            "status": "passed",
            "audited_at": "2026-05-06T00:00:00",
        },
    )
    monkeypatch.setattr(
        subject,
        "build_evidence_bundle",
        lambda **kwargs: {
            "evidence_run_id": "evidence_gate_failed",
            "model_id": kwargs["model_id"],
            "status": "success",
            "gate_status": "FAIL",
            "failed_steps": [],
            "duration_s": 1.0,
        },
    )

    try:
        result = subject.evaluate_champion_candidate(
            conn,
            model_id="candidate_gate_bad",
            pit_audit_run_id="pit_gate_passed",
        )
        row = conn.execute(
            """
            SELECT status, pit_status, evidence_status, gate_status
              FROM mart_champion_candidate_evaluation
             WHERE evaluation_run_id = ?
            """,
            [result["evaluation_run_id"]],
        ).fetchone()

        assert result["status"] == "failed"
        assert result["pit_status"] == "passed"
        assert result["evidence_status"] == "success"
        assert result["gate_status"] == "FAIL"
        assert row["status"] == "failed"
        assert row["pit_status"] == "passed"
        assert row["evidence_status"] == "success"
        assert row["gate_status"] == "FAIL"
    finally:
        conn.close()
