"""Rebuildable Ops projections derived only from accepted margin facts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_evidence import (
    MarginEvidenceLoadError,
    load_margin_evidence_snapshot,
)
from services.data_sources.margin_reconcile import _reconcile_margin_partitions_snapshot
from services.data_sources.margin_schema import DATASET_ID, INGEST_BATCH_TABLE
from services.data_sources.margin_state import _accepted_margin_partitions_snapshot
from services.source_watermarks import (
    ensure_source_watermark_schema,
    record_source_failure,
    resolve_source_failures,
    upsert_watermark,
)


DATA_DOMAIN = "sync:margin"
SOURCE_TIER = 2
GAP_FAILURE_TYPE = "accepted_partition_gap"
RECONCILE_FAILURE_TYPE = "accepted_shadow_reconcile_failed"
FAILURE_ERROR_LIMIT = 1000


class MarginProjectionError(RuntimeError):
    """Accepted facts cannot be projected without inventing or hiding state."""


@dataclass(frozen=True)
class MarginReconcileProjectionFailure:
    partition_value: str
    issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class MarginProjectionResult:
    frontier: str | None
    row_count: int
    accepted_at: Any | None
    expected: tuple[str, ...]
    accepted: tuple[str, ...]
    missing: tuple[str, ...]
    reconcile_failures: tuple[MarginReconcileProjectionFailure, ...]


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    try:
        if len(compact) != 8 or not compact.isdigit():
            raise ValueError
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise MarginProjectionError(f"invalid expected margin partition={value!r}") from exc
    return compact


def _latest_failure_evidence(raw_conn, missing: tuple[str, ...]) -> list[dict[str, Any]]:
    if not raw_conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        [INGEST_BATCH_TABLE],
    ).fetchone():
        return [
            {"partition": partition, "state": "NO_INGEST_BATCH"}
            for partition in missing[:20]
        ]
    evidence: list[dict[str, Any]] = []
    for partition in missing[:20]:
        row = raw_conn.execute(
            f"""
            SELECT batch_id, status, rejection_code, rejection_detail,
                   fragment_outcomes_json, landed_at
              FROM {INGEST_BATCH_TABLE}
             WHERE dataset_id = ? AND partition_value = ?
             ORDER BY landed_at DESC, batch_id DESC
             LIMIT 1
            """,
            [DATASET_ID, partition],
        ).fetchone()
        if row is None:
            evidence.append({"partition": partition, "state": "NO_INGEST_BATCH"})
            continue
        evidence.append({
            "partition": partition,
            "batch_id": str(row[0]),
            "state": str(row[1]),
            "rejection_code": str(row[2]) if row[2] is not None else None,
            "rejection_detail": str(row[3])[:160] if row[3] is not None else None,
            "fragment_outcomes": str(row[4])[:240] if row[4] is not None else None,
            "landed_at": str(row[5]) if row[5] is not None else None,
        })
    return evidence


def _gap_failure_json(
    raw_conn,
    *,
    coverage_start: str,
    expected: tuple[str, ...],
    accepted: tuple[str, ...],
    missing: tuple[str, ...],
) -> str:
    """Return parseable queue evidence within record_source_failure's limit."""
    missing_sample = list(missing[:20])
    evidence = _latest_failure_evidence(raw_conn, missing)
    evidence_compacted = False
    while True:
        payload = {
            "dataset_id": DATASET_ID,
            "coverage_start": coverage_start,
            "expected_count": len(expected),
            "accepted_count": len(accepted),
            "missing_count": len(missing),
            "earliest_missing": missing[0],
            "latest_missing": missing[-1],
            "missing_sample": missing_sample,
            "latest_ingest_evidence": evidence,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded) <= FAILURE_ERROR_LIMIT:
            return encoded
        if len(evidence) > 1:
            no_batch_index = next(
                (
                    index
                    for index in range(len(evidence) - 1, -1, -1)
                    if evidence[index].get("state") == "NO_INGEST_BATCH"
                ),
                len(evidence) - 1,
            )
            evidence.pop(no_batch_index)
            continue
        if evidence and not evidence_compacted:
            evidence = [
                {
                    key: evidence[0].get(key)
                    for key in (
                        "partition",
                        "batch_id",
                        "state",
                        "rejection_code",
                        "landed_at",
                    )
                    if key in evidence[0]
                }
            ]
            evidence_compacted = True
            continue
        if len(missing_sample) > 1:
            missing_sample.pop()
            continue
        raise MarginProjectionError("accepted gap evidence exceeds failure queue limit")


def _reconcile_failure_json(
    failures: tuple[MarginReconcileProjectionFailure, ...],
) -> str:
    sample = [
        {
            "partition": failure.partition_value,
            "issue_codes": list(failure.issue_codes),
        }
        for failure in failures[:20]
    ]
    while True:
        payload = {
            "dataset_id": DATASET_ID,
            "failure_count": len(failures),
            "earliest_partition": failures[0].partition_value,
            "latest_partition": failures[-1].partition_value,
            "failures": sample,
            "recovery": "replay retained landing into legacy shadow; do not refetch provider",
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded) <= FAILURE_ERROR_LIMIT:
            return encoded
        if sample:
            sample.pop()
            continue
        raise MarginProjectionError("accepted shadow reconcile evidence exceeds failure queue limit")


