"""institution_follow B1 stock-state block scaffold (Phase E residual).

Adds one named FeatureBlock on top of B0 bare-K under the same disclosure
``DatasetSnapshot``, folds, costs and execution. Does **not** claim accept:
stock-state lineage/publish + measured conditional edge remain residual.
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
    REASON_PROTOCOL_READY_EDGE_UNMET,
    REQUIRED_SURFACE_STATUS,
    STRATEGY_PACKAGE,
    build_b0_run,
    finalize_b0_verdict,
    is_canary_scope,
    load_frozen_disclosure_snapshot,
)

BLOCK_ID = "B1"
FEATURE_BLOCK_ID = "stock_state_stage_pattern_v0"
REASON_B1_SCAFFOLD_NO_MEASURED_EDGE = "b1_scaffold_stock_state_not_measured"
REASON_B1_DEPENDS_ON_B0 = "b1_requires_b0_protocol_context"

VerdictKind = Literal["accept", "reject", "inconclusive"]


@dataclass(frozen=True)
class StockStateFeatureBlock:
    """Declared B1 feature block — definition only; no measured edge yet."""

    block_id: str = FEATURE_BLOCK_ID
    ablation_parent: str = "B0"
    inputs: tuple[str, ...] = (
        "accepted_nominal_ohlcv_daily",
        "accepted_stock_st_daily",
    )
    outputs: tuple[str, ...] = (
        "stock_state_stage",
        "pattern_event",
    )
    availability: str = "decision_time_visible_only"
    status: str = "declared_scaffold"
    note: str = (
        "Tier1 stock-state publish + PIT zero-diff + conditional paper edge "
        "remain residual; cannot claim accept on scaffold alone"
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "ablation_parent": self.ablation_parent,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "availability": self.availability,
            "status": self.status,
            "note": self.note,
            "config_hash": "undeclared",
            "definition_version": "scaffold_v0",
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
    notes: tuple[str, ...]

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
            "notes": list(self.notes),
            "paper_fills": "not_run",
            "metrics": "unknown",
        }


def build_b1_run(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    b0_run: InstitutionFollowB0Run | None = None,
    measure_b0_paper: bool = True,
    nominal_conn=None,
) -> InstitutionFollowB1Run:
    """Build B1 scaffold bound to the same snapshot/B0 context."""

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
    )
    run_id = uuid4().hex[:12]
    experiment_id = (
        f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{base.snapshot_id}:{run_id}"
    )
    notes = [
        "b1_stock_state_scaffold",
        "one_block_ablation_on_b0",
        "no_optuna_no_accept",
        REASON_B1_SCAFFOLD_NO_MEASURED_EDGE,
    ]
    if is_canary_scope(payload):
        notes.append("canary_scope_blocks_claimable_verdict")
    if str(payload.get("scope") or "") == BOUNDED_SCOPE:
        notes.append("bounded_scope_inherits_b0_protocol_context")
    return InstitutionFollowB1Run(
        experiment_id=experiment_id,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        snapshot_id=base.snapshot_id,
        snapshot_scope=str(payload.get("scope") or ""),
        phase_e_ablation=str(payload.get("phase_e_ablation") or ""),
        surface_status=surface_status,
        feature_block=StockStateFeatureBlock(),
        b0=base,
        notes=tuple(notes),
    )


def finalize_b1_verdict(
    run: InstitutionFollowB1Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    """B1 scaffold never accepts; inherits B0 honesty gates."""

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
                "note": "B1 scaffold under canary cannot claim",
            },
        )

    b0_verdict = finalize_b0_verdict(run.b0, requested_verdict=None)
    reason = REASON_B1_SCAFFOLD_NO_MEASURED_EDGE
    if run.b0.measured_b0 is not None and run.b0.measured_b0.claimable:
        # Protocol power on B0 does not unlock B1 accept.
        reason = REASON_PROTOCOL_READY_EDGE_UNMET
    elif b0_verdict.reason:
        # Prefer scaffold reason; keep B0 reason in details.
        pass

    if wants_accept:
        # Explicit overclaim still refused — never fake accept.
        pass

    return ExperimentVerdict(
        verdict="inconclusive",
        reason=reason,
        blocked=True,
        experiment_id=run.experiment_id,
        block=run.block,
        claimable=False,
        details={
            "requested_verdict": requested_verdict,
            "feature_block": run.feature_block.as_dict(),
            "b0_verdict": b0_verdict.as_dict(),
            "b0_protocol_claimable": bool(
                run.b0.measured_b0.claimable if run.b0.measured_b0 else False
            ),
            "metrics": "unknown",
            "paper_fills": "not_run",
            "note": (
                "B1 stock-state block is declared scaffold only; "
                "measured conditional edge and Tier1 publish residual"
            ),
            "depends_on": REASON_B1_DEPENDS_ON_B0,
        },
    )


def run_b1_scaffold(
    *,
    snapshot: Mapping[str, Any] | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
    measure_b0_paper: bool = True,
    nominal_conn=None,
) -> tuple[InstitutionFollowB1Run, ExperimentVerdict]:
    run = build_b1_run(
        snapshot=snapshot,
        surface_status=surface_status,
        measure_b0_paper=measure_b0_paper,
        nominal_conn=nominal_conn,
    )
    return run, finalize_b1_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )


__all__ = [
    "BLOCK_ID",
    "FEATURE_BLOCK_ID",
    "REASON_B1_DEPENDS_ON_B0",
    "REASON_B1_SCAFFOLD_NO_MEASURED_EDGE",
    "InstitutionFollowB1Run",
    "StockStateFeatureBlock",
    "build_b1_run",
    "finalize_b1_verdict",
    "run_b1_scaffold",
]
