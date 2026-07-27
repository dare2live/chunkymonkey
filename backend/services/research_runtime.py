"""Phase D research runtime: DatasetSnapshot → ExperimentVerdict + PIT gate.

Minimal Tier-3 closed loop. Strategy packages (Phase E) consume this boundary;
this module does not run Optuna, emit StrategyRelease, flip cutover, or loosen
accept gates.

E already partially consumes the boundary via frozen disclosure snapshots and
``ExperimentVerdict``; ``dataset_snapshot_from_disclosure`` + B0 artifact
manifest wiring make that reuse explicit.

Deepened: immutable ``ExperimentPrereg`` + fold/embargo hooks, mid-run snapshot
binding fail-closed, offline measure stub / measured path / B0-bound loop.
Measured offline path is owned here (not a strategy package).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

VerdictKind = Literal["accept", "reject", "inconclusive"]
ProtocolKind = Literal[
    "purged_walk_forward",
    "honest_minimal_short_window",
    "undeclared_stub",
]
_VALID_VERDICTS = frozenset({"accept", "reject", "inconclusive"})

REASON_PHASE_D_SCAFFOLD_SMOKE = "phase_d_scaffold_smoke_inconclusive"
REASON_PHASE_D_OFFLINE_MEASURE_STUB = "phase_d_offline_measure_stub_inconclusive"
REASON_PHASE_D_OFFLINE_MEASURED = "phase_d_offline_measured_inconclusive"
REASON_PIT_LEAK_OR_EMPTY = "phase_d_pit_leak_or_empty_after_truncation"
REASON_PIT_FUTURE_AVAILABLE_AT = "phase_d_future_available_at"
REASON_SNAPSHOT_BINDING_VIOLATED = "phase_d_snapshot_binding_violated"


class ResearchRuntimeError(RuntimeError):
    """Fail-closed research runtime boundary error."""


@dataclass(frozen=True)
class SnapshotInputRef:
    """One immutable input dataset reference inside a DatasetSnapshot."""

    dataset_id: str
    partitions: tuple[str, ...]
    content_hash: str
    config_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "partitions": list(self.partitions),
            "content_hash": self.content_hash,
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True)
class DatasetSnapshot:
    """Immutable research input freeze (MASTER / strategy_validation contract)."""

    snapshot_id: str
    inputs: tuple[SnapshotInputRef, ...]
    universe_id: str
    config_hash: str
    available_at_lower: str
    available_at_upper: str
    content_hash: str
    frozen_at: str
    source_kind: str
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not str(self.snapshot_id or "").strip():
            raise ValueError("snapshot_id is required")
        if not self.inputs:
            raise ValueError("inputs must be non-empty")
        if not str(self.universe_id or "").strip():
            raise ValueError("universe_id is required")
        if not str(self.config_hash or "").strip():
            raise ValueError("config_hash is required")
        if not str(self.content_hash or "").strip():
            raise ValueError("content_hash is required")
        lower = _compact_day(self.available_at_lower)
        upper = _compact_day(self.available_at_upper)
        if len(lower) != 8 or len(upper) != 8:
            raise ValueError(
                f"available_at bounds must be YYYYMMDD; "
                f"got lower={self.available_at_lower!r} upper={self.available_at_upper!r}"
            )
        if lower > upper:
            raise ValueError(
                f"available_at_lower ({lower}) must be <= available_at_upper ({upper})"
            )
        object.__setattr__(self, "available_at_lower", lower)
        object.__setattr__(self, "available_at_upper", upper)

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "inputs": [i.as_dict() for i in self.inputs],
            "universe_id": self.universe_id,
            "config_hash": self.config_hash,
            "available_at_lower": self.available_at_lower,
            "available_at_upper": self.available_at_upper,
            "content_hash": self.content_hash,
            "frozen_at": self.frozen_at,
            "source_kind": self.source_kind,
            "notes": list(self.notes),
        }

    def boundary_dict(self) -> dict[str, Any]:
        """Compact boundary fields for ExperimentRun artifact manifests."""

        return {
            "snapshot_id": self.snapshot_id,
            "universe_id": self.universe_id,
            "config_hash": self.config_hash,
            "available_at_lower": self.available_at_lower,
            "available_at_upper": self.available_at_upper,
            "content_hash": self.content_hash,
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True)
class FoldEmbargoHooks:
    """Typed fold / embargo declaration hooks (stubs ok; not a WF engine)."""

    protocol: ProtocolKind
    n_folds: int
    embargo_days: int
    label_horizon_days: int
    one_touch_holdout: bool
    fold_ids: tuple[str, ...]
    holdout_start: str
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.n_folds < 0:
            raise ValueError("n_folds must be >= 0")
        if self.embargo_days < 0:
            raise ValueError("embargo_days must be >= 0")
        if self.label_horizon_days < 0:
            raise ValueError("label_horizon_days must be >= 0")
        if self.n_folds and len(self.fold_ids) not in (0, self.n_folds):
            raise ValueError(
                f"fold_ids length ({len(self.fold_ids)}) must be 0 or n_folds "
                f"({self.n_folds})"
            )
        holdout = _compact_day(self.holdout_start) if self.holdout_start else ""
        if self.holdout_start and len(holdout) != 8:
            raise ValueError(
                f"holdout_start must be YYYYMMDD or empty; got {self.holdout_start!r}"
            )
        object.__setattr__(self, "holdout_start", holdout)

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "n_folds": self.n_folds,
            "embargo_days": self.embargo_days,
            "label_horizon_days": self.label_horizon_days,
            "one_touch_holdout": self.one_touch_holdout,
            "fold_ids": list(self.fold_ids),
            "holdout_start": self.holdout_start,
            "notes": list(self.notes),
        }


# ExperimentPrereg lives in research_prereg_store (param_hash + single-touch).
from services.research_prereg_store import ExperimentPrereg  # noqa: E402


@dataclass(frozen=True)
class OfflineMeasureResult:
    """Offline measure output.

    Stub status keeps returns unknown. ``measured`` status carries nominal
    T+1/T+2 open-to-open net returns (costs applied); never claimable by itself.
    """

    status: Literal["measured_stub", "measured", "empty_after_pit", "binding_rejected"]
    decision_date: str
    kept_observation_count: int
    input_observation_count: int
    details: dict[str, Any]
    total_return: float | None | Literal["unknown"] = "unknown"
    max_drawdown: float | None | Literal["unknown"] = "unknown"
    paper_fills: Literal["not_run", "measured"] = "not_run"
    n_trades_completed: int = 0
    n_unfilled: int = 0

    def as_dict(self) -> dict[str, Any]:
        if self.status == "measured":
            total: Any = self.total_return
            dd: Any = self.max_drawdown
            fills = self.paper_fills
        else:
            total = "unknown"
            dd = "unknown"
            fills = "not_run"
        return {
            "status": self.status,
            "decision_date": self.decision_date,
            "kept_observation_count": self.kept_observation_count,
            "input_observation_count": self.input_observation_count,
            "details": dict(self.details),
            "total_return": total,
            "max_drawdown": dd,
            "paper_fills": fills,
            "n_trades_completed": self.n_trades_completed,
            "n_unfilled": self.n_unfilled,
        }


@dataclass(frozen=True)
class ResearchObservation:
    """One research feature/event row with explicit availability."""

    entity_id: str
    event_date: str
    available_at: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "event_date": self.event_date,
            "available_at": self.available_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ExperimentRun:
    """Immutable ExperimentRun record (prereg + snapshot binding + artifacts)."""

    experiment_id: str
    strategy_package: str
    block: str
    snapshot_id: str
    snapshot_content_hash: str
    config_hash: str
    universe_id: str
    decision_date: str
    kept_observation_count: int
    pit_ok: bool
    prereg: ExperimentPrereg | None
    artifact_manifest: dict[str, Any]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "strategy_package": self.strategy_package,
            "block": self.block,
            "snapshot_id": self.snapshot_id,
            "snapshot_content_hash": self.snapshot_content_hash,
            "config_hash": self.config_hash,
            "universe_id": self.universe_id,
            "decision_date": self.decision_date,
            "kept_observation_count": self.kept_observation_count,
            "pit_ok": self.pit_ok,
            "prereg": self.prereg.as_dict() if self.prereg is not None else None,
            "artifact_manifest": dict(self.artifact_manifest),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ExperimentVerdict:
    """Shared ExperimentVerdict (Phase D owner; Phase E re-exports)."""

    verdict: VerdictKind
    reason: str
    blocked: bool
    experiment_id: str
    block: str
    claimable: bool
    details: dict[str, Any]

    def __post_init__(self) -> None:
        if self.verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"invalid verdict {self.verdict!r}; "
                f"expected one of {sorted(_VALID_VERDICTS)}"
            )

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


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _available_day(value: Any) -> str:
    return _compact_day(value)


def pit_truncate_observations(
    observations: Sequence[ResearchObservation],
    decision_date: str,
) -> list[ResearchObservation]:
    """Keep observations with available_at calendar day <= decision_date.

    Missing/blank available_at fails closed. Event dates after decision_date
    are also excluded.
    """

    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ValueError(f"invalid decision_date: {decision_date!r}")
    kept: list[ResearchObservation] = []
    for raw in observations:
        avail = _available_day(raw.available_at)
        if not avail:
            raise ValueError(
                "available_at is required for PIT truncation (fail closed)"
            )
        event = _compact_day(raw.event_date)
        if avail > day:
            continue
        if event and event > day:
            continue
        kept.append(
            ResearchObservation(
                entity_id=str(raw.entity_id),
                event_date=event or _compact_day(raw.event_date),
                available_at=str(raw.available_at).strip(),
                payload=dict(raw.payload),
            )
        )
    return kept


def assert_no_future_available_at(
    observations: Sequence[ResearchObservation],
    *,
    decision_date: str,
) -> None:
    """Fail closed if any observation is available after decision_date."""

    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ValueError(f"invalid decision_date: {decision_date!r}")
    for raw in observations:
        avail = _available_day(raw.available_at)
        if not avail:
            raise ValueError(
                "available_at is required for PIT truncation (fail closed)"
            )
        if avail > day:
            raise ValueError(
                f"future available_at {avail!r} after decision_date {day!r} "
                f"(entity={raw.entity_id!r})"
            )


def prove_pit_truncation_invariance(
    base: Sequence[ResearchObservation],
    future: Sequence[ResearchObservation],
    *,
    decision_date: str,
) -> list[ResearchObservation]:
    """Adding future-available rows must 0-diff the truncated pre-cutoff set."""

    before = pit_truncate_observations(base, decision_date)
    after = pit_truncate_observations(tuple(base) + tuple(future), decision_date)
    before_key = [
        (o.entity_id, o.event_date, _available_day(o.available_at), o.payload)
        for o in before
    ]
    after_key = [
        (o.entity_id, o.event_date, _available_day(o.available_at), o.payload)
        for o in after
    ]
    if before_key != after_key:
        raise ResearchRuntimeError(
            "PIT truncation invariance failed: future-available rows changed "
            "pre-cutoff observations"
        )
    return before


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def dataset_snapshot_from_disclosure(
    payload: Mapping[str, Any],
    *,
    universe_id: str = "traded_on_observation_date",
) -> DatasetSnapshot:
    """Adapt Phase E frozen disclosure JSON into a Phase D DatasetSnapshot."""

    snap_id = str(payload.get("snapshot_id") or "").strip()
    if not snap_id:
        raise ResearchRuntimeError("disclosure snapshot missing snapshot_id")

    domains = payload.get("domains") or {}
    if not isinstance(domains, Mapping) or not domains:
        raise ResearchRuntimeError("disclosure snapshot domains must be non-empty")

    inputs: list[SnapshotInputRef] = []
    all_dates: list[str] = []
    config_parts: list[str] = []
    content_parts: list[str] = []

    for domain_name in sorted(domains):
        domain = domains[domain_name]
        if not isinstance(domain, Mapping):
            continue
        accepted = domain.get("accepted") or []
        dataset_id = ""
        partitions: list[str] = []
        content_hashes: list[str] = []
        config_hashes: list[str] = []
        if isinstance(accepted, list) and accepted:
            for row in accepted:
                if not isinstance(row, Mapping):
                    continue
                dataset_id = dataset_id or str(row.get("dataset_id") or "")
                part = _compact_day(row.get("partition"))
                if part:
                    partitions.append(part)
                    all_dates.append(part)
                ch = str(row.get("content_hash") or "")
                if ch:
                    content_hashes.append(ch)
                cfg = str(row.get("config_hash") or "")
                if cfg:
                    config_hashes.append(cfg)
        date_set = domain.get("date_set") or []
        if isinstance(date_set, list):
            for d in date_set:
                part = _compact_day(d)
                if part:
                    partitions.append(part)
                    all_dates.append(part)
        single = _compact_day(domain.get("partition"))
        if single:
            partitions.append(single)
            all_dates.append(single)
        if not dataset_id:
            dataset_id = str(domain.get("dataset_id") or f"disclosure.{domain_name}")
        parts_u = tuple(sorted({p for p in partitions if len(p) == 8}))
        if not parts_u:
            raise ResearchRuntimeError(
                f"disclosure domain {domain_name!r} has no partitions/date_set"
            )
        cfg = config_hashes[0] if config_hashes else _stable_hash(
            {"domain": domain_name, "partitions": parts_u}
        )
        content = (
            content_hashes[0]
            if len(content_hashes) == 1
            else _stable_hash(content_hashes or list(parts_u))
        )
        config_parts.append(cfg)
        content_parts.append(content)
        inputs.append(
            SnapshotInputRef(
                dataset_id=dataset_id,
                partitions=parts_u,
                content_hash=content,
                config_hash=cfg,
            )
        )

    if not all_dates:
        raise ResearchRuntimeError("disclosure snapshot has no available_at dates")
    lower = min(all_dates)
    upper = max(all_dates)
    config_hash = _stable_hash(sorted(config_parts))
    content_hash = _stable_hash(
        {
            "snapshot_id": snap_id,
            "inputs": [i.as_dict() for i in inputs],
            "scope": payload.get("scope"),
            "phase_e_ablation": payload.get("phase_e_ablation"),
        }
    )
    notes = tuple(str(n) for n in (payload.get("notes") or ())) + (
        "adapted_from_disclosure_freeze",
        f"domains={','.join(sorted(domains))}",
    )
    frozen_at = str(payload.get("frozen_at") or datetime.now(timezone.utc).isoformat())
    return DatasetSnapshot(
        snapshot_id=snap_id,
        inputs=tuple(inputs),
        universe_id=universe_id,
        config_hash=config_hash,
        available_at_lower=lower,
        available_at_upper=upper,
        content_hash=content_hash,
        frozen_at=frozen_at,
        source_kind="disclosure_freeze",
        notes=notes,
    )


# Offline helpers live in research_runtime_loop / research_runtime_measure
# (god-file ratchet). Public boundary re-exports via __getattr__.
_LOOP_EXPORTS = frozenset(
    {
        "assert_snapshot_binding",
        "build_experiment_prereg",
        "default_fold_embargo_hooks",
        "fold_embargo_from_walk_forward_plan",
        "measure_observations_stub",
        "run_offline_b0_bound_loop",
        "run_offline_minimal_loop",
    }
)
_MEASURE_EXPORTS = frozenset(
    {
        "measure_observations_offline",
        "run_offline_measured_loop",
    }
)


def __getattr__(name: str) -> Any:
    if name in _LOOP_EXPORTS:
        from services import research_runtime_loop as _loop  # noqa: PLC0415

        return getattr(_loop, name)
    if name in _MEASURE_EXPORTS:
        from services import research_runtime_measure as _measure  # noqa: PLC0415

        return getattr(_measure, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run_smoke_closed_loop(
    snapshot: DatasetSnapshot,
    observations: Sequence[ResearchObservation],
    *,
    decision_date: str,
    block: str = "B0",
    strategy_package: str = "phase_d_smoke",
    require_kept_rows: bool = False,
) -> tuple[ExperimentRun, ExperimentVerdict]:
    """DatasetSnapshot → PIT truncate → ExperimentVerdict (scaffold only)."""

    day = _compact_day(decision_date)
    if len(day) != 8:
        raise ResearchRuntimeError(f"invalid decision_date: {decision_date!r}")
    if day < snapshot.available_at_lower or day > snapshot.available_at_upper:
        # Decision outside declared snapshot bounds is a hard boundary miss.
        experiment_id = (
            f"{strategy_package}:{block}:{snapshot.snapshot_id}:bounds:{uuid4().hex[:8]}"
        )
        run = ExperimentRun(
            experiment_id=experiment_id,
            strategy_package=strategy_package,
            block=block,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            config_hash=snapshot.config_hash,
            universe_id=snapshot.universe_id,
            decision_date=day,
            kept_observation_count=0,
            pit_ok=False,
            prereg=None,
            artifact_manifest={
                "kind": "phase_d_smoke",
                "research_runtime_snapshot": snapshot.boundary_dict(),
                "strategy_release": False,
            },
            notes=("decision_outside_snapshot_available_at_bounds",),
        )
        verdict = ExperimentVerdict(
            verdict="reject",
            reason="phase_d_decision_outside_snapshot_bounds",
            blocked=True,
            experiment_id=experiment_id,
            block=block,
            claimable=False,
            details={
                "strategy_release": False,
                "available_at_lower": snapshot.available_at_lower,
                "available_at_upper": snapshot.available_at_upper,
                "decision_date": day,
            },
        )
        return run, verdict

    try:
        kept = pit_truncate_observations(observations, day)
        # Explicit fail-closed scan of *input* future rows when required empty.
        future_in = [
            o
            for o in observations
            if _available_day(o.available_at) and _available_day(o.available_at) > day
        ]
        pit_ok = True
        if require_kept_rows and not kept:
            pit_ok = False
        if require_kept_rows and future_in and not kept:
            pit_ok = False
    except ValueError as exc:
        experiment_id = (
            f"{strategy_package}:{block}:{snapshot.snapshot_id}:piterr:{uuid4().hex[:8]}"
        )
        run = ExperimentRun(
            experiment_id=experiment_id,
            strategy_package=strategy_package,
            block=block,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            config_hash=snapshot.config_hash,
            universe_id=snapshot.universe_id,
            decision_date=day,
            kept_observation_count=0,
            pit_ok=False,
            prereg=None,
            artifact_manifest={
                "kind": "phase_d_smoke",
                "research_runtime_snapshot": snapshot.boundary_dict(),
                "strategy_release": False,
                "error": str(exc)[:300],
            },
            notes=("pit_fail_closed",),
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

    experiment_id = (
        f"{strategy_package}:{block}:{snapshot.snapshot_id}:{uuid4().hex[:12]}"
    )
    run = ExperimentRun(
        experiment_id=experiment_id,
        strategy_package=strategy_package,
        block=block,
        snapshot_id=snapshot.snapshot_id,
        snapshot_content_hash=snapshot.content_hash,
        config_hash=snapshot.config_hash,
        universe_id=snapshot.universe_id,
        decision_date=day,
        kept_observation_count=len(kept),
        pit_ok=pit_ok,
        prereg=None,
        artifact_manifest={
            "kind": "phase_d_smoke",
            "research_runtime_snapshot": snapshot.boundary_dict(),
            "kept_observation_count": len(kept),
            "input_observation_count": len(tuple(observations)),
            "strategy_release": False,
        },
        notes=("phase_d_scaffold_smoke", "no_strategy_release", "no_optuna"),
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
                "kept_observation_count": len(kept),
                "future_input_count": len(future_in),
            },
        )
        return run, verdict

    verdict = ExperimentVerdict(
        verdict="inconclusive",
        reason=REASON_PHASE_D_SCAFFOLD_SMOKE,
        blocked=False,
        experiment_id=experiment_id,
        block=block,
        claimable=False,
        details={
            "strategy_release": False,
            "kept_observation_count": len(kept),
            "snapshot_id": snapshot.snapshot_id,
            "config_hash": snapshot.config_hash,
            "available_at_lower": snapshot.available_at_lower,
            "available_at_upper": snapshot.available_at_upper,
        },
    )
    return run, verdict
