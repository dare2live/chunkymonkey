"""Snapshot state machine for one accepted margin partition."""

from __future__ import annotations

from decimal import InvalidOperation
from typing import Any

from services.data_sources.margin_acceptance import (
    MarginAcceptanceError,
    prove_current_landed_margin_batch,
)
from services.data_sources.margin_evidence import (
    BATCH_EVIDENCE_FIELDS,
    CANONICAL_EVIDENCE_FIELDS,
    LANDING_EVIDENCE_FIELDS,
    LEGACY_EVIDENCE_FIELDS,
    MarginEvidenceSnapshot,
)
from services.data_sources.margin_legacy_reconcile import (
    MarginReconcileCode,
    MarginReconcileIssue,
    MarginReconcileReport,
    _business_row_issues,
    _canonical_source_issue,
    _grain,
    _landing_business_rows,
    _partition,
    _report,
    _row_dicts,
    _snapshot_schema_issues,
)
from services.data_sources.margin_schema import DATASET_ID, MARGIN_FIELDS
from services.data_sources.margin_validation import canonical_content_hash

def _reconcile_margin_partition_snapshot(
    partition_value: Any,
    *,
    contract,
    evidence_snapshot: MarginEvidenceSnapshot,
) -> MarginReconcileReport:
    """Evaluate one already loaded partition without opening a proof bypass."""
    partition = _partition(partition_value)
    if partition is None:
        return _report(
            str(partition_value or ""),
            (
                MarginReconcileIssue(
                    MarginReconcileCode.INVALID_PARTITION,
                    f"expected a real YYYYMMDD partition, got {partition_value!r}",
                ),
            ),
        )

    legacy_table = contract.compatibility_table
    if (
        evidence_snapshot.contract is not contract
        or not evidence_snapshot.include_legacy
        or evidence_snapshot.partition_value not in (None, partition)
    ):
        return _report(
            partition,
            (
                MarginReconcileIssue(
                    MarginReconcileCode.QUERY_ERROR,
                    "margin evidence snapshot scope/contract is incompatible",
                ),
            ),
        )
    if (scope_error := evidence_snapshot.scope_error()) is not None:
        return _report(
            partition,
            (
                MarginReconcileIssue(
                    MarginReconcileCode.QUERY_ERROR,
                    scope_error,
                ),
            ),
        )
    schema_issues = _snapshot_schema_issues(evidence_snapshot, legacy_table)
    if schema_issues:
        return _report(partition, schema_issues)
    if evidence_snapshot.load_error:
        return _report(
            partition,
            (
                MarginReconcileIssue(
                    MarginReconcileCode.QUERY_ERROR,
                    evidence_snapshot.load_error,
                ),
            ),
        )

    issues: list[MarginReconcileIssue] = []
    accepted_batch_id: str | None = None
    accepted_row_count: int | None = None
    canonical_row_count: int | None = None
    legacy_row_count: int | None = None
    accepted_content_hash: str | None = None
    recoverable_landing_batch_id: str | None = None
    recoverable_landing_payload_hash: str | None = None
    unresolved_landing_batch_ids: tuple[str, ...] = ()

    try:
        batch_evidence = [
            dict(zip(BATCH_EVIDENCE_FIELDS, row, strict=True))
            for row in evidence_snapshot.batch_rows
            if str(row[0]) == partition
        ]
        landed_evidence = [
            (
                value["batch_id"],
                value["contract_version"],
                value["contract_hash"],
                value["config_hash"],
                value["source_name"],
                value["writer_id"],
                value["payload_hash"],
            )
            for value in batch_evidence
            if str(value["status"]) == "LANDED"
        ]
        unresolved_landing_batch_ids = tuple(
            sorted(str(row[0]) for row in landed_evidence)
        )
        landing_proof_error: str | None = None
        try:
            recoverable_landing = prove_current_landed_margin_batch(
                landed_evidence, contract=contract
            )
            if recoverable_landing is not None:
                recoverable_landing_batch_id = recoverable_landing.batch_id
                recoverable_landing_payload_hash = recoverable_landing.payload_hash
        except MarginAcceptanceError as exc:
            landing_proof_error = str(exc)
        if unresolved_landing_batch_ids:
            detail = (
                "margin partition has unresolved landing observations "
                f"batch_ids={list(unresolved_landing_batch_ids)}"
            )
            if landing_proof_error is not None:
                detail += f"; {landing_proof_error}"
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.UNRESOLVED_LANDING,
                    detail,
                )
            )

        accepted_rows = [
            tuple(row[:8])
            for row in evidence_snapshot.accepted_rows
            if str(row[1]) == partition
        ]
        if not accepted_rows:
            return _report(
                partition,
                (
                    *issues,
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTED_PARTITION_MISSING,
                        "no accepted pointer exists for the requested margin partition",
                    ),
                ),
                recoverable_landing_batch_id=recoverable_landing_batch_id,
                recoverable_landing_payload_hash=recoverable_landing_payload_hash,
                unresolved_landing_batch_ids=unresolved_landing_batch_ids,
            )
        if len(accepted_rows) != 1:
            return _report(
                partition,
                (
                    *issues,
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTED_PARTITION_DUPLICATE,
                        f"expected one accepted pointer, found {len(accepted_rows)}",
                    ),
                ),
                recoverable_landing_batch_id=recoverable_landing_batch_id,
                recoverable_landing_payload_hash=recoverable_landing_payload_hash,
                unresolved_landing_batch_ids=unresolved_landing_batch_ids,
            )

        accepted = accepted_rows[0]
        accepted_batch_id = str(accepted[2])
        accepted_row_count = int(accepted[6])
        accepted_content_hash = str(accepted[7])
        accepted_contract_evidence = (
            str(accepted[3]),
            str(accepted[4]),
            str(accepted[5]),
        )
        current_contract_evidence = (
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
        )
        if accepted_contract_evidence != current_contract_evidence:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CURRENT_CONTRACT_MISMATCH,
                    "accepted pointer was published under a non-current contract/config",
                    accepted_value=accepted_contract_evidence,
                    legacy_value=current_contract_evidence,
                )
            )
        batch_rows = []
        for value in batch_evidence:
            if str(value["batch_id"]) != accepted_batch_id:
                continue
            batch_rows.append(
                (
                    value["batch_id"],
                    value["dataset_id"],
                    value["partition_value"],
                    value["status"],
                    value["contract_version"],
                    value["contract_hash"],
                    value["config_hash"],
                    value["canonical_row_count"],
                    value["canonical_hash"],
                )
            )
        batch = batch_rows[0] if len(batch_rows) == 1 else None
        if not batch_rows:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.INGEST_BATCH_MISSING,
                    f"accepted pointer batch_id={accepted_batch_id!r} does not exist",
                )
            )
        elif len(batch_rows) != 1:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.INGEST_BATCH_DUPLICATE,
                    f"batch_id={accepted_batch_id!r} has {len(batch_rows)} rows",
                )
            )
        else:
            if str(batch[1]) != DATASET_ID:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.BATCH_DATASET_MISMATCH,
                        f"batch dataset_id={batch[1]!r} expected={DATASET_ID!r}",
                    )
                )
            if str(batch[2]) != partition:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.BATCH_PARTITION_MISMATCH,
                        f"batch partition={batch[2]!r} expected={partition!r}",
                    )
                )
            if str(batch[3]) != "ACCEPTED":
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.BATCH_NOT_ACCEPTED,
                        f"accepted pointer targets batch status={batch[3]!r}",
                    )
                )
            accepted_evidence = (
                str(accepted[3]),
                str(accepted[4]),
                str(accepted[5]),
                accepted_row_count,
                str(accepted[7]),
            )
            batch_evidence = (
                str(batch[4]),
                str(batch[5]),
                str(batch[6]),
                None if batch[7] is None else int(batch[7]),
                None if batch[8] is None else str(batch[8]),
            )
            if accepted_evidence != batch_evidence:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTANCE_EVIDENCE_MISMATCH,
                        "accepted pointer contract/config/count/hash contradict ingest_batch",
                        accepted_value=accepted_evidence,
                        legacy_value=batch_evidence,
                    )
                )

        landing_payload_rows = [
            (value["fragment_exchange_id"], value["payload_json"])
            for row in evidence_snapshot.landing_rows
            if str(
                (value := dict(zip(LANDING_EVIDENCE_FIELDS, row, strict=True)))[
                    "batch_id"
                ]
            )
            == accepted_batch_id
        ]
        canonical_fields = (
            *MARGIN_FIELDS,
            "ingest_batch_id",
            "contract_version",
            "config_hash",
        )
        canonical_values = []
        for row in evidence_snapshot.canonical_rows:
            value = dict(zip(CANONICAL_EVIDENCE_FIELDS, row, strict=True))
            if str(value["accepted_partition_value"]) != partition:
                continue
            canonical_values.append(tuple(value[field] for field in canonical_fields))
        legacy_values = []
        for row in evidence_snapshot.legacy_rows:
            value = dict(zip(LEGACY_EVIDENCE_FIELDS, row, strict=True))
            if str(value["accepted_partition_value"]) != partition:
                continue
            legacy_values.append(tuple(value[field] for field in MARGIN_FIELDS))
        canonical_rows = _row_dicts(canonical_values, canonical_fields)
        legacy_rows = _row_dicts(legacy_values, MARGIN_FIELDS)
        canonical_row_count = len(canonical_rows)
        legacy_row_count = len(legacy_rows)
    except Exception as exc:
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.QUERY_ERROR,
                f"read-only margin reconciliation query failed: {str(exc)[:500]}",
            )
        )
        return _report(
            partition,
            issues,
            accepted_batch_id=accepted_batch_id,
            accepted_row_count=accepted_row_count,
            canonical_row_count=canonical_row_count,
            legacy_row_count=legacy_row_count,
            accepted_content_hash=accepted_content_hash,
            recoverable_landing_batch_id=recoverable_landing_batch_id,
            recoverable_landing_payload_hash=recoverable_landing_payload_hash,
            unresolved_landing_batch_ids=unresolved_landing_batch_ids,
        )

    if canonical_row_count != accepted_row_count:
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.CANONICAL_COUNT_MISMATCH,
                f"accepted row_count={accepted_row_count} canonical rows={canonical_row_count}",
                accepted_value=accepted_row_count,
                legacy_value=canonical_row_count,
            )
        )

    if not landing_payload_rows:
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.ACCEPTED_CONTENT_HASH_MISMATCH,
                "accepted batch has no retained landing payload to prove content",
            )
        )
    else:
        try:
            source_rows = _landing_business_rows(landing_payload_rows, partition)
            source_hash = canonical_content_hash(source_rows)
        except (InvalidOperation, ValueError, TypeError) as exc:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.ACCEPTED_CONTENT_HASH_MISMATCH,
                    f"accepted landing content is invalid: {str(exc)[:300]}",
                )
            )
        else:
            evidence_hashes = [str(accepted[7])]
            if batch is not None:
                evidence_hashes.append(str(batch[8]))
            if any(value != source_hash for value in evidence_hashes):
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTED_CONTENT_HASH_MISMATCH,
                        "accepted landing hash contradicts pointer or ingest evidence",
                        accepted_value=tuple(evidence_hashes),
                        legacy_value=source_hash,
                    )
                )
            source_issue = _canonical_source_issue(source_rows, canonical_rows)
            if source_issue is not None:
                issues.append(source_issue)

    for row in canonical_rows:
        if str(row["ingest_batch_id"]) != accepted_batch_id:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_BATCH_MISMATCH,
                    "canonical row does not belong to the accepted batch",
                    grain=_grain(row),
                    accepted_value=accepted_batch_id,
                    legacy_value=str(row["ingest_batch_id"]),
                )
            )
        expected_evidence = (str(accepted[3]), str(accepted[5]))
        actual_evidence = (str(row["contract_version"]), str(row["config_hash"]))
        if actual_evidence != expected_evidence:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_EVIDENCE_MISMATCH,
                    "canonical row contract/config differs from accepted pointer",
                    grain=_grain(row),
                    accepted_value=expected_evidence,
                    legacy_value=actual_evidence,
                )
            )

    issues.extend(_business_row_issues(canonical_rows, legacy_rows))

    return _report(
        partition,
        issues,
        accepted_batch_id=accepted_batch_id,
        accepted_row_count=accepted_row_count,
        canonical_row_count=canonical_row_count,
        legacy_row_count=legacy_row_count,
        accepted_content_hash=accepted_content_hash,
        recoverable_landing_batch_id=recoverable_landing_batch_id,
        recoverable_landing_payload_hash=recoverable_landing_payload_hash,
        unresolved_landing_batch_ids=unresolved_landing_batch_ids,
    )


__all__ = ["_reconcile_margin_partition_snapshot"]
