"""institution_follow B0 bare-K research (Phase E).

Frozen disclosure DatasetSnapshot + surface_status → coverage → paper WF.
Canary never accepts; edge gates reject when protocol-ready but unmet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from services.data_sources.disclosure_dataset_snapshot import (
    ABLATION_CANARY,
    DISCLOSURE_SNAPSHOT_RELPATH,
    SCOPE_BOUNDED,
    SCOPE_CANARY,
    default_snapshot_path,
)
from services.data_sources.nominal_ohlcv_schema import DATASET_ID as NOMINAL_OHLCV_DATASET
from services.holdout_guard import (
    HoldoutBoundaryViolation,
    assert_holdout_untouched,
    load_policy,
    training_cutoff_before_holdout,
)
from services.institution_follow_b0_measure import (
    MeasuredB0Result,
    REASON_SHORT_WINDOW,
    load_nominal_bars_by_day,
    measure_b0_paper,
)
from services.institution_follow_edge_gates import (
    REASON_EDGE_GATES_PASSED,
    REASON_EDGE_GATES_UNMET,
    evaluate_accept_edge_gates,
)
from services.research_runtime import (
    ExperimentVerdict,
    VerdictKind,
    dataset_snapshot_from_disclosure,
)

STRATEGY_PACKAGE = "institution_follow_v1"
BLOCK_ID = "B0"
# Must match routers.institution_profile.SURFACE_STATUS (research evidence only).
REQUIRED_SURFACE_STATUS = "tier3_research_evidence_only"
CANARY_SCOPE = SCOPE_CANARY
CANARY_ABLATION = ABLATION_CANARY
BOUNDED_SCOPE = SCOPE_BOUNDED
REASON_CANARY_SCOPE_ONLY = "canary_scope_only"
REASON_MEASURED_COVERAGE_INSUFFICIENT = "measured_coverage_insufficient"
REASON_MEASURED_SHORT_WINDOW = REASON_SHORT_WINDOW
REASON_PROTOCOL_READY_EDGE_UNMET = "measured_protocol_ready_edge_gates_unmet"
REASON_ACCEPT_EDGE_GATES_UNMET = REASON_EDGE_GATES_UNMET
REASON_ACCEPT_EDGE_GATES_PASSED = REASON_EDGE_GATES_PASSED
REASON_SCAFFOLD_NO_MEASURED_EDGE = "scaffold_no_measured_edge"
# Bare-K needs a multi-day nominal window for any forward-return measurement.
MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0 = 5

_VALID_VERDICTS = frozenset({"accept", "reject", "inconclusive"})

# Re-export for Phase E callers; owner is research_runtime (Phase D).
__all__ = (
    "ExperimentVerdict",
    "VerdictKind",
    "InstitutionFollowB0Run",
    "InstitutionFollowB0Error",
    "CanaryScopeOverclaimError",
)


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
class BareKCoverageMeasurement:
    """Measured coverage for B0 bare-K on accepted nominal OHLCV."""

    status: str
    accepted_nominal_partitions: tuple[str, ...]
    accepted_nominal_day_count: int
    disclosure_date_sets: dict[str, list[str]]
    overlapping_eligible_window: tuple[str, str] | None
    sufficient_for_measured_b0: bool
    reason: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted_nominal_partitions": list(self.accepted_nominal_partitions),
            "accepted_nominal_day_count": self.accepted_nominal_day_count,
            "disclosure_date_sets": {
                k: list(v) for k, v in self.disclosure_date_sets.items()
            },
            "overlapping_eligible_window": (
                list(self.overlapping_eligible_window)
                if self.overlapping_eligible_window
                else None
            ),
            "sufficient_for_measured_b0": self.sufficient_for_measured_b0,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class InstitutionFollowB0Run:
    """ExperimentRun skeleton for B0 bare-K under Phase E."""

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
    bare_k_coverage: BareKCoverageMeasurement | None = None
    measured_b0: MeasuredB0Result | None = None

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
            "bare_k_coverage": (
                self.bare_k_coverage.as_dict() if self.bare_k_coverage else None
            ),
            "measured_b0": (
                self.measured_b0.as_dict() if self.measured_b0 else None
            ),
        }


def load_frozen_disclosure_snapshot(
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the frozen disclosure DatasetSnapshot JSON (canary/bounded gate)."""

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


