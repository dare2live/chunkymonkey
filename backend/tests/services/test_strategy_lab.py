from __future__ import annotations

from pathlib import Path

import pytest

from services.research_runtime import DatasetSnapshot, SnapshotInputRef
from services.strategy_lab import (
    ComputeRequest,
    InputSlice,
    ResearchInputBundle,
    StrategyLabError,
    assess_compute,
    build_ingress_plan,
    load_policy,
)


def _snapshot() -> DatasetSnapshot:
    return DatasetSnapshot(
        snapshot_id="synthetic-freeze",
        inputs=(
            SnapshotInputRef(
                dataset_id="tier0.market_data.nominal_ohlcv_daily",
                partitions=("20250528", "20250529", "20250530", "20250602"),
                content_hash="nominal-content",
                config_hash="nominal-config",
            ),
            SnapshotInputRef(
                dataset_id="tier3.main_rally.gt_freeze",
                partitions=("gt_freeze",),
                content_hash="label-content",
                config_hash="label-config",
            ),
        ),
        universe_id="traded_on_observation_date",
        config_hash="snapshot-config",
        available_at_lower="20250528",
        available_at_upper="20250602",
        content_hash="snapshot-content",
        frozen_at="2026-07-27T00:00:00+00:00",
        source_kind="test",
        notes=("synthetic",),
    )


def _development_snapshot() -> DatasetSnapshot:
    snap = _snapshot()
    return DatasetSnapshot(
        snapshot_id="synthetic-development-freeze",
        inputs=(
            SnapshotInputRef(
                dataset_id="tier0.market_data.nominal_ohlcv_daily",
                partitions=("20250528", "20250529", "20250530"),
                content_hash="nominal-content",
                config_hash="nominal-config",
            ),
        ),
        universe_id=snap.universe_id,
        config_hash=snap.config_hash,
        available_at_lower="20250528",
        available_at_upper="20250530",
        content_hash="development-content",
        frozen_at=snap.frozen_at,
        source_kind=snap.source_kind,
        notes=snap.notes,
    )


def test_ingress_bundle_contains_development_data_only() -> None:
    plan = build_ingress_plan(
        _development_snapshot(),
        train_end="20250529",
        holdout_start="20250601",
    )

    assert plan.read_only is True
    assert plan.snapshot_content_hash == "development-content"
    assert plan.training_inputs[0].partitions == ("20250528", "20250529")
    assert plan.validation_inputs[0].partitions == ("20250530",)
    assert "holdout_inputs" not in plan.worker_payload()
    assert "label_only_inputs" not in plan.worker_payload()


def test_ingress_rejects_sealed_holdout_partitions() -> None:
    with pytest.raises(StrategyLabError, match="sealed holdout"):
        build_ingress_plan(
            _snapshot(),
            train_end="20250529",
            holdout_start="20250601",
        )


def test_ingress_rejects_tier3_labels() -> None:
    development = _development_snapshot()
    with_label = DatasetSnapshot(
        snapshot_id="development-with-label",
        inputs=(
            *development.inputs,
            SnapshotInputRef(
                dataset_id="tier3.main_rally.gt_freeze",
                partitions=("gt_freeze",),
                content_hash="label-content",
                config_hash="label-config",
            ),
        ),
        universe_id=development.universe_id,
        config_hash=development.config_hash,
        available_at_lower=development.available_at_lower,
        available_at_upper=development.available_at_upper,
        content_hash="development-with-label-content",
        frozen_at=development.frozen_at,
        source_kind=development.source_kind,
        notes=development.notes,
    )

    with pytest.raises(StrategyLabError, match="Tier3 label"):
        build_ingress_plan(
            with_label,
            train_end="20250529",
            holdout_start="20250601",
        )


def test_ingress_refuses_training_at_or_after_holdout() -> None:
    with pytest.raises(StrategyLabError, match="strictly earlier"):
        build_ingress_plan(
            _snapshot(),
            train_end="20250601",
            holdout_start="20250601",
        )


def test_ingress_requires_nominal_b0_input() -> None:
    disclosure_only = DatasetSnapshot(
        snapshot_id="disclosure-only",
        inputs=(
                SnapshotInputRef(
                    dataset_id="tier0.disclosure.org_holding_detail_period",
                    partitions=("20190430", "20250530"),
                content_hash="disclosure-content",
                config_hash="disclosure-config",
            ),
        ),
        universe_id="traded_on_observation_date",
        config_hash="snapshot-config",
        available_at_lower="20190430",
        available_at_upper="20250530",
        content_hash="snapshot-content",
        frozen_at="2026-07-27T00:00:00+00:00",
        source_kind="test",
        notes=(),
    )

    with pytest.raises(StrategyLabError, match="no accepted nominal OHLCV"):
        build_ingress_plan(
            disclosure_only,
            train_end="20250529",
            holdout_start="20250601",
        )


def test_ingress_requires_development_validation_partitions() -> None:
    with pytest.raises(StrategyLabError, match="no validation partitions"):
        build_ingress_plan(
            _development_snapshot(),
            train_end="20250530",
            holdout_start="20250601",
        )


def test_ingress_requires_nominal_validation_not_any_validation() -> None:
    mixed = DatasetSnapshot(
        snapshot_id="nominal-train-disclosure-validation",
        inputs=(
            SnapshotInputRef(
                dataset_id="tier0.market_data.nominal_ohlcv_daily",
                partitions=("20250528",),
                content_hash="nominal-content",
                config_hash="nominal-config",
            ),
            SnapshotInputRef(
                dataset_id="tier0.disclosure.org_holding_detail_period",
                partitions=("20250530",),
                content_hash="disclosure-content",
                config_hash="disclosure-config",
            ),
        ),
        universe_id="traded_on_observation_date",
        config_hash="snapshot-config",
        available_at_lower="20250528",
        available_at_upper="20250530",
        content_hash="snapshot-content",
        frozen_at="2026-07-27T00:00:00+00:00",
        source_kind="test",
        notes=(),
    )

    with pytest.raises(StrategyLabError, match="nominal OHLCV validation"):
        build_ingress_plan(
            mixed,
            train_end="20250529",
            holdout_start="20250601",
        )


