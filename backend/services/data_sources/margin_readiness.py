"""Live accepted-state and legacy-parity readiness for formal margin data."""
from __future__ import annotations

from typing import Iterable

from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_evidence import (
    MarginEvidenceLoadError,
    load_margin_evidence_snapshot,
)
from services.data_sources.margin_reconcile import (
    _reconcile_margin_partitions_snapshot,
)
from services.data_sources.margin_state import (
    MarginReadiness,
    MarginReconcileFailure,
    MarginStateError,
    _load_margin_accepted_state_snapshot,
    _partition,
)


def evaluate_margin_readiness(
    conn,
    expected_partitions: Iterable[str],
    *,
    contract=None,
    eligible_end: str | None,
    eligibility_reason: str,
    reconcile: bool = True,
) -> MarginReadiness:
    """Evaluate live coverage and optional parity from one in-call snapshot."""

    contract = contract or load_dataset_contract("margin")
    expected = tuple(sorted({
        partition
        for value in expected_partitions
        if (partition := _partition(value)) >= contract.coverage_start
    }))
    try:
        evidence_snapshot = load_margin_evidence_snapshot(
            conn,
            contract=contract,
            include_legacy=reconcile,
        )
    except MarginEvidenceLoadError as exc:
        raise MarginStateError(str(exc)) from exc
    state = _load_margin_accepted_state_snapshot(
        conn,
        contract=contract,
        evidence_snapshot=evidence_snapshot,
    )
    expected_set = set(expected)
    accepted_dates = state.dates
    missing = tuple(partition for partition in expected if partition not in accepted_dates)
    unexpected = tuple(sorted(
        partition
        for partition in accepted_dates
        if partition >= contract.coverage_start and partition not in expected_set
    ))
    failures: list[MarginReconcileFailure] = []
    if reconcile:
        reports = _reconcile_margin_partitions_snapshot(
            conn,
            tuple(item.partition_value for item in state.partitions),
            contract=contract,
            snapshot=evidence_snapshot,
        )
        for item, report in zip(state.partitions, reports, strict=True):
            if not report.ok:
                failures.append(
                    MarginReconcileFailure(
                        partition_value=item.partition_value,
                        issue_codes=tuple(sorted({
                            issue.code.value for issue in report.issues
                        })),
                    )
                )
    return MarginReadiness(
        eligible_end=eligible_end,
        eligibility_reason=eligibility_reason,
        expected=expected,
        accepted_state=state,
        missing=missing,
        unexpected=unexpected,
        reconcile_failures=tuple(failures),
    )


__all__ = ["evaluate_margin_readiness"]
