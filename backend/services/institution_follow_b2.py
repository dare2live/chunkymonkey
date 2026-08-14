"""institution_follow B2 market-sensing block (Phase E measured ablation).

Adds one named FeatureBlock on top of B0 bare-K under the same disclosure
``DatasetSnapshot``, folds, costs and paper execution. Gates top-K on
``MarketContextSnapshot`` risk-on (project-board nominal breadth) when coverage
is sufficient; pulse mart / missing available_at fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from uuid import uuid4

from services.institution_follow_b0 import (
    BOUNDED_SCOPE,
    CANARY_SCOPE,
    CanaryScopeOverclaimError,
    ExperimentVerdict,
    InstitutionFollowB0Error,
    InstitutionFollowB0Run,
    REASON_ACCEPT_EDGE_GATES_PASSED,
    REASON_OFFLINE_FIXTURE_NOT_FORMAL,
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
    finalize_b0_verdict,
    has_formal_b0_evidence,
    is_canary_scope,
    load_frozen_disclosure_snapshot,
)
from services.institution_follow_b0_measure import load_nominal_bars_by_day
from services.institution_follow_edge_gates import (
    REASON_HOLDOUT_LIFT_UNMET,
    evaluate_accept_edge_gates,
    evaluate_holdout_lift_vs_b0,
)
from services.institution_follow_b2_measure import (
    DEFINITION_VERSION,
    METHOD_ID,
    POPULATION_KIND,
    REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT,
    REASON_B2_NO_B0_CONTEXT,
    REASON_B2_PAPER_MEASURED,
    REASON_B2_PULSE_UNTRUSTED,
    SOURCE_NOMINAL_BARS,
    MeasuredB2Result,
    build_context_by_day,
    measure_b2_paper,
)

BLOCK_ID = "B2"
FEATURE_BLOCK_ID = "market_sensing_project_breadth_v0"
REASON_B2_SCAFFOLD_NO_MEASURED_EDGE = "b2_scaffold_market_sensing_not_measured"
REASON_B2_DEPENDS_ON_B0 = "b2_requires_b0_protocol_context"

VerdictKind = Literal["accept", "reject", "inconclusive"]


@dataclass(frozen=True)
class MarketSensingFeatureBlock:
    """Declared B2 feature block — definition + optional measured status."""

    block_id: str = FEATURE_BLOCK_ID
    ablation_parent: str = "B0"
    inputs: tuple[str, ...] = (
        "accepted_nominal_ohlcv_daily",
        "MarketContextSnapshot",
    )
    outputs: tuple[str, ...] = (
        "market_risk_on",
        "project_board_adv_dec_ratio",
    )
    availability: str = "decision_time_eod_available_at_required"
    status: str = "declared"
    note: str = (
        "Gate B0 top-K on EOD project-board breadth risk-on "
        f"(method={METHOD_ID}; population={POPULATION_KIND}); "
        "refuse UNTRUSTED pulse mart; same snapshot/folds/costs/paper as B0"
    )
    definition_version: str = DEFINITION_VERSION
    config_hash: str = "risk_on_adv_dec_ratio_ge_1_v0"

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
        }


@dataclass(frozen=True)
class InstitutionFollowB2Run:
    experiment_id: str
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_scope: str
    phase_e_ablation: str
    surface_status: str
    feature_block: MarketSensingFeatureBlock
    b0: InstitutionFollowB0Run
    measured_b2: MeasuredB2Result | None
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
            "measured_b2": (
                self.measured_b2.as_dict() if self.measured_b2 else None
            ),
            "notes": list(self.notes),
            "artifact_manifest": dict(self.artifact_manifest),
        }


def _run_measured_b2(
    b0: InstitutionFollowB0Run,
    *,
    snapshot: Mapping[str, Any],
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    context_by_day: Mapping[str, Any] | None = None,
    source: str = SOURCE_NOMINAL_BARS,
) -> MeasuredB2Result | None:
    if b0.measured_b0 is None:
        return None
    days = list(b0.measured_b0.walk_forward.trading_days)
    owned_nominal = False
    n_conn = nominal_conn
    try:
        if bars_by_day is None:
            if n_conn is None:
                from services.data_access.resolver import connect_ro

                n_conn = connect_ro("tushare_raw")
                owned_nominal = True
            bars = load_nominal_bars_by_day(n_conn, days, snapshot=snapshot)
        else:
            from services.snapshot_nominal_bind import require_offline_fixture_bars

            bars = require_offline_fixture_bars(bars_by_day)

        ctx = None
        if context_by_day is not None:
            ctx = {str(k): v for k, v in context_by_day.items()}
        elif source != SOURCE_NOMINAL_BARS:
            ctx = build_context_by_day(bars, days, source=source)

        return measure_b2_paper(
            bars,
            b0_measured=b0.measured_b0,
            context_by_day=ctx,
            source=source,
        )
    finally:
        if owned_nominal and n_conn is not None:
            n_conn.close()


def build_b2_run(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    measure_b2_paper_flag: bool = True,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    context_by_day: Mapping[str, Any] | None = None,
    source: str = SOURCE_NOMINAL_BARS,
) -> InstitutionFollowB2Run:
    """Build B2 bound to the same snapshot/B0 context; measure when ready."""

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
    from services.snapshot_nominal_bind import assert_b0_run_matches_snapshot

    assert_b0_run_matches_snapshot(base, payload)
    measured: MeasuredB2Result | None = None
    canary = is_canary_scope(payload)
    if (
        measure_b2_paper_flag
        and not canary
        and base.measured_b0 is not None
    ):
        measured = _run_measured_b2(
            base,
            snapshot=payload,
            nominal_conn=nominal_conn,
            bars_by_day=bars_by_day,
            context_by_day=context_by_day,
            source=source,
        )

    run_id = uuid4().hex[:12]
    experiment_id = (
        f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{base.snapshot_id}:{run_id}"
    )
    fb_status = "declared"
    notes = [
        "b2_market_sensing_block",
        "one_block_ablation_on_b0",
        "project_board_breadth_not_pulse_mart",
        "no_optuna_no_accept_without_edge_gates",
    ]
    if is_canary_scope(payload):
        notes.append("canary_scope_blocks_claimable_verdict")
        fb_status = "declared_scaffold"
    if str(payload.get("scope") or "") == BOUNDED_SCOPE:
        notes.append("bounded_scope_inherits_b0_protocol_context")
    if measured is None:
        notes.append(REASON_B2_SCAFFOLD_NO_MEASURED_EDGE)
        fb_status = "declared_scaffold"
    else:
        notes.append("measured_b2_paper_attempted")
        if not measured.coverage.sufficient:
            notes.append(measured.reason)
            if measured.reason == REASON_B2_PULSE_UNTRUSTED:
                fb_status = "pulse_untrusted_fail_closed"
            else:
                fb_status = "coverage_insufficient"
        else:
            fb_status = "measured_gated"
            notes.append(REASON_B2_PAPER_MEASURED)

    feature_block = MarketSensingFeatureBlock(status=fb_status)
    metrics_label = "unknown"
    paper_label = "not_run"
    if measured is not None:
        if not measured.coverage.sufficient:
            metrics_label = "market_context_coverage_insufficient"
        elif measured.measured is not None:
            metrics_label = "paper_metrics_measured"
            paper_label = "measured"

    return InstitutionFollowB2Run(
        experiment_id=experiment_id,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        snapshot_id=base.snapshot_id,
        snapshot_scope=str(payload.get("scope") or ""),
        phase_e_ablation=str(payload.get("phase_e_ablation") or ""),
        surface_status=surface_status,
        feature_block=feature_block,
        b0=base,
        measured_b2=measured,
        notes=tuple(notes),
        artifact_manifest={
            "kind": "institution_follow_b2",
            "feature_block": feature_block.as_dict(),
            "metrics": metrics_label,
            "paper_fills": paper_label,
            "measured_b2": measured.as_dict() if measured else None,
            "b0_experiment_id": base.experiment_id,
            "method": METHOD_ID,
            "population_kind": POPULATION_KIND,
        },
    )


def finalize_b2_verdict(
    run: InstitutionFollowB2Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    """B2 verdict: coverage/trust → edge gates; never fake improve/accept."""

    wants_accept = requested_verdict == "accept" or force_accept
    if is_canary_scope(
        {
            "scope": run.snapshot_scope,
            "phase_e_ablation": run.phase_e_ablation,
        }
    ) or run.snapshot_scope == CANARY_SCOPE:
        if wants_accept:
            raise CanaryScopeOverclaimError(
                "canary_scope_only blocks B2 accept"
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
                "note": "B2 under canary cannot claim",
            },
        )

    b0_verdict = finalize_b0_verdict(run.b0, requested_verdict=None)
    measured = run.measured_b2

    if measured is None:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_B2_SCAFFOLD_NO_MEASURED_EDGE,
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
                "depends_on": REASON_B2_DEPENDS_ON_B0,
                "note": "B2 paper not run (missing B0 measured context)",
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
                "market_context_coverage": measured.coverage.as_dict(),
                "b0_metrics": (
                    measured.b0_metrics.as_dict()
                    if measured.b0_metrics
                    else None
                ),
                "metrics": "market_context_coverage_insufficient",
                "paper_fills": "not_run",
                "note": (
                    "MarketContextSnapshot coverage/trust insufficient; "
                    "not a fake B2 improve; pulse mart refused when UNTRUSTED"
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
        "delta_b2_minus_b0": (
            measured.delta.as_dict() if measured.delta else None
        ),
        "market_context_coverage": measured.coverage.as_dict(),
        "walk_forward": measured.measured.walk_forward.as_dict(),
        "accept_edge_gates": edge.as_dict(),
        "paper_fills": "measured",
        "protocol_claimable": measured.claimable,
        "b0_protocol_claimable": bool(
            run.b0.measured_b0.claimable if run.b0.measured_b0 else False
        ),
        "method": METHOD_ID,
        "population_kind": POPULATION_KIND,
    }

    if not has_formal_b0_evidence(run.b0):
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_OFFLINE_FIXTURE_NOT_FORMAL,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                **details,
                "depends_on": REASON_B2_DEPENDS_ON_B0,
                "note": "B2 cannot promote diagnostic B0 fixture evidence",
            },
        )

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
                "note": "B2 paper measured but protocol power insufficient",
            },
        )

    stability = evaluate_holdout_lift_vs_b0(
        measured.measured.holdout_metrics,
        measured.b0_holdout_metrics,
    )
    details["holdout_lift_stability"] = stability.as_dict()

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
                    "B2 protocol + accept edge gates + holdout lift vs B0 "
                    "passed"
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
                    "B2 short-window edge gates passed but holdout return "
                    "does not strictly beat B0 — not independent lift; "
                    "claimable=false (≠ StrategyRelease)"
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
                "B2 measured under identical folds/costs; accept edge "
                "gates unmet — claimable=false"
            ),
            "depends_on": REASON_B2_NO_B0_CONTEXT,
        },
    )


def run_b2_scaffold(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    measure_b2_paper_flag: bool = True,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    context_by_day: Mapping[str, Any] | None = None,
    source: str = SOURCE_NOMINAL_BARS,
) -> tuple[InstitutionFollowB2Run, ExperimentVerdict]:
    run = build_b2_run(
        snapshot=snapshot,
        surface_status=surface_status,
        b0_run=b0_run,
        measure_b0_paper=measure_b0_paper,
        measure_b2_paper_flag=measure_b2_paper_flag,
        nominal_conn=nominal_conn,
        bars_by_day=bars_by_day,
        context_by_day=context_by_day,
        source=source,
    )
    return run, finalize_b2_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )


run_b2_measured = run_b2_scaffold


__all__ = [
    "BLOCK_ID",
    "FEATURE_BLOCK_ID",
    "REASON_B2_CONTEXT_COVERAGE_INSUFFICIENT",
    "REASON_B2_DEPENDS_ON_B0",
    "REASON_B2_SCAFFOLD_NO_MEASURED_EDGE",
    "InstitutionFollowB2Run",
    "MarketSensingFeatureBlock",
    "build_b2_run",
    "finalize_b2_verdict",
    "run_b2_measured",
    "run_b2_scaffold",
]
