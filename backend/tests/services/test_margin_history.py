"""Pure execution policy for bounded formal-margin history migration."""
from __future__ import annotations

from dataclasses import replace

import pytest

from services.data_sources.margin_history import (
    MarginHistoryAcceptedEvidence,
    MarginHistoryCheckpointKind,
    MarginHistoryPartitionOutcome,
    MarginHistoryRequest,
    MarginHistoryResult,
    build_margin_history_plan,
    execute_margin_history_plan,
    history_evidence_hash,
)
from services.data_sources.margin_legacy_reconcile import (
    HISTORY_REPAIRABLE_CODES,
    MarginReconcileCode,
    MarginReconcileIssue,
    MarginReconcileReport,
    MarginReconcileStatus,
)


DATASET_ID = "tier0.market_data.margin_exchange_daily"
CONTRACT_HASH = "contract-v3"
CONFIG_HASH = "config-v3"
DATES = ("20260102", "20260105", "20260106", "20260107")


def _report(
    partition: str,
    *,
    accepted_batch_id: str | None,
    status: MarginReconcileStatus,
    codes: tuple[MarginReconcileCode, ...] = (),
    recoverable_landing_batch_id: str | None = None,
    recoverable_landing_payload_hash: str | None = None,
    unresolved_landing_batch_ids: tuple[str, ...] = (),
) -> MarginReconcileReport:
    row_count = 3 if accepted_batch_id is not None else None
    return MarginReconcileReport(
        dataset_id=DATASET_ID,
        partition_value=partition,
        status=status,
        accepted_batch_id=accepted_batch_id,
        accepted_row_count=row_count,
        canonical_row_count=row_count,
        legacy_row_count=row_count,
        issues=tuple(
            MarginReconcileIssue(code=code, detail=f"{partition}:{code.value}")
            for code in codes
        ),
        accepted_content_hash=(
            f"hash-{partition}" if accepted_batch_id is not None else None
        ),
        recoverable_landing_batch_id=recoverable_landing_batch_id,
        recoverable_landing_payload_hash=(
            recoverable_landing_payload_hash
            or (
                f"payload-{recoverable_landing_batch_id}"
                if recoverable_landing_batch_id is not None
                else None
            )
        ),
        unresolved_landing_batch_ids=unresolved_landing_batch_ids,
    )


def _missing(partition: str) -> MarginReconcileReport:
    return _report(
        partition,
        accepted_batch_id=None,
        status=MarginReconcileStatus.FAILED,
        codes=(MarginReconcileCode.ACCEPTED_PARTITION_MISSING,),
    )


def _parity(partition: str) -> MarginReconcileReport:
    return _report(
        partition,
        accepted_batch_id=f"batch-{partition}",
        status=MarginReconcileStatus.PARITY,
    )


def _repair(partition: str) -> MarginReconcileReport:
    return _report(
        partition,
        accepted_batch_id=f"batch-{partition}",
        status=MarginReconcileStatus.FAILED,
        codes=(MarginReconcileCode.VALUE_MISMATCH,),
    )


def _plan(*, reports, max_dates: int = 2, trading_dates=DATES):
    return build_margin_history_plan(
        MarginHistoryRequest(start=DATES[0], end=DATES[-1], max_dates=max_dates),
        configured_max_dates=3,
        trading_dates=trading_dates,
        reconcile_reports=reports,
        dataset_id=DATASET_ID,
        contract_hash=CONTRACT_HASH,
        config_hash=CONFIG_HASH,
    )


@pytest.mark.parametrize(
    ("history_request", "configured_max", "message"),
    [
        (MarginHistoryRequest("20260102", "20260107", 0), 3, "positive"),
        (MarginHistoryRequest("20260102", "20260107", -1), 3, "positive"),
        (MarginHistoryRequest("20260102", "20260107", 4), 3, "configured"),
        (MarginHistoryRequest("20260107", "20260102", 1), 3, "start"),
        (MarginHistoryRequest("2026-01-02", "20260107", 1), 3, "YYYYMMDD"),
    ],
)
def test_request_requires_explicit_valid_bounds_and_bounded_positive_cap(
    history_request, configured_max, message
):
    with pytest.raises(ValueError, match=message):
        build_margin_history_plan(
            history_request,
            configured_max_dates=configured_max,
            trading_dates=DATES,
            reconcile_reports=tuple(_missing(day) for day in DATES),
            dataset_id=DATASET_ID,
            contract_hash=CONTRACT_HASH,
            config_hash=CONFIG_HASH,
        )


def test_request_boundaries_must_both_be_trading_dates():
    with pytest.raises(ValueError, match="start.*trading"):
        build_margin_history_plan(
            MarginHistoryRequest("20260103", "20260107", 1),
            configured_max_dates=3,
            trading_dates=DATES,
            reconcile_reports=tuple(_missing(day) for day in DATES),
            dataset_id=DATASET_ID,
            contract_hash=CONTRACT_HASH,
            config_hash=CONFIG_HASH,
        )


