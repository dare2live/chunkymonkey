"""Phase D research runtime — DatasetSnapshot → ExperimentVerdict + PIT.

Deepens scaffold toward offline minimal loop (prereg, binding, measure stub).
Does NOT claim D complete, StrategyRelease, Optuna, or E gate loosening.
"""
from __future__ import annotations

import dataclasses
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
    ExperimentPrereg,
    ExperimentVerdict,
    ResearchObservation,
    ResearchRuntimeError,
    SnapshotInputRef,
    assert_no_future_available_at,
    assert_snapshot_binding,
    build_experiment_prereg,
    dataset_snapshot_from_disclosure,
    default_fold_embargo_hooks,
    fold_embargo_from_walk_forward_plan,
    pit_truncate_observations,
    prove_pit_truncation_invariance,
    run_offline_b0_bound_loop,
    run_offline_measured_loop,
    run_offline_minimal_loop,
    run_smoke_closed_loop,
)


def _measured_wf_plan() -> dict:
    """Shape of a measured WalkForwardPlan.as_dict() (institution_follow_paper)."""

    return {
        "protocol": "purged_walk_forward",
        "claimable_protocol": True,
        "reason": "purged_walk_forward_ready",
        "trading_days": ["20260116", "20260119", "20260716", "20260717"],
        "folds": [
            {"fold_id": "purged_fold_0", "role": "purged_eval"},
            {"fold_id": "purged_fold_1", "role": "purged_eval"},
            {"fold_id": "purged_fold_2", "role": "purged_eval"},
        ],
        "holdout_dates": ["20260716", "20260717"],
        "embargo_days": 1,
        "label_horizon_days": 1,
        "one_touch_holdout": True,
    }


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


def test_experiment_prereg_binds_snapshot_and_forbids_claimable_target() -> None:
    snap = _synthetic_snapshot()
    hooks = default_fold_embargo_hooks(n_folds=3, embargo_days=1)
    assert hooks.protocol == "purged_walk_forward"
    assert hooks.fold_ids == ("fold_0", "fold_1", "fold_2")
    prereg = build_experiment_prereg(
        snap,
        strategy_package="phase_d_offline",
        block="B0",
        hypothesis="stub_no_edge",
        fold_embargo=hooks,
        register_store=False,
    )
    assert isinstance(prereg, ExperimentPrereg)
    assert prereg.snapshot_content_hash == snap.content_hash
    assert prereg.universe_id == snap.universe_id
    assert prereg.search_space == ()
    assert prereg.claimable_target is False
    assert prereg.param_hash
    assert prereg.single_touch_token
    with pytest.raises(ValueError, match="claimable_target"):
        ExperimentPrereg(
            hypothesis="x",
            primary_metric="holdout_net_return",
            stop_conditions=(),
            search_space=(),
            fold_embargo=hooks,
            strategy_package="phase_d_offline",
            block="B0",
            snapshot_id=snap.snapshot_id,
            snapshot_content_hash=snap.content_hash,
            universe_id=snap.universe_id,
            config_hash=snap.config_hash,
            available_at_lower=snap.available_at_lower,
            available_at_upper=snap.available_at_upper,
            random_seed=0,
            claimable_target=True,
        )


def test_assert_snapshot_binding_fails_on_hash_universe_or_bounds() -> None:
    snap = _synthetic_snapshot()
    prereg = build_experiment_prereg(
        snap,
        strategy_package="phase_d_offline",
        block="B0",
        hypothesis="binding",
        register_store=False,
    )
    assert_snapshot_binding(snap, prereg=prereg, decision_date="20260717")

    drifted = dataclasses.replace(snap, content_hash="tampered_hash")
    with pytest.raises(ResearchRuntimeError, match="content_hash"):
        assert_snapshot_binding(drifted, prereg=prereg)

    wrong_u = dataclasses.replace(snap, universe_id="other_universe")
    with pytest.raises(ResearchRuntimeError, match="universe_id"):
        assert_snapshot_binding(wrong_u, prereg=prereg)

    with pytest.raises(ResearchRuntimeError, match="outside snapshot"):
        assert_snapshot_binding(snap, prereg=prereg, decision_date="20260718")

    with pytest.raises(ResearchRuntimeError, match="observed universe"):
        assert_snapshot_binding(
            snap,
            prereg=prereg,
            decision_date="20260717",
            observed_universe_id="not_the_snapshot_universe",
        )