def is_bounded_scope(snapshot: Mapping[str, Any] | InstitutionFollowB0Run) -> bool:
    if isinstance(snapshot, InstitutionFollowB0Run):
        return snapshot.snapshot_scope == BOUNDED_SCOPE
    return str(snapshot.get("scope") or "") == BOUNDED_SCOPE


def disclosure_date_sets_from_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Extract explicit per-domain date sets from a frozen snapshot."""

    out: dict[str, list[str]] = {}
    domains = snapshot.get("domains") or {}
    if not isinstance(domains, Mapping):
        return out
    for name, payload in domains.items():
        if not isinstance(payload, Mapping):
            continue
        dates = payload.get("date_set")
        if isinstance(dates, list) and dates:
            out[str(name)] = sorted(
                {
                    "".join(ch for ch in str(d) if ch.isdigit())[:8]
                    for d in dates
                    if "".join(ch for ch in str(d) if ch.isdigit())[:8]
                }
            )
            continue
        part = payload.get("partition")
        if part:
            compact = "".join(ch for ch in str(part) if ch.isdigit())[:8]
            if compact:
                out[str(name)] = [compact]
    return out


def _nominal_partitions_from_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    """Return frozen nominal date_set from DatasetSnapshot — never live accepted."""
    domains = snapshot.get("domains") or {}
    nominal = domains.get("nominal_ohlcv") or {}
    dates = nominal.get("date_set") or []
    return sorted(
        {
            "".join(ch for ch in str(d) if ch.isdigit())[:8]
            for d in dates
            if len("".join(ch for ch in str(d) if ch.isdigit())[:8]) == 8
        }
    )


def measure_bare_k_coverage(
    snapshot: Mapping[str, Any],
    *,
    nominal_conn=None,
) -> BareKCoverageMeasurement:
    """Measure snapshot-frozen nominal-K coverage for the disclosure date sets.

    Partition membership comes only from ``domains.nominal_ohlcv.date_set``.
    Live ``accepted_partition`` calendars are not consulted (fail-closed if the
    freeze omitted nominal). ``nominal_conn`` is retained for API compatibility
    with paper-bar loading callers but is unused for coverage membership.
    """
    del nominal_conn  # Coverage is snapshot-bound; bars still use conn elsewhere.

    date_sets = disclosure_date_sets_from_snapshot(snapshot)
    disclosure_dates = sorted({d for parts in date_sets.values() for d in parts})
    nominal_parts = _nominal_partitions_from_snapshot(snapshot)

    n_days = len(nominal_parts)
    overlap_window: tuple[str, str] | None = None
    if nominal_parts:
        overlap_window = (nominal_parts[0], nominal_parts[-1])

    # Overlap of disclosure event dates with accepted K days (thin by design).
    disclosure_on_k = sorted(set(disclosure_dates) & set(nominal_parts))
    sufficient = n_days >= MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0
    reason = (
        "measured_nominal_window_ready"
        if sufficient
        else REASON_MEASURED_COVERAGE_INSUFFICIENT
    )
    return BareKCoverageMeasurement(
        status="MEASURED" if n_days else "EMPTY",
        accepted_nominal_partitions=tuple(nominal_parts),
        accepted_nominal_day_count=n_days,
        disclosure_date_sets=date_sets,
        overlapping_eligible_window=overlap_window,
        sufficient_for_measured_b0=sufficient,
        reason=reason,
        details={
            "min_required_nominal_days": MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0,
            "disclosure_dates_on_accepted_k": disclosure_on_k,
            "disclosure_date_count": len(disclosure_dates),
            "a3_daily_accepted_thin": n_days < MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0,
            "nominal_dataset_id": NOMINAL_OHLCV_DATASET,
            "nominal_source": "snapshot_domains.nominal_ohlcv.date_set",
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


def _run_measured_paper(
    coverage: BareKCoverageMeasurement,
    *,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
) -> MeasuredB0Result | None:
    """Run short-window WF + paper fills when coverage is ready.

    Returns ``None`` only when coverage is insufficient. Load/measure failures
    raise so the caller does not silently fall back to a scaffold verdict.
    """

    if not coverage.sufficient_for_measured_b0:
        return None
    days = list(coverage.accepted_nominal_partitions)
    if not days:
        return None

    owned_conn = False
    conn = nominal_conn
    try:
        if bars_by_day is None:
            if conn is None:
                from services.data_access.resolver import connect_ro

                conn = connect_ro("tushare_raw")
                owned_conn = True
            bars = load_nominal_bars_by_day(conn, days)
        else:
            bars = {
                str(k): list(v) for k, v in bars_by_day.items()  # type: ignore[arg-type]
            }
        return measure_b0_paper(bars, days)
    finally:
        if owned_conn and conn is not None:
            conn.close()


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
) -> InstitutionFollowB0Run:
    """Build a B0 ExperimentRun bound to the disclosure snapshot.

    Under bounded scope, measures accepted nominal-K coverage and, when the
    window is ready, runs honest minimal WF + paper fills. Does not run
    Optuna or full-history search.
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
    snap_nominal = _nominal_partitions_from_snapshot(payload)
    actual_end = snap_nominal[-1] if snap_nominal else None
    assert_holdout_untouched(end, actual_data_end=actual_end)

    canary = is_canary_scope(payload)
    bounded = is_bounded_scope(payload)
    holdout_start = str(load_policy()["holdout_start"])
    training_cutoff = training_cutoff_before_holdout()
    snap_id = str(payload.get("snapshot_id") or "")
    if not snap_id:
        raise InstitutionFollowB0Error("snapshot_id required on DatasetSnapshot")

    # Phase D boundary: adapt disclosure freeze into DatasetSnapshot (E consumes D).
    runtime_snap = dataset_snapshot_from_disclosure(payload)

    allowed = (
        bool(cutover_allowed)
        if cutover_allowed is not None
        else bool(payload.get("cutover_allowed"))
    )
    run_id = uuid4().hex[:12]
    experiment_id = f"{STRATEGY_PACKAGE}:{BLOCK_ID}:{snap_id}:{run_id}"

    coverage: BareKCoverageMeasurement | None = None
    if measure_coverage and (bounded or not canary):
        coverage = measure_bare_k_coverage(payload, nominal_conn=nominal_conn)
        if coverage is not None and coverage.accepted_nominal_partitions:
            cov_end = coverage.accepted_nominal_partitions[-1]
            assert_holdout_untouched(end, actual_data_end=cov_end)

    measured: MeasuredB0Result | None = None
    if (
        measure_paper
        and coverage is not None
        and coverage.sufficient_for_measured_b0
        and not canary
    ):
        measured = _run_measured_paper(
            coverage, nominal_conn=nominal_conn, bars_by_day=bars_by_day
        )

    notes = [
        "b0_bare_k",
        "no_optuna_no_full_history_search",
        "pit_hooks_declared_not_full_ablation",
        f"disclosure_snapshot_relpath={DISCLOSURE_SNAPSHOT_RELPATH}",
        "research_runtime_dataset_snapshot_bound",
    ]
    if canary:
        notes.append("canary_scope_blocks_claimable_verdict")
    if bounded:
        notes.append("bounded_scope_measured_coverage_attempted")
    if coverage is not None and not coverage.sufficient_for_measured_b0:
        notes.append("measured_coverage_insufficient_for_bare_k_edge")
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
        pit_hooks=_default_pit_hooks(
            canary=canary, measured=coverage is not None
        ),
        holdout=HoldoutHookSpec(
            holdout_start=holdout_start.replace("-", "")[:8],
            training_cutoff=training_cutoff,
            status="exercised",
        ),
        artifact_manifest={
            "kind": "institution_follow_b0",
            "disclosure_snapshot": DISCLOSURE_SNAPSHOT_RELPATH,
            "research_runtime_snapshot": runtime_snap.boundary_dict(),
            "domains": sorted((payload.get("domains") or {}).keys()),
            "metrics": metrics_label,
            "paper_fills": paper_label,
            "bare_k_coverage": coverage.as_dict() if coverage else None,
            "measured_b0": measured.as_dict() if measured else None,
        },
        notes=tuple(notes),
        bare_k_coverage=coverage,
        measured_b0=measured,
    )


