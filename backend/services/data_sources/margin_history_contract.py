"""Immutable evidence contracts for bounded formal margin-history migration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum


class MarginHistoryCheckpointKind(str, Enum):
    """Stable action derived from one reconcile checkpoint."""

    SKIP = "SKIP"
    SELECTED = "SELECTED"
    REPAIR = "REPAIR"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class MarginHistoryRequest:
    """Explicit, inclusive history window and per-run action cap."""

    start: str
    end: str
    max_dates: int


@dataclass(frozen=True)
class MarginHistoryAcceptedEvidence:
    """Positive acceptance evidence returned by a partition executor."""

    partition_value: str
    batch_id: str
    row_count: int
    content_hash: str


@dataclass(frozen=True)
class MarginHistoryFailure:
    """One stable, resumable failure; execution stops after the first."""

    partition_value: str
    code: str
    detail: str
    evidence_hash: str | None = None


@dataclass(frozen=True)
class MarginHistoryPartitionOutcome:
    """Typed adapter boundary for a caller-owned one-partition executor."""

    partition_value: str
    accepted_evidence: MarginHistoryAcceptedEvidence | None = None
    failure: MarginHistoryFailure | None = None

    def __post_init__(self) -> None:
        if (self.accepted_evidence is None) == (self.failure is None):
            raise ValueError(
                "partition outcome requires exactly one of accepted_evidence/failure"
            )

    @classmethod
    def accepted(
        cls, evidence: MarginHistoryAcceptedEvidence
    ) -> MarginHistoryPartitionOutcome:
        return cls(
            partition_value=evidence.partition_value,
            accepted_evidence=evidence,
        )

    @classmethod
    def failed(
        cls,
        partition_value: str,
        *,
        code: str,
        detail: str,
        evidence_hash: str | None = None,
    ) -> MarginHistoryPartitionOutcome:
        return cls(
            partition_value=partition_value,
            failure=MarginHistoryFailure(
                partition_value, code, detail, evidence_hash
            ),
        )


@dataclass(frozen=True)
class MarginHistoryCheckpoint:
    """Classification of one trading partition in the requested window."""

    partition_value: str
    kind: MarginHistoryCheckpointKind
    accepted_batch_id: str | None
    accepted_row_count: int | None
    issue_codes: tuple[str, ...]
    accepted_content_hash: str | None = None
    recoverable_landing_batch_id: str | None = None
    recoverable_landing_payload_hash: str | None = None
    unresolved_landing_batch_ids: tuple[str, ...] = ()


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def history_evidence_hash(payload: object) -> str:
    """Content-address typed failure evidence with the canonical serializer."""

    return _stable_hash(payload)


def _checkpoint_payload(checkpoint: MarginHistoryCheckpoint) -> dict[str, object]:
    return {
        "partition_value": checkpoint.partition_value,
        "kind": checkpoint.kind.value,
        "accepted_batch_id": checkpoint.accepted_batch_id,
        "accepted_row_count": checkpoint.accepted_row_count,
        "accepted_content_hash": checkpoint.accepted_content_hash,
        "recoverable_landing_batch_id": checkpoint.recoverable_landing_batch_id,
        "recoverable_landing_payload_hash": (
            checkpoint.recoverable_landing_payload_hash
        ),
        "unresolved_landing_batch_ids": list(
            checkpoint.unresolved_landing_batch_ids
        ),
        "issue_codes": list(checkpoint.issue_codes),
    }


@dataclass(frozen=True)
class MarginHistoryPlan:
    """Deterministic oldest-first plan derived entirely from supplied evidence."""

    request: MarginHistoryRequest
    configured_max_dates: int
    dataset_id: str
    contract_hash: str
    config_hash: str
    window_dates: tuple[str, ...]
    checkpoints: tuple[MarginHistoryCheckpoint, ...]
    execution_dates: tuple[str, ...]
    deferred_dates: tuple[str, ...]
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "plan_hash",
            _stable_hash(
                {
                    "request": {
                        "start": self.request.start,
                        "end": self.request.end,
                        "max_dates": self.request.max_dates,
                    },
                    "configured_max_dates": self.configured_max_dates,
                    "dataset_id": self.dataset_id,
                    "contract_hash": self.contract_hash,
                    "config_hash": self.config_hash,
                    "window_dates": list(self.window_dates),
                    "checkpoints": [
                        _checkpoint_payload(item) for item in self.checkpoints
                    ],
                    "execution_dates": list(self.execution_dates),
                    "deferred_dates": list(self.deferred_dates),
                }
            ),
        )

    def _dates_for(
        self, kind: MarginHistoryCheckpointKind, *, selected_only: bool = False
    ) -> tuple[str, ...]:
        selected = set(self.execution_dates) if selected_only else None
        return tuple(
            item.partition_value
            for item in self.checkpoints
            if item.kind is kind
            and (selected is None or item.partition_value in selected)
        )

    @property
    def skipped_dates(self) -> tuple[str, ...]:
        return self._dates_for(MarginHistoryCheckpointKind.SKIP)

    @property
    def selected_dates(self) -> tuple[str, ...]:
        return self._dates_for(
            MarginHistoryCheckpointKind.SELECTED, selected_only=True
        )

    @property
    def repair_dates(self) -> tuple[str, ...]:
        return self._dates_for(
            MarginHistoryCheckpointKind.REPAIR, selected_only=True
        )

    @property
    def blocked_dates(self) -> tuple[str, ...]:
        return self._dates_for(MarginHistoryCheckpointKind.BLOCKED)


def _evidence_payload(evidence: MarginHistoryAcceptedEvidence) -> dict[str, object]:
    return {
        "partition_value": evidence.partition_value,
        "batch_id": evidence.batch_id,
        "row_count": evidence.row_count,
        "content_hash": evidence.content_hash,
    }


def _failure_hash_payload(failure: MarginHistoryFailure) -> dict[str, str]:
    """Hash stable machine evidence; human exception text is display-only."""

    return {
        "partition_value": failure.partition_value,
        "code": failure.code,
        "evidence_hash": failure.evidence_hash,
    }


@dataclass(frozen=True)
class MarginHistoryResult:
    """Exact run evidence; unattempted/deferred dates are never failures."""

    dataset_id: str
    contract_hash: str
    config_hash: str
    plan_hash: str
    window_dates: tuple[str, ...]
    attempted_dates: tuple[str, ...]
    skipped_dates: tuple[str, ...]
    accepted_evidence: tuple[MarginHistoryAcceptedEvidence, ...]
    failures: tuple[MarginHistoryFailure, ...]
    deferred_dates: tuple[str, ...]
    next_start: str | None
    blocked_partition: str | None = None
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "result_hash",
            _stable_hash(
                {
                    "dataset_id": self.dataset_id,
                    "contract_hash": self.contract_hash,
                    "config_hash": self.config_hash,
                    "plan_hash": self.plan_hash,
                    "window_dates": list(self.window_dates),
                    "attempted_dates": list(self.attempted_dates),
                    "skipped_dates": list(self.skipped_dates),
                    "accepted_evidence": [
                        _evidence_payload(item) for item in self.accepted_evidence
                    ],
                    "failures": [
                        _failure_hash_payload(item) for item in self.failures
                    ],
                    "deferred_dates": list(self.deferred_dates),
                    "next_start": self.next_start,
                    "blocked_partition": self.blocked_partition,
                }
            ),
        )

    @property
    def accepted_dates(self) -> tuple[str, ...]:
        return tuple(item.partition_value for item in self.accepted_evidence)

    @property
    def failed_dates(self) -> tuple[str, ...]:
        return tuple(item.partition_value for item in self.failures)


__all__ = [
    "MarginHistoryAcceptedEvidence",
    "MarginHistoryCheckpoint",
    "MarginHistoryCheckpointKind",
    "MarginHistoryFailure",
    "MarginHistoryPartitionOutcome",
    "MarginHistoryPlan",
    "MarginHistoryRequest",
    "MarginHistoryResult",
    "history_evidence_hash",
]
