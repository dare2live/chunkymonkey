"""main_rally B0 setup-entry short-horizon research (Phase F / F1).

Frozen main_rally DatasetSnapshot → nominal coverage → pivot-setup paper WF.
Always non-StrategyRelease; claimable only via E-style edge gates (expected
reject/inconclusive on the 120d accepted window).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from services.holdout_guard import (
    HoldoutBoundaryViolation,
    assert_holdout_untouched,
    load_policy,
    training_cutoff_before_holdout,
)
from services.institution_follow_b0_measure import (
    MeasuredB0Result,
    REASON_SHORT_WINDOW,
)
from services.institution_follow_edge_gates import (
    REASON_EDGE_GATES_PASSED,
    REASON_EDGE_GATES_UNMET,
    evaluate_accept_edge_gates,
)
from services.main_rally_b0_measure import measure_main_rally_b0_paper
from services.main_rally_dataset_snapshot import (
    ABLATION_BOUNDED,
    ABLATION_CANARY,
    MAIN_RALLY_SNAPSHOT_RELPATH,
    SCOPE_BOUNDED,
    SCOPE_CANARY,
    dataset_snapshot_from_main_rally,
    default_snapshot_path,
    load_frozen_main_rally_snapshot,
)
from services.research_runtime import (
    ExperimentVerdict,
    VerdictKind,
    assert_snapshot_binding,
    build_experiment_prereg,
    fold_embargo_from_walk_forward_plan,
)

STRATEGY_PACKAGE = "main_rally_v1"
BLOCK_ID = "B0"
REQUIRED_SURFACE_STATUS = "tier3_research_evidence_only"
CANARY_SCOPE = SCOPE_CANARY
BOUNDED_SCOPE = SCOPE_BOUNDED
CANARY_ABLATION = ABLATION_CANARY
BOUNDED_ABLATION = ABLATION_BOUNDED
REASON_CANARY_SCOPE_ONLY = "canary_scope_only"
REASON_MEASURED_COVERAGE_INSUFFICIENT = "measured_coverage_insufficient"
REASON_MEASURED_SHORT_WINDOW = REASON_SHORT_WINDOW
REASON_PROTOCOL_READY_EDGE_UNMET = "measured_protocol_ready_edge_gates_unmet"
REASON_ACCEPT_EDGE_GATES_UNMET = REASON_EDGE_GATES_UNMET
REASON_ACCEPT_EDGE_GATES_PASSED = REASON_EDGE_GATES_PASSED
REASON_SCAFFOLD_NO_MEASURED_EDGE = "scaffold_no_measured_edge"
REASON_OFFLINE_FIXTURE_NOT_FORMAL = "offline_fixture_not_formal_evidence"
MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0 = 5

_VALID_VERDICTS = frozenset({"accept", "reject", "inconclusive"})


class MainRallyB0Error(RuntimeError):
    """Scaffold / gate failure for main_rally B0."""


class CanaryScopeOverclaimError(MainRallyB0Error):
    """Raised when a canary-scope run attempts an accept claim."""


@dataclass(frozen=True)
class PitHookSpec:
    name: str
    rule: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "rule": self.rule, "status": self.status}


@dataclass(frozen=True)
class HoldoutHookSpec:
    holdout_start: str
    training_cutoff: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return {
            "holdout_start": self.holdout_start,
            "training_cutoff": self.training_cutoff,
            "status": self.status,
        }


@dataclass(frozen=True)
class SetupCoverageMeasurement:
    status: str
    accepted_nominal_partitions: tuple[str, ...]
    accepted_nominal_day_count: int
    sufficient_for_measured_b0: bool
    reason: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted_nominal_partitions": list(self.accepted_nominal_partitions),
            "accepted_nominal_day_count": self.accepted_nominal_day_count,
            "sufficient_for_measured_b0": self.sufficient_for_measured_b0,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MainRallyB0Run:
    experiment_id: str
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_scope: str
    phase_f_ablation: str
    surface_status: str
    cutover_allowed: bool
    data_end_date: str
    pit_hooks: tuple[PitHookSpec, ...]
    holdout: HoldoutHookSpec
    artifact_manifest: dict[str, Any]
    notes: tuple[str, ...]
    measurement_source: str = "not_measured"
    prereg_registered: bool = False
    holdout_consumed: bool = False
    setup_coverage: SetupCoverageMeasurement | None = None
    measured_b0: MeasuredB0Result | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "strategy_package": self.strategy_package,
            "block": self.block,
            "snapshot_id": self.snapshot_id,
            "snapshot_scope": self.snapshot_scope,
            "phase_f_ablation": self.phase_f_ablation,
            "surface_status": self.surface_status,
            "cutover_allowed": self.cutover_allowed,
            "data_end_date": self.data_end_date,
            "pit_hooks": [h.as_dict() for h in self.pit_hooks],
            "holdout": self.holdout.as_dict(),
            "artifact_manifest": dict(self.artifact_manifest),
            "notes": list(self.notes),
            "measurement_source": self.measurement_source,
            "prereg_registered": self.prereg_registered,
            "holdout_consumed": self.holdout_consumed,
            "setup_coverage": (
                self.setup_coverage.as_dict() if self.setup_coverage else None
            ),
            "measured_b0": (
                self.measured_b0.as_dict() if self.measured_b0 else None
            ),
        }


def is_canary_scope(snapshot: Mapping[str, Any] | MainRallyB0Run) -> bool:
    if isinstance(snapshot, MainRallyB0Run):
        return (
            snapshot.snapshot_scope == CANARY_SCOPE
            or snapshot.phase_f_ablation == CANARY_ABLATION
        )
    return (
        str(snapshot.get("scope") or "") == CANARY_SCOPE
        or str(snapshot.get("phase_f_ablation") or "") == CANARY_ABLATION
    )


def is_bounded_scope(snapshot: Mapping[str, Any] | MainRallyB0Run) -> bool:
    if isinstance(snapshot, MainRallyB0Run):
        return snapshot.snapshot_scope == BOUNDED_SCOPE
    return str(snapshot.get("scope") or "") == BOUNDED_SCOPE


def _nominal_partitions_from_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    domains = snapshot.get("domains") or {}
    nominal = domains.get("nominal_ohlcv") or {}
    dates = nominal.get("date_set") or []
    out = sorted(
        {
            "".join(ch for ch in str(d) if ch.isdigit())[:8]
            for d in dates
            if len("".join(ch for ch in str(d) if ch.isdigit())[:8]) == 8
        }
    )
    return out


def measure_setup_coverage(
    snapshot: Mapping[str, Any],
    *,
    accepted_nominal_partitions: Sequence[str] | None = None,
) -> SetupCoverageMeasurement:
    parts = (
        list(accepted_nominal_partitions)
        if accepted_nominal_partitions is not None
        else _nominal_partitions_from_snapshot(snapshot)
    )
    parts = sorted(
        {
            "".join(ch for ch in str(p) if ch.isdigit())[:8]
            for p in parts
            if len("".join(ch for ch in str(p) if ch.isdigit())[:8]) == 8
        }
    )
    n_days = len(parts)
    sufficient = n_days >= MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0
    return SetupCoverageMeasurement(
        status="MEASURED" if n_days else "EMPTY",
        accepted_nominal_partitions=tuple(parts),
        accepted_nominal_day_count=n_days,
        sufficient_for_measured_b0=sufficient,
        reason=(
            "measured_nominal_window_ready"
            if sufficient
            else REASON_MEASURED_COVERAGE_INSUFFICIENT
        ),
        details={
            "min_required_nominal_days": MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0,
            "measurement_kind": "setup_entry_short_horizon",
            "full_episode_not_attempted": True,
            "measured_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _default_pit_hooks(*, canary: bool, measured: bool) -> tuple[PitHookSpec, ...]:
    if canary:
        status = "declared_canary_only"
    elif measured:
        status = "declared_measured_coverage"
    else:
        status = "declared"
    return (
        PitHookSpec(
            name="decision_time_truncation",
            rule="features_and_candidates_zero_diff_before_cutoff",
            status=status,
        ),
        PitHookSpec(
            name="pivot_confirmation_lag",
            rule="signal_available_at_equals_bottom_plus_pivot_low_window",
            status=status,
        ),
        PitHookSpec(
            name="label_table_isolation",
            rule="candidate_generator_never_reads_fact_rally_gt_or_negative",
            status=status,
        ),
        PitHookSpec(
            name="nominal_execution_truth",
            rule="orders_use_nominal_ohlcv_not_qfq",
            status=status,
        ),
    )


def build_b0_run(
    *,
    snapshot: Mapping[str, Any] | None = None,
    snapshot_path: Path | str | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    data_end_date: str | None = None,
    cutover_allowed: bool | None = None,
    measure_coverage: bool = True,
    measure_paper: bool = True,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    accepted_nominal_partitions: Sequence[str] | None = None,
    prereg_store_dir: Path | str | None = None,
) -> MainRallyB0Run:
    payload = (
        dict(snapshot)
        if snapshot is not None
        else load_frozen_main_rally_snapshot(snapshot_path)
    )
    if (
        accepted_nominal_partitions is not None
        and measure_paper
        and bars_by_day is None
    ):
        raise MainRallyB0Error(
            "formal measurement must use the frozen snapshot nominal date_set; "
            "accepted_nominal_partitions override is fixture-only"
        )
    if surface_status != REQUIRED_SURFACE_STATUS:
        raise MainRallyB0Error(
            f"surface_status must be {REQUIRED_SURFACE_STATUS!r}, "
            f"got {surface_status!r}"
        )

    end = data_end_date or training_cutoff_before_holdout()
    snap_nominal = _nominal_partitions_from_snapshot(payload)
    actual_end = snap_nominal[-1] if snap_nominal else None
    assert_holdout_untouched(end, actual_data_end=actual_end)

    canary = is_canary_scope(payload)
    bounded = is_bounded_scope(payload)
    holdout_start = str(load_policy()["holdout_start"])
    training_cutoff = training_cutoff_before_holdout()
    snap_id = str(payload.get("snapshot_id") or "")
    if not snap_id:
        raise MainRallyB0Error("snapshot_id required on DatasetSnapshot")

    allowed = (
        bool(cutover_allowed)
        if cutover_allowed is not None
        else bool(payload.get("cutover_allowed"))
    )
    run_id = uuid4().hex[:12]
    experiment_id = f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{snap_id}:{run_id}"

    coverage: SetupCoverageMeasurement | None = None
    if measure_coverage and (bounded or not canary):
        coverage = measure_setup_coverage(
            payload, accepted_nominal_partitions=accepted_nominal_partitions
        )
        if coverage is not None and coverage.accepted_nominal_partitions:
            assert_holdout_untouched(
                end, actual_data_end=coverage.accepted_nominal_partitions[-1]
            )

    plan = None
    fold_embargo = None
    if (
        measure_paper
        and coverage is not None
        and coverage.sufficient_for_measured_b0
        and not canary
    ):
        from services.institution_follow_b0_measure import plan_walk_forward

        plan = plan_walk_forward(list(coverage.accepted_nominal_partitions))
        fold_embargo = fold_embargo_from_walk_forward_plan(plan)

    runtime_snap = dataset_snapshot_from_main_rally(payload)
    formal_measure = plan is not None and bars_by_day is None
    prereg = build_experiment_prereg(
        runtime_snap,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        hypothesis="main_rally_b0_setup_entry_short_horizon_no_edge_claim",
        fold_embargo=fold_embargo,
        register_store=formal_measure,
        store_dir=prereg_store_dir,
    )
    assert_snapshot_binding(runtime_snap, prereg=prereg)

    measured: MeasuredB0Result | None = None
    if plan is not None and coverage is not None:
        days = list(coverage.accepted_nominal_partitions)
        owned = False
        conn = nominal_conn
        try:
            from services.holdout_guard import consume_holdout_single_touch

            if bars_by_day is None:
                if conn is None:
                    from services.data_access.resolver import connect_ro

                    conn = connect_ro("tushare_raw")
                    owned = True
                from services.snapshot_nominal_bind import (
                    assert_live_nominal_pointer_matches_snapshot,
                    load_snapshot_bound_nominal_bars_by_day,
                )

                assert_live_nominal_pointer_matches_snapshot(
                    payload, conn, days=days
                )
                if plan.holdout_dates:
                    consume_holdout_single_touch(
                        prereg.single_touch_token, store_dir=prereg_store_dir
                    )
                bars = load_snapshot_bound_nominal_bars_by_day(
                    payload, conn, days=days
                )
            else:
                from services.snapshot_nominal_bind import require_offline_fixture_bars

                bars = require_offline_fixture_bars(bars_by_day)
            measured = measure_main_rally_b0_paper(bars, days, walk_forward=plan)
            if measured.walk_forward.as_dict() != plan.as_dict():
                raise MainRallyB0Error("measured walk-forward plan drifted from prereg")
            assert_snapshot_binding(runtime_snap, prereg=prereg)
        finally:
            if owned and conn is not None:
                conn.close()

    notes = [
        "b0_setup_entry_short_horizon",
        "no_optuna_no_full_history_search",
        "no_full_episode_measurement",
        "gt_labels_not_read_by_candidate_generator",
        f"main_rally_snapshot_relpath={MAIN_RALLY_SNAPSHOT_RELPATH}",
        "research_runtime_dataset_snapshot_bound",
    ]
    if canary:
        notes.append("canary_scope_blocks_claimable_verdict")
    if bounded:
        notes.append("bounded_scope_measured_coverage_attempted")
    if coverage is not None and not coverage.sufficient_for_measured_b0:
        notes.append("measured_coverage_insufficient_for_setup_edge")
    if measured is not None:
        notes.append("measured_wf_paper_attempted")
        if not measured.claimable:
            notes.append("short_window_protocol_not_claimable")

    metrics_label = "unknown"
    paper_label = "not_run"
    if coverage is not None:
        metrics_label = (
            "coverage_measured_insufficient"
            if not coverage.sufficient_for_measured_b0
            else "coverage_measured_ready"
        )
    if measured is not None:
        metrics_label = "paper_metrics_measured"
        paper_label = "measured"
    measurement_source = (
        "live_snapshot_canonical"
        if measured is not None and formal_measure
        else "offline_fixture"
        if measured is not None and bars_by_day is not None
        else "not_measured"
    )
    holdout_consumed = bool(
        measured is not None
        and formal_measure
        and plan is not None
        and plan.holdout_dates
    )

    return MainRallyB0Run(
        experiment_id=experiment_id,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        snapshot_id=snap_id,
        snapshot_scope=str(payload.get("scope") or ""),
        phase_f_ablation=str(payload.get("phase_f_ablation") or ""),
        surface_status=surface_status,
        cutover_allowed=allowed,
        data_end_date=str(end).replace("-", "")[:8],
        pit_hooks=_default_pit_hooks(
            canary=canary, measured=coverage is not None
        ),
        holdout=HoldoutHookSpec(
            holdout_start=holdout_start.replace("-", "")[:8],
            training_cutoff=training_cutoff,
            status="exercised",
        ),
        artifact_manifest={
            "kind": "main_rally_b0",
            "main_rally_snapshot": MAIN_RALLY_SNAPSHOT_RELPATH,
            "research_runtime_snapshot": runtime_snap.boundary_dict(),
            "prereg": prereg.as_dict(),
            "metrics": metrics_label,
            "paper_fills": paper_label,
            "setup_coverage": coverage.as_dict() if coverage else None,
            "measured_b0": measured.as_dict() if measured else None,
            "strategy_release": False,
            "optuna": False,
        },
        notes=tuple(notes),
        measurement_source=measurement_source,
        prereg_registered=bool(measured is not None and formal_measure),
        holdout_consumed=holdout_consumed,
        setup_coverage=coverage,
        measured_b0=measured,
    )


def finalize_b0_verdict(
    run: MainRallyB0Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    if requested_verdict is not None and requested_verdict not in _VALID_VERDICTS:
        raise MainRallyB0Error(
            f"invalid verdict {requested_verdict!r}; "
            f"expected one of {sorted(_VALID_VERDICTS)}"
        )

    canary = is_canary_scope(run)
    wants_accept = force_accept or requested_verdict == "accept"

    if canary and wants_accept:
        raise CanaryScopeOverclaimError(
            "main_rally B0 cannot accept under canary DatasetSnapshot "
            f"scope={run.snapshot_scope!r} phase_f_ablation={run.phase_f_ablation!r}; "
            f"reason={REASON_CANARY_SCOPE_ONLY}"
        )

    if canary:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_CANARY_SCOPE_ONLY,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "snapshot_scope": run.snapshot_scope,
                "phase_f_ablation": run.phase_f_ablation,
                "requested_verdict": requested_verdict,
                "surface_status": run.surface_status,
                "cutover_allowed": run.cutover_allowed,
                "strategy_release": False,
                "metrics": "unknown",
                "note": "canary scope only; not a claimable B0 setup baseline",
            },
        )

    coverage = run.setup_coverage
    if coverage is not None and not coverage.sufficient_for_measured_b0:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_MEASURED_COVERAGE_INSUFFICIENT,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "snapshot_scope": run.snapshot_scope,
                "phase_f_ablation": run.phase_f_ablation,
                "requested_verdict": requested_verdict,
                "surface_status": run.surface_status,
                "cutover_allowed": run.cutover_allowed,
                "setup_coverage": coverage.as_dict(),
                "bare_k_coverage": coverage.as_dict(),
                "strategy_release": False,
                "metrics": "coverage_measured_insufficient",
                "note": "accepted nominal OHLCV window too thin for setup edge",
            },
        )

    measured = run.measured_b0
    if measured is not None:
        if not has_formal_b0_evidence(run):
            return ExperimentVerdict(
                verdict="inconclusive",
                reason=REASON_OFFLINE_FIXTURE_NOT_FORMAL,
                blocked=True,
                experiment_id=run.experiment_id,
                block=run.block,
                claimable=False,
                details={
                    "requested_verdict": requested_verdict,
                    "measurement_source": run.measurement_source,
                    "prereg_registered": run.prereg_registered,
                    "holdout_consumed": run.holdout_consumed,
                    "metrics": measured.metrics.as_dict(),
                    "strategy_release": False,
                    "note": "offline fixture measurements are diagnostic only",
                },
            )
        edge = evaluate_accept_edge_gates(
            measured.walk_forward,
            measured.metrics,
            measured.holdout_metrics,
            prereg=measured.prereg,
        )
        base = {
            "requested_verdict": requested_verdict,
            "metrics": measured.metrics.as_dict(),
            "holdout_metrics": measured.holdout_metrics.as_dict(),
            "walk_forward": measured.walk_forward.as_dict(),
            "accept_edge_gates": edge.as_dict(),
            "setup_coverage": coverage.as_dict() if coverage else None,
            "bare_k_coverage": coverage.as_dict() if coverage else None,
            "paper_fills": "measured",
            "surface_status": run.surface_status,
            "prereg": measured.prereg.as_dict(),
            "strategy_release": False,
            "measurement_kind": "setup_entry_short_horizon",
            "full_episode_not_attempted": True,
        }
        if not measured.claimable:
            return ExperimentVerdict(
                verdict="inconclusive",
                reason=REASON_MEASURED_SHORT_WINDOW,
                blocked=True,
                experiment_id=run.experiment_id,
                block=run.block,
                claimable=False,
                details={**base, "protocol_claimable": False,
                         "note": "prereg power gates not met"},
            )
        if edge.passed:
            return ExperimentVerdict(
                verdict="accept",
                reason=REASON_ACCEPT_EDGE_GATES_PASSED,
                blocked=False,
                experiment_id=run.experiment_id,
                block=run.block,
                claimable=True,
                details={**base, "protocol_claimable": True,
                         "note": "protocol power + accept edge gates passed"},
            )
        return ExperimentVerdict(
            verdict="reject",
            reason=REASON_PROTOCOL_READY_EDGE_UNMET,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={**base, "protocol_claimable": True,
                     "note": "accept edge gates unmet"},
        )

    verdict: VerdictKind = requested_verdict or "inconclusive"
    if verdict == "accept":
        return ExperimentVerdict(
            verdict="inconclusive",
            reason="scaffold_metrics_unknown",
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "requested_verdict": requested_verdict,
                "metrics": "unknown",
                "setup_coverage": coverage.as_dict() if coverage else None,
                "strategy_release": False,
                "note": "B0 cannot accept without measured paper results",
            },
        )
    return ExperimentVerdict(
        verdict=verdict,
        reason=(
            REASON_SCAFFOLD_NO_MEASURED_EDGE
            if verdict == "inconclusive"
            else "explicit"
        ),
        blocked=False,
        experiment_id=run.experiment_id,
        block=run.block,
        claimable=False,
        details={
            "requested_verdict": requested_verdict,
            "metrics": "unknown",
            "surface_status": run.surface_status,
            "setup_coverage": coverage.as_dict() if coverage else None,
            "strategy_release": False,
        },
    )


def has_formal_b0_evidence(run: MainRallyB0Run) -> bool:
    """True only for preregistered, consumed, snapshot-bound live outcomes."""

    return (
        run.measurement_source == "live_snapshot_canonical"
        and run.prereg_registered
        and run.holdout_consumed
    )


def run_b0_scaffold(
    *,
    snapshot: Mapping[str, Any] | None = None,
    snapshot_path: Path | str | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    data_end_date: str | None = None,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
    measure_coverage: bool = True,
    measure_paper: bool = True,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    accepted_nominal_partitions: Sequence[str] | None = None,
) -> tuple[MainRallyB0Run, ExperimentVerdict]:
    run = build_b0_run(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        surface_status=surface_status,
        data_end_date=data_end_date,
        measure_coverage=measure_coverage,
        measure_paper=measure_paper,
        nominal_conn=nominal_conn,
        bars_by_day=bars_by_day,
        accepted_nominal_partitions=accepted_nominal_partitions,
    )
    verdict = finalize_b0_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )
    return run, verdict


__all__ = [
    "BLOCK_ID",
    "BOUNDED_ABLATION",
    "BOUNDED_SCOPE",
    "CANARY_ABLATION",
    "CANARY_SCOPE",
    "CanaryScopeOverclaimError",
    "ExperimentVerdict",
    "HoldoutBoundaryViolation",
    "HoldoutHookSpec",
    "MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0",
    "MainRallyB0Error",
    "MainRallyB0Run",
    "PitHookSpec",
    "REASON_ACCEPT_EDGE_GATES_PASSED",
    "REASON_ACCEPT_EDGE_GATES_UNMET",
    "REASON_CANARY_SCOPE_ONLY",
    "REASON_MEASURED_COVERAGE_INSUFFICIENT",
    "REASON_MEASURED_SHORT_WINDOW",
    "REASON_PROTOCOL_READY_EDGE_UNMET",
    "REASON_SCAFFOLD_NO_MEASURED_EDGE",
    "REQUIRED_SURFACE_STATUS",
    "STRATEGY_PACKAGE",
    "SetupCoverageMeasurement",
    "build_b0_run",
    "default_snapshot_path",
    "finalize_b0_verdict",
    "is_bounded_scope",
    "is_canary_scope",
    "load_frozen_main_rally_snapshot",
    "measure_setup_coverage",
    "run_b0_scaffold",
]
