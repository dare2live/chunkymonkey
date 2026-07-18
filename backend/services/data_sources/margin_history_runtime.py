"""Side-effectful adapter for bounded formal margin-history migration."""

from __future__ import annotations

import json
from typing import Any, Callable

from services.data_sources import margin_history as history
from services.data_sources.runtime_limits import apply_fetch_socket_timeout


def run_margin_history_domain(
    domain: str,
    spec: dict[str, Any],
    *,
    contract: Any,
    request: history.MarginHistoryRequest,
    eligibility: Any,
    target_conn_factory: Callable[[dict[str, Any]], Any],
    adapter_factory: Callable[[str], Any],
    trading_days: Callable[[str, str], list[str]],
    fetch_logical_batch: Callable[..., Any],
    quota_wall_classifier: Callable[[str], bool],
    ops_conn_factory: Callable[[], Any],
    authorization_error_type: type[BaseException],
    quota_error_type: type[BaseException],
) -> dict[str, Any]:
    """Own the bounded formal-history runtime outside the generic sync loop."""

    from services.data_sources import margin_ingest
    from services.data_sources import margin_history_ingest
    from services.data_sources.batch_integrity import BatchCompletenessError
    from services.data_sources.margin_acceptance import MarginAcceptanceError
    from services.data_sources.margin_reconcile import reconcile_margin_partitions

    sessions = history.prove_margin_history_sessions(
        request,
        trading_days(request.start, request.end),
    )
    conn = target_conn_factory(spec)
    adapter = None
    provider_accepted_dates: set[str] = set()
    projection = None
    try:
        reports = reconcile_margin_partitions(conn, sessions, contract=contract)
        plan = history.build_margin_history_plan(
            request,
            configured_max_dates=history.history_replay_cap(spec),
            trading_dates=sessions,
            reconcile_reports=reports,
            dataset_id=contract.dataset_id,
            contract_hash=contract.contract_hash,
            config_hash=contract.config_hash,
        )

        def execute_partition(partition: str) -> history.MarginHistoryPartitionOutcome:
            nonlocal adapter
            checkpoint = next(
                item
                for item in plan.checkpoints
                if item.partition_value == partition
            )
            fetched_from_provider = (
                checkpoint.recoverable_landing_batch_id is None
            )
            try:
                # Reject checkpoint drift before even constructing the provider
                # adapter.  The ingest seam repeats this proof immediately before
                # it consumes the checkpoint.
                margin_history_ingest.prove_history_execution_checkpoint(
                    conn,
                    partition,
                    contract=contract,
                    checkpoint=checkpoint,
                )
                if fetched_from_provider and adapter is None:
                    apply_fetch_socket_timeout(spec)
                    adapter = adapter_factory(spec["source"])
                ingest = margin_history_ingest.execute_history_partition(
                    conn,
                    adapter,
                    spec,
                    {str(spec.get("date_param") or "trade_date"): partition},
                    fetch_logical_batch=fetch_logical_batch,
                    quota_wall_classifier=quota_wall_classifier,
                    contract=contract,
                    checkpoint=checkpoint,
                )
            except (authorization_error_type, quota_error_type):
                raise
            except BatchCompletenessError as exc:
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="batch_incomplete",
                    detail=str(exc)[:500],
                    evidence_hash=history.history_evidence_hash(
                        {
                            "batch_id": getattr(exc, "batch_id", None),
                            "rejection_code": getattr(
                                exc, "rejection_code", type(exc).__name__
                            ),
                        }
                    ),
                )
            except MarginAcceptanceError as exc:
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="checkpoint_invalid",
                    detail=str(exc)[:500],
                    evidence_hash=history.history_evidence_hash(
                        {"error_type": type(exc).__name__, "detail": str(exc)}
                    ),
                )
            except margin_history_ingest.MarginHistoryCheckpointDrift as exc:
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="checkpoint_drift",
                    detail=str(exc)[:500],
                    evidence_hash=history.history_evidence_hash(exc.evidence),
                )
            except margin_ingest.MarginReconcileError as exc:
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="reconcile_failed",
                    detail=str(exc)[:500],
                    evidence_hash=history.history_evidence_hash(
                        {"error_type": type(exc).__name__, "detail": str(exc)}
                    ),
                )
            except TimeoutError as exc:
                batch_id = getattr(exc, "history_batch_id", None)
                if not str(batch_id or "").strip():
                    raise
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="provider_timeout",
                    detail=str(exc)[:500],
                    evidence_hash=history.history_evidence_hash(
                        {
                            "batch_id": batch_id,
                            "error_type": type(exc).__name__,
                        }
                    ),
                )
            except ConnectionError as exc:
                batch_id = getattr(exc, "history_batch_id", None)
                if not str(batch_id or "").strip():
                    raise
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="provider_transport_failed",
                    detail=str(exc)[:500],
                    evidence_hash=history.history_evidence_hash(
                        {
                            "batch_id": batch_id,
                            "error_type": type(exc).__name__,
                        }
                    ),
                )
            if (
                ingest.kind
                is margin_history_ingest.MarginHistoryIngestKind.LEGACY_CONFLICT
            ):
                conflict_code = (
                    "legacy_evidence_failed"
                    if any(code.value == "QUERY_ERROR" for code in ingest.issue_codes)
                    else "legacy_conflict"
                )
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code=conflict_code,
                    detail=json.dumps(
                        {
                            "batch_id": ingest.batch_id,
                            "candidate_hash": ingest.candidate_hash,
                            "legacy_hash": ingest.legacy_hash,
                            "issue_codes": [code.value for code in ingest.issue_codes],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    evidence_hash=history.history_evidence_hash(
                        {
                            "batch_id": ingest.batch_id,
                            "candidate_hash": ingest.candidate_hash,
                            "legacy_hash": ingest.legacy_hash,
                            "issue_codes": [
                                code.value for code in ingest.issue_codes
                            ],
                        }
                    ),
                )
            if ingest.kind not in {
                margin_history_ingest.MarginHistoryIngestKind.ACCEPTED,
            }:
                return history.MarginHistoryPartitionOutcome.failed(
                    partition,
                    code="ingest_outcome_unknown",
                    detail=str(ingest.kind),
                    evidence_hash=history.history_evidence_hash(
                        {"ingest_kind": str(ingest.kind)}
                    ),
                )
            if (
                ingest.kind is margin_history_ingest.MarginHistoryIngestKind.ACCEPTED
                and fetched_from_provider
            ):
                provider_accepted_dates.add(partition)
            return history.MarginHistoryPartitionOutcome.accepted(
                history.MarginHistoryAcceptedEvidence(
                    partition_value=partition,
                    batch_id=ingest.batch_id,
                    row_count=ingest.row_count,
                    content_hash=ingest.content_hash,
                )
            )

        projection_expected = (
            trading_days(contract.coverage_start, eligibility.eligible_end)
            if eligibility.eligible_end
            else []
        )
        try:
            result = history.execute_margin_history_plan(
                plan,
                execute_partition,
                propagate_exceptions=(
                    authorization_error_type,
                    quota_error_type,
                ),
            )
        except (authorization_error_type, quota_error_type) as exc:
            margin_ingest.project_ops_state(
                conn,
                projection_expected,
                contract=contract,
                ops_conn_factory=ops_conn_factory,
                provider_succeeded=bool(provider_accepted_dates),
                quota_error=(
                    "quota_wall_halt"
                    if isinstance(exc, quota_error_type)
                    else None
                ),
                best_effort_message=(
                    "formal margin history failure projection also failed"
                ),
            )
            raise
        projection = margin_ingest.project_ops_state(
            conn,
            projection_expected,
            contract=contract,
            ops_conn_factory=ops_conn_factory,
            provider_succeeded=bool(provider_accepted_dates),
        )
    finally:
        conn.close()

    attempted = set(result.attempted_dates)
    attempted_evidence = tuple(
        item
        for item in result.accepted_evidence
        if item.partition_value in attempted
    )
    if result.failures:
        status = (
            "BLOCKED"
            if result.failures[0].code == "checkpoint_blocked"
            else "FAILED"
        )
    elif not plan.execution_dates:
        status = "ALREADY_CURRENT"
    else:
        status = "CHUNK_ACCEPTED"
    return {
        "domain": domain,
        "mode": "formal_history",
        "status": status,
        "ok": not result.failures,
        "batches": len(result.attempted_dates),
        "rows": sum(item.row_count for item in attempted_evidence),
        "failed_batches": len(result.failures),
        "window": {
            "start": request.start,
            "end": request.end,
            "trading_dates": len(sessions),
        },
        "max_dates": request.max_dates,
        "configured_max_dates": plan.configured_max_dates,
        "execution_dates": list(plan.execution_dates),
        "selected_dates": list(plan.selected_dates),
        "repair_dates": list(plan.repair_dates),
        "skipped_dates": list(result.skipped_dates),
        "accepted_dates": list(result.accepted_dates),
        "failed_dates": list(result.failed_dates),
        "deferred_dates": list(result.deferred_dates),
        "next_start": result.next_start,
        "blocked_partition": result.blocked_partition,
        "accepted_evidence": [
            {
                "partition_value": item.partition_value,
                "batch_id": item.batch_id,
                "row_count": item.row_count,
                "content_hash": item.content_hash,
            }
            for item in result.accepted_evidence
        ],
        "failures": [
            {
                "partition_value": item.partition_value,
                "code": item.code,
                "detail": item.detail,
                "evidence_hash": item.evidence_hash,
            }
            for item in result.failures
        ],
        "contract_version": contract.contract_version,
        "contract_hash": contract.contract_hash,
        "config_hash": contract.config_hash,
        "plan_hash": plan.plan_hash,
        "result_hash": result.result_hash,
        "global_projection": {
            "frontier": getattr(projection, "frontier", None),
            "ready": bool(projection.ready),
            "missing_count": len(getattr(projection, "missing", ())),
            "reconcile_failure_count": len(
                getattr(projection, "reconcile_failures", ())
            ),
        },
    }
