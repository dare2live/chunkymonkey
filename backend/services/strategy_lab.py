"""Fail-closed strategy-lab ingress and compute admission.

This is a thin boundary around the existing research runtime. It does not
implement a second experiment store, scheduler, Optuna study, Modal app, or
StrategyRelease path.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from services.formal_rx_evidence import validate_formal_rx_evidence
from services.research_runtime import DatasetSnapshot, SnapshotInputRef
from services.strategy_spec import StrategySpecError, load_strategy_spec


REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO / "backend" / "config" / "strategy_lab.yaml"
DEFAULT_GOAL_PATH = REPO / "goal.md"

Stage = Literal["local_smoke", "formal_rx", "optuna"]
Executor = Literal["local", "modal"]


class StrategyLabError(RuntimeError):
    """The lab boundary is missing or violates a fail-closed invariant."""


def _compact_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _is_day(value: str) -> bool:
    return len(value) == 8 and value.isdigit()


@dataclass(frozen=True)
class StrategyLabPolicy:
    status: str
    execution_mode: str
    formal_rx_authorization: str
    phase_n_authorization: str
    remote_compute_authorization: str
    validation_calendar_days: int
    require_read_only_bundle: bool
    forbid_live_database: bool


def load_policy(
    path: Path | str = DEFAULT_POLICY_PATH,
    *,
    goal_path: Path | str = DEFAULT_GOAL_PATH,
) -> StrategyLabPolicy:
    policy_path = Path(path)
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StrategyLabError(f"strategy lab policy unreadable: {exc}") from exc
    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        raise StrategyLabError("strategy lab policy must be a version=1 mapping")
    authorizations = raw.get("authorizations")
    development = raw.get("development_split")
    remote = raw.get("remote_compute")
    if (
        not isinstance(authorizations, Mapping)
        or not isinstance(development, Mapping)
        or not isinstance(remote, Mapping)
    ):
        raise StrategyLabError(
            "strategy lab policy requires authorizations, development_split, "
            "and remote_compute mappings"
        )
    execution_mode = str(raw.get("execution_mode") or "")
    if execution_mode != "manual_only":
        raise StrategyLabError("strategy lab execution_mode must remain manual_only")
    for key in ("require_read_only_bundle", "forbid_live_database"):
        if not isinstance(remote.get(key), bool):
            raise StrategyLabError(f"remote_compute.{key} must be boolean")
        if remote.get(key) is not True:
            raise StrategyLabError(f"remote_compute.{key} must remain true")
    validation_days = development.get("validation_calendar_days")
    if type(validation_days) is not int or validation_days <= 0:
        raise StrategyLabError(
            "development_split.validation_calendar_days must be a positive integer"
        )
    policy = StrategyLabPolicy(
        status=str(raw.get("status") or ""),
        execution_mode=execution_mode,
        formal_rx_authorization=str(authorizations.get("formal_rx") or "").strip(),
        phase_n_authorization=str(
            authorizations.get("phase_n_optuna") or ""
        ).strip(),
        remote_compute_authorization=str(
            authorizations.get("remote_compute") or ""
        ).strip(),
        validation_calendar_days=validation_days,
        require_read_only_bundle=bool(remote.get("require_read_only_bundle")),
        forbid_live_database=bool(remote.get("forbid_live_database")),
    )
    try:
        goal_text = Path(goal_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise StrategyLabError(f"goal authorization owner unreadable: {exc}") from exc
    for token, authorization in (
        ("RX_AUTH", policy.formal_rx_authorization),
        ("PHASE_N_AUTH", policy.phase_n_authorization),
        ("REMOTE_COMPUTE_AUTH", policy.remote_compute_authorization),
    ):
        if authorization and f"{token}={authorization}" not in goal_text:
            raise StrategyLabError(
                f"{token} authorization is not bound in goal.md"
            )
    return policy


@dataclass(frozen=True)
class InputSlice:
    dataset_id: str
    partitions: tuple[str, ...]
    content_hash: str
    config_hash: str

    def __post_init__(self) -> None:
        if not self.dataset_id or not self.partitions:
            raise StrategyLabError("input slice requires dataset_id and partitions")
        if not self.content_hash or not self.config_hash:
            raise StrategyLabError("input slice requires content_hash and config_hash")

    @classmethod
    def from_ref(
        cls, ref: SnapshotInputRef, partitions: tuple[str, ...]
    ) -> "InputSlice":
        return cls(
            dataset_id=ref.dataset_id,
            partitions=partitions,
            content_hash=ref.content_hash,
            config_hash=ref.config_hash,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "partitions": list(self.partitions),
            "content_hash": self.content_hash,
            "config_hash": self.config_hash,
        }


@dataclass(frozen=True)
class ResearchInputBundle:
    """Development-only manifest presented to a future pure trial evaluator."""

    snapshot_id: str
    snapshot_content_hash: str
    snapshot_config_hash: str
    universe_id: str
    available_at_lower: str
    available_at_upper: str
    train_end: str
    holdout_start: str
    training_inputs: tuple[InputSlice, ...]
    validation_inputs: tuple[InputSlice, ...]
    metadata_inputs: tuple[InputSlice, ...]
    read_only: bool = True

    def __post_init__(self) -> None:
        lower = _compact_day(self.available_at_lower)
        upper = _compact_day(self.available_at_upper)
        train_end = _compact_day(self.train_end)
        holdout_start = _compact_day(self.holdout_start)
        if not all(_is_day(day) for day in (lower, upper, train_end, holdout_start)):
            raise StrategyLabError("bundle date boundaries must be YYYYMMDD")
        if lower > upper or train_end >= holdout_start or upper >= holdout_start:
            raise StrategyLabError(
                "development bundle date bounds cross the sealed holdout"
            )
        if self.read_only is not True:
            raise StrategyLabError("development bundle must remain read_only")
        if not self.training_inputs or not self.validation_inputs:
            raise StrategyLabError(
                "development bundle requires training and validation inputs"
            )
        all_inputs = (
            *self.training_inputs,
            *self.validation_inputs,
            *self.metadata_inputs,
        )
        if any(item.dataset_id.startswith("tier3.") for item in all_inputs):
            raise StrategyLabError("development bundle must not contain Tier3 inputs")
        if not any(
            item.dataset_id == "tier0.market_data.nominal_ohlcv_daily"
            for item in self.training_inputs
        ):
            raise StrategyLabError(
                "development bundle requires accepted nominal OHLCV training input"
            )
        if not any(
            item.dataset_id == "tier0.market_data.nominal_ohlcv_daily"
            for item in self.validation_inputs
        ):
            raise StrategyLabError(
                "development bundle requires accepted nominal OHLCV validation input"
            )
        for item in self.training_inputs:
            if any(not _is_day(part) or part > train_end for part in item.partitions):
                raise StrategyLabError("training input partition exceeds train_end")
        for item in self.validation_inputs:
            if any(
                not _is_day(part) or not (train_end < part < holdout_start)
                for part in item.partitions
            ):
                raise StrategyLabError(
                    "validation input partition is outside development bounds"
                )

    def worker_payload(self) -> dict[str, Any]:
        """The complete object is safe for a development worker."""

        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_content_hash": self.snapshot_content_hash,
            "snapshot_config_hash": self.snapshot_config_hash,
            "universe_id": self.universe_id,
            "available_at_lower": self.available_at_lower,
            "available_at_upper": self.available_at_upper,
            "train_end": self.train_end,
            "holdout_start": self.holdout_start,
            "read_only": True,
            "training_inputs": [item.as_dict() for item in self.training_inputs],
            "validation_inputs": [item.as_dict() for item in self.validation_inputs],
            "metadata_inputs": [item.as_dict() for item in self.metadata_inputs],
        }

    def as_dict(self) -> dict[str, Any]:
        return self.worker_payload()


def build_ingress_plan(
    snapshot: DatasetSnapshot,
    *,
    train_end: str,
    holdout_start: str,
) -> ResearchInputBundle:
    """Build a development-only bundle; sealed/label data fail closed."""

    train_day = _compact_day(train_end)
    holdout_day = _compact_day(holdout_start)
    if not _is_day(train_day) or not _is_day(holdout_day):
        raise StrategyLabError("train_end and holdout_start must be YYYYMMDD")
    if train_day >= holdout_day:
        raise StrategyLabError("train_end must be strictly earlier than holdout_start")

    training: list[InputSlice] = []
    validation: list[InputSlice] = []
    metadata: list[InputSlice] = []
    for ref in snapshot.inputs:
        if ref.dataset_id.startswith("tier3."):
            raise StrategyLabError(
                "development snapshot must not contain Tier3 label inputs"
            )
        dated = tuple(sorted(part for part in ref.partitions if _is_day(part)))
        opaque = tuple(part for part in ref.partitions if not _is_day(part))
        train_parts = tuple(part for part in dated if part <= train_day)
        validation_parts = tuple(
            part for part in dated if train_day < part < holdout_day
        )
        holdout_parts = tuple(part for part in dated if part >= holdout_day)
        if holdout_parts:
            raise StrategyLabError(
                "development snapshot contains sealed holdout partitions"
            )
        if train_parts:
            training.append(InputSlice.from_ref(ref, train_parts))
        if validation_parts:
            validation.append(InputSlice.from_ref(ref, validation_parts))
        if opaque:
            metadata.append(InputSlice.from_ref(ref, opaque))
    if not training:
        raise StrategyLabError(
            "snapshot has no dated training partitions before the declared cutoff"
        )
    if not any(
        item.dataset_id == "tier0.market_data.nominal_ohlcv_daily"
        for item in training
    ):
        raise StrategyLabError(
            "snapshot has no accepted nominal OHLCV training input for B0"
        )
    if not validation:
        raise StrategyLabError(
            "development snapshot has no validation partitions after train_end"
        )
    if not any(
        item.dataset_id == "tier0.market_data.nominal_ohlcv_daily"
        for item in validation
    ):
        raise StrategyLabError(
            "development snapshot has no accepted nominal OHLCV validation input"
        )
    return ResearchInputBundle(
        snapshot_id=snapshot.snapshot_id,
        snapshot_content_hash=snapshot.content_hash,
        snapshot_config_hash=snapshot.config_hash,
        universe_id=snapshot.universe_id,
        available_at_lower=snapshot.available_at_lower,
        available_at_upper=snapshot.available_at_upper,
        train_end=train_day,
        holdout_start=holdout_day,
        training_inputs=tuple(training),
        validation_inputs=tuple(validation),
        metadata_inputs=tuple(metadata),
    )


@dataclass(frozen=True)
class ComputeRequest:
    stage: Stage
    executor: Executor
    spec_id: str | None = None


@dataclass(frozen=True)
class ComputeAdmission:
    allowed: bool
    claimable: bool
    stage: Stage
    executor: Executor
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "claimable": self.claimable,
            "stage": self.stage,
            "executor": self.executor,
            "reasons": list(self.reasons),
        }


def assess_compute(
    bundle: ResearchInputBundle,
    request: ComputeRequest,
) -> ComputeAdmission:
    """Admit local-smoke (optional loaded spec) and gated formal_rx; never claimable."""

    active_policy = load_policy()
    reasons: list[str] = []
    if request.stage not in {"local_smoke", "formal_rx", "optuna"}:
        reasons.append("unknown_stage")
    if request.executor not in {"local", "modal"}:
        reasons.append("unknown_executor")
    if active_policy.execution_mode != "manual_only":
        reasons.append("execution_mode_not_manual_only")

    formal = request.stage in {"formal_rx", "optuna"}
    if formal:
        if not active_policy.formal_rx_authorization:
            reasons.append("formal_rx_not_authorized")
        reasons.extend(validate_formal_rx_evidence(bundle))

    if request.stage == "optuna":
        if not active_policy.phase_n_authorization:
            reasons.append("phase_n_optuna_not_authorized")
        reasons.append("optuna_runner_not_implemented")

    if request.executor == "modal":
        if not active_policy.remote_compute_authorization:
            reasons.append("remote_compute_not_authorized")
        reasons.append("modal_adapter_not_implemented")

    if request.spec_id:
        try:
            load_strategy_spec(request.spec_id)
        except StrategySpecError:
            reasons.append("strategy_spec_not_loadable")

    return ComputeAdmission(
        allowed=not reasons,
        claimable=False,
        stage=request.stage,
        executor=request.executor,
        reasons=tuple(reasons),
    )


__all__ = [
    "ComputeAdmission",
    "ComputeRequest",
    "InputSlice",
    "ResearchInputBundle",
    "StrategyLabError",
    "StrategyLabPolicy",
    "assess_compute",
    "build_ingress_plan",
    "load_policy",
]
