"""Formal Tier0 ingest seam for TuShare exchange-level margin data.

This module owns the dataset-specific state machine.  ``sync_runner`` keeps the
generic calendar, retry/paging/split, legacy-write, and connection machinery
and passes those operations in as callbacks; this module never imports the
runner back.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from services.data_sources.batch_integrity import BatchCompletenessError
from services.data_sources.contracts import dataset_contract_from_spec
from services.data_sources.margin_acceptance import (
    MarginFragment,
    MarginLandingBatch,
    accept_margin_batch,
    find_current_landed_margin_batch,
    land_margin_batch,
    recover_margin_batch,
    validate_margin_batch,
)
from services.data_sources.margin_projections import (
    derive_margin_accepted_state,
    project_margin_accepted_state,
)
from services.data_sources.margin_reconcile import reconcile_margin_partition
from services.data_sources.margin_schema import DATASET_ID
from services.data_sources.margin_state import (
    accepted_margin_dates,
    latest_accepted_margin_frontier,
)
from services.data_sources.margin_validation import MarginValidationError


log = logging.getLogger(__name__)


FragmentCallback = Callable[
    [str, dict[str, Any], list[dict[str, Any]] | None, BaseException | None],
    None,
]
FetchLogicalBatch = Callable[..., list[dict[str, Any]] | None]
WriteBatch = Callable[..., int]
QuotaWallClassifier = Callable[[str], bool]


class LegacyWriteError(RuntimeError):
    """Legacy shadow write failed after durable landing, before acceptance."""


class MarginReconcileError(RuntimeError):
    """Accepted canonical and the legacy shadow are not partition-identical."""


FormalMarginOutcomeKind = Literal[
    "accepted",
    "authorization_failed",
    "quota_halt",
    "batch_incomplete",
    "legacy_write_failed",
    "reconcile_failed",
]


@dataclass(frozen=True)
class FormalMarginPartitionOutcome:
    """Dataset-owned result adapted by incremental and gap-drain transports."""

    kind: FormalMarginOutcomeKind
    rows: int = 0
    error: BaseException | None = None

    def require_error(self) -> BaseException:
        if self.error is None:
            raise RuntimeError(f"formal margin outcome {self.kind!r} lost its error")
        return self.error


def contract_for_spec(spec: dict[str, Any]):
    """Return the typed contract only for this formal Tier0 tracer."""
    domain = str(spec.get("domain") or "").strip()
    metadata = spec.get("dataset_contract")
    if domain == "margin" and (
        not isinstance(metadata, dict) or metadata.get("dataset_id") != DATASET_ID
    ):
        raise ValueError(
            "margin is a blocking formal Tier0 dataset; missing or mismatched "
            "dataset_contract cannot fall back to the legacy path"
        )
    if not isinstance(metadata, dict):
        return None
    if metadata.get("dataset_id") == DATASET_ID and domain != "margin":
        raise ValueError(
            f"formal margin dataset_contract cannot be attached to domain={domain!r}"
        )
    if metadata.get("dataset_id") != DATASET_ID:
        return None
    contract = dataset_contract_from_spec(domain, spec)
    if contract.dataset_id != DATASET_ID:
        raise ValueError(f"formal margin contract id drift: {contract.dataset_id!r}")
    transport_mismatches = {}
    if str(spec.get("batch_mode") or "") != "by_trade_date":
        transport_mismatches["batch_mode"] = spec.get("batch_mode")
    if str(spec.get("date_param") or "trade_date") != "trade_date":
        transport_mismatches["date_param"] = spec.get("date_param")
    if str(spec.get("write_mode") or "") != "replace_partition":
        transport_mismatches["write_mode"] = spec.get("write_mode")
    split = spec.get("split_by")
    if not isinstance(split, dict) or str(split.get("param") or "") != "exchange_id":
        transport_mismatches["split_by.param"] = (
            split.get("param") if isinstance(split, dict) else None
        )
        configured_groups: set[str] = set()
    else:
        raw_groups = split.get("values")
        if not isinstance(raw_groups, list) or not raw_groups:
            transport_mismatches["split_by.values"] = raw_groups
            configured_groups = set()
        elif any(
            not isinstance(value, str)
            or not value
            or value.strip() != value
            or value.upper() != value
            for value in raw_groups
        ):
            transport_mismatches["split_by.values"] = raw_groups
            configured_groups = set()
        elif len(raw_groups) != len(set(raw_groups)):
            transport_mismatches["split_by.values"] = raw_groups
            configured_groups = set()
        else:
            configured_groups = set(raw_groups)
    required_groups = set(contract.batch_completeness.required_groups)
    required_groups.update(
        group
        for group, _effective_date in (
            contract.batch_completeness.required_groups_since
        )
    )
    if (
        "split_by.values" not in transport_mismatches
        and configured_groups != required_groups
    ):
        transport_mismatches["split_by.values"] = sorted(configured_groups)
    if transport_mismatches:
        raise ValueError(
            "formal margin transport wiring drift: "
            f"actual={transport_mismatches} required_groups={sorted(required_groups)}"
        )
    return contract


def accepted_frontier(
    spec: dict[str, Any],
    *,
    contract=None,
    conn=None,
    target_conn_factory: Callable[[dict[str, Any]], Any] | None = None,
):
    """Read progress from accepted facts, never legacy raw or a watermark."""
    own = conn is None
    if own:
        if target_conn_factory is None:
            raise ValueError("accepted_frontier requires conn or target_conn_factory")
        conn = target_conn_factory(spec)
    try:
        return latest_accepted_margin_frontier(conn, contract=contract)
    finally:
        if own:
            conn.close()


def accepted_dates(conn, *, contract=None) -> set[str]:
    """Return partitions proven by the current AcceptedPartition contract."""
    return accepted_margin_dates(conn, contract=contract)


def project_ops_state(
    raw_conn,
    expected_partitions: list[str],
    *,
    contract=None,
    ops_conn_factory: Callable[[], Any],
    provider_succeeded: bool = False,
    quota_error: str | None = None,
    record: bool = True,
    best_effort_message: str | None = None,
):
    """Rebuild retryable Ops projections from accepted facts.

    The accepted transaction has already committed in ``raw_conn``.  The Ops
    database is a separate rebuildable boundary: projection failure surfaces,
    but cannot erase accepted evidence or justify another provider fetch.
    """
    if not record:
        return derive_margin_accepted_state(
            raw_conn, expected_partitions, contract=contract
        )
    ops_conn = None
    try:
        ops_conn = ops_conn_factory()
        return project_margin_accepted_state(
            raw_conn,
            ops_conn,
            expected_partitions,
            contract=contract,
            provider_succeeded=provider_succeeded,
            quota_error=quota_error,
        )
    except Exception:
        if best_effort_message is None:
            raise
        log.exception(best_effort_message)
        return None
    finally:
        if ops_conn is not None:
            ops_conn.close()


def _classify_fetch_error(
    exc: BaseException,
    *,
    quota_wall_classifier: QuotaWallClassifier | None = None,
) -> tuple[str, str]:
    """Map a terminal provider exception to durable, disjoint evidence."""
    name = type(exc).__name__.lower()
    detail = str(exc)[:500]
    message = detail.lower()
    if "authorization" in name or "auth" in name:
        return "authorization", detail
    if "quota" in name or (
        quota_wall_classifier is not None and quota_wall_classifier(detail)
    ):
        return "quota", detail
    if isinstance(exc, TimeoutError) or "timeout" in name or "timed out" in message:
        return "timeout", detail
    if (
        isinstance(exc, ConnectionError)
        or "connection" in name
        or "connection" in message
        or "connect" in message
    ):
        return "connection", detail
    return "provider_error", detail


class _MarginTraceAdapter:
    """Observe provider attempts without owning retry, paging, or splitting."""

    def __init__(
        self,
        delegate,
        split_param: str,
        *,
        quota_wall_classifier: QuotaWallClassifier | None = None,
    ):
        self._delegate = delegate
        self._split_param = split_param
        self._quota_wall_classifier = quota_wall_classifier
        self._failures: dict[str, tuple[str, str | None, str | None]] = {}

    def fetch_raw(self, api: str, **params):
        group = str(params.get(self._split_param) or "").upper()
        try:
            rows = self._delegate.fetch_raw(api, **params)
        except Exception as exc:
            error_type, detail = _classify_fetch_error(
                exc, quota_wall_classifier=self._quota_wall_classifier
            )
            self._failures[group] = ("error", error_type, detail)
            raise
        if rows:
            # A later successful retry/page supersedes an earlier transient failure.
            self._failures.pop(group, None)
        else:
            self._failures[group] = ("empty", None, None)
        return rows

    def terminal_evidence(
        self, group: str, exc: BaseException | None = None
    ) -> tuple[str, str | None, str | None]:
        key = str(group).upper()
        if key in self._failures:
            return self._failures[key]
        if exc is not None:
            error_type, detail = _classify_fetch_error(
                exc, quota_wall_classifier=self._quota_wall_classifier
            )
            return "error", error_type, detail
        return "error", "provider_error", "logical fragment did not complete"


def execute_partition(
    conn,
    adapter,
    spec: dict[str, Any],
    params: dict[str, Any],
    *,
    fetch_logical_batch: FetchLogicalBatch,
    write_batch: WriteBatch,
    quota_wall_classifier: QuotaWallClassifier | None = None,
    contract=None,
    effective_min_rows: int | None = None,
    observed_at: datetime | None = None,
    batch_id: str | None = None,
) -> int:
    """Recover/fetch, validate, update legacy raw, then formally accept."""
    contract = contract or contract_for_spec(spec)
    if contract is None or contract.dataset_id != DATASET_ID:
        raise ValueError("margin partition executor requires the formal margin contract")

    date_param = str(spec.get("date_param") or "trade_date")
    partition = str(params.get(date_param) or "").replace("-", "")
    if len(partition) != 8 or not partition.isdigit():
        raise ValueError(f"formal margin partition must be YYYYMMDD: {partition!r}")
    split = spec.get("split_by") or {}
    split_param = str(split.get("param") or "")
    configured = [str(value).upper() for value in split.get("values") or []]
    required = tuple(contract.batch_completeness.required_groups_for(partition))
    required_set = set(required)
    split_values = tuple(value for value in configured if value in required_set)
    if (
        not split_param
        or len(split_values) != len(required_set)
        or set(split_values) != required_set
    ):
        raise ValueError(
            "formal margin split/contract mismatch "
            f"configured={configured} required={sorted(required_set)}"
        )

    if not callable(find_current_landed_margin_batch) or not callable(
        validate_margin_batch
    ):
        raise RuntimeError(
            "formal margin runner requires acceptance recovery/validation APIs; "
            "provider fetch is blocked"
        )

    evidence_batch_id = find_current_landed_margin_batch(
        conn, partition, contract=contract
    )
    fetch_error: Exception | None = None
    if evidence_batch_id is None:
        trace_adapter = _MarginTraceAdapter(
            adapter,
            split_param,
            quota_wall_classifier=quota_wall_classifier,
        )
        fragment_trace: dict[str, dict[str, Any]] = {}

        def record_fragment(
            value: str,
            request: dict[str, Any],
            rows: list[dict[str, Any]] | None,
            exc: BaseException | None,
        ) -> None:
            group = str(value).upper()
            if rows:
                outcome, error_type, error_detail = "success", None, None
            else:
                outcome, error_type, error_detail = trace_adapter.terminal_evidence(
                    group, exc
                )
            fragment_trace[group] = {
                "exchange_id": group,
                "request": dict(request),
                "rows": list(rows or []),
                "outcome": outcome,
                "error_type": error_type,
                "error_detail": error_detail,
            }

        try:
            fetch_logical_batch(
                trace_adapter,
                spec,
                params,
                split_values_override=split_values,
                fragment_callback=record_fragment,
            )
        except Exception as exc:
            fetch_error = exc

        for value in split_values:
            fragment_trace.setdefault(
                value,
                {
                    "exchange_id": value,
                    "request": {**params, split_param: value},
                    "rows": [],
                    "outcome": "error",
                    "error_type": "not_attempted",
                    "error_detail": "earlier required fragment did not complete",
                },
            )
        fragments = tuple(
            MarginFragment(
                exchange_id=value,
                request=dict(fragment_trace[value]["request"]),
                rows=list(fragment_trace[value]["rows"]),
                outcome=str(fragment_trace[value]["outcome"]),
                error_type=fragment_trace[value]["error_type"],
                error_detail=fragment_trace[value]["error_detail"],
            )
            for value in split_values
        )
        stamp = observed_at or datetime.now(timezone.utc)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("formal margin observed_at must be timezone-aware")
        evidence_batch_id = batch_id or f"margin:{partition}:{uuid4().hex}"
        land_margin_batch(
            conn,
            MarginLandingBatch(
                batch_id=evidence_batch_id,
                partition_value=partition,
                observed_at=stamp,
                # First observation is the only defensible availability evidence.
                available_at=stamp,
                fragments=fragments,
                source=contract.source,
                contract_version=contract.contract_version,
            ),
            contract=contract,
        )

    try:
        prepared = validate_margin_batch(
            conn, evidence_batch_id, contract=contract
        )
    except MarginValidationError as exc:
        rejection = accept_margin_batch(
            conn, evidence_batch_id, contract=contract
        )
        if rejection.status != "REJECTED" or rejection.rejection_code != exc.code:
            raise RuntimeError(
                "pure validation and durable rejection disagree: "
                f"validated={exc.code} "
                f"accepted={rejection.status}/{rejection.rejection_code}"
            ) from exc
        if fetch_error is not None:
            raise fetch_error
        raise BatchCompletenessError(
            f"formal margin validation rejected partition={partition} code={exc.code}"
        ) from exc
    if fetch_error is not None:
        raise fetch_error
    if (
        str(prepared.batch_id) != str(evidence_batch_id)
        or str(prepared.partition_value).replace("-", "") != partition
    ):
        raise RuntimeError(
            "validated margin batch identity contradicts requested partition"
        )
    rows = [dict(row) for row in prepared.legacy_rows]
    if not rows:
        raise RuntimeError("validated margin batch returned no legacy rows")
    try:
        written = write_batch(
            conn,
            spec,
            rows,
            effective_min_rows=effective_min_rows,
            expected_partition={
                column: params.get(column)
                for column in spec.get("partition_by") or []
            },
        )
    except BatchCompletenessError:
        raise
    except Exception as exc:
        raise LegacyWriteError(str(exc)) from exc

    try:
        outcome = accept_margin_batch(
            conn, evidence_batch_id, contract=contract
        )
    except Exception:
        # Tx-B may have committed before acknowledgement was lost.
        outcome = recover_margin_batch(
            conn, evidence_batch_id, contract=contract
        )
    if outcome.status != "ACCEPTED":
        raise BatchCompletenessError(
            "formal margin acceptance rejected after legacy write "
            f"partition={partition} code={outcome.rejection_code or outcome.status}"
        )
    reconcile = reconcile_margin_partition(
        conn, partition, contract=contract
    )
    if not reconcile.ok:
        codes = sorted({issue.code.value for issue in reconcile.issues})
        raise MarginReconcileError(
            f"formal margin shadow parity failed partition={partition} codes={codes}"
        )
    return written


def execute_partition_outcome(
    *,
    conn,
    adapter,
    spec: dict[str, Any],
    params: dict[str, Any],
    contract,
    effective_min_rows: int,
    fetch_logical_batch: FetchLogicalBatch,
    write_batch: WriteBatch,
    quota_wall_classifier: QuotaWallClassifier,
    authorization_error_type: type[BaseException],
    quota_error_type: type[BaseException],
) -> FormalMarginPartitionOutcome:
    """Adapt dataset and transport exceptions once without choosing loop policy."""

    try:
        rows = execute_partition(
            conn=conn,
            adapter=adapter,
            spec=spec,
            params=params,
            fetch_logical_batch=fetch_logical_batch,
            write_batch=write_batch,
            quota_wall_classifier=quota_wall_classifier,
            contract=contract,
            effective_min_rows=effective_min_rows,
        )
    except authorization_error_type as exc:
        return FormalMarginPartitionOutcome("authorization_failed", error=exc)
    except quota_error_type as exc:
        return FormalMarginPartitionOutcome("quota_halt", error=exc)
    except BatchCompletenessError as exc:
        return FormalMarginPartitionOutcome("batch_incomplete", error=exc)
    except LegacyWriteError as exc:
        return FormalMarginPartitionOutcome("legacy_write_failed", error=exc)
    except MarginReconcileError as exc:
        return FormalMarginPartitionOutcome("reconcile_failed", error=exc)
    return FormalMarginPartitionOutcome("accepted", rows=rows)


__all__ = [
    "FormalMarginPartitionOutcome",
    "LegacyWriteError",
    "MarginReconcileError",
    "accepted_dates",
    "accepted_frontier",
    "contract_for_spec",
    "execute_partition",
    "execute_partition_outcome",
    "project_ops_state",
]
