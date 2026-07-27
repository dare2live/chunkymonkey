"""institution_follow B1 stock-state block (Phase E measured ablation).

Adds one named FeatureBlock on top of B0 bare-K under the same disclosure
``DatasetSnapshot``, folds, costs and paper execution. Conditions top-K on
Tier1 stock state (trend=up or breakout) when coverage is sufficient;
otherwise inconclusive with an explicit coverage reason.

Stock-state loads go through ``load_stock_state_by_day`` →
``resolve_tier12_production_read`` (cutover gate). Default yaml keeps
``cutover_allowed=false`` so the live path remains ``fact_stock_form_daily``.
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
from services.institution_follow_edge_gates import evaluate_accept_edge_gates
from services.institution_follow_b1_measure import (
    DEFINITION_VERSION,
    MeasuredB1Result,
    REASON_B1_NO_B0_CONTEXT,
    REASON_B1_PAPER_MEASURED,
    REASON_B1_STATE_COVERAGE_INSUFFICIENT,
    load_stock_state_by_day,
    measure_b1_paper,
    open_stock_state_conn,
)

BLOCK_ID = "B1"
FEATURE_BLOCK_ID = "stock_state_stage_pattern_v0"
REASON_B1_SCAFFOLD_NO_MEASURED_EDGE = "b1_scaffold_stock_state_not_measured"
REASON_B1_DEPENDS_ON_B0 = "b1_requires_b0_protocol_context"

VerdictKind = Literal["accept", "reject", "inconclusive"]


@dataclass(frozen=True)
class StockStateFeatureBlock:
    """Declared B1 feature block — definition + optional measured status."""

    block_id: str = FEATURE_BLOCK_ID
    ablation_parent: str = "B0"
    inputs: tuple[str, ...] = (
        "accepted_nominal_ohlcv_daily",
        "accepted_stock_st_daily",
        "fact_stock_form_daily",
    )
    outputs: tuple[str, ...] = (
        "stock_state_stage",
        "pattern_event",
    )
    availability: str = "decision_time_visible_only"
    status: str = "declared"
    note: str = (
        "Condition B0 top-K on EOD axis_trend=up or is_breakout_event; "
        "same snapshot/folds/costs/paper as B0"
    )
    definition_version: str = DEFINITION_VERSION
    config_hash: str = "trend_up_or_breakout_v0"

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
        }


@dataclass(frozen=True)
class InstitutionFollowB1Run:
    experiment_id: str
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_scope: str
    phase_e_ablation: str
    surface_status: str
    feature_block: StockStateFeatureBlock
    b0: InstitutionFollowB0Run
    measured_b1: MeasuredB1Result | None
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
            "measured_b1": (
                self.measured_b1.as_dict() if self.measured_b1 else None
            ),
            "notes": list(self.notes),
            "artifact_manifest": dict(self.artifact_manifest),
        }


def _run_measured_b1(
    b0: InstitutionFollowB0Run,
    *,
    snapshot: Mapping[str, Any],
    nominal_conn=None,
    state_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    state_by_day: Mapping[str, Any] | None = None,
) -> MeasuredB1Result | None:
    if b0.measured_b0 is None:
        return None
    days = list(b0.measured_b0.walk_forward.trading_days)
    owned_nominal = False
    owned_state = False
    n_conn = nominal_conn
    s_conn = state_conn
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

        if state_by_day is None:
            if s_conn is None:
                s_conn = open_stock_state_conn()
                owned_state = True
            state = load_stock_state_by_day(s_conn, days)
        else:
            state = {
                str(k): {
                    str(c): dict(row)
                    for c, row in (v or {}).items()  # type: ignore[union-attr]
                }
                for k, v in state_by_day.items()
            }
        return measure_b1_paper(
            bars,
            b0_measured=b0.measured_b0,
            state_by_day=state,
        )
    finally:
        if owned_nominal and n_conn is not None:
            n_conn.close()
        if owned_state and s_conn is not None:
            s_conn.close()


def build_b1_run(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    measure_b1_paper_flag: bool = True,
    nominal_conn=None,
    state_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    state_by_day: Mapping[str, Any] | None = None,
) -> InstitutionFollowB1Run:
    """Build B1 bound to the same snapshot/B0 context; measure when ready."""

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
    measured: MeasuredB1Result | None = None
    canary = is_canary_scope(payload)
    if (
        measure_b1_paper_flag
        and not canary
        and base.measured_b0 is not None
    ):
        measured = _run_measured_b1(
            base,
            snapshot=payload,
            nominal_conn=nominal_conn,
            state_conn=state_conn,
            bars_by_day=bars_by_day,
            state_by_day=state_by_day,
        )

    run_id = uuid4().hex[:12]
    experiment_id = (
        f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{base.snapshot_id}:{run_id}"
    )
    fb_status = "declared"
    notes = [
        "b1_stock_state_block",
        "one_block_ablation_on_b0",
        "no_optuna_no_accept_without_edge_gates",
    ]
    if is_canary_scope(payload):
        notes.append("canary_scope_blocks_claimable_verdict")
        fb_status = "declared_scaffold"
    if str(payload.get("scope") or "") == BOUNDED_SCOPE:
        notes.append("bounded_scope_inherits_b0_protocol_context")
    if measured is None:
        notes.append(REASON_B1_SCAFFOLD_NO_MEASURED_EDGE)
        fb_status = "declared_scaffold"
    else:
        notes.append("measured_b1_paper_attempted")
        if not measured.coverage.sufficient:
            notes.append(REASON_B1_STATE_COVERAGE_INSUFFICIENT)
            fb_status = "coverage_insufficient"
        else:
            fb_status = "measured_conditioned"
            notes.append(REASON_B1_PAPER_MEASURED)

    feature_block = StockStateFeatureBlock(status=fb_status)
    metrics_label = "unknown"
    paper_label = "not_run"
    if measured is not None:
        if not measured.coverage.sufficient:
            metrics_label = "state_coverage_insufficient"
        elif measured.measured is not None:
            metrics_label = "paper_metrics_measured"
            paper_label = "measured"

    return InstitutionFollowB1Run(
        experiment_id=experiment_id,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        snapshot_id=base.snapshot_id,
        snapshot_scope=str(payload.get("scope") or ""),
        phase_e_ablation=str(payload.get("phase_e_ablation") or ""),
        surface_status=surface_status,
        feature_block=feature_block,
        b0=base,
        measured_b1=measured,
        notes=tuple(notes),
        artifact_manifest={
            "kind": "institution_follow_b1",
            "feature_block": feature_block.as_dict(),
            "metrics": metrics_label,
            "paper_fills": paper_label,
            "measured_b1": measured.as_dict() if measured else None,
            "b0_experiment_id": base.experiment_id,
        },
    )


def finalize_b1_verdict(
    run: InstitutionFollowB1Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    """B1 verdict: coverage → edge gates; never fake improve/accept."""

    wants_accept = requested_verdict == "accept" or force_accept
    if is_canary_scope(
        {
            "scope": run.snapshot_scope,
            "phase_e_ablation": run.phase_e_ablation,
        }
    ) or run.snapshot_scope == CANARY_SCOPE:
        if wants_accept:
            raise CanaryScopeOverclaimError(
                "canary_scope_only blocks B1 accept"
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
                "note": "B1 under canary cannot claim",
            },
        )

    b0_verdict = finalize_b0_verdict(run.b0, requested_verdict=None)
    measured = run.measured_b1

    if measured is None:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_B1_SCAFFOLD_NO_MEASURED_EDGE,
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
                "depends_on": REASON_B1_DEPENDS_ON_B0,
                "note": "B1 paper not run (missing B0 measured context)",
            },
        )

    if not measured.coverage.sufficient:
        return ExperimentVerdict(
            verdict="inconclusive",
            reason=REASON_B1_STATE_COVERAGE_INSUFFICIENT,
            blocked=True,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=False,
            details={
                "requested_verdict": requested_verdict,
                "feature_block": run.feature_block.as_dict(),
                "b0_verdict": b0_verdict.as_dict(),
                "stock_state_coverage": measured.coverage.as_dict(),
                "b0_metrics": (
                    measured.b0_metrics.as_dict()
                    if measured.b0_metrics
                    else None
                ),
                "metrics": "state_coverage_insufficient",
                "paper_fills": "not_run",
                "note": (
                    "Tier1 stock-state coverage insufficient for window; "
                    "not a fake B1 improve"
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
        "delta_b1_minus_b0": (
            measured.delta.as_dict() if measured.delta else None
        ),
        "stock_state_coverage": measured.coverage.as_dict(),
        "walk_forward": measured.measured.walk_forward.as_dict(),
        "accept_edge_gates": edge.as_dict(),
        "paper_fills": "measured",
        "protocol_claimable": measured.claimable,
        "b0_protocol_claimable": bool(
            run.b0.measured_b0.claimable if run.b0.measured_b0 else False
        ),
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
                "depends_on": REASON_B1_DEPENDS_ON_B0,
                "note": "B1 cannot promote diagnostic B0 fixture evidence",
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
                "note": "B1 paper measured but protocol power insufficient",
            },
        )

    if edge.passed:
        return ExperimentVerdict(
            verdict="accept",
            reason=REASON_ACCEPT_EDGE_GATES_PASSED,
            blocked=False,
            experiment_id=run.experiment_id,
            block=run.block,
            claimable=True,
            details={
                **details,
                "note": "B1 protocol + accept edge gates passed",
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
                "B1 measured under identical folds/costs; accept edge "
                "gates unmet — claimable=false"
            ),
            "depends_on": REASON_B1_NO_B0_CONTEXT,
        },
    )


def run_b1_scaffold(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    measure_b1_paper_flag: bool = True,
    nominal_conn=None,
    state_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
    state_by_day: Mapping[str, Any] | None = None,
) -> tuple[InstitutionFollowB1Run, ExperimentVerdict]:
    run = build_b1_run(
        snapshot=snapshot,
        surface_status=surface_status,
        b0_run=b0_run,
        measure_b0_paper=measure_b0_paper,
        measure_b1_paper_flag=measure_b1_paper_flag,
        nominal_conn=nominal_conn,
        state_conn=state_conn,
        bars_by_day=bars_by_day,
        state_by_day=state_by_day,
    )
    return run, finalize_b1_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )


# Alias matching B0 naming.
run_b1_measured = run_b1_scaffold


__all__ = [
    "BLOCK_ID",
    "FEATURE_BLOCK_ID",
    "REASON_B1_DEPENDS_ON_B0",
    "REASON_B1_SCAFFOLD_NO_MEASURED_EDGE",
    "REASON_B1_STATE_COVERAGE_INSUFFICIENT",
    "InstitutionFollowB1Run",
    "StockStateFeatureBlock",
    "build_b1_run",
    "finalize_b1_verdict",
    "run_b1_measured",
    "run_b1_scaffold",
]
