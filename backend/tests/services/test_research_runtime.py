"""Phase D research runtime scaffold — DatasetSnapshot → ExperimentVerdict + PIT.

Does NOT claim D complete, StrategyRelease, Optuna, or E gate loosening.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.data_sources.disclosure_dataset_snapshot import (
    DISCLOSURE_SNAPSHOT_RELPATH,
    default_snapshot_path,
)
from services.institution_follow_b0 import (
    ExperimentVerdict as EExperimentVerdict,
    build_b0_run,
    finalize_b0_verdict,
    load_frozen_disclosure_snapshot,
)
from services.research_runtime import (
    DatasetSnapshot,
    ExperimentVerdict,
    ResearchObservation,
    SnapshotInputRef,
    assert_no_future_available_at,
    dataset_snapshot_from_disclosure,
    pit_truncate_observations,
    prove_pit_truncation_invariance,
    run_smoke_closed_loop,
)


def _obs(
    entity: str,
    event_date: str,
    *,
    available_at: str,
    value: float = 1.0,
) -> ResearchObservation:
    return ResearchObservation(
        entity_id=entity,
        event_date=event_date,
        available_at=available_at,
        payload={"value": value},
    )


def _synthetic_snapshot() -> DatasetSnapshot:
    return DatasetSnapshot(
        snapshot_id="phase_d_smoke_synth",
        inputs=(
            SnapshotInputRef(
                dataset_id="tier0.test.nominal_smoke",
                partitions=("20260716", "20260717"),
                content_hash="abc123",
                config_hash="cfg001",
            ),
        ),
        universe_id="traded_on_observation_date",
        config_hash="cfg001",
        available_at_lower="20260716",
        available_at_upper="20260717",
        content_hash="snapcontent001",
        frozen_at="2026-07-20T00:00:00+00:00",
        source_kind="synthetic_smoke",
        notes=("phase_d_scaffold",),
    )


def test_dataset_snapshot_requires_immutable_core_fields() -> None:
    snap = _synthetic_snapshot()
    assert snap.snapshot_id
    assert snap.inputs
    assert snap.universe_id
    assert snap.config_hash
    assert snap.available_at_lower == "20260716"
    assert snap.available_at_upper == "20260717"
    assert snap.content_hash
    with pytest.raises(ValueError, match="snapshot_id"):
        DatasetSnapshot(
            snapshot_id="",
            inputs=snap.inputs,
            universe_id=snap.universe_id,
            config_hash=snap.config_hash,
            available_at_lower=snap.available_at_lower,
            available_at_upper=snap.available_at_upper,
            content_hash=snap.content_hash,
            frozen_at=snap.frozen_at,
            source_kind=snap.source_kind,
            notes=(),
        )
    with pytest.raises(ValueError, match="available_at"):
        DatasetSnapshot(
            snapshot_id="x",
            inputs=snap.inputs,
            universe_id=snap.universe_id,
            config_hash=snap.config_hash,
            available_at_lower="20260718",
            available_at_upper="20260717",
            content_hash=snap.content_hash,
            frozen_at=snap.frozen_at,
            source_kind=snap.source_kind,
            notes=(),
        )


def test_pit_truncate_drops_future_available_at() -> None:
    kept = pit_truncate_observations(
        (
            _obs("600000", "20260716", available_at="20260716"),
            _obs("600000", "20260717", available_at="20260717"),
            _obs("600000", "20260717", available_at="20260718"),  # future
            _obs("600001", "20260718", available_at="20260718"),
        ),
        decision_date="20260717",
    )
    assert [(o.entity_id, o.available_at) for o in kept] == [
        ("600000", "20260716"),
        ("600000", "20260717"),
    ]


def test_pit_truncate_missing_available_at_fails_closed() -> None:
    with pytest.raises(ValueError, match="available_at"):
        pit_truncate_observations(
            (
                ResearchObservation(
                    entity_id="600000",
                    event_date="20260717",
                    available_at="",
                    payload={"value": 1.0},
                ),
            ),
            decision_date="20260717",
        )


def test_assert_no_future_available_at_fails_closed() -> None:
    with pytest.raises(ValueError, match="future available_at"):
        assert_no_future_available_at(
            (_obs("600000", "20260717", available_at="20260718"),),
            decision_date="20260717",
        )


def test_pit_invariance_future_rows_zero_diff() -> None:
    decision = "20260717"
    base = (
        _obs("600000", "20260716", available_at="20260716", value=10.0),
        _obs("600000", "20260717", available_at="20260717", value=11.0),
    )
    future = (
        _obs("600000", "20260718", available_at="20260718", value=-999.0),
        _obs("600000", "20260717", available_at="20260718", value=-888.0),
    )
    prove_pit_truncation_invariance(base, future, decision_date=decision)


def test_smoke_closed_loop_emits_inconclusive_not_release() -> None:
    snap = _synthetic_snapshot()
    obs = (
        _obs("600000", "20260716", available_at="20260716"),
        _obs("600000", "20260717", available_at="20260717"),
    )
    run, verdict = run_smoke_closed_loop(
        snap,
        obs,
        decision_date="20260717",
        block="B0",
        strategy_package="phase_d_smoke",
    )
    assert run.snapshot_id == snap.snapshot_id
    assert run.snapshot_content_hash == snap.content_hash
    assert run.config_hash == snap.config_hash
    assert isinstance(verdict, ExperimentVerdict)
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.blocked is False
    assert verdict.details.get("strategy_release") is False
    assert "phase_d_scaffold" in verdict.reason or "scaffold" in verdict.reason


def test_smoke_closed_loop_rejects_pit_leak() -> None:
    snap = _synthetic_snapshot()
    obs = (_obs("600000", "20260717", available_at="20260718"),)
    run, verdict = run_smoke_closed_loop(
        snap,
        obs,
        decision_date="20260717",
        block="B0",
        strategy_package="phase_d_smoke",
        require_kept_rows=True,
    )
    assert verdict.verdict == "reject"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert "pit" in verdict.reason
    assert run.pit_ok is False


def test_disclosure_snapshot_adapts_to_dataset_snapshot() -> None:
    root = Path(__file__).resolve().parents[3]
    path = root / DISCLOSURE_SNAPSHOT_RELPATH
    assert path == default_snapshot_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    snap = dataset_snapshot_from_disclosure(payload)
    assert snap.snapshot_id == payload["snapshot_id"]
    assert snap.universe_id == "traded_on_observation_date"
    assert snap.source_kind == "disclosure_freeze"
    assert snap.config_hash
    assert snap.content_hash
    assert len(snap.available_at_lower) == 8
    assert len(snap.available_at_upper) == 8
    assert snap.available_at_lower <= snap.available_at_upper
    assert {ref.dataset_id for ref in snap.inputs} >= {
        "tier0.disclosure.top10_float_holders_period",
    }
    assert "holders_top10" in " ".join(snap.notes) or any(
        "holders" in n for n in snap.notes
    )


def test_phase_e_b0_consumes_research_runtime_boundary() -> None:
    """E B0 path already partially consumes D: same ExperimentVerdict type + adapter."""

    payload = load_frozen_disclosure_snapshot()
    runtime_snap = dataset_snapshot_from_disclosure(payload)
    run = build_b0_run(
        snapshot=payload,
        measure_coverage=False,
        measure_paper=False,
    )
    assert run.snapshot_id == runtime_snap.snapshot_id
    manifest = run.artifact_manifest
    bound = manifest.get("research_runtime_snapshot") or {}
    assert bound.get("snapshot_id") == runtime_snap.snapshot_id
    assert bound.get("content_hash") == runtime_snap.content_hash
    assert bound.get("config_hash") == runtime_snap.config_hash
    assert bound.get("available_at_lower") == runtime_snap.available_at_lower
    assert bound.get("available_at_upper") == runtime_snap.available_at_upper
    assert bound.get("universe_id") == runtime_snap.universe_id

    verdict = finalize_b0_verdict(run)
    assert isinstance(verdict, ExperimentVerdict)
    assert type(verdict) is type(EExperimentVerdict(  # noqa: PLC2801 — identity check
        verdict="inconclusive",
        reason="type_probe",
        blocked=True,
        experiment_id="x",
        block="B0",
        claimable=False,
        details={},
    ))
    assert EExperimentVerdict is ExperimentVerdict
    assert verdict.claimable is False
    assert verdict.details.get("strategy_release") is not True
