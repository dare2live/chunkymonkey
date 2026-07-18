"""Pure planning and execution policy for bounded margin-history migration.

The module deliberately owns no database, provider, writer, or CLI behavior.
Callers supply the trading-calendar window, read-only reconciliation evidence,
and a one-partition executor.  This keeps selection/checkpoint semantics usable
without creating another source of Tier0 truth.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from services.data_sources.availability import SyncWindowError

from services.data_sources.margin_legacy_reconcile import (
    HISTORY_REPAIRABLE_CODES,
    MarginReconcileCode,
    MarginReconcileReport,
    MarginReconcileStatus,
)
from services.data_sources.margin_history_contract import (
    MarginHistoryAcceptedEvidence,
    MarginHistoryCheckpoint,
    MarginHistoryCheckpointKind,
    MarginHistoryFailure,
    MarginHistoryPartitionOutcome,
    MarginHistoryPlan,
    MarginHistoryRequest,
    MarginHistoryResult,
    history_evidence_hash,
)
from services.data_sources.runtime_limits import fetch_socket_timeout_seconds


def history_replay_cap(spec: dict[str, Any]) -> int:
    """Return the mandatory per-run resource ceiling for history migration."""

    policy = spec.get("history_replay")
    if not isinstance(policy, dict):
        raise SyncWindowError(
            "formal margin backfill requires history_replay configuration"
        )
    cap = policy.get("max_partitions_per_run")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise SyncWindowError(
            "history_replay.max_partitions_per_run must be a positive integer"
        )
    return cap


def _request_bound(value: Any, field: str) -> str:
    if value is None:
        raise SyncWindowError(
            f"formal margin backfill requires explicit --{field} YYYYMMDD"
        )
    compact = str(value).strip()
    if len(compact) != 8 or not compact.isdigit():
        raise SyncWindowError(
            f"formal margin backfill --{field} must use YYYYMMDD"
        )
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise SyncWindowError(
            f"formal margin backfill --{field} must use a valid YYYYMMDD date"
        ) from exc
    return compact


def validate_margin_history_request(
    domain: str,
    spec: dict[str, Any],
    *,
    start: Any,
    end: Any,
    max_dates: Any,
) -> MarginHistoryRequest:
    """Validate the static request shape before locks, adapters, or databases."""

    if domain != "margin" or str(spec.get("batch_mode")) != "by_trade_date":
        raise SyncWindowError(
            "formal margin backfill is restricted to single domain=margin"
        )
    try:
        fetch_socket_timeout_seconds(spec)
    except ValueError as exc:
        raise SyncWindowError(
            f"formal margin backfill provider timeout invalid: {exc}"
        ) from exc
    start_date = _request_bound(start, "start")
    end_date = _request_bound(end, "end")
    if start_date > end_date:
        raise SyncWindowError(
            f"formal margin backfill start={start_date} exceeds end={end_date}"
        )
    data_start = _request_bound(spec.get("data_start"), "data_start")
    if start_date < data_start:
        raise SyncWindowError(
            f"formal margin backfill start={start_date} precedes data_start={data_start}"
        )
    if isinstance(max_dates, bool) or not isinstance(max_dates, int):
        raise SyncWindowError(
            "formal margin backfill requires explicit --max-dates positive integer"
        )
    if max_dates <= 0:
        raise SyncWindowError("formal margin backfill --max-dates must be positive")
    configured_cap = history_replay_cap(spec)
    if max_dates > configured_cap:
        raise SyncWindowError(
            f"--max-dates={max_dates} exceeds "
            "history_replay.max_partitions_per_run="
            f"{configured_cap}"
        )
    return MarginHistoryRequest(start_date, end_date, max_dates)


def prove_margin_history_sessions(
    request: MarginHistoryRequest,
    sessions: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    """Prove both explicit bounds against the trading-calendar truth."""

    normalized_values: list[str] = []
    for value in sessions:
        compact = str(value or "").replace("-", "")
        try:
            normalized_values.append(_partition(compact, label="trading session"))
        except ValueError as exc:
            raise SyncWindowError(str(exc)) from exc
    if len(normalized_values) != len(set(normalized_values)):
        raise SyncWindowError(
            "formal margin backfill calendar contains duplicate trading sessions"
        )
    normalized = tuple(sorted(
        day
        for day in normalized_values
        if request.start <= day <= request.end
    ))
    if not normalized:
        raise SyncWindowError(
            "formal margin backfill window contains zero trading sessions"
        )
    session_set = set(normalized)
    if request.start not in session_set:
        raise SyncWindowError(
            f"formal margin backfill start={request.start} is not a trading session"
        )
    if request.end not in session_set:
        raise SyncWindowError(
            f"formal margin backfill end={request.end} is not a trading session"
        )
    return normalized


def _partition(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError(f"{label} must use YYYYMMDD")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYYMMDD") from exc
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"{label} must use YYYYMMDD")
    return value


def _blocked_checkpoint(
    partition_value: str,
    *,
    issue_codes: tuple[str, ...],
    accepted_batch_id: str | None = None,
    accepted_row_count: int | None = None,
    accepted_content_hash: str | None = None,
    recoverable_landing_batch_id: str | None = None,
    recoverable_landing_payload_hash: str | None = None,
    unresolved_landing_batch_ids: tuple[str, ...] = (),
) -> MarginHistoryCheckpoint:
    return MarginHistoryCheckpoint(
        partition_value=partition_value,
        kind=MarginHistoryCheckpointKind.BLOCKED,
        accepted_batch_id=accepted_batch_id,
        accepted_row_count=accepted_row_count,
        issue_codes=issue_codes,
        accepted_content_hash=accepted_content_hash,
        recoverable_landing_batch_id=recoverable_landing_batch_id,
        recoverable_landing_payload_hash=recoverable_landing_payload_hash,
        unresolved_landing_batch_ids=unresolved_landing_batch_ids,
    )


def _classify_report(
    partition_value: str,
    report: MarginReconcileReport | None,
    *,
    dataset_id: str,
) -> MarginHistoryCheckpoint:
    if report is None:
        return _blocked_checkpoint(
            partition_value, issue_codes=("RECONCILE_REPORT_MISSING",)
        )
    codes = tuple(sorted({issue.code.value for issue in report.issues}))
    if report.dataset_id != dataset_id or report.partition_value != partition_value:
        return _blocked_checkpoint(
            partition_value,
            issue_codes=("RECONCILE_REPORT_SCOPE_MISMATCH", *codes),
            accepted_batch_id=report.accepted_batch_id,
            accepted_row_count=report.accepted_row_count,
        )
    accepted_id = str(report.accepted_batch_id or "").strip() or None
    accepted_rows = report.accepted_row_count
    accepted_hash = str(report.accepted_content_hash or "").strip() or None
    recovery_id = str(report.recoverable_landing_batch_id or "").strip() or None
    recovery_payload_hash = (
        str(report.recoverable_landing_payload_hash or "").strip() or None
    )
    unresolved_ids = tuple(
        sorted(str(value).strip() for value in report.unresolved_landing_batch_ids)
    )
    landing_metadata_valid = (
        recovery_id is None
        and recovery_payload_hash is None
        and not unresolved_ids
    ) or (
        recovery_id is not None
        and recovery_payload_hash is not None
        and unresolved_ids == (recovery_id,)
    )
    has_unresolved_landing = (
        MarginReconcileCode.UNRESOLVED_LANDING.value in codes
    )
    if (
        not landing_metadata_valid
        or has_unresolved_landing != bool(unresolved_ids)
    ):
        return _blocked_checkpoint(
            partition_value,
            issue_codes=("LANDING_EVIDENCE_CONTRADICTION", *codes),
            accepted_batch_id=accepted_id,
            accepted_row_count=accepted_rows,
            accepted_content_hash=accepted_hash,
            recoverable_landing_batch_id=recovery_id,
            recoverable_landing_payload_hash=recovery_payload_hash,
            unresolved_landing_batch_ids=unresolved_ids,
        )
    accepted_rows_valid = (
        not isinstance(accepted_rows, bool)
        and isinstance(accepted_rows, int)
        and accepted_rows > 0
    )
    if report.status is MarginReconcileStatus.PARITY:
        if (
            accepted_id is None
            or not accepted_rows_valid
            or accepted_hash is None
            or codes
        ):
            return _blocked_checkpoint(
                partition_value,
                issue_codes=("PARITY_EVIDENCE_CONTRADICTION", *codes),
                accepted_batch_id=accepted_id,
                accepted_row_count=accepted_rows,
                accepted_content_hash=accepted_hash,
                recoverable_landing_batch_id=recovery_id,
                recoverable_landing_payload_hash=recovery_payload_hash,
                unresolved_landing_batch_ids=unresolved_ids,
            )
        return MarginHistoryCheckpoint(
            partition_value=partition_value,
            kind=MarginHistoryCheckpointKind.SKIP,
            accepted_batch_id=accepted_id,
            accepted_row_count=accepted_rows,
            issue_codes=(),
            accepted_content_hash=accepted_hash,
        )
    issue_code_set = {issue.code for issue in report.issues}
    action_codes = issue_code_set - {MarginReconcileCode.UNRESOLVED_LANDING}
    if (
        accepted_id is None
        and report.status is MarginReconcileStatus.FAILED
        and action_codes == {MarginReconcileCode.ACCEPTED_PARTITION_MISSING}
        and (
            not has_unresolved_landing
            or recovery_id is not None
        )
    ):
        return MarginHistoryCheckpoint(
            partition_value=partition_value,
            kind=MarginHistoryCheckpointKind.SELECTED,
            accepted_batch_id=None,
            accepted_row_count=None,
            issue_codes=codes,
            recoverable_landing_batch_id=recovery_id,
            recoverable_landing_payload_hash=recovery_payload_hash,
            unresolved_landing_batch_ids=unresolved_ids,
        )
    if (
        accepted_id is not None
        and accepted_rows_valid
        and accepted_hash is not None
        and report.status is MarginReconcileStatus.FAILED
        and action_codes
        and action_codes <= HISTORY_REPAIRABLE_CODES
        and (
            not has_unresolved_landing
            or recovery_id is not None
        )
    ):
        return MarginHistoryCheckpoint(
            partition_value=partition_value,
            kind=MarginHistoryCheckpointKind.REPAIR,
            accepted_batch_id=accepted_id,
            accepted_row_count=accepted_rows,
            issue_codes=codes,
            accepted_content_hash=accepted_hash,
            recoverable_landing_batch_id=recovery_id,
            recoverable_landing_payload_hash=recovery_payload_hash,
            unresolved_landing_batch_ids=unresolved_ids,
        )
    return _blocked_checkpoint(
        partition_value,
        issue_codes=("RECONCILE_EVIDENCE_CONTRADICTION", *codes),
        accepted_batch_id=accepted_id,
        accepted_row_count=accepted_rows,
        accepted_content_hash=accepted_hash,
        recoverable_landing_batch_id=recovery_id,
        recoverable_landing_payload_hash=recovery_payload_hash,
        unresolved_landing_batch_ids=unresolved_ids,
    )


def build_margin_history_plan(
    request: MarginHistoryRequest,
    *,
    configured_max_dates: int,
    trading_dates: tuple[str, ...],
    reconcile_reports: tuple[MarginReconcileReport, ...],
    dataset_id: str,
    contract_hash: str,
    config_hash: str,
) -> MarginHistoryPlan:
    """Classify a bounded, inclusive window without performing side effects."""
    start = _partition(request.start, label="start")
    end = _partition(request.end, label="end")
    if start > end:
        raise ValueError("start must not be after end")
    if (
        isinstance(request.max_dates, bool)
        or not isinstance(request.max_dates, int)
        or request.max_dates <= 0
    ):
        raise ValueError("max_dates must be a positive integer")
    if (
        isinstance(configured_max_dates, bool)
        or not isinstance(configured_max_dates, int)
        or configured_max_dates <= 0
    ):
        raise ValueError("configured_max_dates must be a positive integer")
    if request.max_dates > configured_max_dates:
        raise ValueError(
            "max_dates exceeds the configured history safety limit "
            f"({request.max_dates}>{configured_max_dates})"
        )
    for label, value in (
        ("dataset_id", dataset_id),
        ("contract_hash", contract_hash),
        ("config_hash", config_hash),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be non-empty")

    normalized_dates = tuple(
        _partition(value, label="trading date") for value in trading_dates
    )
    if len(normalized_dates) != len(set(normalized_dates)):
        raise ValueError("trading_dates contains duplicate partitions")
    all_trading_dates = tuple(sorted(normalized_dates))
    trading_set = set(all_trading_dates)
    if start not in trading_set:
        raise ValueError("start must be a trading date")
    if end not in trading_set:
        raise ValueError("end must be a trading date")
    window_dates = tuple(day for day in all_trading_dates if start <= day <= end)

    by_partition: dict[str, MarginReconcileReport] = {}
    duplicate_reports: set[str] = set()
    for report in reconcile_reports:
        raw_partition = str(getattr(report, "partition_value", "") or "")
        if raw_partition not in window_dates:
            raise ValueError(
                "reconcile report is outside the requested history window "
                f"partition={raw_partition!r}"
            )
        if raw_partition in by_partition:
            duplicate_reports.add(raw_partition)
        else:
            by_partition[raw_partition] = report

    checkpoints: list[MarginHistoryCheckpoint] = []
    for day in window_dates:
        if day in duplicate_reports:
            checkpoints.append(
                _blocked_checkpoint(
                    day, issue_codes=("RECONCILE_REPORT_DUPLICATE",)
                )
            )
        else:
            checkpoints.append(
                _classify_report(day, by_partition.get(day), dataset_id=dataset_id)
            )

    candidates = tuple(
        item.partition_value
        for item in checkpoints
        if item.kind
        in {MarginHistoryCheckpointKind.SELECTED, MarginHistoryCheckpointKind.REPAIR}
    )
    execution_dates = candidates[: request.max_dates]
    deferred_dates = candidates[request.max_dates :]
    return MarginHistoryPlan(
        request=request,
        configured_max_dates=configured_max_dates,
        dataset_id=dataset_id.strip(),
        contract_hash=contract_hash.strip(),
        config_hash=config_hash.strip(),
        window_dates=window_dates,
        checkpoints=tuple(checkpoints),
        execution_dates=execution_dates,
        deferred_dates=deferred_dates,
    )


def _skipped_evidence(
    plan: MarginHistoryPlan,
) -> tuple[MarginHistoryAcceptedEvidence, ...]:
    return tuple(
        MarginHistoryAcceptedEvidence(
            partition_value=item.partition_value,
            batch_id=str(item.accepted_batch_id),
            row_count=int(item.accepted_row_count),
            content_hash=str(item.accepted_content_hash),
        )
        for item in plan.checkpoints
        if item.kind is MarginHistoryCheckpointKind.SKIP
    )


def _next_start(*date_groups: tuple[str, ...]) -> str | None:
    dates = [value for group in date_groups for value in group]
    return min(dates) if dates else None


def execute_margin_history_plan(
    plan: MarginHistoryPlan,
    executor: Callable[[str], MarginHistoryPartitionOutcome],
    *,
    propagate_exceptions: tuple[type[BaseException], ...] = (),
) -> MarginHistoryResult:
    """Run at most the planned partitions and stop at the first failure."""
    skipped_evidence = _skipped_evidence(plan)
    if plan.blocked_dates:
        blocked = plan.blocked_dates[0]
        checkpoint = next(
            item for item in plan.checkpoints if item.partition_value == blocked
        )
        failure = MarginHistoryFailure(
            partition_value=blocked,
            code="checkpoint_blocked",
            detail=",".join(checkpoint.issue_codes),
            evidence_hash=history_evidence_hash(
                {"issue_codes": list(checkpoint.issue_codes)}
            ),
        )
        deferred = tuple(
            day
            for day in plan.window_dates
            if day not in plan.skipped_dates and day != blocked
        )
        return MarginHistoryResult(
            dataset_id=plan.dataset_id,
            contract_hash=plan.contract_hash,
            config_hash=plan.config_hash,
            plan_hash=plan.plan_hash,
            window_dates=plan.window_dates,
            attempted_dates=(),
            skipped_dates=plan.skipped_dates,
            accepted_evidence=skipped_evidence,
            failures=(failure,),
            deferred_dates=deferred,
            next_start=_next_start(deferred, (blocked,)),
            blocked_partition=blocked,
        )

    attempted: list[str] = []
    accepted: list[MarginHistoryAcceptedEvidence] = list(skipped_evidence)
    failures: list[MarginHistoryFailure] = []
    unattempted: tuple[str, ...] = ()
    for index, partition_value in enumerate(plan.execution_dates):
        attempted.append(partition_value)
        try:
            outcome = executor(partition_value)
        except Exception as exc:
            if not isinstance(exc, propagate_exceptions):
                # Unknown programming/runtime failures are not retryable data
                # outcomes and must retain their traceback for adjudication.
                raise
            failures.append(
                MarginHistoryFailure(
                    partition_value=partition_value,
                    code="account_halt",
                    detail=type(exc).__name__,
                    evidence_hash=history_evidence_hash(
                        {"halt_type": type(exc).__name__}
                    ),
                )
            )
            unattempted = plan.execution_dates[index + 1 :]
            deferred = tuple(sorted((*unattempted, *plan.deferred_dates)))
            partial = MarginHistoryResult(
                dataset_id=plan.dataset_id,
                contract_hash=plan.contract_hash,
                config_hash=plan.config_hash,
                plan_hash=plan.plan_hash,
                window_dates=plan.window_dates,
                attempted_dates=tuple(attempted),
                skipped_dates=plan.skipped_dates,
                accepted_evidence=tuple(
                    sorted(accepted, key=lambda item: item.partition_value)
                ),
                failures=tuple(failures),
                deferred_dates=deferred,
                next_start=partition_value,
                blocked_partition=None,
            )
            setattr(exc, "history_result", partial)
            raise
        if not isinstance(outcome, MarginHistoryPartitionOutcome):
            failures.append(
                MarginHistoryFailure(
                    partition_value=partition_value,
                    code="executor_contradiction",
                    detail="executor returned an untyped partition outcome",
                    evidence_hash=history_evidence_hash(
                        {"actual_type": type(outcome).__name__}
                    ),
                )
            )
            unattempted = plan.execution_dates[index + 1 :]
            break
        if outcome.partition_value != partition_value:
            failures.append(
                MarginHistoryFailure(
                    partition_value=partition_value,
                    code="executor_contradiction",
                    detail=(
                        "executor partition mismatch: "
                        f"expected={partition_value} actual={outcome.partition_value}"
                    ),
                    evidence_hash=history_evidence_hash(
                        {
                            "expected_partition": partition_value,
                            "actual_partition": outcome.partition_value,
                        }
                    ),
                )
            )
            unattempted = plan.execution_dates[index + 1 :]
            break
        if outcome.failure is not None:
            failure = outcome.failure
            if (
                failure.partition_value != partition_value
                or not str(failure.evidence_hash or "").strip()
            ):
                failure = MarginHistoryFailure(
                    partition_value=partition_value,
                    code="executor_contradiction",
                    detail=(
                        "executor failure evidence is incomplete: "
                        f"expected_partition={partition_value} "
                        f"actual_partition={failure.partition_value}"
                    ),
                    evidence_hash=history_evidence_hash(
                        {
                            "expected_partition": partition_value,
                            "actual_partition": failure.partition_value,
                            "has_evidence_hash": bool(failure.evidence_hash),
                        }
                    ),
                )
            failures.append(failure)
            unattempted = plan.execution_dates[index + 1 :]
            break
        evidence = outcome.accepted_evidence
        if (
            evidence is None
            or evidence.partition_value != partition_value
            or not str(evidence.batch_id or "").strip()
            or isinstance(evidence.row_count, bool)
            or not isinstance(evidence.row_count, int)
            or evidence.row_count <= 0
            or not str(evidence.content_hash or "").strip()
        ):
            failures.append(
                MarginHistoryFailure(
                    partition_value=partition_value,
                    code="executor_contradiction",
                    detail="executor returned invalid acceptance evidence",
                    evidence_hash=history_evidence_hash(
                        {
                            "partition_value": getattr(
                                evidence, "partition_value", None
                            ),
                            "batch_id": getattr(evidence, "batch_id", None),
                            "row_count": getattr(evidence, "row_count", None),
                            "content_hash": getattr(evidence, "content_hash", None),
                        }
                    ),
                )
            )
            unattempted = plan.execution_dates[index + 1 :]
            break
        accepted.append(evidence)

    deferred = tuple(sorted((*unattempted, *plan.deferred_dates)))
    failure_dates = tuple(item.partition_value for item in failures)
    next_start = _next_start(failure_dates, deferred)
    return MarginHistoryResult(
        dataset_id=plan.dataset_id,
        contract_hash=plan.contract_hash,
        config_hash=plan.config_hash,
        plan_hash=plan.plan_hash,
        window_dates=plan.window_dates,
        attempted_dates=tuple(attempted),
        skipped_dates=plan.skipped_dates,
        accepted_evidence=tuple(sorted(accepted, key=lambda item: item.partition_value)),
        failures=tuple(failures),
        deferred_dates=deferred,
        next_start=next_start,
        blocked_partition=None,
    )
