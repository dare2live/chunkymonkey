"""Authoritative proof orchestration for margin-to-legacy reconciliation."""
from __future__ import annotations

from typing import Any, Iterable

from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_evidence import (
    MarginEvidenceLoadError,
    MarginEvidenceSnapshot,
    load_margin_evidence_snapshot,
)
from services.data_sources.margin_legacy_reconcile import (
    MarginReconcileCode,
    MarginReconcileIssue,
    MarginReconcileReport,
    MarginReconcileStatus,
    _partition,
    _report,
    _snapshot_schema_issues,
    _with_issue,
)
from services.data_sources.margin_reconcile_snapshot import (
    _reconcile_margin_partition_snapshot,
)
from services.data_sources.margin_state import (
    MarginStateError,
    _prove_margin_partitions_snapshot,
)


def _invalid_partition_report(value: Any) -> MarginReconcileReport:
    return _report(
        str(value or ""),
        (
            MarginReconcileIssue(
                MarginReconcileCode.INVALID_PARTITION,
                f"expected a real YYYYMMDD partition, got {value!r}",
            ),
        ),
    )


def _query_error_reports(
    requested: tuple[Any, ...], detail: str
) -> tuple[MarginReconcileReport, ...]:
    return tuple(
        _invalid_partition_report(value)
        if (partition := _partition(value)) is None
        else _report(
            partition,
            (MarginReconcileIssue(MarginReconcileCode.QUERY_ERROR, detail),),
        )
        for value in requested
    )


def _snapshot_error(
    snapshot: MarginEvidenceSnapshot,
    contract,
    partitions: tuple[str, ...],
) -> str | None:
    if snapshot.contract is not contract:
        return "margin evidence snapshot contract identity drift"
    if not snapshot.include_legacy:
        return "margin evidence snapshot excludes the legacy comparison surface"
    if snapshot.partition_value is not None and any(
        partition != snapshot.partition_value for partition in partitions
    ):
        return "margin evidence snapshot declared scope does not match request"
    return snapshot.scope_error()


def _scope_evidence(
    snapshot: MarginEvidenceSnapshot,
    partitions: tuple[str, ...],
) -> dict[str, MarginEvidenceSnapshot]:
    grouped = {
        partition: {
            "accepted_rows": [],
            "batch_rows": [],
            "landing_rows": [],
            "canonical_rows": [],
            "legacy_rows": [],
        }
        for partition in set(partitions)
    }
    for row in snapshot.accepted_rows:
        if (bucket := grouped.get(str(row[1]))) is not None:
            bucket["accepted_rows"].append(row)
    for field in ("batch_rows", "landing_rows", "canonical_rows", "legacy_rows"):
        for row in getattr(snapshot, field):
            if (bucket := grouped.get(str(row[0]))) is not None:
                bucket[field].append(row)
    return {
        partition: MarginEvidenceSnapshot(
            contract=snapshot.contract,
            partition_value=partition,
            include_legacy=snapshot.include_legacy,
            schemas=snapshot.schemas,
            accepted_rows=tuple(rows["accepted_rows"]),
            batch_rows=tuple(rows["batch_rows"]),
            landing_rows=tuple(rows["landing_rows"]),
            canonical_rows=tuple(rows["canonical_rows"]),
            legacy_rows=tuple(rows["legacy_rows"]),
            load_error=snapshot.load_error,
        )
        for partition, rows in grouped.items()
    }