def derive_margin_accepted_state(
    raw_conn,
    expected_partitions: Iterable[str],
    *,
    contract=None,
) -> MarginProjectionResult:
    """Derive coverage and shadow parity without touching the Ops database."""

    contract = contract or load_dataset_contract("margin")
    expected = tuple(sorted({
        partition
        for value in expected_partitions
        if (partition := _partition(value)) >= contract.coverage_start
    }))
    try:
        evidence_snapshot = load_margin_evidence_snapshot(
            raw_conn,
            contract=contract,
            include_legacy=True,
        )
    except MarginEvidenceLoadError as exc:
        raise MarginProjectionError(str(exc)) from exc
    accepted_items = _accepted_margin_partitions_snapshot(
        raw_conn,
        contract=contract,
        evidence_snapshot=evidence_snapshot,
        partition_value=evidence_snapshot.partition_value,
    )
    accepted_by_partition = {
        item.partition_value: item for item in accepted_items
    }
    expected_set = set(expected)
    unexpected = sorted(
        partition
        for partition in accepted_by_partition
        if partition >= contract.coverage_start and partition not in expected_set
    )
    if unexpected:
        raise MarginProjectionError(
            f"accepted partitions outside eligible projection window={unexpected[:20]}"
        )
    accepted = tuple(
        partition for partition in expected if partition in accepted_by_partition
    )
    missing = tuple(
        partition for partition in expected if partition not in accepted_by_partition
    )
    latest = accepted_by_partition[max(accepted)] if accepted else None
    reconcile_failures: list[MarginReconcileProjectionFailure] = []
    reports = _reconcile_margin_partitions_snapshot(
        raw_conn,
        tuple(accepted),
        contract=contract,
        snapshot=evidence_snapshot,
    )
    for report in reports:
        partition = report.partition_value
        if report.ok:
            continue
        issue_codes = tuple(sorted({issue.code.value for issue in report.issues}))
        reconcile_failures.append(
            MarginReconcileProjectionFailure(
                partition_value=partition,
                issue_codes=issue_codes or ("UNKNOWN_RECONCILE_FAILURE",),
            )
        )
    return MarginProjectionResult(
        frontier=latest.partition_value if latest is not None else None,
        row_count=latest.row_count if latest is not None else 0,
        accepted_at=latest.accepted_at if latest is not None else None,
        expected=expected,
        accepted=accepted,
        missing=missing,
        reconcile_failures=tuple(reconcile_failures),
    )


def project_margin_accepted_state(
    raw_conn,
    ops_conn,
    expected_partitions: Iterable[str],
    *,
    contract=None,
    provider_succeeded: bool = False,
    quota_error: str | None = None,
) -> MarginProjectionResult:
    """Replace watermark/failure projections from current accepted evidence.

    The raw and Ops databases cannot share one transaction; accepted raw facts
    remain authoritative.  Within the Ops database, however, watermark and
    failure state are one projection and must commit atomically so a crash
    cannot expose a new healthy watermark without its accepted-gap evidence.
    """
    contract = contract or load_dataset_contract("margin")
    result = derive_margin_accepted_state(
        raw_conn, expected_partitions, contract=contract
    )
    expected = result.expected
    accepted = result.accepted
    missing = result.missing

    ensure_source_watermark_schema(ops_conn)
    ops_conn.execute("BEGIN TRANSACTION")
    try:
        # The watermark key includes source and tier, so an upsert alone cannot
        # remove legacy keys for the same logical domain.  Replace the complete
        # domain projection inside the same transaction as gap/quota evidence.
        ops_conn.execute(
            "DELETE FROM mart_data_source_watermark WHERE data_domain = ?",
            [DATA_DOMAIN],
        )
        upsert_watermark(
            ops_conn,
            {
                "data_domain": DATA_DOMAIN,
                "source_name": contract.source,
                "source_tier": SOURCE_TIER,
                "last_success_at": result.accepted_at,
                "last_data_date": result.frontier,
                "row_count": result.row_count,
                "parser_version": f"margin_accepted_contract_{contract.contract_version}",
            },
        )

        if missing:
            record_source_failure(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                source_tier=SOURCE_TIER,
                error_type=GAP_FAILURE_TYPE,
                last_error=_gap_failure_json(
                    raw_conn,
                    coverage_start=contract.coverage_start,
                    expected=expected,
                    accepted=accepted,
                    missing=missing,
                ),
            )
        else:
            resolve_source_failures(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                error_type=GAP_FAILURE_TYPE,
            )
            # The pre-contract runtime queue is no longer a truth source.  Once
            # accepted coverage is complete, close its stale margin-only residue.
            resolve_source_failures(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                error_type="sync_batch_failed",
            )

        if result.reconcile_failures:
            record_source_failure(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                source_tier=SOURCE_TIER,
                error_type=RECONCILE_FAILURE_TYPE,
                last_error=_reconcile_failure_json(result.reconcile_failures),
            )
        else:
            resolve_source_failures(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                error_type=RECONCILE_FAILURE_TYPE,
            )

        if quota_error:
            record_source_failure(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                source_tier=SOURCE_TIER,
                error_type="sync_quota_halt",
                last_error=quota_error,
            )
        elif provider_succeeded:
            resolve_source_failures(
                ops_conn,
                data_domain=DATA_DOMAIN,
                source_name=contract.source,
                error_type="sync_quota_halt",
            )
        ops_conn.execute("COMMIT")
    except BaseException:
        ops_conn.execute("ROLLBACK")
        raise
    return result


__all__ = [
    "GAP_FAILURE_TYPE",
    "RECONCILE_FAILURE_TYPE",
    "MarginProjectionError",
    "MarginProjectionResult",
    "MarginReconcileProjectionFailure",
    "derive_margin_accepted_state",
    "project_margin_accepted_state",
]