def test_offline_minimal_loop_emits_inconclusive_claimable_false() -> None:
    snap = _synthetic_snapshot()
    obs = (
        _obs("600000", "20260716", available_at="20260716"),
        _obs("600000", "20260717", available_at="20260717"),
    )
    run, verdict = run_offline_minimal_loop(
        snap,
        obs,
        decision_date="20260717",
        fold_embargo=default_fold_embargo_hooks(n_folds=3),
    )
    assert run.prereg is not None
    assert run.prereg.snapshot_content_hash == snap.content_hash
    assert run.universe_id == snap.universe_id
    assert run.kept_observation_count == 2
    assert run.artifact_manifest.get("strategy_release") is False
    assert run.artifact_manifest.get("prereg", {}).get("search_space") == []
    assert verdict.verdict == "inconclusive"
    assert verdict.claimable is False
    assert verdict.details.get("strategy_release") is False
    assert "offline_measure_stub" in verdict.reason


def test_offline_minimal_loop_rejects_mid_run_binding_violation() -> None:
    snap = _synthetic_snapshot()
    obs = (_obs("600000", "20260717", available_at="20260717"),)
    run, verdict = run_offline_minimal_loop(
        snap,
        obs,
        decision_date="20260717",
        observed_universe_id="wrong_universe",
    )
    assert verdict.verdict == "reject"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert "binding" in verdict.reason
    assert run.pit_ok is False
    assert run.prereg is not None


def test_offline_b0_bound_loop_reuses_harness_claimable_false() -> None:
    run, verdict = run_offline_b0_bound_loop()
    assert run.prereg is not None
    assert run.prereg.strategy_package == "institution_follow"
    assert run.prereg.block == "B0"
    assert run.prereg.fold_embargo.n_folds == 3
    assert run.artifact_manifest.get("kind") == "phase_d_offline_b0_bound"
    assert run.artifact_manifest.get("strategy_release") is False
    assert isinstance(verdict, ExperimentVerdict)
    assert verdict.claimable is False
    assert verdict.details.get("phase_d_bound") is True
    assert verdict.details.get("claimable_forced_false_by_research_runtime") is True
    assert verdict.details.get("strategy_release") is False


def test_fold_embargo_from_measured_plan_binds_real_folds() -> None:
    hooks = fold_embargo_from_walk_forward_plan(_measured_wf_plan())
    assert hooks.protocol == "purged_walk_forward"
    assert hooks.n_folds == 3
    assert hooks.fold_ids == ("purged_fold_0", "purged_fold_1", "purged_fold_2")
    assert hooks.embargo_days == 1
    assert hooks.label_horizon_days == 1
    assert hooks.one_touch_holdout is True
    assert hooks.holdout_start == "20260716"
    assert "bound_from_measured_walk_forward_plan" in hooks.notes
    # It must not be mistakable for the default stub (fold_0/fold_1/...).
    assert hooks.fold_ids != default_fold_embargo_hooks(n_folds=3).fold_ids


def test_fold_embargo_from_plan_fails_closed() -> None:
    with pytest.raises(ResearchRuntimeError, match="mapping"):
        fold_embargo_from_walk_forward_plan("not_a_plan")

    bad_protocol = _measured_wf_plan()
    bad_protocol["protocol"] = "latest_snapshot_backtest"
    with pytest.raises(ResearchRuntimeError, match="protocol"):
        fold_embargo_from_walk_forward_plan(bad_protocol)

    missing_fold_id = _measured_wf_plan()
    missing_fold_id["folds"] = [{"role": "purged_eval"}]
    with pytest.raises(ResearchRuntimeError, match="fold_id"):
        fold_embargo_from_walk_forward_plan(missing_fold_id)

    purged_no_folds = _measured_wf_plan()
    purged_no_folds["folds"] = []
    with pytest.raises(ResearchRuntimeError, match="at least one fold"):
        fold_embargo_from_walk_forward_plan(purged_no_folds)

    bad_embargo = _measured_wf_plan()
    del bad_embargo["embargo_days"]
    with pytest.raises(ResearchRuntimeError, match="embargo"):
        fold_embargo_from_walk_forward_plan(bad_embargo)


def test_offline_b0_bound_loop_binds_real_plan_into_prereg() -> None:
    run, verdict = run_offline_b0_bound_loop(walk_forward_plan=_measured_wf_plan())
    assert run.prereg is not None
    fe = run.prereg.fold_embargo
    assert fe.protocol == "purged_walk_forward"
    assert fe.fold_ids == ("purged_fold_0", "purged_fold_1", "purged_fold_2")
    assert fe.holdout_start == "20260716"
    assert "bound_from_measured_walk_forward_plan" in fe.notes
    # Real fold bind never upgrades the claim: still forced claimable=false.
    assert verdict.claimable is False
    assert verdict.details.get("claimable_forced_false_by_research_runtime") is True
    assert verdict.details.get("strategy_release") is False