def _partition_proof_errors(
    conn,
    *,
    contract,
    snapshot: MarginEvidenceSnapshot,
    partitions: tuple[str, ...],
) -> tuple[dict[str, list[str]], str | None]:
    local: dict[str, list[str]] = {}
    try:
        proofs = _prove_margin_partitions_snapshot(
            conn,
            contract=contract,
            evidence_snapshot=snapshot,
            partition_value=snapshot.partition_value,
        )
    except MarginStateError as exc:
        return local, str(exc)

    for failure in proofs.failures:
        local.setdefault(failure.partition_value, []).append(failure.detail)
    expected: dict[str, list[tuple[str, int, str]]] = {}
    actual: dict[str, list[tuple[str, int, str]]] = {}
    try:
        for row in snapshot.accepted_rows:
            expected.setdefault(str(row[1]), []).append(
                (str(row[2]), int(row[6]), str(row[7]))
            )
        for item in proofs.accepted:
            actual.setdefault(item.partition_value, []).append(
                (str(item.batch_id), int(item.row_count), str(item.content_hash))
            )
    except (IndexError, TypeError, ValueError) as exc:
        return local, f"malformed accepted proof evidence: {exc}"
    for partition in set(partitions) - set(local):
        if sorted(actual.get(partition, ())) != sorted(expected.get(partition, ())):
            local.setdefault(partition, []).append(
                "accepted proof does not match the evidence snapshot"
            )
    return local, None


def _reconcile_margin_partitions_snapshot(
    conn,
    requested: tuple[Any, ...],
    *,
    contract,
    snapshot: MarginEvidenceSnapshot,
) -> tuple[MarginReconcileReport, ...]:
    valid = tuple(
        partition
        for value in requested
        if (partition := _partition(value)) is not None
    )
    if (detail := _snapshot_error(snapshot, contract, valid)) is not None:
        return _query_error_reports(requested, detail)

    scoped = _scope_evidence(snapshot, valid)
    local_errors: dict[str, list[str]] = {}
    global_error: str | None = None
    schema_issues = _snapshot_schema_issues(
        snapshot, contract.compatibility_table
    )
    if not schema_issues and not snapshot.load_error:
        local_errors, global_error = _partition_proof_errors(
            conn,
            contract=contract,
            snapshot=snapshot,
            partitions=valid,
        )

    reports: list[MarginReconcileReport] = []
    for value in requested:
        partition = _partition(value)
        if partition is None:
            reports.append(_invalid_partition_report(value))
            continue
        report = _reconcile_margin_partition_snapshot(
            partition,
            contract=contract,
            evidence_snapshot=scoped[partition],
        )
        formal_error = global_error
        if formal_error is None and partition in local_errors:
            formal_error = "; ".join(local_errors[partition])
        if formal_error is not None:
            report = _with_issue(
                report,
                MarginReconcileIssue(
                    MarginReconcileCode.FORMAL_EVIDENCE_INVALID,
                    formal_error,
                ),
            )
        reports.append(report)
    return tuple(reports)


def reconcile_margin_partitions(
    conn,
    partition_values: Iterable[Any],
    *,
    contract=None,
) -> tuple[MarginReconcileReport, ...]:
    """Load live evidence and reconcile without accepting injected snapshots."""

    requested = tuple(partition_values)
    if not requested:
        return ()
    valid = tuple(
        partition
        for value in requested
        if (partition := _partition(value)) is not None
    )
    if not valid:
        return tuple(_invalid_partition_report(value) for value in requested)
    contract = contract or load_dataset_contract("margin")
    exact_partition = next(iter(set(valid))) if len(set(valid)) == 1 else None
    try:
        snapshot = load_margin_evidence_snapshot(
            conn,
            contract=contract,
            partition_value=exact_partition,
            include_legacy=True,
        )
    except MarginEvidenceLoadError as exc:
        return _query_error_reports(requested, str(exc))
    return _reconcile_margin_partitions_snapshot(
        conn,
        requested,
        contract=contract,
        snapshot=snapshot,
    )


def reconcile_margin_partition(
    conn,
    partition_value: Any,
    *,
    contract=None,
) -> MarginReconcileReport:
    """Reconcile one partition through the same live-evidence boundary."""

    return reconcile_margin_partitions(
        conn,
        (partition_value,),
        contract=contract,
    )[0]


__all__ = [
    "MarginReconcileCode",
    "MarginReconcileIssue",
    "MarginReconcileReport",
    "MarginReconcileStatus",
    "reconcile_margin_partition",
    "reconcile_margin_partitions",
]
