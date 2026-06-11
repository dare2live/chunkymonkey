"""Regression tests for the Modal experiment-job adapter.

These tests assert the adapter (1) reuses the same plan gate the local backend
uses, (2) refuses to touch the Modal SDK for a blocked plan, (3) builds a
correct artifact manifest, and (4) spawns the right function with a fully
mocked Modal SDK -- no real Modal job is ever launched and no credentials are
required.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.compute import modal_adapter
from services.experiment_jobs import load_experiment_job_contract


# --- fakes: stand in for the Modal SDK so no real job runs -----------------


class _FakeCall:
    def __init__(self, object_id: str = "fc-fake-123") -> None:
        self.object_id = object_id


class _FakeFunction:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []

    def spawn(self, **kwargs: object) -> _FakeCall:
        self.spawn_calls.append(kwargs)
        return _FakeCall()


class _FakeFunctionClass:
    def __init__(self, function: _FakeFunction) -> None:
        self._function = function
        self.lookup_calls: list[tuple[str, str]] = []

    def lookup(self, app_name: str, function_name: str) -> _FakeFunction:
        self.lookup_calls.append((app_name, function_name))
        return self._function


class _FakeModalSdk:
    def __init__(self) -> None:
        self.function = _FakeFunction()
        self.Function = _FakeFunctionClass(self.function)


# --- fixtures --------------------------------------------------------------


def _complete_model_training_kwargs() -> dict[str, object]:
    return {
        "fn_ref": "chunkymonkey-train::train_window",
        "input_snapshot": "smartmoney.duckdb@2026-06-05",
        "objective": "train LambdaMART probe on modal",
        "rollback_plan": "discard modal artifact dir; no DB write",
        "gate_evidence": (
            "leakage_audit=data/reports/leakage.json",
            "train_log_integrity=data/reports/train_log.json",
            "phase4_gate=data/reports/phase4_gate.json",
        ),
        "model_id": "m_modal_probe",
    }


# --- gate reuse: blocked plans never touch the SDK -------------------------


def test_missing_plan_fields_block_submission_without_sdk_call() -> None:
    sdk = _FakeModalSdk()
    with pytest.raises(modal_adapter.ModalSubmissionBlocked) as excinfo:
        modal_adapter.submit_job(
            "model_training",
            fn_ref="chunkymonkey-train::train_window",
            input_snapshot=None,
            objective=None,
            rollback_plan=None,
            modal_sdk=sdk,
        )

    reasons = excinfo.value.blocked_reasons
    assert "missing_plan_field:input_snapshot" in reasons
    assert "missing_plan_field:objective" in reasons
    assert "missing_plan_field:rollback_plan" in reasons
    assert "missing_gate_evidence:leakage_audit" in reasons
    # the SDK must never be reached for a blocked plan
    assert sdk.Function.lookup_calls == []
    assert sdk.function.spawn_calls == []


def test_missing_gate_evidence_blocks_submission() -> None:
    sdk = _FakeModalSdk()
    kwargs = _complete_model_training_kwargs()
    kwargs["gate_evidence"] = ("leakage_audit=data/reports/leakage.json",)
    with pytest.raises(modal_adapter.ModalSubmissionBlocked) as excinfo:
        modal_adapter.submit_job("model_training", modal_sdk=sdk, **kwargs)

    reasons = excinfo.value.blocked_reasons
    assert "missing_gate_evidence:train_log_integrity" in reasons
    assert "missing_gate_evidence:phase4_gate" in reasons
    assert sdk.function.spawn_calls == []


def test_family_disallowing_modal_is_blocked() -> None:
    sdk = _FakeModalSdk()
    # data_validation only allows the local backend
    with pytest.raises(modal_adapter.ModalSubmissionBlocked) as excinfo:
        modal_adapter.submit_job(
            "data_validation",
            fn_ref="chunkymonkey-data::validate",
            input_snapshot="market.duckdb@2026-06-05",
            objective="x",
            rollback_plan="y",
            gate_evidence=(
                "data_health_snapshot=data/reports/data_health_latest.json",
                "data_audit=backend/services/data_audit.py",
                "source_watermark_sla=data/audit/watermark_sla_latest.json",
            ),
            modal_sdk=sdk,
        )

    assert "backend_not_allowed:modal" in excinfo.value.blocked_reasons
    assert sdk.function.spawn_calls == []


# --- manifest generation ---------------------------------------------------


def test_dry_run_builds_manifest_without_sdk_call() -> None:
    sdk = _FakeModalSdk()
    handle = modal_adapter.submit_job(
        "parameter_search",
        fn_ref="chunkymonkey-sweep::run_sweep",
        input_snapshot="alpha158.duckdb@2026-06-05",
        objective="alpha factor sweep on modal",
        rollback_plan="discard sweep artifacts; no promotion",
        gate_evidence=(
            "plan_validator=data/reports/plan_validator.json",
            "backtest_preflight=data/reports/preflight.json",
            "checkpoint_manifest=data/reports/checkpoint_manifest.json",
        ),
        job_id="alpha_sweep_modal",
        dry_run=True,
        modal_sdk=sdk,
        now=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
    )

    assert handle.submitted is False  # dry-run: no live call
    assert sdk.function.spawn_calls == []
    manifest = handle.artifact_manifest
    assert manifest["backend_id"] == "modal"
    assert manifest["job_id"] == "alpha_sweep_modal"
    assert manifest["family_id"] == "parameter_search"
    assert manifest["input_snapshot"] == "alpha158.duckdb@2026-06-05"
    assert manifest["input_snapshot_hash"].startswith("sha256:")
    assert manifest["objective"] == "alpha factor sweep on modal"
    assert manifest["rollback_plan"] == "discard sweep artifacts; no promotion"
    assert manifest["required_gates"] == [
        "plan_validator",
        "backtest_preflight",
        "checkpoint_manifest",
    ]
    assert manifest["ready_to_run"] is True
    assert manifest["blocked_reasons"] == []
    assert manifest["provider"]["name"] == "modal"
    assert manifest["provider"]["app_name"] == "chunkymonkey-sweep"
    assert manifest["provider"]["fn_ref"] == "chunkymonkey-sweep::run_sweep"
    assert manifest["created_at"] == "2026-06-11T12:00:00+00:00"


def test_input_snapshot_hash_is_deterministic_and_distinct() -> None:
    h1 = modal_adapter._input_snapshot_hash("smartmoney.duckdb@2026-06-05")
    h2 = modal_adapter._input_snapshot_hash("smartmoney.duckdb@2026-06-05")
    h3 = modal_adapter._input_snapshot_hash("smartmoney.duckdb@2026-06-06")
    assert h1 == h2
    assert h1 != h3
    assert h1.startswith("sha256:")


# --- budget read from YAML, not hardcoded ----------------------------------


def test_budget_is_read_from_yaml_contract() -> None:
    contract = load_experiment_job_contract()
    budget = modal_adapter._resolve_budget(contract)
    assert budget == 30.0  # config/experiment_jobs.yaml modal.notes budget_monthly_usd

    handle = modal_adapter.submit_job(
        "model_training",
        contract=contract,
        dry_run=True,
        modal_sdk=_FakeModalSdk(),
        **_complete_model_training_kwargs(),
    )
    assert handle.artifact_manifest["provider"]["budget_monthly_usd"] == 30.0


# --- live submit path (fully mocked SDK) -----------------------------------


def test_complete_plan_spawns_modal_function_with_manifest() -> None:
    sdk = _FakeModalSdk()
    handle = modal_adapter.submit_job(
        "model_training",
        modal_sdk=sdk,
        dry_run=False,   # explicit: live spawn path under mocked SDK
        **_complete_model_training_kwargs(),
    )

    assert handle.submitted is True
    assert sdk.Function.lookup_calls == [("chunkymonkey-train", "train_window")]
    assert len(sdk.function.spawn_calls) == 1
    spawned = sdk.function.spawn_calls[0]
    # the manifest is passed through to the remote function
    assert spawned["manifest"]["job_id"] == handle.job_id
    assert spawned["manifest"]["backend_id"] == "modal"
    assert spawned["manifest"]["objective"] == "train LambdaMART probe on modal"
    assert isinstance(handle.call, _FakeCall)


def test_extra_fn_kwargs_are_forwarded_to_spawn() -> None:
    sdk = _FakeModalSdk()
    handle = modal_adapter.submit_job(
        "model_training",
        modal_sdk=sdk,
        dry_run=False,   # explicit: live spawn path under mocked SDK
        fn_kwargs={"n_trials": 100, "seed": 42},
        **_complete_model_training_kwargs(),
    )
    spawned = sdk.function.spawn_calls[0]
    assert spawned["n_trials"] == 100
    assert spawned["seed"] == 42
    assert "manifest" in spawned
    assert handle.submitted is True


# --- fn_ref parsing edge cases ---------------------------------------------


def test_app_name_separate_from_function_name() -> None:
    sdk = _FakeModalSdk()
    handle = modal_adapter.submit_job(
        "model_training",
        modal_sdk=sdk,
        dry_run=False,   # explicit: live spawn path under mocked SDK
        app_name="explicit-app",
        **{**_complete_model_training_kwargs(), "fn_ref": "train_window"},
    )
    assert sdk.Function.lookup_calls == [("explicit-app", "train_window")]
    assert handle.app_name == "explicit-app"


def test_empty_fn_ref_is_rejected() -> None:
    with pytest.raises(ValueError, match="fn_ref"):
        modal_adapter.submit_job(
            "model_training",
            fn_ref="   ",
            input_snapshot="x",
            objective="y",
            rollback_plan="z",
        )


def test_fn_ref_without_app_is_rejected() -> None:
    with pytest.raises(ValueError, match="no app"):
        modal_adapter.submit_job(
            "model_training",
            fn_ref="train_window",
            input_snapshot="x",
            objective="y",
            rollback_plan="z",
        )


def test_default_is_dry_run_no_spawn_even_with_complete_plan_and_sdk():
    """Fable-5 复查防回退: 完整 plan + SDK 在场, 但不传 dry_run → 默认 True 不 spawn.

    防 latent 烧钱: 调用方忘记 dry_run 不会触发付费 modal job.
    """
    sdk = _FakeModalSdk()
    handle = modal_adapter.submit_job(
        "model_training",
        modal_sdk=sdk,
        **_complete_model_training_kwargs(),
    )
    assert handle.submitted is False, "默认必须 dry_run, 不得 spawn"
    assert sdk.function.spawn_calls == [], "默认不传 dry_run 时绝不可触达 SDK spawn"
