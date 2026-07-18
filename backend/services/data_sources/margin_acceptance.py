"""Formal Tier0 acceptance boundary for the TuShare ``margin`` dataset.

This is intentionally domain-specific.  A second accepted dataset must prove
which transaction and schema mechanics are actually reusable before they move
to shared infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from services.data_sources.margin_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    MARGIN_SCHEMA_HASH,
    MARGIN_SCHEMA_ID,
    MARGIN_FIELDS,
    MarginAcceptanceError,
    ensure_margin_acceptance_schema,
)
from services.data_sources.margin_validation import (
    MarginValidationError,
    _batch_payload_hash,
    _candidate_rows,
    _provider_rows,
    _sha256,
    _stable_json,
    canonical_content_hash,
    validate_margin_publication_time,
)


@dataclass(frozen=True)
class MarginFragment:
    exchange_id: str
    rows: Iterable[dict[str, Any]]
    request: dict[str, Any] = field(default_factory=dict)
    outcome: str = "success"
    error_type: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class MarginLandingBatch:
    batch_id: str
    partition_value: str
    observed_at: datetime | str
    available_at: datetime | str
    fragments: Iterable[MarginFragment]
    source: str = "tushare"
    contract_version: str = "2"


@dataclass(frozen=True)
class AcceptanceOutcome:
    status: str
    batch_id: str
    partition_value: str
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None


@dataclass(frozen=True)
class ValidatedMarginBatch:
    """A committed landing batch proven safe for shadow and formal publication."""

    batch_id: str
    partition_value: str
    observed_at: datetime
    available_at: datetime
    canonical_rows: tuple[dict[str, Any], ...]
    legacy_rows: tuple[dict[str, Any], ...]
    content_hash: str

    @property
    def row_count(self) -> int:
        return len(self.canonical_rows)


_BATCH_FIELDS = (
    "status", "partition_value", "observed_at", "available_at",
    "contract_version", "contract_hash", "config_hash", "canonical_hash",
    "canonical_row_count", "request_json", "fragment_outcomes_json",
    "expected_fragment_count", "completed_fragment_count", "failed_fragment_count",
    "landing_row_count", "payload_hash", "source_name", "writer_id",
)


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise MarginAcceptanceError(f"invalid margin partition={value!r}")
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise MarginAcceptanceError(f"invalid margin partition={value!r}") from exc
    return compact


def _contract(contract=None):
    from services.data_sources.contracts import load_dataset_contract

    contract = contract or load_dataset_contract("margin")
    if contract.dataset_id != DATASET_ID:
        raise MarginAcceptanceError(
            f"margin contract dataset_id mismatch: {contract.dataset_id!r}"
        )
    expected = {
        "source": "tushare",
        "api": "margin",
        "target_db": "tushare_raw",
        "canonical_table": CANONICAL_TABLE,
        "compatibility_table": "raw_tushare_margin",
        "schema_id": MARGIN_SCHEMA_ID,
        "schema_hash": MARGIN_SCHEMA_HASH,
        "grain": ("trade_date", "exchange_id"),
        "partition_by": "trade_date",
        "available_after": "t+1",
        "availability_policy": {
            "axis": "trading_day",
            "rule": "next_trading_session_at",
            "at": "09:00",
        },
        "writer": "services.data_sources.margin_acceptance",
        "failure_policy": "fail_closed",
    }
    mismatched = {
        field: (
            getattr(contract, field).payload()
            if field == "availability_policy"
            else getattr(contract, field),
            value,
        )
        for field, value in expected.items()
        if (
            getattr(contract, field).payload()
            if field == "availability_policy"
            else getattr(contract, field)
        )
        != value
    }
    if mismatched:
        raise MarginAcceptanceError(f"margin contract wiring drift: {mismatched}")
    return contract


def _load_margin_batch(conn, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT status, partition_value, observed_at, available_at, contract_version,
               contract_hash, config_hash, canonical_hash, canonical_row_count,
               request_json, fragment_outcomes_json, expected_fragment_count,
               completed_fragment_count, failed_fragment_count, landing_row_count,
               payload_hash, source_name, writer_id
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ? AND dataset_id = ?
        """,
        [batch_id, DATASET_ID],
    ).fetchone()
    if row is None:
        raise MarginAcceptanceError(f"unknown margin batch_id={batch_id!r}")
    return dict(zip(_BATCH_FIELDS, row, strict=True))