def _trade_obs(
    entity: str,
    event_date: str,
    *,
    available_at: str,
    entry_px: float,
    exit_px: float,
    fold_role: str = "one_touch_holdout",
) -> ResearchObservation:
    """Observation with explicit T+1/T+2 nominal open legs (runtime-owned measure)."""

    return ResearchObservation(
        entity_id=entity,
        event_date=event_date,
        available_at=available_at,
        payload={
            "entry_px": entry_px,
            "exit_px": exit_px,
            "entry_date": event_date,  # synthetic: treat event as entry day
            "fold_role": fold_role,
        },
    )


def test_offline_measured_loop_emits_measured_returns_claimable_false() -> None:
    """D residual: measured offline path owned by research_runtime, not a strategy package."""

    snap = _synthetic_snapshot()
    obs = (
        _trade_obs(
            "600000.SH",
            "20260716",
            available_at="20260716",
            entry_px=10.0,
            exit_px=10.5,
        ),
        _trade_obs(
            "600001.SH",
            "20260717",
            available_at="20260717",
            entry_px=20.0,
            exit_px=19.0,
            fold_role="purged_eval",
        ),
    )
    run, verdict = run_offline_measured_loop(
        snap,
        obs,
        decision_date="20260717",
        fold_embargo=default_fold_embargo_hooks(n_folds=3),
    )
    assert run.prereg is not None
    assert run.prereg.strategy_package == "phase_d_offline"
    assert run.prereg.strategy_package != "institution_follow"
    assert run.prereg.claimable_target is False
    assert run.artifact_manifest.get("kind") == "phase_d_offline_measured"
    assert run.artifact_manifest.get("strategy_release") is False
    measure = run.artifact_manifest.get("measure") or {}
    assert measure.get("status") == "measured"
    assert measure.get("paper_fills") == "measured"
    assert isinstance(measure.get("total_return"), float)
    assert isinstance(measure.get("max_drawdown"), float)
    assert measure.get("total_return") != "unknown"
    assert measure.get("n_trades_completed") == 2
    assert isinstance(verdict, ExperimentVerdict)
    assert verdict.claimable is False
    assert verdict.verdict == "inconclusive"
    assert "offline_measured" in verdict.reason
    assert "stub" not in verdict.reason
    assert verdict.details.get("strategy_release") is False
    assert verdict.details.get("optuna") is False


def test_offline_measured_loop_pit_drops_future_and_marks_unknown_prices() -> None:
    snap = _synthetic_snapshot()
    obs = (
        _trade_obs(
            "600000.SH",
            "20260716",
            available_at="20260716",
            entry_px=10.0,
            exit_px=11.0,
        ),
        # Future availability — must be PIT-dropped, not filled.
        _trade_obs(
            "600099.SH",
            "20260717",
            available_at="20260718",
            entry_px=10.0,
            exit_px=12.0,
        ),
        # Missing prices → unfilled; does not invent 0 returns.
        ResearchObservation(
            entity_id="600002.SH",
            event_date="20260717",
            available_at="20260717",
            payload={"fold_role": "purged_eval"},
        ),
    )
    run, verdict = run_offline_measured_loop(
        snap, obs, decision_date="20260717", require_kept_rows=True
    )
    measure = run.artifact_manifest.get("measure") or {}
    assert run.kept_observation_count == 2  # future row dropped
    assert measure.get("n_trades_completed") == 1
    assert measure.get("n_unfilled") == 1
    assert verdict.claimable is False
    assert measure.get("paper_fills") == "measured"


def test_offline_measured_loop_rejects_binding_violation_claimable_false() -> None:
    snap = _synthetic_snapshot()
    obs = (
        _trade_obs(
            "600000.SH",
            "20260717",
            available_at="20260717",
            entry_px=10.0,
            exit_px=10.1,
        ),
    )
    run, verdict = run_offline_measured_loop(
        snap,
        obs,
        decision_date="20260717",
        observed_universe_id="wrong_universe",
    )
    assert verdict.verdict == "reject"
    assert verdict.claimable is False
    assert verdict.blocked is True
    assert "binding" in verdict.reason
    assert run.pit_ok is False


def test_offline_measured_path_module_does_not_import_strategy_package() -> None:
    """Ownership: measured module must not import strategy-package harnesses."""

    import ast

    import services.research_runtime_measure as measure_mod

    tree = ast.parse(Path(measure_mod.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("institution_follow" in name for name in imported)
    src = Path(measure_mod.__file__).read_text(encoding="utf-8")
    assert "build_b0_run" not in src
    assert "measure_b0_paper" not in src
    assert "finalize_b0_verdict" not in src
