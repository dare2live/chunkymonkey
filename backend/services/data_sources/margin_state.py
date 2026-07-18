"""Read-only projections derived from accepted TuShare margin facts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from services.data_sources.batch_integrity import VerifiedBatchFrontier
from services.data_sources.contracts import load_dataset_contract
from services.data_sources import margin_validation
from services.data_sources.margin_evidence import (
    ACCEPTED_EVIDENCE_FIELDS,
    BATCH_EVIDENCE_FIELDS,
    CANONICAL_EVIDENCE_FIELDS,
    LANDING_EVIDENCE_FIELDS,
    MarginEvidenceLoadError,
    MarginEvidenceSnapshot,
    load_margin_evidence_snapshot,
)
from services.data_sources.margin_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    MARGIN_FIELDS,
)
from services.data_sources.margin_validation import (
    MarginValidationError,
    _candidate_rows,
    canonical_content_hash,
)


class MarginStateError(RuntimeError):
    """Accepted-state evidence is missing or internally contradictory."""


@dataclass(frozen=True)
class AcceptedMarginPartition:
    partition_value: str
    batch_id: str
    row_count: int
    content_hash: str
    accepted_at: Any


@dataclass(frozen=True)
class MarginPartitionProofFailure:
    """One accepted partition whose formal evidence could not be reproved."""

    partition_value: str
    detail: str


@dataclass(frozen=True)
class MarginPartitionProofs:
    """Partition-local results from one immutable evidence snapshot."""

    accepted: tuple[AcceptedMarginPartition, ...]
    failures: tuple[MarginPartitionProofFailure, ...]


@dataclass(frozen=True)
class MarginAcceptedState:
    """One internally verified snapshot of all current accepted partitions."""

    partitions: tuple[AcceptedMarginPartition, ...]

    @property
    def dates(self) -> frozenset[str]:
        return frozenset(item.partition_value for item in self.partitions)

    @property
    def batch_by_partition(self) -> dict[str, str]:
        return {
            item.partition_value: item.batch_id
            for item in self.partitions
        }

    @property
    def frontier(self) -> VerifiedBatchFrontier | None:
        if not self.partitions:
            return None
        latest = max(self.partitions, key=lambda item: item.partition_value)
        return VerifiedBatchFrontier(
            last_date=latest.partition_value,
            row_count=latest.row_count,
            last_success_at=latest.accepted_at,
        )


@dataclass(frozen=True)
class MarginReconcileFailure:
    partition_value: str
    issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class MarginReadiness:
    """Typed read-only verdict consumed by pipeline gates."""

    eligible_end: str | None
    eligibility_reason: str
    expected: tuple[str, ...]
    accepted_state: MarginAcceptedState
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    reconcile_failures: tuple[MarginReconcileFailure, ...]

    @property
    def ready(self) -> bool:
        return bool(
            self.eligible_end
            and self.expected
            and not self.missing
            and not self.unexpected
            and not self.reconcile_failures
        )


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    try:
        if len(compact) != 8 or not compact.isdigit():
            raise ValueError
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise MarginStateError(f"invalid accepted margin partition={value!r}") from exc
    return compact


def _prove_margin_pointer(
    conn,
    *,
    contract,
    partition: str,
    pointer: dict[str, Any],
    batches_by_id: dict[str, list[dict[str, Any]]],
    landing_by_batch: dict[str, list[tuple[Any, ...]]],
    canonical_by_partition: dict[str, list[tuple[Any, ...]]],
    publication_index,
) -> AcceptedMarginPartition:
    batch_id = str(pointer["batch_id"])
    row_count = int(pointer["row_count"])
    content_hash = str(pointer["content_hash"])
    if (
        str(pointer["dataset_id"]) != DATASET_ID
        or str(pointer["contract_version"]) != contract.contract_version
        or str(pointer["contract_hash"]) != contract.contract_hash
        or str(pointer["config_hash"]) != contract.config_hash
    ):
        raise MarginStateError(
            f"accepted pointer contract drift partition={partition} batch={batch_id}"
        )

    batch_matches = batches_by_id.get(batch_id, [])
    batch = batch_matches[0] if len(batch_matches) == 1 else None
    if batch is None or (
        str(batch["evidence_partition_value"]) != partition
        or str(batch["dataset_id"]) != DATASET_ID
        or str(batch["status"]) != "ACCEPTED"
        or _partition(batch["partition_value"]) != partition
        or int(batch["canonical_row_count"] or 0) != row_count
        or str(batch["canonical_hash"] or "") != content_hash
        or str(batch["contract_version"]) != str(pointer["contract_version"])
        or str(batch["contract_hash"]) != str(pointer["contract_hash"])
        or str(batch["config_hash"]) != str(pointer["config_hash"])
        or str(batch["source_name"]) != contract.source
        or str(batch["writer_id"]) != contract.writer
        or batch["observed_at"] != pointer["observed_at"]
        or batch["available_at"] != pointer["available_at"]
    ):
        raise MarginStateError(
            f"accepted batch evidence mismatch partition={partition} batch={batch_id}"
        )

    try:
        source_rows = _candidate_rows(
            conn,
            batch_id,
            partition,
            contract,
            batch,
            landed_rows=landing_by_batch.get(batch_id, ()),
        )
    except MarginValidationError as exc:
        raise MarginStateError(
            f"accepted landing evidence mismatch partition={partition} "
            f"batch={batch_id} code={exc.code}"
        ) from exc
    if len(source_rows) != row_count or canonical_content_hash(source_rows) != content_hash:
        raise MarginStateError(
            f"accepted landing content mismatch partition={partition} batch={batch_id}"
        )
    source_by_grain = {
        (str(row["trade_date"]), str(row["exchange_id"])): row
        for row in source_rows
    }
    if len(source_by_grain) != row_count:
        raise MarginStateError(
            f"accepted landing grain mismatch partition={partition} batch={batch_id}"
        )

    canonical_rows = canonical_by_partition.get(partition, [])
    normalized = [
        dict(zip(CANONICAL_EVIDENCE_FIELDS[1:], row, strict=True))
        for row in canonical_rows
    ]
    for row in normalized:
        row["trade_date"] = _partition(row["trade_date"])
    if len(normalized) != row_count or any(
        str(row["ingest_batch_id"]) != batch_id
        or str(row["contract_version"]) != contract.contract_version
        or str(row["config_hash"]) != contract.config_hash
        or row["available_at"] != pointer["available_at"]
        for row in normalized
    ):
        raise MarginStateError(
            f"canonical count/batch mismatch partition={partition} batch={batch_id}"
        )
    try:
        margin_validation.validate_margin_publication_time(
            contract,
            partition,
            pointer["available_at"],
            trading_day_values=publication_index,
        )
    except MarginValidationError as exc:
        raise MarginStateError(
            "accepted publication evidence mismatch "
            f"partition={partition} batch={batch_id} code={exc.code}"
        ) from exc
    if canonical_content_hash(normalized) != content_hash:
        raise MarginStateError(
            f"canonical content mismatch partition={partition} batch={batch_id}"
        )
    for row in normalized:
        grain = (str(row["trade_date"]), str(row["exchange_id"]))
        source = source_by_grain.get(grain)
        if source is None or str(row["source_row_hash"]) != str(
            source["source_row_hash"]
        ):
            raise MarginStateError(
                f"canonical landing lineage mismatch partition={partition} "
                f"batch={batch_id} grain={grain}"
            )
    return AcceptedMarginPartition(
        partition_value=partition,
        batch_id=batch_id,
        row_count=row_count,
        content_hash=content_hash,
        accepted_at=pointer["accepted_at"],
    )


def _prove_margin_partitions_snapshot(
    conn,
    *,
    contract,
    evidence_snapshot: MarginEvidenceSnapshot,
    partition_value: str | None = None,
) -> MarginPartitionProofs:
    """Reprove a trusted in-call snapshot while retaining local failures."""

    if evidence_snapshot.contract is not contract:
        raise MarginStateError("margin evidence snapshot contract identity drift")
    if partition_value is not None:
        partition_value = _partition(partition_value)
    if evidence_snapshot.partition_value != partition_value:
        raise MarginStateError(
            "margin evidence snapshot partition scope drift "
            f"snapshot={evidence_snapshot.partition_value!r} requested={partition_value!r}"
        )
    if (scope_error := evidence_snapshot.scope_error()) is not None:
        raise MarginStateError(scope_error)
    if evidence_snapshot.load_error:
        raise MarginStateError(evidence_snapshot.load_error)

    required_tables = (
        ACCEPTED_TABLE,
        INGEST_BATCH_TABLE,
        LANDING_TABLE,
        CANONICAL_TABLE,
    )
    available = {
        table
        for table in required_tables
        if (schema := evidence_snapshot.schema_for(table)) is not None
        and schema.available
    }
    if not available:
        return MarginPartitionProofs((), ())
    missing = [table for table in required_tables if table not in available]
    if missing:
        raise MarginStateError(f"partial formal margin schema missing={missing}")

    try:
        prepared_pointers = [
            (
                _partition(pointer["partition_value"]),
                pointer,
            )
            for pointer in (
                dict(zip(ACCEPTED_EVIDENCE_FIELDS, row, strict=True))
                for row in evidence_snapshot.accepted_rows
            )
        ]
        batches_by_id: dict[str, list[dict[str, Any]]] = {}
        for row in evidence_snapshot.batch_rows:
            batch = dict(zip(BATCH_EVIDENCE_FIELDS, row, strict=True))
            batches_by_id.setdefault(str(batch["batch_id"]), []).append(batch)
        landing_by_batch: dict[str, list[tuple[Any, ...]]] = {}
        for row in evidence_snapshot.landing_rows:
            landing = dict(zip(LANDING_EVIDENCE_FIELDS, row, strict=True))
            landing_by_batch.setdefault(str(landing["batch_id"]), []).append(
                tuple(landing[field] for field in LANDING_EVIDENCE_FIELDS[2:])
            )
        canonical_by_partition: dict[str, list[tuple[Any, ...]]] = {}
        for row in evidence_snapshot.canonical_rows:
            canonical = dict(zip(CANONICAL_EVIDENCE_FIELDS, row, strict=True))
            canonical_by_partition.setdefault(
                _partition(canonical["accepted_partition_value"]), []
            ).append(
                tuple(canonical[field] for field in CANONICAL_EVIDENCE_FIELDS[1:])
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarginStateError(f"malformed margin evidence snapshot: {exc}") from exc
    if not prepared_pointers:
        return MarginPartitionProofs((), ())

    first_partition = min(partition for partition, _pointer in prepared_pointers)
    try:
        publication_days = margin_validation.load_margin_publication_sessions(
            first_partition, limit=None
        )
        publication_index = margin_validation.prepare_trading_session_index(
            publication_days
        )
    except Exception as exc:
        raise MarginStateError(
            f"margin publication calendar evidence unavailable: {str(exc)[:500]}"
        ) from exc

    accepted: list[AcceptedMarginPartition] = []
    failures: list[MarginPartitionProofFailure] = []
    for partition, pointer in prepared_pointers:
        try:
            accepted.append(
                _prove_margin_pointer(
                    conn,
                    contract=contract,
                    partition=partition,
                    pointer=pointer,
                    batches_by_id=batches_by_id,
                    landing_by_batch=landing_by_batch,
                    canonical_by_partition=canonical_by_partition,
                    publication_index=publication_index,
                )
            )
        except MarginStateError as exc:
            failures.append(MarginPartitionProofFailure(partition, str(exc)))
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(
                MarginPartitionProofFailure(
                    partition,
                    f"malformed accepted evidence partition={partition}: {exc}",
                )
            )
    return MarginPartitionProofs(tuple(accepted), tuple(failures))


def _accepted_margin_partitions_snapshot(
    conn,
    *,
    contract,
    evidence_snapshot: MarginEvidenceSnapshot,
    partition_value: str | None = None,
) -> tuple[AcceptedMarginPartition, ...]:
    proofs = _prove_margin_partitions_snapshot(
        conn,
        contract=contract,
        evidence_snapshot=evidence_snapshot,
        partition_value=partition_value,
    )
    if proofs.failures:
        raise MarginStateError(proofs.failures[0].detail)
    return proofs.accepted


def accepted_margin_partitions(
    conn,
    *,
    contract=None,
    partition_value: str | None = None,
) -> tuple[AcceptedMarginPartition, ...]:
    """Load and prove accepted partitions from the supplied live connection."""

    contract = contract or load_dataset_contract("margin")
    if partition_value is not None:
        partition_value = _partition(partition_value)
    try:
        evidence_snapshot = load_margin_evidence_snapshot(
            conn,
            contract=contract,
            partition_value=partition_value,
        )
    except MarginEvidenceLoadError as exc:
        raise MarginStateError(str(exc)) from exc
    return _accepted_margin_partitions_snapshot(
        conn,
        contract=contract,
        evidence_snapshot=evidence_snapshot,
        partition_value=partition_value,
    )


def accepted_margin_dates(conn, *, contract=None) -> set[str]:
    return set(load_margin_accepted_state(conn, contract=contract).dates)


def _load_margin_accepted_state_snapshot(
    conn,
    *,
    contract,
    evidence_snapshot: MarginEvidenceSnapshot,
) -> MarginAcceptedState:
    return MarginAcceptedState(
        _accepted_margin_partitions_snapshot(
            conn,
            contract=contract,
            evidence_snapshot=evidence_snapshot,
            partition_value=evidence_snapshot.partition_value,
        )
    )


def load_margin_accepted_state(conn, *, contract=None) -> MarginAcceptedState:
    """Load once so a consumer cannot combine independently read snapshots."""

    return MarginAcceptedState(
        accepted_margin_partitions(conn, contract=contract)
    )


def missing_accepted_margin_dates(
    conn, trading_days: Iterable[str], *, contract=None
) -> list[str]:
    contract = contract or load_dataset_contract("margin")
    expected = sorted(
        {
            _partition(day)
            for day in trading_days
            if _partition(day) >= contract.coverage_start
        }
    )
    accepted = accepted_margin_dates(conn, contract=contract)
    return [day for day in expected if day not in accepted]


def latest_accepted_margin_frontier(
    conn, *, contract=None
) -> VerifiedBatchFrontier | None:
    return load_margin_accepted_state(conn, contract=contract).frontier


__all__ = [
    "AcceptedMarginPartition",
    "MarginAcceptedState",
    "MarginPartitionProofFailure",
    "MarginPartitionProofs",
    "MarginReadiness",
    "MarginReconcileFailure",
    "MarginStateError",
    "accepted_margin_dates",
    "accepted_margin_partitions",
    "latest_accepted_margin_frontier",
    "load_margin_accepted_state",
    "missing_accepted_margin_dates",
]