def _aware_datetime(value: datetime | str, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarginAcceptanceError(f"invalid {field}={value!r}") from exc
    else:
        raise MarginAcceptanceError(f"invalid {field} type={type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarginAcceptanceError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _call(after_step: Callable[[str], None] | None, step: str) -> None:
    if after_step is not None:
        after_step(step)


def land_margin_batch(
    conn,
    batch: MarginLandingBatch,
    *,
    contract=None,
    after_step: Callable[[str], None] | None = None,
) -> str:
    """Tx-A: persist the complete provider response without semantic filtering."""
    contract = _contract(contract)
    ensure_margin_acceptance_schema(conn)
    batch_id = str(batch.batch_id or "").strip()
    if not batch_id:
        raise MarginAcceptanceError("batch_id must be non-empty")
    partition = _partition(batch.partition_value)
    if str(batch.contract_version) != contract.contract_version:
        raise MarginAcceptanceError(
            f"batch contract_version={batch.contract_version!r} current={contract.contract_version!r}"
        )
    if str(batch.source) != contract.source:
        raise MarginAcceptanceError(
            f"batch source={batch.source!r} current={contract.source!r}"
        )
    observed_at = _aware_datetime(batch.observed_at, "observed_at")
    available_at = _aware_datetime(batch.available_at, "available_at")
    if observed_at != available_at:
        raise MarginAcceptanceError(
            "available_at must equal first observed_at when provider publication time "
            "is unavailable"
        )
    fragments = list(batch.fragments)
    if not fragments:
        raise MarginAcceptanceError("margin landing requires at least one fragment")
    fragment_ids = [str(fragment.exchange_id).upper() for fragment in fragments]
    if len(fragment_ids) != len(set(fragment_ids)):
        raise MarginAcceptanceError("duplicate fragment exchange_id")

    landing_rows: list[tuple[Any, ...]] = []
    outcomes: list[dict[str, Any]] = []
    payload_signature: list[str] = []
    requests: list[dict[str, Any]] = []
    for fragment_ordinal, (fragment, exchange_id) in enumerate(
        zip(fragments, fragment_ids, strict=True), start=1
    ):
        rows = list(fragment.rows)
        request = dict(fragment.request)
        requests.append({"fragment_exchange_id": exchange_id, "request": request})
        outcome = str(fragment.outcome or "").strip().lower()
        if outcome == "success" and not rows:
            outcome = "empty"
        if outcome not in {"success", "empty", "error"}:
            raise MarginAcceptanceError(
                f"invalid margin fragment outcome={fragment.outcome!r}"
            )
        if outcome == "empty" and rows:
            raise MarginAcceptanceError("empty margin fragment cannot carry rows")
        error_type = str(fragment.error_type or "").strip() or None
        error_detail = str(fragment.error_detail or "").strip() or None
        if outcome == "error" and error_type is None:
            raise MarginAcceptanceError("error margin fragment requires error_type")
        if outcome != "error" and (error_type is not None or error_detail is not None):
            raise MarginAcceptanceError(
                "successful/empty margin fragment cannot carry error metadata"
            )
        outcomes.append({
            "exchange_id": exchange_id,
            "status": outcome,
            "row_count": len(rows),
            "error_type": error_type,
            "error_detail": error_detail,
        })
        request_json = _stable_json(request)
        for row_ordinal, row in enumerate(rows, start=1):
            payload_json = _stable_json(row)
            row_hash = _sha256(payload_json)
            payload_signature.append(f"{fragment_ordinal}:{row_ordinal}:{row_hash}")
            landing_rows.append((
                batch_id, exchange_id, fragment_ordinal, row_ordinal,
                request_json, payload_json, row_hash,
            ))
    payload_hash = _batch_payload_hash(
        partition=partition,
        source=str(batch.source),
        contract_version=str(batch.contract_version),
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        observed_at=observed_at,
        available_at=available_at,
        requests=requests,
        outcomes=outcomes,
        row_signatures=payload_signature,
    )

    existing = conn.execute(
        f"SELECT payload_hash, status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [batch_id],
    ).fetchone()
    if existing is not None:
        if existing[0] == payload_hash:
            return batch_id
        raise MarginAcceptanceError(
            f"batch_id {batch_id!r} already exists with different payload"
        )

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"""
            INSERT INTO {INGEST_BATCH_TABLE} (
                batch_id, dataset_id, contract_version, contract_hash, config_hash,
                writer_id, partition_value, source_name, status, request_json,
                fragment_outcomes_json, expected_fragment_count, completed_fragment_count,
                failed_fragment_count, landing_row_count, payload_hash, observed_at,
                available_at, landed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'LANDED', ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                batch_id, DATASET_ID, str(batch.contract_version), contract.contract_hash,
                contract.config_hash, contract.writer, partition, batch.source,
                _stable_json(requests), _stable_json(outcomes),
                len(contract.batch_completeness.required_groups_for(partition)),
                sum(outcome["status"] != "error" for outcome in outcomes),
                sum(outcome["status"] == "error" for outcome in outcomes),
                len(landing_rows), payload_hash, observed_at,
                available_at,
            ],
        )
        _call(after_step, "after_batch_insert")
        conn.executemany(
            f"""
            INSERT INTO {LANDING_TABLE} (
                batch_id, fragment_exchange_id, fragment_ordinal, row_ordinal,
                request_json, payload_json, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            landing_rows,
        )
        _call(after_step, "after_landing_insert")
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise
    _call(after_step, "after_landing_commit")
    return batch_id


def find_current_landed_margin_batch(
    conn, partition_value: Any, *, contract=None
) -> str | None:
    """Find the sole recoverable Tx-A checkpoint for the current contract.

    A stale or ambiguous LANDED checkpoint is a hard contradiction: silently
    fetching again would create two unresolved observations for one partition.
    """
    contract = _contract(contract)
    ensure_margin_acceptance_schema(conn)
    partition = _partition(partition_value)
    rows = conn.execute(
        f"""
        SELECT batch_id, contract_version, contract_hash, config_hash,
               source_name, writer_id
          FROM {INGEST_BATCH_TABLE}
         WHERE dataset_id = ? AND partition_value = ? AND status = 'LANDED'
         ORDER BY landed_at, batch_id
        """,
        [DATASET_ID, partition],
    ).fetchall()
    if len(rows) > 1:
        raise MarginAcceptanceError(
            f"ambiguous LANDED margin checkpoints partition={partition} count={len(rows)}"
        )
    if not rows:
        return None
    row = rows[0]
    actual = tuple(str(row[index]) for index in range(1, 6))
    expected = (
        contract.contract_version,
        contract.contract_hash,
        contract.config_hash,
        contract.source,
        contract.writer,
    )
    if actual != expected:
        raise MarginAcceptanceError(
            f"stale LANDED margin checkpoint batch_id={row[0]!r}; "
            "adjudicate it before fetching another observation"
        )
    return str(row[0])


def _validate_loaded_margin_batch(
    conn,
    batch_id: str,
    contract,
    batch: dict[str, Any],
) -> ValidatedMarginBatch:
    partition = _partition(batch["partition_value"])
    wiring_mismatch = (
        str(batch["contract_version"]) != contract.contract_version
        or str(batch["contract_hash"]) != contract.contract_hash
        or str(batch["config_hash"]) != contract.config_hash
        or str(batch["source_name"]) != contract.source
        or str(batch["writer_id"]) != contract.writer
    )
    if wiring_mismatch:
        raise MarginValidationError(
            "CONTRACT_DRIFT", "landed contract/config hash is no longer current"
        )

    validate_margin_publication_time(
        contract, partition, batch["available_at"]
    )

    canonical_rows = _candidate_rows(conn, batch_id, partition, contract, batch)
    content_hash = canonical_content_hash(canonical_rows)
    current = conn.execute(
        f"""
        SELECT observed_at, content_hash, batch_id FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [DATASET_ID, partition],
    ).fetchone()
    if current is not None:
        if current[0] > batch["observed_at"]:
            raise MarginValidationError(
                "STALE_OBSERVED_AT",
                f"candidate={batch['observed_at']} current={current[0]}",
            )
        if current[0] == batch["observed_at"]:
            code = (
                "DUPLICATE_OBSERVATION"
                if str(current[1]) == content_hash
                else "OBSERVATION_CONFLICT"
            )
            raise MarginValidationError(
                code,
                f"equal observed_at already owned by batch={current[2]}",
            )
    return ValidatedMarginBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=batch["observed_at"],
        available_at=batch["available_at"],
        canonical_rows=tuple(canonical_rows),
        legacy_rows=_provider_rows(conn, batch_id),
        content_hash=content_hash,
    )


def validate_margin_batch(
    conn, batch_id: str, *, contract=None
) -> ValidatedMarginBatch:
    """Validate a durable Tx-A checkpoint without publishing or changing status."""
    contract = _contract(contract)
    ensure_margin_acceptance_schema(conn)
    batch = _load_margin_batch(conn, batch_id)
    if str(batch["status"]) != "LANDED":
        raise MarginAcceptanceError(
            f"margin batch {batch_id!r} is not LANDED: {batch['status']!r}"
        )
    return _validate_loaded_margin_batch(conn, batch_id, contract, batch)


def _reject(
    conn,
    batch_id: str,
    partition: str,
    exc: MarginValidationError,
    *,
    after_step: Callable[[str], None] | None = None,
) -> AcceptanceOutcome:
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'REJECTED', validated_at = CURRENT_TIMESTAMP,
                   rejection_code = ?, rejection_detail = ?
             WHERE batch_id = ? AND status = 'LANDED'
            """,
            [exc.code, exc.detail[:1000], batch_id],
        )
        _call(after_step, "after_rejection_update")
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise
    _call(after_step, "after_rejection_commit")
    return AcceptanceOutcome("REJECTED", batch_id, partition, rejection_code=exc.code)


def _prove_accepted_outcome(
    conn, batch_id: str, partition: str, contract
) -> AcceptanceOutcome:
    """Re-prove the current contract, landing, canonical and pointer chain."""

    from services.data_sources.margin_state import (
        MarginStateError,
        accepted_margin_partitions,
    )

    try:
        matches = [
            item
            for item in accepted_margin_partitions(
                conn,
                contract=contract,
                partition_value=partition,
            )
            if item.partition_value == partition and item.batch_id == batch_id
        ]
    except MarginStateError as exc:
        raise MarginAcceptanceError(
            "accepted margin evidence violates current policy "
            f"partition={partition} batch={batch_id!r}: {exc}"
        ) from exc
    if len(matches) != 1:
        raise MarginAcceptanceError(
            f"accepted margin evidence is not current/durable partition={partition} "
            f"batch={batch_id!r}"
        )
    item = matches[0]
    return AcceptanceOutcome(
        "ACCEPTED",
        batch_id,
        partition,
        item.row_count,
        item.content_hash,
    )


def accept_margin_batch(
    conn,
    batch_id: str,
    *,
    contract=None,
    after_step: Callable[[str], None] | None = None,
) -> AcceptanceOutcome:
    """Tx-B: validate committed landing, then atomically publish canonical + pointer."""
    contract = _contract(contract)
    ensure_margin_acceptance_schema(conn)
    batch = _load_margin_batch(conn, batch_id)
    status, partition = str(batch["status"]), _partition(batch["partition_value"])
    if status == "ACCEPTED":
        return _prove_accepted_outcome(conn, batch_id, partition, contract)
    if status == "REJECTED":
        rejection = conn.execute(
            f"SELECT rejection_code FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
            [batch_id],
        ).fetchone()
        return AcceptanceOutcome(status, batch_id, partition, rejection_code=rejection[0])
    if status != "LANDED":
        raise MarginAcceptanceError(f"unsupported margin batch status={status!r}")
    try:
        validated = _validate_loaded_margin_batch(conn, batch_id, contract, batch)
    except MarginValidationError as exc:
        return _reject(conn, batch_id, partition, exc, after_step=after_step)

    rows = list(validated.canonical_rows)
    content_hash = validated.content_hash

    partition_iso = f"{partition[:4]}-{partition[4:6]}-{partition[6:]}"
    canonical_values = [
        (
            partition_iso,
            row["exchange_id"], row["rzye"], row["rzmre"],
            row["rzche"], row["rqye"], row["rqmcl"], row["rzrqye"], row["rqyl"],
            batch["available_at"], batch_id, row["source_row_hash"],
            str(batch["contract_version"]), contract.config_hash,
        )
        for row in rows
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"DELETE FROM {CANONICAL_TABLE} WHERE trade_date = CAST(? AS DATE)",
            [partition_iso],
        )
        _call(after_step, "after_canonical_delete")
        conn.executemany(
            f"""
            INSERT INTO {CANONICAL_TABLE} (
                trade_date, exchange_id, rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl,
                available_at, ingest_batch_id, source_row_hash, contract_version, config_hash,
                built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            canonical_values,
        )
        _call(after_step, "after_canonical_insert")
        conn.execute(
            f"DELETE FROM {ACCEPTED_TABLE} WHERE dataset_id = ? AND partition_value = ?",
            [DATASET_ID, partition],
        )
        conn.execute(
            f"""
            INSERT INTO {ACCEPTED_TABLE} (
                dataset_id, partition_value, batch_id, contract_version, contract_hash,
                config_hash, row_count, content_hash, observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                DATASET_ID, partition, batch_id, str(batch["contract_version"]),
                contract.contract_hash, contract.config_hash, len(rows), content_hash,
                batch["observed_at"], batch["available_at"],
            ],
        )
        _call(after_step, "after_accepted_upsert")
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'ACCEPTED', canonical_row_count = ?, canonical_hash = ?,
                   validated_at = CURRENT_TIMESTAMP, accepted_at = CURRENT_TIMESTAMP,
                   rejection_code = NULL, rejection_detail = NULL
             WHERE batch_id = ? AND status = 'LANDED'
            """,
            [len(rows), content_hash, batch_id],
        )
        _call(after_step, "after_batch_update")
        conn.execute("COMMIT")
    except Exception as primary_error:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_error:
            primary_error.add_note(
                "ROLLBACK failed; connection state is unknown: "
                f"{type(rollback_error).__name__}: {str(rollback_error)[:300]}"
            )
        raise
    _call(after_step, "after_commit")
    return AcceptanceOutcome("ACCEPTED", batch_id, partition, len(rows), content_hash)


def recover_margin_batch(
    conn,
    batch_id: str,
    *,
    contract=None,
    after_step: Callable[[str], None] | None = None,
) -> AcceptanceOutcome:
    """Recover a LANDED batch or prove an ACK-lost ACCEPTED batch is already durable."""
    contract = _contract(contract)
    ensure_margin_acceptance_schema(conn)
    row = conn.execute(
        f"SELECT status, partition_value, canonical_hash, canonical_row_count "
        f"FROM {INGEST_BATCH_TABLE} WHERE batch_id = ? AND dataset_id = ?",
        [batch_id, DATASET_ID],
    ).fetchone()
    if row is None:
        raise MarginAcceptanceError(f"unknown margin batch_id={batch_id!r}")
    if row[0] == "LANDED":
        return accept_margin_batch(
            conn, batch_id, contract=contract, after_step=after_step
        )
    if row[0] == "REJECTED":
        return accept_margin_batch(conn, batch_id, contract=contract)
    if row[0] != "ACCEPTED":
        raise MarginAcceptanceError(f"unsupported margin batch status={row[0]!r}")
    return _prove_accepted_outcome(
        conn, batch_id, _partition(row[1]), contract
    )