def test_bundle_cannot_be_directly_constructed_with_future_bounds() -> None:
    nominal = InputSlice(
        dataset_id="tier0.market_data.nominal_ohlcv_daily",
        partitions=("20250528",),
        content_hash="content",
        config_hash="config",
    )
    validation = InputSlice(
        dataset_id="tier0.market_data.nominal_ohlcv_daily",
        partitions=("20250530",),
        content_hash="content",
        config_hash="config",
    )

    with pytest.raises(StrategyLabError, match="cross the sealed holdout"):
        ResearchInputBundle(
            snapshot_id="bad-direct-construction",
            snapshot_content_hash="snapshot-content",
            snapshot_config_hash="snapshot-config",
            universe_id="traded_on_observation_date",
            available_at_lower="20250528",
            available_at_upper="20270101",
            train_end="20250529",
            holdout_start="20250601",
            training_inputs=(nominal,),
            validation_inputs=(validation,),
            metadata_inputs=(),
        )


def test_local_smoke_is_allowed_but_never_claimable() -> None:
    admission = assess_compute(
        build_ingress_plan(
            _development_snapshot(),
            train_end="20250529",
            holdout_start="20250601",
        ),
        ComputeRequest(stage="local_smoke", executor="local"),
    )

    assert admission.allowed is True
    assert admission.claimable is False
    assert admission.reasons == ()


def test_formal_rx_is_blocked_until_real_validators_exist() -> None:
    ingress = build_ingress_plan(
        _development_snapshot(),
        train_end="20250529",
        holdout_start="20250601",
    )
    admission = assess_compute(
        ingress,
        ComputeRequest(stage="formal_rx", executor="local"),
    )

    assert admission.allowed is False
    assert "formal_rx_not_authorized" in admission.reasons
    assert "formal_evidence_validators_not_implemented" in admission.reasons


def test_optuna_is_blocked_until_real_runner_and_validators_exist() -> None:
    ingress = build_ingress_plan(
        _development_snapshot(),
        train_end="20250529",
        holdout_start="20250601",
    )
    admission = assess_compute(
        ingress,
        ComputeRequest(stage="optuna", executor="local"),
    )

    assert admission.allowed is False
    assert "formal_evidence_validators_not_implemented" in admission.reasons
    assert "optuna_runner_not_implemented" in admission.reasons


def test_modal_is_blocked_until_real_adapter_exists() -> None:
    ingress = build_ingress_plan(
        _development_snapshot(),
        train_end="20250529",
        holdout_start="20250601",
    )
    admission = assess_compute(
        ingress,
        ComputeRequest(stage="formal_rx", executor="modal"),
    )

    assert admission.allowed is False
    assert "remote_compute_not_authorized" in admission.reasons
    assert "modal_adapter_not_implemented" in admission.reasons


def test_live_policy_is_framework_only() -> None:
    policy = load_policy()
    assert policy.status == "framework_only"
    assert policy.execution_mode == "manual_only"
    assert not policy.formal_rx_authorization
    assert not policy.phase_n_authorization
    assert not policy.remote_compute_authorization


def test_authorization_id_must_be_bound_by_goal_owner(tmp_path: Path) -> None:
    policy_path = tmp_path / "strategy_lab.yaml"
    goal_path = tmp_path / "goal.md"
    policy_path.write_text(
        """
version: 1
status: framework_only
execution_mode: manual_only
development_split:
  validation_calendar_days: 20
authorizations:
  formal_rx: RX-001
  phase_n_optuna: ""
  remote_compute: ""
remote_compute:
  require_read_only_bundle: true
  forbid_live_database: true
""",
        encoding="utf-8",
    )
    goal_path.write_text("RX remains blocked\n", encoding="utf-8")

    with pytest.raises(StrategyLabError, match="not bound in goal.md"):
        load_policy(policy_path, goal_path=goal_path)


def test_remote_safety_invariants_cannot_be_disabled(tmp_path: Path) -> None:
    policy_path = tmp_path / "strategy_lab.yaml"
    goal_path = tmp_path / "goal.md"
    policy_path.write_text(
        """
version: 1
status: framework_only
execution_mode: manual_only
development_split:
  validation_calendar_days: 20
authorizations:
  formal_rx: ""
  phase_n_optuna: ""
  remote_compute: ""
remote_compute:
  require_read_only_bundle: false
  forbid_live_database: true
""",
        encoding="utf-8",
    )
    goal_path.write_text("", encoding="utf-8")

    with pytest.raises(StrategyLabError, match="must remain true"):
        load_policy(policy_path, goal_path=goal_path)


def test_validation_window_rejects_yaml_boolean(tmp_path: Path) -> None:
    policy_path = tmp_path / "strategy_lab.yaml"
    goal_path = tmp_path / "goal.md"
    policy_path.write_text(
        """
version: 1
status: framework_only
execution_mode: manual_only
development_split:
  validation_calendar_days: true
authorizations:
  formal_rx: ""
  phase_n_optuna: ""
  remote_compute: ""
remote_compute:
  require_read_only_bundle: true
  forbid_live_database: true
""",
        encoding="utf-8",
    )
    goal_path.write_text("", encoding="utf-8")

    with pytest.raises(StrategyLabError, match="positive integer"):
        load_policy(policy_path, goal_path=goal_path)
