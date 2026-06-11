from __future__ import annotations

from pathlib import Path

import pytest

from services.experiment_jobs import load_experiment_job_contract


def test_default_contract_declares_job_families_and_no_legacy_cloud_backend() -> None:
    contract = load_experiment_job_contract()

    assert set(contract.families) == {
        "backtest_validation",
        "data_validation",
        "model_training",
        "parameter_search",
    }
    assert set(contract.backends) == {"local", "modal"}
    assert contract.backends["local"].active is True
    # 2026-06-11: modal adapter (services/compute/modal_adapter.py) 上线后激活,
    # status planned -> active (reuses local plan gate; budget $30/mo; ~/.modal.toml)
    assert contract.backends["modal"].status == "active"


def test_local_data_validation_plan_is_runnable_contract_only() -> None:
    contract = load_experiment_job_contract()

    plan = contract.plan(
        "data_validation",
        job_id="daily_data_gate",
        input_snapshot="market.duckdb@2026-06-05",
        objective="refresh data health evidence",
        rollback_plan="read-only validation; no rollback needed",
        gate_evidence=(
            "data_health_snapshot=data/reports/data_health_latest.json",
            "data_audit=backend/services/data_audit.py",
            "source_watermark_sla=data/audit/watermark_sla_latest.json",
        ),
    )

    assert plan.ready_to_run is True
    report = plan.to_report()
    assert report["job_id"] == "daily_data_gate"
    assert report["family"]["command_family"] == "data_validation"
    assert "data_audit" in report["family"]["required_gates"]
    assert report["artifact_dir"] == "data/reports/experiment_jobs/daily_data_gate"


def test_missing_plan_fields_and_gate_evidence_block_readiness() -> None:
    contract = load_experiment_job_contract()

    plan = contract.plan("model_training", model_id="m1")

    assert plan.ready_to_run is False
    assert "missing_plan_field:input_snapshot" in plan.blocked_reasons
    assert "missing_plan_field:objective" in plan.blocked_reasons
    assert "missing_plan_field:rollback_plan" in plan.blocked_reasons
    assert "missing_gate_evidence:leakage_audit" in plan.blocked_reasons


def test_empty_or_malformed_gate_evidence_does_not_satisfy_required_gates() -> None:
    contract = load_experiment_job_contract()

    plan = contract.plan(
        "data_validation",
        input_snapshot="market.duckdb@2026-06-05",
        objective="refresh data health evidence",
        rollback_plan="read-only validation; no rollback needed",
        gate_evidence=(
            "data_health_snapshot=",
            "data_audit",
            "=watermark.json",
            "source_watermark_sla= ",
        ),
    )

    assert plan.ready_to_run is False
    assert plan.gate_evidence == ()
    assert "empty_gate_evidence:data_health_snapshot" in plan.blocked_reasons
    assert "malformed_gate_evidence:2" in plan.blocked_reasons
    assert "invalid_gate_evidence:3" in plan.blocked_reasons
    assert "empty_gate_evidence:source_watermark_sla" in plan.blocked_reasons
    assert "missing_gate_evidence:data_health_snapshot" in plan.blocked_reasons
    assert "missing_gate_evidence:data_audit" in plan.blocked_reasons
    assert "missing_gate_evidence:source_watermark_sla" in plan.blocked_reasons


def test_active_modal_backend_ready_when_plan_complete() -> None:
    # 2026-06-11: 取代旧 test_planned_modal_backend_blocks_until_adapter_exists.
    # modal adapter 上线 + status active 后, model_training (allowed_backends 含 modal)
    # 在 input_snapshot/objective/rollback_plan/required gate evidence 齐全时应 ready_to_run.
    contract = load_experiment_job_contract()

    plan = contract.plan(
        "model_training",
        backend_id="modal",
        model_id="m1",
        input_snapshot="smartmoney.duckdb@2026-06-05",
        objective="train probe",
        rollback_plan="discard artifact dir",
        gate_evidence=(
            "leakage_audit=data/reports/leakage.json",
            "train_log_integrity=data/reports/train_log.json",
            "phase4_gate=data/reports/phase4_gate.json",
        ),
    )

    assert plan.ready_to_run is True
    assert plan.blocked_reasons == ()


def test_unallowed_backend_is_blocked_even_when_backend_exists() -> None:
    contract = load_experiment_job_contract()

    plan = contract.plan("data_validation", backend_id="modal")

    assert plan.ready_to_run is False
    # data_validation 仅 allowed_backends=[local], modal 虽 active 但不在该 family 白名单
    assert "backend_not_allowed:modal" in plan.blocked_reasons
    assert "missing_plan_field:input_snapshot" in plan.blocked_reasons


def test_loader_rejects_family_referencing_unknown_backend(tmp_path: Path) -> None:
    config_path = tmp_path / "experiment_jobs.yaml"
    config_path.write_text(
        """
version: 1
backends:
  local:
    status: active
    execution_mode: same_host_command
    artifact_mode: local_manifest
job_families:
  data_validation:
    owner_module: data_quality
    purpose: unit
    allowed_backends: [unknown_backend]
    command_family: data_validation
    required_plan_fields: [input_snapshot]
    required_gates: [data_audit]
    artifact_contracts:
      - id: data_audit
        kind: report
        required: true
        path_glob: data/reports/audit.json
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown backend"):
        load_experiment_job_contract(config_path)
