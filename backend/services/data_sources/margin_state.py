"""Read-only projections derived from accepted TuShare margin facts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from services.data_sources.batch_integrity import VerifiedBatchFrontier
from services.data_sources.contracts import load_dataset_contract
from services.data_sources import margin_validation
from services.data_sources.margin_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    MARGIN_FIELDS,
)
from services.data_sources.margin_validation import (
    MarginValidationError,
    _candidate_rows,
    canonical_content_hash,
)


class MarginStateError(RuntimeError):
    """Accepted-state evidence is missing or internally contradictory."""


@dataclass(frozen=True)
class AcceptedMarginPartition:
    partition_value: str
    batch_id: str
    row_count: int
    content_hash: str
    accepted_at: Any


@dataclass(frozen=True)
class MarginAcceptedState:
    """One internally verified snapshot of all current accepted partitions."""

    partitions: tuple[AcceptedMarginPartition, ...]

    @property
    def dates(self) -> frozenset[str]:
        return frozenset(item.partition_value for item in self.partitions)

    @property
    def batch_by_partition(self) -> dict[str, str]:
        return {
            item.partition_value: item.batch_id
            for item in self.partitions
        }

    @property
    def frontier(self) -> VerifiedBatchFrontier | None:
        if not self.partitions:
            return None
        latest = max(self.partitions, key=lambda item: item.partition_value)
        return VerifiedBatchFrontier(
            last_date=latest.partition_value,
            row_count=latest.row_count,
            last_success_at=latest.accepted_at,
        )


@dataclass(frozen=True)
class MarginReconcileFailure:
    partition_value: str
    issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class MarginReadiness:
    """Typed read-only verdict consumed by pipeline gates."""

    eligible_end: str | None
    eligibility_reason: str
    expected: tuple[str, ...]
    accepted_state: MarginAcceptedState
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    reconcile_failures: tuple[MarginReconcileFailure, ...]

    @property
    def ready(self) -> bool:
        return bool(
            self.eligible_end
            and self.expected
            and not self.missing
            and not self.unexpected
            and not self.reconcile_failures
        )


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [table],
        ).fetchone()
    )


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    try:
        if len(compact) != 8 or not compact.isdigit():
            raise ValueError
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise MarginStateError(f"invalid accepted margin partition={value!r}") from exc
    return compact


def accepted_margin_partitions(
    conn, *, contract=None
) -> tuple[AcceptedMarginPartition, ...]:
    """Return all internally proven accepted partitions for the current contract."""
    required_tables = (
        ACCEPTED_TABLE,
        INGEST_BATCH_TABLE,
        LANDING_TABLE,
        CANONICAL_TABLE,
    )
    if not any(_table_exists(conn, table) for table in required_tables):
        return ()
    missing = [table for table in required_tables if not _table_exists(conn, table)]
    if missing:
        raise MarginStateError(f"partial formal margin schema missing={missing}")

    contract = contract or load_dataset_contract("margin")
    pointer_rows = conn.execute(
        f"""
        SELECT partition_value, batch_id, row_count, content_hash, accepted_at,
               contract_version, contract_hash, config_hash, observed_at, available_at
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
         ORDER BY partition_value
        """,
        [DATASET_ID],
    ).fetchall()
    if not pointer_rows:
        return ()
    prepared_pointers: list[tuple[str, tuple[Any, ...]]] = []
    for pointer in pointer_rows:
        partition = _partition(pointer[0])
        if (
            str(pointer[5]) != contract.contract_version
            or str(pointer[6]) != contract.contract_hash
            or str(pointer[7]) != contract.config_hash
        ):
            raise MarginStateError(
                "accepted pointer contract drift "
                f"partition={partition} batch={str(pointer[1])}"
            )
        prepared_pointers.append((partition, pointer))

    first_partition = min(partition for partition, _pointer in prepared_pointers)
    publication_days = margin_validation.load_margin_publication_sessions(
        first_partition, limit=None
    )
    accepted: list[AcceptedMarginPartition] = []
    for partition, pointer in prepared_pointers:
        batch_id = str(pointer[1])
        row_count = int(pointer[2])
        content_hash = str(pointer[3])

        batch_fields = (
            "status",
            "partition_value",
            "canonical_row_count",
            "canonical_hash",
            "contract_version",
            "contract_hash",
            "config_hash",
            "source_name",
            "writer_id",
            "request_json",
            "fragment_outcomes_json",
            "expected_fragment_count",
            "completed_fragment_count",
            "failed_fragment_count",
            "landing_row_count",
            "payload_hash",
            "observed_at",
            "available_at",
        )
        batch_row = conn.execute(
            f"""
            SELECT {', '.join(batch_fields)}
              FROM {INGEST_BATCH_TABLE}
             WHERE batch_id = ? AND dataset_id = ?
            """,
            [batch_id, DATASET_ID],
        ).fetchone()
        batch = (
            None
            if batch_row is None
            else dict(zip(batch_fields, batch_row, strict=True))
        )
        if batch is None or (
            str(batch["status"]) != "ACCEPTED"
            or _partition(batch["partition_value"]) != partition
            or int(batch["canonical_row_count"] or 0) != row_count
            or str(batch["canonical_hash"] or "") != content_hash
            or str(batch["contract_version"]) != str(pointer[5])
            or str(batch["contract_hash"]) != str(pointer[6])
            or str(batch["config_hash"]) != str(pointer[7])
            or str(batch["source_name"]) != contract.source
            or str(batch["writer_id"]) != contract.writer
            or batch["observed_at"] != pointer[8]
            or batch["available_at"] != pointer[9]
        ):
            raise MarginStateError(
                f"accepted batch evidence mismatch partition={partition} batch={batch_id}"
            )

        try:
            source_rows = _candidate_rows(conn, batch_id, partition, contract, batch)
        except MarginValidationError as exc:
            raise MarginStateError(
                f"accepted landing evidence mismatch partition={partition} "
                f"batch={batch_id} code={exc.code}"
            ) from exc
        if len(source_rows) != row_count or canonical_content_hash(source_rows) != content_hash:
            raise MarginStateError(
                f"accepted landing content mismatch partition={partition} batch={batch_id}"
            )
        source_by_grain = {
            (str(row["trade_date"]), str(row["exchange_id"])): row
            for row in source_rows
        }
        if len(source_by_grain) != row_count:
            raise MarginStateError(
                f"accepted landing grain mismatch partition={partition} batch={batch_id}"
            )

        canonical_fields = (
            *MARGIN_FIELDS,
            "ingest_batch_id",
            "source_row_hash",
            "contract_version",
            "config_hash",
            "available_at",
        )
        canonical_rows = conn.execute(
            f"""
            SELECT {', '.join(canonical_fields)}
              FROM {CANONICAL_TABLE}
             WHERE trade_date = CAST(? AS DATE)
             ORDER BY trade_date, exchange_id
            """,
            [f"{partition[:4]}-{partition[4:6]}-{partition[6:]}"],
        ).fetchall()
        normalized = [
            {
                **{
                    field: (
                        _partition(row[index]) if field == "trade_date" else row[index]
                    )
                    for index, field in enumerate(MARGIN_FIELDS)
                },
                **{
                    field: row[index]
                    for index, field in enumerate(canonical_fields[len(MARGIN_FIELDS):], start=len(MARGIN_FIELDS))
                },
            }
            for row in canonical_rows
        ]
        if len(normalized) != row_count or any(
            str(row["ingest_batch_id"]) != batch_id
            or str(row["contract_version"]) != contract.contract_version
            or str(row["config_hash"]) != contract.config_hash
            or row["available_at"] != pointer[9]
            for row in normalized
        ):
            raise MarginStateError(
                f"canonical count/batch mismatch partition={partition} batch={batch_id}"
            )
        try:
            margin_validation.validate_margin_publication_time(
                contract,
                partition,
                pointer[9],
                trading_day_values=publication_days,
            )
        except MarginValidationError as exc:
            raise MarginStateError(
                "accepted publication evidence mismatch "
                f"partition={partition} batch={batch_id} code={exc.code}"
            ) from exc
        if canonical_content_hash(normalized) != content_hash:
            raise MarginStateError(
                f"canonical content mismatch partition={partition} batch={batch_id}"
            )
        for row in normalized:
            grain = (str(row["trade_date"]), str(row["exchange_id"]))
            source = source_by_grain.get(grain)
            if source is None or str(row["source_row_hash"]) != str(
                source["source_row_hash"]
            ):
                raise MarginStateError(
                    f"canonical landing lineage mismatch partition={partition} "
                    f"batch={batch_id} grain={grain}"
                )
        accepted.append(
            AcceptedMarginPartition(
                partition_value=partition,
                batch_id=batch_id,
                row_count=row_count,
                content_hash=content_hash,
                accepted_at=pointer[4],
            )
        )
    return tuple(accepted)


def accepted_margin_dates(conn, *, contract=None) -> set[str]:
    return set(load_margin_accepted_state(conn, contract=contract).dates)


def load_margin_accepted_state(conn, *, contract=None) -> MarginAcceptedState:
    """Load once so a consumer cannot combine several independently read snapshots."""

    return MarginAcceptedState(
        accepted_margin_partitions(conn, contract=contract)
    )


def missing_accepted_margin_dates(
    conn, trading_days: Iterable[str], *, contract=None
) -> list[str]:
    contract = contract or load_dataset_contract("margin")
    expected = sorted(
        {
            _partition(day)
            for day in trading_days
            if _partition(day) >= contract.coverage_start
        }
    )
    accepted = accepted_margin_dates(conn, contract=contract)
    return [day for day in expected if day not in accepted]


def latest_accepted_margin_frontier(
    conn, *, contract=None
) -> VerifiedBatchFrontier | None:
    return load_margin_accepted_state(conn, contract=contract).frontier


def evaluate_margin_readiness(
    conn,
    expected_partitions: Iterable[str],
    *,
    contract=None,
    eligible_end: str | None,
    eligibility_reason: str,
    reconcile: bool = True,
    accepted_state: MarginAcceptedState | None = None,
) -> MarginReadiness:
    """Evaluate coverage and optional shadow parity from one accepted snapshot."""

    contract = contract or load_dataset_contract("margin")
    expected = tuple(sorted({
        partition
        for value in expected_partitions
        if (partition := _partition(value)) >= contract.coverage_start
    }))
    state = accepted_state or load_margin_accepted_state(
        conn, contract=contract
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
        from services.data_sources.margin_reconcile import reconcile_margin_partition

        for item in state.partitions:
            report = reconcile_margin_partition(
                conn, item.partition_value, contract=contract
            )
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


__all__ = [
    "AcceptedMarginPartition",
    "MarginAcceptedState",
    "MarginReadiness",
    "MarginReconcileFailure",
    "MarginStateError",
    "accepted_margin_dates",
    "accepted_margin_partitions",
    "evaluate_margin_readiness",
    "latest_accepted_margin_frontier",
    "load_margin_accepted_state",
    "missing_accepted_margin_dates",
]