def test_calendar_duplicates_are_rejected_before_they_can_be_normalized():
    request = MarginHistoryRequest(DATES[0], DATES[1], 1)

    with pytest.raises(ValueError, match="duplicate trading sessions"):
        from services.data_sources.margin_history import prove_margin_history_sessions

        prove_margin_history_sessions(
            request,
            [DATES[0], DATES[0], DATES[1]],
        )
    with pytest.raises(ValueError, match="end.*trading"):
        build_margin_history_plan(
            MarginHistoryRequest("20260102", "20260108", 1),
            configured_max_dates=3,
            trading_dates=DATES,
            reconcile_reports=tuple(_missing(day) for day in DATES),
            dataset_id=DATASET_ID,
            contract_hash=CONTRACT_HASH,
            config_hash=CONFIG_HASH,
        )


def test_plan_classifies_checkpoints_and_caps_action_oldest_first():
    reports = (
        _repair("20260106"),
        _missing("20260107"),
        _parity("20260102"),
        _missing("20260105"),
    )

    plan = _plan(reports=reports)

    assert plan.window_dates == DATES
    assert plan.skipped_dates == ("20260102",)
    assert plan.selected_dates == ("20260105",)
    assert plan.repair_dates == ("20260106",)
    assert plan.execution_dates == ("20260105", "20260106")
    assert plan.deferred_dates == ("20260107",)
    assert plan.blocked_dates == ()
    assert tuple(item.kind for item in plan.checkpoints) == (
        MarginHistoryCheckpointKind.SKIP,
        MarginHistoryCheckpointKind.SELECTED,
        MarginHistoryCheckpointKind.REPAIR,
        MarginHistoryCheckpointKind.SELECTED,
    )


def test_plan_hash_is_stable_across_equivalent_input_order():
    reports = (_parity(DATES[0]), *tuple(_missing(day) for day in DATES[1:]))
    left = _plan(reports=reports, trading_dates=DATES)
    right = _plan(
        reports=tuple(reversed(reports)),
        trading_dates=tuple(reversed(DATES)),
    )

    assert left == right
    assert left.plan_hash == right.plan_hash
    assert len(left.plan_hash) == 64


def test_reconcile_report_outside_requested_window_fails_closed():
    reports = (
        *tuple(_missing(day) for day in DATES),
        _missing("20260108"),
    )

    with pytest.raises(ValueError, match="outside the requested history window"):
        _plan(reports=reports)


def test_configured_history_cap_is_part_of_plan_evidence():
    request = MarginHistoryRequest(DATES[0], DATES[-1], 1)
    reports = tuple(_missing(day) for day in DATES)

    left = build_margin_history_plan(
        request,
        configured_max_dates=2,
        trading_dates=DATES,
        reconcile_reports=reports,
        dataset_id=DATASET_ID,
        contract_hash=CONTRACT_HASH,
        config_hash=CONFIG_HASH,
    )
    right = build_margin_history_plan(
        request,
        configured_max_dates=3,
        trading_dates=DATES,
        reconcile_reports=reports,
        dataset_id=DATASET_ID,
        contract_hash=CONTRACT_HASH,
        config_hash=CONFIG_HASH,
    )

    assert left.configured_max_dates == 2
    assert right.configured_max_dates == 3
    assert left.plan_hash != right.plan_hash


def test_landed_checkpoint_matrix_is_explicit_and_hashed():
    current = "recoverable-batch"
    repair = _report(
        DATES[0],
        accepted_batch_id="accepted-batch",
        status=MarginReconcileStatus.FAILED,
        codes=(
            MarginReconcileCode.VALUE_MISMATCH,
            MarginReconcileCode.UNRESOLVED_LANDING,
        ),
        recoverable_landing_batch_id=current,
        unresolved_landing_batch_ids=(current,),
    )
    selected = _report(
        DATES[1],
        accepted_batch_id=None,
        status=MarginReconcileStatus.FAILED,
        codes=(
            MarginReconcileCode.ACCEPTED_PARTITION_MISSING,
            MarginReconcileCode.UNRESOLVED_LANDING,
        ),
        recoverable_landing_batch_id=current,
        unresolved_landing_batch_ids=(current,),
    )
    parity_with_landing = _report(
        DATES[2],
        accepted_batch_id="accepted-parity",
        status=MarginReconcileStatus.FAILED,
        codes=(MarginReconcileCode.UNRESOLVED_LANDING,),
        recoverable_landing_batch_id=current,
        unresolved_landing_batch_ids=(current,),
    )
    stale = _report(
        DATES[3],
        accepted_batch_id=None,
        status=MarginReconcileStatus.FAILED,
        codes=(
            MarginReconcileCode.ACCEPTED_PARTITION_MISSING,
            MarginReconcileCode.UNRESOLVED_LANDING,
        ),
        unresolved_landing_batch_ids=("stale-batch",),
    )

    plan = _plan(
        reports=(repair, selected, parity_with_landing, stale),
        max_dates=3,
    )

    assert tuple(item.kind for item in plan.checkpoints) == (
        MarginHistoryCheckpointKind.REPAIR,
        MarginHistoryCheckpointKind.SELECTED,
        MarginHistoryCheckpointKind.BLOCKED,
        MarginHistoryCheckpointKind.BLOCKED,
    )
    assert plan.checkpoints[0].recoverable_landing_batch_id == current
    assert plan.checkpoints[1].recoverable_landing_batch_id == current
    changed = replace(
        repair,
        recoverable_landing_batch_id="different-batch",
        recoverable_landing_payload_hash="different-payload",
        unresolved_landing_batch_ids=("different-batch",),
    )
    changed_plan = _plan(
        reports=(changed, selected, parity_with_landing, stale),
        max_dates=3,
    )
    assert changed_plan.plan_hash != plan.plan_hash


