"""Phase D offline research loop: prereg → bind → measure → ExperimentVerdict.

Owner boundary remains ``research_runtime`` (types + PIT + smoke). This module
holds the offline minimal / B0-bound loop so ``research_runtime.py`` stays under
the god-file ratchet. Public re-exports live on ``research_runtime``.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from services.research_runtime import (
    REASON_PHASE_D_OFFLINE_MEASURE_STUB,
    REASON_PIT_FUTURE_AVAILABLE_AT,
    REASON_PIT_LEAK_OR_EMPTY,
    REASON_SNAPSHOT_BINDING_VIOLATED,
    DatasetSnapshot,
    ExperimentPrereg,
    ExperimentRun,
    ExperimentVerdict,
    FoldEmbargoHooks,
    OfflineMeasureResult,
    ProtocolKind,
    ResearchObservation,
    ResearchRuntimeError,
    _compact_day,
    dataset_snapshot_from_disclosure,
    pit_truncate_observations,
)


def default_fold_embargo_hooks(
    *,
    n_folds: int = 0,
    embargo_days: int = 1,
    label_horizon_days: int = 1,
    holdout_start: str = "",
) -> FoldEmbargoHooks:
    """Typed fold/embargo stubs for Phase D (not a claimable WF plan)."""

    fold_ids = tuple(f"fold_{i}" for i in range(n_folds)) if n_folds else ()
    protocol: ProtocolKind = (
        "purged_walk_forward" if n_folds >= 3 else "undeclared_stub"
    )
    return FoldEmbargoHooks(
        protocol=protocol,
        n_folds=n_folds,
        embargo_days=embargo_days,
        label_horizon_days=label_horizon_days,
        one_touch_holdout=True,
        fold_ids=fold_ids,
        holdout_start=holdout_start,
        notes=(
            "phase_d_fold_embargo_hooks",
            "not_claimable_walk_forward",
            "no_optuna",
        ),
    )


def build_experiment_prereg(
    snapshot: DatasetSnapshot,
    *,
    strategy_package: str,
    block: str,
    hypothesis: str,
    primary_metric: str = "holdout_net_return",
    stop_conditions: Sequence[str] = (
        "no_strategy_release",
        "claimable_target_false",
        "empty_search_space",
    ),
    search_space: Sequence[str] = (),
    fold_embargo: FoldEmbargoHooks | None = None,
    random_seed: int = 0,
) -> ExperimentPrereg:
    """Freeze prereg fields against an immutable DatasetSnapshot."""

    return ExperimentPrereg(
        hypothesis=hypothesis,
        primary_metric=primary_metric,
        stop_conditions=tuple(stop_conditions),
        search_space=tuple(search_space),
        fold_embargo=fold_embargo or default_fold_embargo_hooks(),
        strategy_package=strategy_package,
        block=block,
        snapshot_id=snapshot.snapshot_id,
        snapshot_content_hash=snapshot.content_hash,
        universe_id=snapshot.universe_id,
        config_hash=snapshot.config_hash,
        available_at_lower=snapshot.available_at_lower,
        available_at_upper=snapshot.available_at_upper,
        random_seed=random_seed,
        claimable_target=False,
    )


def assert_snapshot_binding(
    snapshot: DatasetSnapshot,
    *,
    expected_content_hash: str | None = None,
    expected_config_hash: str | None = None,
    expected_universe_id: str | None = None,
    expected_available_at_lower: str | None = None,
    expected_available_at_upper: str | None = None,
    decision_date: str | None = None,
    observed_universe_id: str | None = None,
    prereg: ExperimentPrereg | None = None,
) -> None:
    """Fail closed if mid-run snapshot hash/universe/available_at binding drifts.

    Compares live ``snapshot`` to either explicit expected_* values or a frozen
    ``prereg``. Decision dates outside snapshot bounds are binding violations.
    """

    content_hash = (
        expected_content_hash
        if expected_content_hash is not None
        else (prereg.snapshot_content_hash if prereg is not None else None)
    )
    config_hash = (
        expected_config_hash
        if expected_config_hash is not None
        else (prereg.config_hash if prereg is not None else None)
    )
    universe_id = (
        expected_universe_id
        if expected_universe_id is not None
        else (prereg.universe_id if prereg is not None else None)
    )
    lower = (
        expected_available_at_lower
        if expected_available_at_lower is not None
        else (prereg.available_at_lower if prereg is not None else None)
    )
    upper = (
        expected_available_at_upper
        if expected_available_at_upper is not None
        else (prereg.available_at_upper if prereg is not None else None)
    )

    if content_hash is not None and snapshot.content_hash != content_hash:
        raise ResearchRuntimeError(
            f"snapshot content_hash binding violated: live={snapshot.content_hash!r} "
            f"expected={content_hash!r}"
        )
    if config_hash is not None and snapshot.config_hash != config_hash:
        raise ResearchRuntimeError(
            f"snapshot config_hash binding violated: live={snapshot.config_hash!r} "
            f"expected={config_hash!r}"
        )
    if universe_id is not None and snapshot.universe_id != universe_id:
        raise ResearchRuntimeError(
            f"snapshot universe_id binding violated: live={snapshot.universe_id!r} "
            f"expected={universe_id!r}"
        )
    if lower is not None and snapshot.available_at_lower != _compact_day(lower):
        raise ResearchRuntimeError(
            f"snapshot available_at_lower binding violated: "
            f"live={snapshot.available_at_lower!r} expected={lower!r}"
        )
    if upper is not None and snapshot.available_at_upper != _compact_day(upper):
        raise ResearchRuntimeError(
            f"snapshot available_at_upper binding violated: "
            f"live={snapshot.available_at_upper!r} expected={upper!r}"
        )
    if (
        observed_universe_id is not None
        and observed_universe_id != snapshot.universe_id
    ):
        raise ResearchRuntimeError(
            f"observed universe_id {observed_universe_id!r} != "
            f"snapshot.universe_id {snapshot.universe_id!r}"
        )
    if decision_date is not None:
        day = _compact_day(decision_date)
        if len(day) != 8:
            raise ResearchRuntimeError(f"invalid decision_date: {decision_date!r}")
        if day < snapshot.available_at_lower or day > snapshot.available_at_upper:
            raise ResearchRuntimeError(
                f"decision_date {day!r} outside snapshot available_at bounds "
                f"[{snapshot.available_at_lower}, {snapshot.available_at_upper}]"
            )


def measure_observations_stub(
    snapshot: DatasetSnapshot,
    observations: Sequence[ResearchObservation],
    *,
    decision_date: str,
    prereg: ExperimentPrereg,
) -> OfflineMeasureResult:
    """PIT-truncate observations under frozen prereg binding (offline stub)."""

    assert_snapshot_binding(snapshot, prereg=prereg, decision_date=decision_date)
    day = _compact_day(decision_date)
    kept = pit_truncate_observations(observations, day)
    status: Literal["measured_stub", "empty_after_pit"] = (
        "empty_after_pit" if not kept else "measured_stub"
    )
    return OfflineMeasureResult(
        status=status,
        decision_date=day,
        kept_observation_count=len(kept),
        input_observation_count=len(tuple(observations)),
        details={
            "strategy_release": False,
            "optuna": False,
            "snapshot_id": snapshot.snapshot_id,
            "universe_id": snapshot.universe_id,
            "prereg_primary_metric": prereg.primary_metric,
            "fold_embargo": prereg.fold_embargo.as_dict(),
        },
    )


def run_offline_minimal_loop(
    snapshot: DatasetSnapshot,
    observations: Sequence[ResearchObservation],
    *,
    decision_date: str,
    block: str = "B0",
    strategy_package: str = "phase_d_offline",
    hypothesis: str = "offline_measure_stub_no_edge_claim",
    fold_embargo: FoldEmbargoHooks | None = None,
    require_kept_rows: bool = False,
    observed_universe_id: str | None = None,
) -> tuple[ExperimentRun, ExperimentVerdict]:
    """End-to-end offline: prereg → bind → measure stub → ExperimentVerdict.

    Always ``claimable=false``. Does not emit StrategyRelease or run Optuna.
    """

    prereg = build_experiment_prereg(
        snapshot,
        strategy_package=strategy_package,
        block=block,
        hypothesis=hypothesis,
        fold_embargo=fold_embargo,
    )
    experiment_id = (
        f"{strategy_package}:{block}:{snapshot.snapshot_id}:{uuid4().hex[:12]}"
    )

    try:
        assert_snapshot_binding(
            snapshot,
            prereg=prereg,
            decision_date=decision_date,
            observed_universe_id=observed_universe_id,
        )
        measured = measure_observations_stub(
            snapshot,
            observations,
            decision_date=decision_date,
            prereg=prereg,
        )
        # Mid-run re-bind: catch hash/universe drift after measure.
        assert_snapshot_binding(snapshot, prereg=prereg, decision_date=decision_date)
    except ResearchRuntimeError as exc:
        run = ExperimentRun(
            experiment_id=experiment_id,
            strategy_package=strategy_package,
            block=block,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            config_hash=snapshot.config_hash,
            universe_id=snapshot.universe_id,
            decision_date=_compact_day(decision_date),
            kept_observation_count=0,
            pit_ok=False,
            prereg=prereg,
            artifact_manifest={
                "kind": "phase_d_offline_minimal",
                "research_runtime_snapshot": snapshot.boundary_dict(),
                "prereg": prereg.as_dict(),
                "strategy_release": False,
                "error": str(exc)[:300],
            },
            notes=("snapshot_binding_fail_closed", "no_strategy_release", "no_optuna"),
        )
        verdict = ExperimentVerdict(
            verdict="reject",
            reason=REASON_SNAPSHOT_BINDING_VIOLATED,
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={
                "strategy_release": False,
                "error": str(exc)[:300],
                "prereg": prereg.as_dict(),
            },
        )
        return run, verdict
    except ValueError as exc:
        run = ExperimentRun(
            experiment_id=experiment_id,
            strategy_package=strategy_package,
            block=block,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            config_hash=snapshot.config_hash,
            universe_id=snapshot.universe_id,
            decision_date=_compact_day(decision_date),
            kept_observation_count=0,
            pit_ok=False,
            prereg=prereg,
            artifact_manifest={
                "kind": "phase_d_offline_minimal",
                "research_runtime_snapshot": snapshot.boundary_dict(),
                "prereg": prereg.as_dict(),
                "strategy_release": False,
                "error": str(exc)[:300],
            },
            notes=("pit_fail_closed", "no_strategy_release", "no_optuna"),
        )
        verdict = ExperimentVerdict(
            verdict="reject",
            reason=REASON_PIT_FUTURE_AVAILABLE_AT,
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={"strategy_release": False, "error": str(exc)[:300]},
        )
        return run, verdict

    pit_ok = True
    if require_kept_rows and measured.kept_observation_count == 0:
        pit_ok = False

    run = ExperimentRun(
        experiment_id=experiment_id,
        strategy_package=strategy_package,
        block=block,
        snapshot_id=snapshot.snapshot_id,
        snapshot_content_hash=snapshot.content_hash,
        config_hash=snapshot.config_hash,
        universe_id=snapshot.universe_id,
        decision_date=measured.decision_date,
        kept_observation_count=measured.kept_observation_count,
        pit_ok=pit_ok,
        prereg=prereg,
        artifact_manifest={
            "kind": "phase_d_offline_minimal",
            "research_runtime_snapshot": snapshot.boundary_dict(),
            "prereg": prereg.as_dict(),
            "measure": measured.as_dict(),
            "strategy_release": False,
            "optuna": False,
        },
        notes=(
            "phase_d_offline_minimal_loop",
            "prereg_before_measure",
            "no_strategy_release",
            "no_optuna",
            "claimable_false",
        ),
    )
    if not pit_ok:
        verdict = ExperimentVerdict(
            verdict="reject",
            reason=REASON_PIT_LEAK_OR_EMPTY,
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={
                "strategy_release": False,
                "measure": measured.as_dict(),
                "prereg": prereg.as_dict(),
            },
        )
        return run, verdict

    verdict = ExperimentVerdict(
        verdict="inconclusive",
        reason=REASON_PHASE_D_OFFLINE_MEASURE_STUB,
        blocked=False,
        experiment_id=experiment_id,
        block=block,
        claimable=False,
        details={
            "strategy_release": False,
            "optuna": False,
            "measure": measured.as_dict(),
            "prereg": prereg.as_dict(),
            "snapshot_id": snapshot.snapshot_id,
            "universe_id": snapshot.universe_id,
            "config_hash": snapshot.config_hash,
            "available_at_lower": snapshot.available_at_lower,
            "available_at_upper": snapshot.available_at_upper,
        },
    )
    return run, verdict


def run_offline_b0_bound_loop(
    disclosure_snapshot: Mapping[str, Any] | None = None,
    *,
    snapshot_path: str | None = None,
) -> tuple[ExperimentRun, ExperimentVerdict]:
    """Reuse E B0 harness offline under Phase D prereg + snapshot binding.

    Lazy-imports B0 to avoid import cycles. Coverage/paper measure stay off so
    the path is offline-deterministic; verdict remains ``claimable=false``.
    """

    # Lazy import: institution_follow_b0 → research_runtime (owner).
    from services.institution_follow_b0 import (  # noqa: PLC0415
        build_b0_run,
        finalize_b0_verdict,
        load_frozen_disclosure_snapshot,
    )

    payload = (
        dict(disclosure_snapshot)
        if disclosure_snapshot is not None
        else load_frozen_disclosure_snapshot(snapshot_path)
    )
    runtime_snap = dataset_snapshot_from_disclosure(payload)
    prereg = build_experiment_prereg(
        runtime_snap,
        strategy_package="institution_follow",
        block="B0",
        hypothesis="b0_bare_k_offline_bound_to_research_runtime",
        fold_embargo=default_fold_embargo_hooks(
            n_folds=3,
            embargo_days=1,
            label_horizon_days=1,
        ),
    )
    assert_snapshot_binding(runtime_snap, prereg=prereg)

    b0_run = build_b0_run(
        snapshot=payload,
        measure_coverage=False,
        measure_paper=False,
    )
    # Mid-run: B0 manifest must still match frozen runtime snapshot.
    bound = (b0_run.artifact_manifest or {}).get("research_runtime_snapshot") or {}
    if bound.get("content_hash") != runtime_snap.content_hash:
        raise ResearchRuntimeError(
            "B0 research_runtime_snapshot content_hash drifted mid-run"
        )
    if bound.get("universe_id") != runtime_snap.universe_id:
        raise ResearchRuntimeError(
            "B0 research_runtime_snapshot universe_id drifted mid-run"
        )
    assert_snapshot_binding(runtime_snap, prereg=prereg)

    e_verdict = finalize_b0_verdict(b0_run)
    run = ExperimentRun(
        experiment_id=b0_run.experiment_id,
        strategy_package=b0_run.strategy_package,
        block=b0_run.block,
        snapshot_id=runtime_snap.snapshot_id,
        snapshot_content_hash=runtime_snap.content_hash,
        config_hash=runtime_snap.config_hash,
        universe_id=runtime_snap.universe_id,
        decision_date=runtime_snap.available_at_upper,
        kept_observation_count=0,
        pit_ok=True,
        prereg=prereg,
        artifact_manifest={
            "kind": "phase_d_offline_b0_bound",
            "research_runtime_snapshot": runtime_snap.boundary_dict(),
            "prereg": prereg.as_dict(),
            "b0_artifact_manifest": dict(b0_run.artifact_manifest),
            "strategy_release": False,
            "optuna": False,
        },
        notes=(
            "phase_d_offline_b0_bound",
            "reuses_institution_follow_b0",
            "measure_coverage_false",
            "measure_paper_false",
            "no_strategy_release",
            "no_optuna",
        ),
    )
    # Force claimable=false at D boundary even if E path ever loosens.
    verdict = ExperimentVerdict(
        verdict=e_verdict.verdict,
        reason=e_verdict.reason,
        blocked=e_verdict.blocked,
        experiment_id=e_verdict.experiment_id,
        block=e_verdict.block,
        claimable=False,
        details={
            **dict(e_verdict.details),
            "strategy_release": False,
            "phase_d_bound": True,
            "prereg": prereg.as_dict(),
            "claimable_forced_false_by_research_runtime": True,
        },
    )
    return run, verdict
