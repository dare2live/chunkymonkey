"""institution_follow B0 bare-K research scaffold (Phase E start).

Consumes the frozen disclosure ``DatasetSnapshot`` and research
``surface_status``. Builds an ``ExperimentRun`` skeleton with declared
PIT / holdout hooks. Under canary snapshot scope the only honest verdicts
are ``inconclusive`` or blocked with ``reason=canary_scope_only`` —
never ``accept``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from services.data_sources.disclosure_dataset_snapshot import (
    DISCLOSURE_SNAPSHOT_RELPATH,
    default_snapshot_path,
)
from services.holdout_guard import (
    HoldoutBoundaryViolation,
    assert_holdout_untouched,
    load_policy,
    training_cutoff_before_holdout,
)

STRATEGY_PACKAGE = "institution_follow_v1"
BLOCK_ID = "B0"
# Must match routers.institution_profile.SURFACE_STATUS (research evidence only).
REQUIRED_SURFACE_STATUS = "tier3_research_evidence_only"
CANARY_SCOPE = "canary_accepted_partitions"
CANARY_ABLATION = "blocked_canary_scope_only"
REASON_CANARY_SCOPE_ONLY = "canary_scope_only"

VerdictKind = Literal["accept", "reject", "inconclusive"]
_VALID_VERDICTS = frozenset({"accept", "reject", "inconclusive"})


class InstitutionFollowB0Error(RuntimeError):
    """Scaffold / gate failure for institution_follow B0."""


class CanaryScopeOverclaimError(InstitutionFollowB0Error):
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
class InstitutionFollowB0Run:
    """Minimal ExperimentRun skeleton for B0 bare-K under Phase E."""

    experiment_id: str
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_scope: str
    phase_e_ablation: str
    surface_status: str
    cutover_allowed: bool
    data_end_date: str
    pit_hooks: tuple[PitHookSpec, ...]
    holdout: HoldoutHookSpec
    artifact_manifest: dict[str, Any]
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
            "cutover_allowed": self.cutover_allowed,
            "data_end_date": self.data_end_date,
            "pit_hooks": [h.as_dict() for h in self.pit_hooks],
            "holdout": self.holdout.as_dict(),
            "artifact_manifest": dict(self.artifact_manifest),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ExperimentVerdict:
    verdict: VerdictKind
    reason: str
    blocked: bool
    experiment_id: str
    block: str
    claimable: bool
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "blocked": self.blocked,
            "experiment_id": self.experiment_id,
            "block": self.block,
            "claimable": self.claimable,
            "details": dict(self.details),
        }


def load_frozen_disclosure_snapshot(
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the frozen disclosure DatasetSnapshot JSON (canary gate input)."""

    target = Path(path) if path is not None else default_snapshot_path()
    if not target.is_file():
        raise InstitutionFollowB0Error(
            f"disclosure DatasetSnapshot missing at {target}"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InstitutionFollowB0Error("disclosure DatasetSnapshot must be an object")
    return payload


def is_canary_scope(snapshot: Mapping[str, Any] | InstitutionFollowB0Run) -> bool:
    """True when Phase E may only smoke — not claim a full B0→B4 ablation."""

    if isinstance(snapshot, InstitutionFollowB0Run):
        return (
            snapshot.snapshot_scope == CANARY_SCOPE
            or snapshot.phase_e_ablation == CANARY_ABLATION
        )
    return (
        str(snapshot.get("scope") or "") == CANARY_SCOPE
        or str(snapshot.get("phase_e_ablation") or "") == CANARY_ABLATION
    )


def _default_pit_hooks(*, canary: bool) -> tuple[PitHookSpec, ...]:
    status = "declared_canary_only" if canary else "declared"
    return (
        PitHookSpec(
            name="decision_time_truncation",
            rule="features_and_candidates_zero_diff_before_cutoff",
            status=status,
        ),
        PitHookSpec(
            name="availability_cutoff",
            rule="notice_date_or_available_at_required_null_excluded",
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
) -> InstitutionFollowB0Run:
    """Build a B0 ExperimentRun skeleton bound to the disclosure snapshot.

    Does not run Optuna, full-history search, or paper fills. Holdout is
    exercised via ``assert_holdout_untouched``; PIT hooks are declared only.
    """

    payload = dict(snapshot) if snapshot is not None else load_frozen_disclosure_snapshot(
        snapshot_path
    )
    if surface_status != REQUIRED_SURFACE_STATUS:
        raise InstitutionFollowB0Error(
            f"surface_status must be {REQUIRED_SURFACE_STATUS!r}, "
            f"got {surface_status!r}"
        )

    end = data_end_date or training_cutoff_before_holdout()
    assert_holdout_untouched(end)

    canary = is_canary_scope(payload)
    holdout_start = str(load_policy()["holdout_start"])
    training_cutoff = training_cutoff_before_holdout()
    snap_id = str(payload.get("snapshot_id") or "")
    if not snap_id:
        raise InstitutionFollowB0Error("snapshot_id required on DatasetSnapshot")

    allowed = (
        bool(cutover_allowed)
        if cutover_allowed is not None
        else bool(payload.get("cutover_allowed"))
    )
    # run_id is a scaffold identity token (not a trade_date / end_date).
    run_id = uuid4().hex[:12]
    experiment_id = f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{snap_id}:{run_id}"

    notes = [
        "b0_bare_k_scaffold_only",
        "no_optuna_no_full_history_search",
        "pit_hooks_declared_not_full_ablation",
        f"disclosure_snapshot_relpath={DISCLOSURE_SNAPSHOT_RELPATH}",
    ]
    if canary:
        notes.append("canary_scope_blocks_claimable_verdict")

    return InstitutionFollowB0Run(
        experiment_id=experiment_id,
        strategy_package=STRATEGY_PACKAGE,
        block=BLOCK_ID,
        snapshot_id=snap_id,
        snapshot_scope=str(payload.get("scope") or ""),
        phase_e_ablation=str(payload.get("phase_e_ablation") or ""),
        surface_status=surface_status,
        cutover_allowed=allowed,
        data_end_date=str(end).replace("-", "")[:8],
        pit_hooks=_default_pit_hooks(canary=canary),
        holdout=HoldoutHookSpec(
            holdout_start=holdout_start.replace("-", "")[:8],
            training_cutoff=training_cutoff,
            status="exercised",
        ),
        artifact_manifest={
            "kind": "institution_follow_b0_scaffold",
            "disclosure_snapshot": DISCLOSURE_SNAPSHOT_RELPATH,
            "domains": sorted((payload.get("domains") or {}).keys()),
            "metrics": "unknown",
            "paper_fills": "not_run",
        },
        notes=tuple(notes),
    )


def finalize_b0_verdict(
    run: InstitutionFollowB0Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    """Emit an ExperimentVerdict. Canary scope never yields accept.

    Default canary outcome: ``inconclusive`` + ``blocked`` with
    ``reason=canary_scope_only``. Overclaim (``force_accept`` or
    ``requested_verdict='accept'``) raises ``CanaryScopeOverclaimError``.
    """

    if requested_verdict is not None and requested_verdict not in _VALID_VERDICTS:
        raise InstitutionFollowB0Error(
            f"invalid verdict {requested_verdict!r}; "
            f"expected one of {sorted(_VALID_VERDICTS)}"
        )

    canary = is_canary_scope(run)
    wants_accept = force_accept or requested_verdict == "accept"

    if canary and wants_accept:
        raise CanaryScopeOverclaimError(
            "institution_follow B0 cannot accept under canary DatasetSnapshot "
            f"scope={run.snapshot_scope!r} phase_e_ablation={run.phase_e_ablation!r}; "
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
                "phase_e_ablation": run.phase_e_ablation,
                "requested_verdict": requested_verdict,
                "surface_status": run.surface_status,
                "cutover_allowed": run.cutover_allowed,
                "metrics": "unknown",
                "note": (
                    "canary disclosure partitions + scaffold only; "
                    "not a claimable B0 bare-K baseline"
                ),
            },
        )

    # Broader snapshot path still has no metrics in this scaffold.
    verdict: VerdictKind = requested_verdict or "inconclusive"
    if verdict == "accept":
        # Scaffold has no measured edge — refuse accept even off canary.
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
                "note": "B0 scaffold cannot accept without measured paper results",
            },
        )

    return ExperimentVerdict(
        verdict=verdict,
        reason="scaffold_no_measured_edge" if verdict == "inconclusive" else "explicit",
        blocked=False,
        experiment_id=run.experiment_id,
        block=run.block,
        claimable=verdict != "inconclusive",
        details={
            "requested_verdict": requested_verdict,
            "metrics": "unknown",
            "surface_status": run.surface_status,
        },
    )


def run_b0_scaffold(
    *,
    snapshot: Mapping[str, Any] | None = None,
    snapshot_path: Path | str | None = None,
    surface_status: str = REQUIRED_SURFACE_STATUS,
    data_end_date: str | None = None,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> tuple[InstitutionFollowB0Run, ExperimentVerdict]:
    """Convenience: build run + finalize verdict (fixture / canary-day only)."""

    run = build_b0_run(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        surface_status=surface_status,
        data_end_date=data_end_date,
    )
    verdict = finalize_b0_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )
    return run, verdict


__all__ = [
    "BLOCK_ID",
    "CANARY_ABLATION",
    "CANARY_SCOPE",
    "CanaryScopeOverclaimError",
    "ExperimentVerdict",
    "HoldoutHookSpec",
    "InstitutionFollowB0Error",
    "InstitutionFollowB0Run",
    "PitHookSpec",
    "REASON_CANARY_SCOPE_ONLY",
    "REQUIRED_SURFACE_STATUS",
    "STRATEGY_PACKAGE",
    "build_b0_run",
    "finalize_b0_verdict",
    "is_canary_scope",
    "load_frozen_disclosure_snapshot",
    "run_b0_scaffold",
    "HoldoutBoundaryViolation",
]