@pytest.mark.parametrize(
    "code",
    [
        MarginReconcileCode.SCHEMA_MISMATCH,
        MarginReconcileCode.QUERY_ERROR,
        MarginReconcileCode.FORMAL_EVIDENCE_INVALID,
        MarginReconcileCode.CURRENT_CONTRACT_MISMATCH,
        MarginReconcileCode.ACCEPTED_PARTITION_DUPLICATE,
    ],
)
def test_schema_query_and_formal_contradictions_are_blocked(code):
    reports = [*(_missing(day) for day in DATES)]
    reports[1] = _report(
        DATES[1],
        accepted_batch_id="contradictory-batch",
        status=MarginReconcileStatus.FAILED,
        codes=(code,),
    )

    plan = _plan(reports=tuple(reports))

    assert plan.blocked_dates == (DATES[1],)
    assert plan.checkpoints[1].kind is MarginHistoryCheckpointKind.BLOCKED


@pytest.mark.parametrize("code", tuple(MarginReconcileCode))
def test_every_reconcile_code_defaults_blocked_unless_owner_marks_repairable(code):
    reports = [*(_missing(day) for day in DATES)]
    reports[1] = _report(
        DATES[1],
        accepted_batch_id="accepted-batch",
        status=MarginReconcileStatus.FAILED,
        codes=(code,),
    )

    plan = _plan(reports=tuple(reports))
    expected = (
        MarginHistoryCheckpointKind.REPAIR
        if code in HISTORY_REPAIRABLE_CODES
        else MarginHistoryCheckpointKind.BLOCKED
    )

    assert plan.checkpoints[1].kind is expected


def test_only_accepted_parity_is_skipped_and_missing_report_blocks():
    bad_parity = replace(_parity(DATES[0]), accepted_batch_id=None)
    plan = _plan(
        reports=(bad_parity, _missing(DATES[1]), _missing(DATES[2]))
    )

    assert plan.blocked_dates == (DATES[0], DATES[3])
    assert plan.skipped_dates == ()


def test_claiming_accepted_and_missing_at_once_is_a_blocking_contradiction():
    contradictory = _report(
        DATES[0],
        accepted_batch_id="claimed-batch",
        status=MarginReconcileStatus.FAILED,
        codes=(MarginReconcileCode.ACCEPTED_PARTITION_MISSING,),
    )

    plan = _plan(
        reports=(contradictory, *tuple(_missing(day) for day in DATES[1:]))
    )

    assert plan.blocked_dates == (DATES[0],)


def test_execution_stops_on_first_failure_and_defers_without_false_failures():
    plan = _plan(reports=tuple(_missing(day) for day in DATES), max_dates=3)
    calls: list[str] = []

    def executor(partition: str) -> MarginHistoryPartitionOutcome:
        calls.append(partition)
        if partition == DATES[1]:
            return MarginHistoryPartitionOutcome.failed(
                partition,
                code="provider_timeout",
                detail="timed out",
                evidence_hash=history_evidence_hash(
                    {"provider_attempt": partition}
                ),
            )
        return MarginHistoryPartitionOutcome.accepted(
            MarginHistoryAcceptedEvidence(
                partition_value=partition,
                batch_id=f"new-{partition}",
                row_count=3,
                content_hash=f"hash-{partition}",
            )
        )

    result = execute_margin_history_plan(plan, executor)

    assert calls == [DATES[0], DATES[1]]
    assert result.attempted_dates == (DATES[0], DATES[1])
    assert result.accepted_dates == (DATES[0],)
    assert result.failed_dates == (DATES[1],)
    assert result.deferred_dates == (DATES[2], DATES[3])
    assert result.next_start == DATES[1]
    assert result.contract_hash == CONTRACT_HASH
    assert result.config_hash == CONFIG_HASH
    assert result.plan_hash == plan.plan_hash
    assert len(result.result_hash) == 64