def finalize_b0_verdict(
    run: InstitutionFollowB0Run,
    *,
    requested_verdict: VerdictKind | None = None,
    force_accept: bool = False,
) -> ExperimentVerdict:
    """Emit an ExperimentVerdict. Never fake-accept on thin coverage."""

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

    coverage = run.bare_k_coverage
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
                "phase_e_ablation": run.phase_e_ablation,
                "requested_verdict": requested_verdict,
                "surface_status": run.surface_status,
                "cutover_allowed": run.cutover_allowed,
                "bare_k_coverage": coverage.as_dict(),
                "metrics": "coverage_measured_insufficient",
                "note": "accepted nominal OHLCV window too thin for bare-K edge",
            },
        )

    measured = run.measured_b0
    if measured is not None:
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
            "bare_k_coverage": coverage.as_dict() if coverage else None,
            "paper_fills": "measured",
            "surface_status": run.surface_status,
            "prereg": measured.prereg.as_dict(),
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
                "bare_k_coverage": coverage.as_dict() if coverage else None,
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
        claimable=verdict != "inconclusive",
        details={
            "requested_verdict": requested_verdict,
            "metrics": "unknown",
            "surface_status": run.surface_status,
            "bare_k_coverage": coverage.as_dict() if coverage else None,
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
    measure_coverage: bool = True,
    measure_paper: bool = True,
    nominal_conn=None,
    bars_by_day: Mapping[str, Any] | None = None,
) -> tuple[InstitutionFollowB0Run, ExperimentVerdict]:
    """Convenience: build run + finalize verdict."""

    run = build_b0_run(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        surface_status=surface_status,
        data_end_date=data_end_date,
        measure_coverage=measure_coverage,
        measure_paper=measure_paper,
        nominal_conn=nominal_conn,
        bars_by_day=bars_by_day,
    )
    verdict = finalize_b0_verdict(
        run,
        requested_verdict=requested_verdict,
        force_accept=force_accept,
    )
    return run, verdict


# Back-compat alias used by tests / docs.
run_b0_measured = run_b0_scaffold


__all__ = [
    "BLOCK_ID",
    "BOUNDED_SCOPE",
    "CANARY_ABLATION",
    "CANARY_SCOPE",
    "CanaryScopeOverclaimError",
    "BareKCoverageMeasurement",
    "ExperimentVerdict",
    "HoldoutHookSpec",
    "InstitutionFollowB0Error",
    "InstitutionFollowB0Run",
    "MIN_ACCEPTED_NOMINAL_DAYS_FOR_MEASURED_B0",
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
    "build_b0_run",
    "disclosure_date_sets_from_snapshot",
    "finalize_b0_verdict",
    "is_bounded_scope",
    "is_canary_scope",
    "load_frozen_disclosure_snapshot",
    "measure_bare_k_coverage",
    "run_b0_measured",
    "run_b0_scaffold",
    "HoldoutBoundaryViolation",
]
