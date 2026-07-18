"""Compare-before-publish execution for bounded formal margin history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from services.data_sources.margin_acceptance import (
    RecoverableMarginLanding,
    accept_margin_batch,
    find_current_landed_margin_batch,
)
from services.data_sources.margin_history_contract import (
    MarginHistoryCheckpoint,
    MarginHistoryCheckpointKind,
)
from services.data_sources.margin_ingest import (
    FetchLogicalBatch,
    MarginHistoryBatchIncomplete,
    MarginReconcileError,
    QuotaWallClassifier,
    _prepare_margin_partition,
    contract_for_spec,
)
from services.data_sources.margin_legacy_reconcile import (
    MarginHistoryComparison,
    MarginReconcileCode,
    MarginReconcileIssue,
    compare_margin_history_rows,
)
from services.data_sources.margin_reconcile import reconcile_margin_partition
from services.data_sources.margin_schema import DATASET_ID, MARGIN_FIELDS
from services.data_sources.margin_state import accepted_margin_partitions


class MarginHistoryCheckpointDrift(RuntimeError):
    """Live accepted/LANDED evidence no longer matches the immutable plan."""

    def __init__(self, detail: str, *, evidence: dict[str, Any]):
        super().__init__(detail)
        self.evidence = dict(evidence)


class MarginHistoryIngestKind(str, Enum):
    """Stable partition outcomes consumed by the bounded history controller."""

    ACCEPTED = "ACCEPTED"
    LEGACY_CONFLICT = "LEGACY_CONFLICT"


@dataclass(frozen=True)
class MarginHistoryIngestOutcome:
    """Content-addressed evidence for one history partition attempt."""

    kind: MarginHistoryIngestKind
    partition_value: str
    batch_id: str
    row_count: int
    content_hash: str
    candidate_hash: str
    legacy_hash: str | None
    issues: tuple[MarginReconcileIssue, ...] = ()

    @property
    def issue_codes(self) -> tuple[MarginReconcileCode, ...]:
        return tuple(
            sorted({issue.code for issue in self.issues}, key=lambda code: code.value)
        )


def prove_history_execution_checkpoint(
    conn,
    partition: str,
    *,
    contract,
    checkpoint: MarginHistoryCheckpoint,
) -> RecoverableMarginLanding | None:
    """Re-prove the plan's accepted and landing identities before provider I/O."""

    if (
        checkpoint.partition_value != partition
        or checkpoint.kind not in {
            MarginHistoryCheckpointKind.SELECTED,
            MarginHistoryCheckpointKind.REPAIR,
        }
    ):
        raise MarginHistoryCheckpointDrift(
            "history executor received a non-actionable checkpoint",
            evidence={
                "expected_partition": partition,
                "checkpoint_partition": checkpoint.partition_value,
                "checkpoint_kind": checkpoint.kind.value,
            },
        )

    accepted = accepted_margin_partitions(
        conn, contract=contract, partition_value=partition
    )
    actual_accepted = None
    if accepted:
        if len(accepted) != 1 or accepted[0].partition_value != partition:
            raise MarginHistoryCheckpointDrift(
                "history accepted checkpoint became ambiguous after planning",
                evidence={
                    "surface": "accepted",
                    "partition": partition,
                    "actual_count": len(accepted),
                },
            )
        actual_accepted = (
            accepted[0].batch_id,
            accepted[0].row_count,
            accepted[0].content_hash,
        )
    expected_accepted = None
    if checkpoint.accepted_batch_id is not None:
        expected_accepted = (
            checkpoint.accepted_batch_id,
            checkpoint.accepted_row_count,
            checkpoint.accepted_content_hash,
        )
    if actual_accepted != expected_accepted:
        raise MarginHistoryCheckpointDrift(
            "history accepted checkpoint drifted after planning "
            f"partition={partition}",
            evidence={
                "surface": "accepted",
                "expected": expected_accepted,
                "actual": actual_accepted,
            },
        )

    actual_landing = find_current_landed_margin_batch(
        conn, partition, contract=contract
    )
    actual_landing_evidence = None if actual_landing is None else (
        actual_landing.batch_id,
        actual_landing.payload_hash,
    )
    expected_landing = None
    if checkpoint.recoverable_landing_batch_id is not None:
        expected_landing = (
            checkpoint.recoverable_landing_batch_id,
            checkpoint.recoverable_landing_payload_hash,
        )
    if actual_landing_evidence != expected_landing:
        raise MarginHistoryCheckpointDrift(
            "history LANDED checkpoint drifted after planning "
            f"partition={partition}",
            evidence={
                "surface": "landing",
                "expected": expected_landing,
                "actual": actual_landing_evidence,
            },
        )
    return actual_landing


def _load_history_legacy_rows(
    conn, *, contract, partition: str
) -> list[dict[str, Any]]:
    fields = ", ".join(MARGIN_FIELDS)
    order = ", ".join(("exchange_id", *MARGIN_FIELDS[2:]))
    rows = conn.execute(
        f"""
        SELECT {fields}
          FROM {contract.compatibility_table}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') = ?
         ORDER BY {order}
        """,
        [partition],
    ).fetchall()
    return [dict(zip(MARGIN_FIELDS, row, strict=True)) for row in rows]


def _history_conflict(
    *,
    partition: str,
    batch_id: str,
    row_count: int,
    content_hash: str,
    legacy_hash: str | None,
    issues: tuple[MarginReconcileIssue, ...],
) -> MarginHistoryIngestOutcome:
    return MarginHistoryIngestOutcome(
        kind=MarginHistoryIngestKind.LEGACY_CONFLICT,
        partition_value=partition,
        batch_id=batch_id,
        row_count=row_count,
        content_hash=content_hash,
        candidate_hash=content_hash,
        legacy_hash=legacy_hash,
        issues=issues,
    )