def test_execution_includes_skipped_accepted_evidence_and_stable_result_hash():
    reports = (_parity(DATES[0]), *tuple(_missing(day) for day in DATES[1:]))
    plan = _plan(reports=reports, max_dates=2)

    def executor(partition: str) -> MarginHistoryPartitionOutcome:
        return MarginHistoryPartitionOutcome.accepted(
            MarginHistoryAcceptedEvidence(
                partition_value=partition,
                batch_id=f"new-{partition}",
                row_count=3,
                content_hash=f"hash-{partition}",
            )
        )

    left = execute_margin_history_plan(plan, executor)
    right = execute_margin_history_plan(plan, executor)

    assert left.skipped_dates == (DATES[0],)
    assert tuple(item.partition_value for item in left.accepted_evidence) == (
        DATES[0],
        DATES[1],
        DATES[2],
    )
    assert left.accepted_evidence[0].content_hash == f"hash-{DATES[0]}"
    assert left.failed_dates == ()
    assert left.deferred_dates == (DATES[3],)
    assert left.next_start == DATES[3]
    assert left.result_hash == right.result_hash


def test_result_hash_binds_typed_failure_evidence_not_display_text():
    plan = _plan(reports=tuple(_missing(day) for day in DATES), max_dates=1)

    def failed(detail: str, evidence: str):
        return execute_margin_history_plan(
            plan,
            lambda partition: MarginHistoryPartitionOutcome.failed(
                partition,
                code="legacy_conflict",
                detail=detail,
                evidence_hash=history_evidence_hash(
                    {"candidate_hash": evidence}
                ),
            ),
        )

    left = failed("display A", "candidate-A")
    changed_evidence = failed("display A", "candidate-B")
    changed_display = failed("display B", "candidate-A")

    assert left.result_hash != changed_evidence.result_hash
    assert left.result_hash == changed_display.result_hash


def test_blocked_plan_never_calls_executor_and_returns_one_typed_failure():
    reports = [*(_missing(day) for day in DATES)]
    reports[2] = _report(
        DATES[2],
        accepted_batch_id="bad-evidence",
        status=MarginReconcileStatus.FAILED,
        codes=(MarginReconcileCode.FORMAL_EVIDENCE_INVALID,),
    )
    plan = _plan(reports=tuple(reports))

    def should_not_run(_partition: str) -> MarginHistoryPartitionOutcome:
        raise AssertionError("blocked history plan reached provider executor")

    result = execute_margin_history_plan(plan, should_not_run)

    assert result.attempted_dates == ()
    assert result.failed_dates == (DATES[2],)
    assert result.next_start == DATES[0]
    assert result.blocked_partition == DATES[2]
    assert set(result.deferred_dates) == set(DATES) - {DATES[2]}


def test_executor_exception_and_mismatched_evidence_fail_closed():
    plan = _plan(reports=tuple(_missing(day) for day in DATES), max_dates=1)

    with pytest.raises(RuntimeError, match="boom"):
        execute_margin_history_plan(
            plan, lambda _partition: (_ for _ in ()).throw(RuntimeError("boom"))
        )

    mismatched = execute_margin_history_plan(
        plan,
        lambda _partition: MarginHistoryPartitionOutcome.accepted(
            MarginHistoryAcceptedEvidence(
                partition_value="20251231",
                batch_id="wrong",
                row_count=3,
                content_hash="wrong-hash",
            )
        ),
    )
    assert mismatched.failed_dates == (DATES[0],)
    assert mismatched.failures[0].code == "executor_contradiction"


def test_account_level_halt_exception_can_propagate_to_the_public_cli():
    class AuthorizationHalt(RuntimeError):
        pass

    plan = _plan(reports=tuple(_missing(day) for day in DATES), max_dates=1)

    with pytest.raises(AuthorizationHalt, match="expired") as caught:
        execute_margin_history_plan(
            plan,
            lambda _partition: (_ for _ in ()).throw(
                AuthorizationHalt("expired")
            ),
            propagate_exceptions=(AuthorizationHalt,),
        )

    partial = caught.value.history_result
    assert isinstance(partial, MarginHistoryResult)
    assert partial.attempted_dates == (DATES[0],)
    assert partial.failed_dates == (DATES[0],)
    assert partial.deferred_dates == (DATES[1], DATES[2], DATES[3])
    assert partial.next_start == DATES[0]
    assert partial.failures[0].code == "account_halt"
    assert len(partial.failures[0].evidence_hash) == 64
    assert len(partial.result_hash) == 64
