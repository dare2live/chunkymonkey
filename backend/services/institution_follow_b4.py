"""institution_follow B4 institution/event block (Phase E measured ablation).

Adds one named FeatureBlock on top of B0 bare-K under the same disclosure
``DatasetSnapshot``, folds, costs and paper execution. Gates top-K on
PIT-safe holder increase events (``notice_date`` / ``available_at``) with
§8.1 next-open chase. Prefer inconclusive when disclosure coverage is thin.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from services.institution_follow_b0 import (
    BOUNDED_SCOPE,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    ExperimentVerdict,
    InstitutionFollowB0Error,
    InstitutionFollowB0Run,
    REASON_ACCEPT_EDGE_GATES_PASSED,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
    finalize_b0_verdict,
    is_canary_scope,
    load_frozen_disclosure_snapshot,
)
from services.institution_follow_b0_measure import load_nominal_bars_by_day
from services.institution_follow_edge_gates import (
    REASON_HOLDOUT_LIFT_UNMET,
    evaluate_accept_edge_gates,
    evaluate_holdout_lift_vs_b0,
)
from services.institution_follow_b4_measure import (
    DEFINITION_VERSION,
    MAX_CHASE_DAYS,
    METHOD_ID,
    POPULATION_KIND,
    REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT,
    REASON_B4_NO_B0_CONTEXT,
    REASON_B4_PAPER_MEASURED,
    DisclosureEpisode,
    MeasuredB4Result,
    episodes_from_holder_rows,
    load_holder_rows_for_snapshot,
    measure_b4_paper,
    open_holders_conn,
)

BLOCK_ID = "B4"
FEATURE_BLOCK_ID = "institution_event_holders_disclosure_v0"
REASON_B4_SCAFFOLD_NO_MEASURED_EDGE = "b4_scaffold_institution_event_not_measured"
REASON_B4_DEPENDS_ON_B0 = "b4_requires_b0_protocol_context"

VerdictKind = Literal["accept", "reject", "inconclusive"]


@dataclass(frozen=True)
class InstitutionEventFeatureBlock:
    """Declared B4 feature block — definition + optional measured status."""

    block_id: str = FEATURE_BLOCK_ID
    ablation_parent: str = "B0"
    inputs: tuple[str, ...] = (
        "accepted_nominal_ohlcv_daily",
        "DatasetSnapshot.holders_top10",
        "canonical_top10_float_holders_period",
    )
    outputs: tuple[str, ...] = (
        "disclosure_event_eligible",
        "institution_increase_score",
    )
    availability: str = "notice_date_and_available_at_required_null_excluded"
    status: str = "declared"
    note: str = (
        "Gate B0 top-K on PIT-safe holder increase events "
        f"(method={METHOD_ID}; chase≤{MAX_CHASE_DAYS}); "
        "same snapshot/folds/costs/paper as B0; thin coverage → inconclusive"
    )
    definition_version: str = DEFINITION_VERSION
    config_hash: str = "increase_event_day_chase3_v0"

    def as_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "ablation_parent": self.ablation_parent,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "availability": self.availability,
            "status": self.status,
            "note": self.note,
            "config_hash": self.config_hash,
            "definition_version": self.definition_version,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
            "max_chase_days": MAX_CHASE_DAYS,
        }


@dataclass(frozen=True)
class InstitutionFollowB4Run:
    experiment_id: str
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_scope: str
    phase_e_ablation: str
    surface_status: str
    feature_block: InstitutionEventFeatureBlock
    b0: InstitutionFollowB0Run
    measured_b4: MeasuredB4Result | None
    notes: tuple[str, ...]
    artifact_manifest: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "strategy_package": self.strategy_package,
            "block": self.block,
            "snapshot_id": self.snapshot_id,
            "snapshot_scope": self.snapshot_scope,
            "phase_e_ablation": self.phase_e_ablation,
            "surface_status": self.surface_status,
            "feature_block": self.feature_block.as_dict(),
            "b0": self.b0.as_dict(),
            "measured_b4": (
                self.measured_b4.as_dict() if self.measured_b4 else None
            ),
            "notes": list(self.notes),
            "artifact_manifest": dict(self.artifact_manifest),
        }


def _run_measured_b4(
    b0: InstitutionFollowB0Run,
    snapshot: Mapping[str, Any],
    *,
    nominal_conn=None,
    holders_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    episodes: Sequence[DisclosureEpisode] | None = None,
    holder_rows: Sequence[Mapping[str, Any]] | None = None,
) -> MeasuredB4Result | None:
    if b0.measured_b0 is None:
        return None
    days = list(b0.measured_b0.walk_forward.trading_days)
    owned_nominal = False
    owned_holders = False
    n_conn = nominal_conn
    h_conn = holders_conn
    try:
        if bars_by_day is None:
            if n_conn is None:
                from services.data_access.resolver import connect_ro

                n_conn = connect_ro("tushare_raw")
                owned_nominal = True
            bars = load_nominal_bars_by_day(n_conn, days)
        else:
            bars = {str(k): list(v) for k, v in bars_by_day.items()}  # type: ignore[arg-type]

        null_excl = 0
        miss_avail = 0
        if episodes is not None:
            eps = tuple(episodes)
        else:
            rows: list[Mapping[str, Any]]
            if holder_rows is not None:
                rows = list(holder_rows)
            else:
                if h_conn is None:
                    h_conn = open_holders_conn()
                    owned_holders = True
                rows = load_holder_rows_for_snapshot(h_conn, snapshot)
            eps, null_excl, miss_avail = episodes_from_holder_rows(rows)

        return measure_b4_paper(
            bars,
            b0_measured=b0.measured_b0,
            episodes=eps,
            null_notice_excluded=null_excl,
            missing_available_at_excluded=miss_avail,
        )
    finally:
        if owned_nominal and n_conn is not None:
            n_conn.close()
        if owned_holders and h_conn is not None:
            h_conn.close()


def build_b4_run(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    measure_b4_paper_flag: bool = True,
    nominal_conn=None,
    holders_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    episodes: Sequence[DisclosureEpisode] | None = None,
    holder_rows: Sequence[Mapping[str, Any]] | None = None,
) -> InstitutionFollowB4Run:
    """Build B4 bound to the same snapshot/B0 context; measure when ready."""

    payload = dict(snapshot) if snapshot is not None else load_frozen_disclosure_snapshot()
    if surface_status != REQUIRED_SURFACE_STATUS:
        raise InstitutionFollowB0Error(
            f"surface_status must be {REQUIRED_SURFACE_STATUS!r}, "
            f"got {surface_status!r}"
        )
    base = b0_run or build_b0_run(
        snapshot=payload,
        surface_status=surface_status,
        measure_paper=measure_b0_paper,
        nominal_conn=nominal_conn,
        bars_by_day=bars_by_day,
    )
    measured: MeasuredB4Result | None = None
    canary = is_canary_scope(payload)
    if (
        measure_b4_paper_flag
        and not canary
        and base.measured_b0 is not None
    ):
        measured = _run_measured_b4(
            base,
            payload,
            nominal_conn=nominal_conn,
            holders_conn=holders_conn,
            bars_by_day=bars_by_day,
            episodes=episodes,
            holder_rows=holder_rows,
        )

    run_id = uuid4().hex[:12]
    experiment_id = (
        f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{base.snapshot_id}:{run_id}"
    )
    fb_status = "declared"
    notes = [
        "b4_institution_event_block",
        "one_block_ablation_on_b0",
        "pit_notice_date_available_at",
        "chase_next_open_max_3",
        "no_optuna_no_strategy_release",
        "prefer_inconclusive_if_coverage_thin",
    ]
    if is_canary_scope(payload):
        notes.append("canary_scope_blocks_claimable_verdict")
        fb_status = "declared_scaffold"
    if str(payload.get("scope") or "") == BOUNDED_SCOPE:
        notes.append("bounded_scope_inherits_b0_protocol_context")
    if measured is None:
        notes.append(REASON_B4_SCAFFOLD_NO_MEASURED_EDGE)
        fb_status = "declared_scaffold"
    else:
        notes.append("measured_b4_paper_attempted")
        if not measured.coverage.sufficient:
            notes.append(measured.reason)
            fb_status = "coverage_insufficient"
        else:
            fb_status = "measured_gated"
            notes.append(REASON_B4_PAPER_MEASURED)

    feature_block = InstitutionEventFeatureBlock(status=fb_status)
    metrics_label = "unknown"
    paper_label = "not_run"
    if measured is not None:
        if not measured.coverage.sufficient:
            metrics_label = "disclosure_event_coverage_insufficient"
        elif measured.measured is not None:
            metrics_label = "paper_metrics_measured"
            paper_label = "measured"

    return InstitutionFollowB4Run(
        experiment_id=experiment_id,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        snapshot_id=base.snapshot_id,
        snapshot_scope=str(payload.get("scope") or ""),
        phase_e_ablation=str(payload.get("phase_e_ablation") or ""),
        surface_status=surface_status,
        feature_block=feature_block,
        b0=base,
        measured_b4=measured,
        notes=tuple(notes),
        artifact_manifest={
            "kind": "institution_follow_b4",
            "feature_block": feature_block.as_dict(),
            "metrics": metrics_label,
            "paper_fills": paper_label,
            "measured_b4": measured.as_dict() if measured else None,
            "b0_experiment_id": base.experiment_id,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
            "max_chase_days": MAX_CHASE_DAYS,
        },
    )


def finalize_b4_verdict(
    run: InstitutionFollowB4Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    """B4 verdict: coverage → edge gates → holdout lift; never fake accept."""

    wants_accept = requested_verdict == "accept" or force_accept
    if is_canary_scope(
        {
            "scope": run.snapshot_scope,
            "phase_e_ablation": run.phase_e_ablation,
        }
    ) or run.snapshot_scope == CANARY_SCOPE:
        if wants_accept:
            raise CanaryScopeOverclaimError(
                "canary_scope_only blocks B4 accept"
            )
        return ExperimentVerdict(
            verdict="inconclusive",
            reason="canary_scope_only",
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "feature_block": run.feature_block.as_dict(),
                "note": "B4 under canary cannot claim",
            },
        )

    b0_verdict = finalize_b0_verdict(run.b0, requested_verdict=None)
    measured = run.measured_b4

    if measured is None:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_B4_SCAFFOLD_NO_MEASURED_EDGE,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "requested_verdict": requested_verdict,
                "feature_block": run.feature_block.as_dict(),
                "b0_verdict": b0_verdict.as_dict(),
                "metrics": "unknown",
                "paper_fills": "not_run",
                "depends_on": REASON_B4_DEPENDS_ON_B0,
                "note": "B4 paper not run (missing B0 measured context)",
            },
        )

    if not measured.coverage.sufficient:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=measured.reason,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "requested_verdict": requested_verdict,
                "feature_block": run.feature_block.as_dict(),
                "b0_verdict": b0_verdict.as_dict(),
                "disclosure_event_coverage": measured.coverage.as_dict(),
                "b0_metrics": (
                    measured.b0_metrics.as_dict()
                    if measured.b0_metrics
                    else None
                ),
                "metrics": "disclosure_event_coverage_insufficient",
                "paper_fills": "not_run",
                "note": (
                    "Disclosure event coverage/PIT thin on bounded snapshot; "
                    "not a fake B4 improve — prefer inconclusive"
                ),
            },
        )

    assert measured.measured is not None
    edge = evaluate_accept_edge_gates(
        measured.measured.walk_forward,
        measured.measured.metrics,
        measured.measured.holdout_metrics,
        prereg=measured.measured.prereg,
    )
    stability = evaluate_holdout_lift_vs_b0(
        measured.measured.holdout_metrics,
        measured.b0_holdout_metrics,
    )
    details = {
        "requested_verdict": requested_verdict,
        "feature_block": run.feature_block.as_dict(),
        "b0_verdict": b0_verdict.as_dict(),
        "b0_metrics": (
            measured.b0_metrics.as_dict() if measured.b0_metrics else None
        ),
        "b0_holdout_metrics": (
            measured.b0_holdout_metrics.as_dict()
            if measured.b0_holdout_metrics
            else None
        ),
        "metrics": measured.measured.metrics.as_dict(),
        "holdout_metrics": measured.measured.holdout_metrics.as_dict(),
        "delta_b4_minus_b0": (
            measured.delta.as_dict() if measured.delta else None
        ),
        "disclosure_event_coverage": measured.coverage.as_dict(),
        "walk_forward": measured.measured.walk_forward.as_dict(),
        "accept_edge_gates": edge.as_dict(),
        "holdout_lift_stability": stability.as_dict(),
        "paper_fills": "measured",
        "protocol_claimable": measured.claimable,
        "b0_protocol_claimable": bool(
            run.b0.measured_b0.claimable if run.b0.measured_b0 else False
        ),
        "method": METHOD_ID,
        "population_kind": POPULATION_KIND,
        "max_chase_days": MAX_CHASE_DAYS,
    }

    if not measured.claimable:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=measured.reason,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                **details,
                "note": "B4 paper measured but protocol power insufficient",
            },
        )

    if edge.passed and stability.passed:
        return ExperimentVerdict(
            verdict="accept",
            reason=REASON_ACCEPT_EDGE_GATES_PASSED,
            blocked=False,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=True,
            details={
                **details,
                "note": (
                    "B4 protocol + accept edge gates + holdout lift vs B0 "
                    "passed (still ≠ StrategyRelease)"
                ),
            },
        )

    if edge.passed and not stability.passed:
        return ExperimentVerdict(
            verdict="reject",
            reason=REASON_HOLDOUT_LIFT_UNMET,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                **details,
                "note": (
                    "B4 edge gates passed but holdout does not strictly beat "
                    "B0 — claimable=false"
                ),
            },
        )

    return ExperimentVerdict(
        verdict="reject",
        reason=REASON_PROTOCOL_READY_EDGE_UNMET,
        blocked=True,
        experiment_id=run.experiment_id,
        block=run.block,
        claimable=False,
        details={
            **details,
            "note": (
                "B4 measured under identical folds/costs; accept edge "
                "gates unmet — claimable=false"
            ),
            "depends_on": REASON_B4_NO_B0_CONTEXT,
        },
    )


def run_b4_scaffold(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    measure_b4_paper_flag: bool = True,
    nominal_conn=None,
    holders_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    episodes: Sequence[DisclosureEpisode] | None = None,
    holder_rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[InstitutionFollowB4Run, ExperimentVerdict]:
    run = build_b4_run(
        snapshot=snapshot,
        surface_status=surface_status,
        b0_run=b0_run,
        measure_b0_paper=measure_b0_paper,
        measure_b4_paper_flag=measure_b4_paper_flag,
        nominal_conn=nominal_conn,
        holders_conn=holders_conn,
        bars_by_day=bars_by_day,
        episodes=episodes,
        holder_rows=holder_rows,
    )
    return run, finalize_b4_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )


run_b4_measured = run_b4_scaffold


__all__ = [
    "BLOCK_ID",
    "FEATURE_BLOCK_ID",
    "REASON_B4_DEPENDS_ON_B0",
    "REASON_B4_DISCLOSURE_COVERAGE_INSUFFICIENT",
    "REASON_B4_SCAFFOLD_NO_MEASURED_EDGE",
    "InstitutionEventFeatureBlock",
    "InstitutionFollowB4Run",
    "build_b4_run",
    "finalize_b4_verdict",
    "run_b4_measured",
    "run_b4_scaffold",
]