def execute_history_partition(
    conn,
    adapter,
    spec: dict[str, Any],
    params: dict[str, Any],
    *,
    fetch_logical_batch: FetchLogicalBatch,
    quota_wall_classifier: QuotaWallClassifier | None = None,
    contract=None,
    observed_at: datetime | None = None,
    batch_id: str | None = None,
    checkpoint: MarginHistoryCheckpoint,
) -> MarginHistoryIngestOutcome:
    """Compare a history candidate with legacy before publishing canonical data.

    This path deliberately has no legacy writer callback.  A disagreement leaves
    the durable landing untouched for adjudication and a retry reuses it without
    another provider observation.
    """

    contract = contract or contract_for_spec(spec)
    if contract is None or contract.dataset_id != DATASET_ID:
        raise ValueError("margin history executor requires the formal margin contract")
    date_param = str(spec.get("date_param") or "trade_date")
    partition = str(params.get(date_param) or "").replace("-", "")
    if len(partition) != 8 or not partition.isdigit():
        raise ValueError(f"formal margin partition must be YYYYMMDD: {partition!r}")

    known_landed_batch = prove_history_execution_checkpoint(
        conn,
        partition,
        contract=contract,
        checkpoint=checkpoint,
    )

    contract, partition, evidence_batch_id, prepared = _prepare_margin_partition(
        conn,
        adapter,
        spec,
        params,
        fetch_logical_batch=fetch_logical_batch,
        quota_wall_classifier=quota_wall_classifier,
        contract=contract,
        observed_at=observed_at,
        batch_id=batch_id,
        known_landed_batch=known_landed_batch,
    )
    try:
        legacy_rows = _load_history_legacy_rows(
            conn, contract=contract, partition=partition
        )
    except Exception as exc:
        issue = MarginReconcileIssue(
            MarginReconcileCode.QUERY_ERROR,
            f"history legacy read failed: {str(exc)[:300]}",
        )
        return _history_conflict(
            partition=partition,
            batch_id=evidence_batch_id,
            row_count=prepared.row_count,
            content_hash=prepared.content_hash,
            legacy_hash=None,
            issues=(issue,),
        )

    comparison: MarginHistoryComparison = compare_margin_history_rows(
        partition,
        prepared.canonical_rows,
        legacy_rows,
    )
    if comparison.candidate_hash != prepared.content_hash:
        raise RuntimeError(
            "history candidate hash contradicts validated landing "
            f"partition={partition}"
        )
    if not comparison.ok:
        return _history_conflict(
            partition=partition,
            batch_id=evidence_batch_id,
            row_count=prepared.row_count,
            content_hash=prepared.content_hash,
            legacy_hash=comparison.legacy_hash,
            issues=comparison.issues,
        )
    if comparison.legacy_hash != prepared.content_hash:
        raise RuntimeError(
            "history parity passed but normalized content hashes differ "
            f"partition={partition}"
        )

    try:
        outcome = accept_margin_batch(conn, evidence_batch_id, contract=contract)
    except Exception as accept_error:
        # Only a read-side proof of the exact committed publication may convert
        # an ACK-loss into success.  A still-LANDED batch is a checkpoint for a
        # later plan; retrying acceptance here would hide programming failures.
        try:
            reconcile = reconcile_margin_partition(
                conn, partition, contract=contract
            )
        except Exception as proof_error:
            accept_error.add_note(
                "post-error acceptance proof also failed: "
                f"{type(proof_error).__name__}: {str(proof_error)[:300]}"
            )
            raise accept_error
        if not (
            reconcile.ok
            and reconcile.accepted_batch_id == evidence_batch_id
            and reconcile.accepted_row_count == prepared.row_count
            and reconcile.accepted_content_hash == prepared.content_hash
        ):
            accept_error.add_note(
                "acceptance was not committed with the planned batch/content; "
                "the original exception is authoritative"
            )
            raise
        outcome_row_count = int(reconcile.accepted_row_count)
        outcome_content_hash = str(reconcile.accepted_content_hash)
    else:
        if (
            outcome.status != "ACCEPTED"
            or outcome.batch_id != evidence_batch_id
            or outcome.content_hash != prepared.content_hash
        ):
            rejection_code = str(outcome.rejection_code or outcome.status)
            raise MarginHistoryBatchIncomplete(
                "formal margin history acceptance was not durably reproved "
                f"partition={partition} code={rejection_code}",
                batch_id=evidence_batch_id,
                rejection_code=rejection_code,
            )
        outcome_row_count = outcome.row_count
        outcome_content_hash = str(outcome.content_hash)
        reconcile = reconcile_margin_partition(conn, partition, contract=contract)
    if not reconcile.ok:
        codes = sorted({issue.code.value for issue in reconcile.issues})
        raise MarginReconcileError(
            f"formal margin history parity failed partition={partition} codes={codes}"
        )
    return MarginHistoryIngestOutcome(
        kind=MarginHistoryIngestKind.ACCEPTED,
        partition_value=partition,
        batch_id=evidence_batch_id,
        row_count=outcome_row_count,
        content_hash=outcome_content_hash,
        candidate_hash=prepared.content_hash,
        legacy_hash=comparison.legacy_hash,
    )


__all__ = [
    "MarginHistoryCheckpointDrift",
    "MarginHistoryIngestKind",
    "MarginHistoryIngestOutcome",
    "execute_history_partition",
    "prove_history_execution_checkpoint",
]
