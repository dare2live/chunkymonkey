"""Bounded calendar-eligible margin land/accept for contract v3+.

Knife 1b: SSE+SZSE external_aggregate only. No mass history replay, no
``--all-due`` drain, no product thaw of pulse ``rzrqye``. Legacy raw shadow
write is intentionally out of scope — Continuity reads canonical first;
product trust waits for an explicit shadow knife.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from services.data_sources.margin_acceptance import (
    AcceptanceOutcome,
    MarginFragment,
    MarginLandingBatch,
    accept_margin_batch,
    find_current_landed_margin_batch,
    land_margin_batch,
    recover_margin_batch,
)
from services.data_sources.margin_ingest import contract_for_spec
from services.data_sources.margin_population_scope import (
    MARGIN_ACCEPTED_VENUE_IDS,
    assert_margin_transport_matches_accepted_scope,
)
from services.data_sources.margin_schema import DATASET_ID


FetchLogicalBatch = Callable[..., list[dict[str, Any]] | None]
QuotaWallClassifier = Callable[[str], bool]


class MarginCatchupError(RuntimeError):
    """Bounded margin catchup refused or failed closed."""


@dataclass(frozen=True)
class MarginCatchupDayResult:
    partition_value: str
    status: str
    batch_id: str | None = None
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None
    detail: str | None = None


def _classify_fetch_error(
    exc: BaseException,
    *,
    quota_wall_classifier: QuotaWallClassifier | None = None,
) -> tuple[str, str]:
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


def _require_v3_contract(spec: dict[str, Any]):
    assert_margin_transport_matches_accepted_scope(spec)
    contract = contract_for_spec(spec)
    if contract is None or contract.dataset_id != DATASET_ID:
        raise MarginCatchupError("margin catchup requires the formal margin contract")
    version = str(contract.contract_version)
    if version < "3":
        raise MarginCatchupError(
            f"margin catchup requires contract_version>=3; got {version!r}"
        )
    return contract


def land_then_accept_margin_day(
    conn,
    adapter,
    spec: dict[str, Any],
    trade_date: str,
    *,
    fetch_logical_batch: FetchLogicalBatch,
    quota_wall_classifier: QuotaWallClassifier | None = None,
    contract=None,
    observed_at: datetime | None = None,
    batch_id: str | None = None,
) -> MarginCatchupDayResult:
    """Fetch SSE+SZSE fragments, land, then accept one calendar-eligible day."""

    contract = contract or _require_v3_contract(spec)
    partition = str(trade_date or "").replace("-", "")
    if len(partition) != 8 or not partition.isdigit():
        raise MarginCatchupError(
            f"margin catchup partition must be YYYYMMDD: {trade_date!r}"
        )
    required = tuple(contract.batch_completeness.required_groups_for(partition))
    if tuple(sorted(required)) != MARGIN_ACCEPTED_VENUE_IDS:
        raise MarginCatchupError(
            f"margin catchup required_groups must be {list(MARGIN_ACCEPTED_VENUE_IDS)}; "
            f"got {list(required)}"
        )
    split = spec.get("split_by") or {}
    split_param = str(split.get("param") or "")
    if split_param != "exchange_id":
        raise MarginCatchupError("margin catchup requires split_by.param=exchange_id")

    landed = find_current_landed_margin_batch(conn, partition, contract=contract)
    evidence_batch_id = landed.batch_id if landed is not None else None
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

        fetch_error: Exception | None = None
        try:
            fetch_logical_batch(
                trace_adapter,
                spec,
                {"trade_date": partition},
                split_values_override=required,
                fragment_callback=record_fragment,
            )
        except Exception as exc:  # noqa: BLE001 — classify into fragment evidence
            fetch_error = exc

        for value in required:
            fragment_trace.setdefault(
                value,
                {
                    "exchange_id": value,
                    "request": {"trade_date": partition, split_param: value},
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
            for value in required
        )
        stamp = observed_at or datetime.now(timezone.utc)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise MarginCatchupError(
                "margin catchup observed_at must be timezone-aware"
            )
        evidence_batch_id = batch_id or f"margin:v3:{partition}:{uuid4().hex}"
        land_margin_batch(
            conn,
            MarginLandingBatch(
                batch_id=evidence_batch_id,
                partition_value=partition,
                observed_at=stamp,
                available_at=stamp,
                fragments=fragments,
                source=contract.source,
                contract_version=str(contract.contract_version),
            ),
            contract=contract,
        )
        if fetch_error is not None:
            # Landing retained durable evidence; surface provider failure after.
            raise MarginCatchupError(
                f"margin catchup provider failed partition={partition}: "
                f"{type(fetch_error).__name__}: {str(fetch_error)[:300]}"
            ) from fetch_error

    try:
        outcome: AcceptanceOutcome = accept_margin_batch(
            conn, evidence_batch_id, contract=contract
        )
    except Exception:
        outcome = recover_margin_batch(
            conn, evidence_batch_id, contract=contract
        )
    if outcome.status == "ACCEPTED":
        return MarginCatchupDayResult(
            partition_value=partition,
            status="ACCEPTED",
            batch_id=outcome.batch_id,
            row_count=int(outcome.row_count or 0),
            content_hash=outcome.content_hash,
        )
    if outcome.status == "REJECTED":
        return MarginCatchupDayResult(
            partition_value=partition,
            status="REJECTED",
            batch_id=outcome.batch_id,
            rejection_code=outcome.rejection_code,
            detail=f"validation rejected code={outcome.rejection_code}",
        )
    raise MarginCatchupError(
        f"margin catchup unexpected status={outcome.status!r} "
        f"partition={partition} batch={evidence_batch_id}"
    )


__all__ = [
    "MarginCatchupDayResult",
    "MarginCatchupError",
    "land_then_accept_margin_day",
]
