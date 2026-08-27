"""Shared land→validate→accept mechanics for E0 disclosure event-time domains.

Each domain still owns schema identity, contract factory, provider-row validation,
and public runtime.  This module is not a plugin framework — it only shares the
Tx-A / Tx-B evidence tables and partition pointer choreography used by
holders_top10 / org_holding / stk_holdertrade tracers.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from services.data_sources.accepted_schema import (
    ACCEPTED_PARTITION_DDL,
    ACCEPTED_TABLE,
    INGEST_BATCH_DDL,
    INGEST_BATCH_TABLE,
    verify_accepted_evidence_schema,
)
from services.data_sources.security_day_partition import sha256_text, stable_json


class DisclosureEventError(RuntimeError):
    """Disclosure event-time acceptance cannot proceed safely."""


class DisclosureEventValidationError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DisclosureEventLandingBatch:
    batch_id: str
    partition_value: str
    observed_at: datetime | str
    available_at: datetime | str | None
    rows: Iterable[Mapping[str, Any]]
    request: Mapping[str, Any]
    source: str
    contract_version: str


@dataclass(frozen=True)
class DisclosureEventAcceptanceOutcome:
    status: str
    batch_id: str
    partition_value: str
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None


@dataclass(frozen=True)
class DisclosureEventDomain:
    domain: str
    dataset_id: str
    landing_table: str
    canonical_table: str
    source: str
    writer_id: str
    partition_field: str
    grain: tuple[str, ...]
    content_hash_fields: tuple[str, ...]
    schema_contract: Mapping[str, Any]
    validate_provider_row: Callable[[Mapping[str, Any], str], dict[str, Any]]
    # partition = delete all rows for partition_field (default).
    # report_dates_in_batch = org_holding: multiple report_date share available_date.
    canonical_delete_scope: str = "partition"


def require_disclosure_handoff(
    *,
    domain: str,
    contract: Any,
    handoff: Any,
    verify: Callable[[Any], Any],
    error_type: type[Exception] = DisclosureEventError,
) -> Any:
    if handoff is None or handoff is not contract:
        raise error_type(
            f"{domain} formal land/accept requires disclosure execution_handoff "
            "(propagate_disclosure_execution_contract); naked writes are forbidden"
        )
    return verify(handoff)


def partition_yyyymmdd(
    value: Any,
    *,
    field: str = "partition",
    error_type: type[Exception] = DisclosureEventError,
) -> str:
    compact = str(value or "").replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise error_type(f"invalid {field}={value!r}")
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise error_type(f"invalid {field}={value!r}") from exc
    return compact


def aware_instant(
    value: datetime | str | None,
    field: str,
    *,
    error_type: type[Exception] = DisclosureEventError,
) -> datetime:
    if value is None:
        raise error_type(f"{field} is required (fail closed)")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise error_type(f"invalid {field}={value!r}") from exc
    else:
        raise error_type(f"invalid {field} type={type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error_type(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def event_cutoff_shanghai(partition: str) -> datetime:
    """Earliest legal visibility: partition date 00:00 Asia/Shanghai."""

    local = datetime(
        int(partition[:4]),
        int(partition[4:6]),
        int(partition[6:8]),
        0,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    return local.astimezone(timezone.utc)


def _columns(conn, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE {table}").fetchall()
    }


def _canonical_column_sql(field: Mapping[str, Any]) -> str:
    name = str(field["name"])
    parts = [name, str(field["duckdb_type"])]
    if not bool(field["nullable"]):
        parts.append("NOT NULL")
    return " ".join(parts)


def ensure_disclosure_event_schema(
    conn,
    domain: DisclosureEventDomain,
    *,
    error_type: type[Exception] = DisclosureEventError,
) -> None:
    fields = tuple(domain.schema_contract["fields"])
    columns_sql = ",\n        ".join(_canonical_column_sql(field) for field in fields)
    pk_sql = ", ".join(domain.schema_contract["primary_key"])
    ddl = (
        INGEST_BATCH_DDL,
        f"""
        CREATE TABLE IF NOT EXISTS {domain.landing_table} (
            batch_id VARCHAR NOT NULL,
            row_ordinal INTEGER NOT NULL,
            request_json VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            row_hash VARCHAR NOT NULL,
            PRIMARY KEY (batch_id, row_ordinal)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {domain.canonical_table} (
            {columns_sql},
            PRIMARY KEY ({pk_sql})
        )
        """,
        ACCEPTED_PARTITION_DDL,
    )
    expected_landing = {
        "batch_id",
        "row_ordinal",
        "request_json",
        "payload_json",
        "row_hash",
    }
    expected_canonical = {str(field["name"]) for field in fields}
    conn.execute("BEGIN TRANSACTION")
    try:
        for statement in ddl:
            conn.execute(statement)
        verify_accepted_evidence_schema(conn, error_type=error_type)
        landing_cols = set(_columns(conn, domain.landing_table))
        if landing_cols != expected_landing:
            raise error_type(
                f"{domain.landing_table} schema drift: "
                f"missing={sorted(expected_landing - landing_cols)} "
                f"extra={sorted(landing_cols - expected_landing)}"
            )
        canonical_cols = set(_columns(conn, domain.canonical_table))
        if canonical_cols != expected_canonical:
            raise error_type(
                f"{domain.canonical_table} schema drift: "
                f"missing={sorted(expected_canonical - canonical_cols)} "
                f"extra={sorted(canonical_cols - expected_canonical)}"
            )
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


def _call(after_step: Callable[[str], None] | None, step: str) -> None:
    if after_step is not None:
        after_step(step)


def land_disclosure_event_batch(
    conn,
    domain: DisclosureEventDomain,
    batch: DisclosureEventLandingBatch,
    *,
    contract_version: str,
    contract_hash: str,
    config_hash: str,
    after_step: Callable[[str], None] | None = None,
    error_type: type[Exception] = DisclosureEventError,
) -> str:
    """Tx-A: persist provider rows without universe filtering."""

    ensure_disclosure_event_schema(conn, domain, error_type=error_type)
    batch_id = str(batch.batch_id or "").strip()
    if not batch_id:
        raise error_type("batch_id must be non-empty")
    partition = partition_yyyymmdd(
        batch.partition_value,
        field=domain.partition_field,
        error_type=error_type,
    )
    if str(batch.contract_version) != contract_version:
        raise error_type(
            f"batch contract_version={batch.contract_version!r} "
            f"current={contract_version!r}"
        )
    if str(batch.source) != domain.source:
        raise error_type(
            f"batch source={batch.source!r} current={domain.source!r}"
        )
    observed_at = aware_instant(
        batch.observed_at, "observed_at", error_type=error_type
    )
    available_at = aware_instant(
        batch.available_at, "available_at", error_type=error_type
    )
    if observed_at != available_at:
        raise error_type(
            f"available_at must equal observed_at for {domain.domain} tracer "
            "(provider publication clock unavailable independently)"
        )
    rows = list(batch.rows)
    if not rows:
        raise error_type(f"{domain.domain} landing rejects empty rows")
    request = dict(batch.request)
    request_json = stable_json(request)
    landing_rows: list[tuple[Any, ...]] = []
    signatures: list[str] = []
    for ordinal, row in enumerate(rows, start=1):
        payload_json = stable_json(row)
        row_hash = sha256_text(payload_json)
        signatures.append(f"{ordinal}:{row_hash}")
        landing_rows.append((batch_id, ordinal, request_json, payload_json, row_hash))
    payload_hash = sha256_text(
        stable_json(
            {
                "partition": partition,
                "source": batch.source,
                "contract_version": batch.contract_version,
                "contract_hash": contract_hash,
                "config_hash": config_hash,
                "observed_at": observed_at.isoformat(),
                "available_at": available_at.isoformat(),
                "request": request,
                "row_signatures": signatures,
            }
        )
    )
    existing = conn.execute(
        f"SELECT payload_hash, status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
        [batch_id],
    ).fetchone()
    if existing is not None:
        if existing[0] == payload_hash:
            return batch_id
        raise error_type(
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'LANDED', ?, ?, 1, 1, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                batch_id,
                domain.dataset_id,
                contract_version,
                contract_hash,
                config_hash,
                domain.writer_id,
                partition,
                batch.source,
                request_json,
                stable_json([{"status": "success", "row_count": len(rows)}]),
                len(landing_rows),
                payload_hash,
                observed_at,
                available_at,
            ],
        )
        _call(after_step, "after_batch_insert")
        conn.executemany(
            f"""
            INSERT INTO {domain.landing_table} (
                batch_id, row_ordinal, request_json, payload_json, row_hash
            ) VALUES (?, ?, ?, ?, ?)
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


def _load_batch(
    conn,
    domain: DisclosureEventDomain,
    batch_id: str,
    *,
    error_type: type[Exception] = DisclosureEventError,
) -> dict[str, Any]:
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
        [batch_id, domain.dataset_id],
    ).fetchone()
    if row is None:
        raise error_type(f"unknown batch_id={batch_id!r}")
    keys = (
        "status",
        "partition_value",
        "observed_at",
        "available_at",
        "contract_version",
        "contract_hash",
        "config_hash",
        "canonical_hash",
        "canonical_row_count",
        "request_json",
        "fragment_outcomes_json",
        "expected_fragment_count",
        "completed_fragment_count",
        "failed_fragment_count",
        "landing_row_count",
        "payload_hash",
        "source_name",
        "writer_id",
    )
    return dict(zip(keys, row, strict=True))


def _reject(
    conn,
    domain: DisclosureEventDomain,
    batch_id: str,
    *,
    code: str,
    detail: str,
) -> DisclosureEventAcceptanceOutcome:
    batch = _load_batch(conn, domain, batch_id)
    partition = partition_yyyymmdd(
        batch["partition_value"], field=domain.partition_field
    )
    conn.execute(
        f"""
        UPDATE {INGEST_BATCH_TABLE}
           SET status = 'REJECTED',
               validated_at = CURRENT_TIMESTAMP,
               rejection_code = ?,
               rejection_detail = ?
         WHERE batch_id = ?
        """,
        [code, detail[:500], batch_id],
    )
    return DisclosureEventAcceptanceOutcome(
        status="REJECTED",
        batch_id=batch_id,
        partition_value=partition,
        rejection_code=code,
    )


def _canonical_content_hash(
    domain: DisclosureEventDomain, rows: Sequence[Mapping[str, Any]]
) -> str:
    fields = list(domain.content_hash_fields)
    payload = [
        {name: row[name] for name in fields}
        for row in sorted(
            rows,
            key=lambda item: tuple(str(item[k]) for k in domain.grain),
        )
    ]
    return sha256_text(stable_json(payload))


def partition_accepted_pointer_stats(
    conn,
    domain: DisclosureEventDomain,
    partition: str,
) -> tuple[int, str]:
    """Full-partition row_count + content_hash for accepted_partition pointer.

    For ``report_dates_in_batch`` domains (org_holding), one available_date may
    hold multiple report_date batches. The pointer must describe the merged
    canonical partition, not the last batch alone.

    Streams ordered rows into the same JSON-array digest as ``stable_json`` of
    the full payload (no intermediate all-rows list).
    """
    partition_col = domain.partition_field
    # ``stable_json`` sorts mapping keys. Selecting them in that same order lets
    # the hot path skip a per-row recursive normalization + key sort while
    # preserving the byte-for-byte hash contract.
    fields = sorted(domain.content_hash_fields)
    if not fields:
        raise DisclosureEventError(
            f"{domain.domain}: content_hash_fields empty; cannot build pointer"
        )
    select_cols = ", ".join(fields)
    order_cols = ", ".join(f"CAST({k} AS VARCHAR)" for k in domain.grain)
    result = conn.execute(
        f"""
        SELECT {select_cols}
          FROM {domain.canonical_table}
         WHERE {partition_col} = ?
         ORDER BY {order_cols}
        """,
        [partition],
    )
    digest = sha256()
    digest.update(b"[")
    n = 0
    while True:
        chunk = result.fetchmany(50_000)
        if not chunk:
            break
        for row in chunk:
            if n:
                digest.update(b",")
            item = dict(zip(fields, row, strict=True))
            digest.update(
                json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            n += 1
    digest.update(b"]")
    return n, digest.hexdigest()


def _existing_canonical_keys(
    conn,
    domain: DisclosureEventDomain,
    rows: Sequence[Mapping[str, Any]],
) -> set[tuple[Any, ...]]:
    """PK tuples already in canonical for the grains in ``rows``."""
    if not rows:
        return set()
    grain = domain.grain
    select_cols = ", ".join(grain)
    if "report_date" in grain:
        dates = sorted({str(row["report_date"]) for row in rows})
        placeholders = ", ".join("?" for _ in dates)
        fetched = conn.execute(
            f"""
            SELECT {select_cols}
              FROM {domain.canonical_table}
             WHERE report_date IN ({placeholders})
            """,
            dates,
        ).fetchall()
    else:
        fetched = conn.execute(
            f"SELECT {select_cols} FROM {domain.canonical_table}"
        ).fetchall()
    return {tuple(row) for row in fetched}


def _candidate_rows(
    conn,
    domain: DisclosureEventDomain,
    batch_id: str,
    partition: str,
    *,
    available_at: datetime,
    contract_version: str,
    config_hash: str,
) -> tuple[dict[str, Any], ...]:
    cutoff = event_cutoff_shanghai(partition)
    if available_at < cutoff:
        raise DisclosureEventValidationError(
            "FORGED_AVAILABLE_AT",
            f"available_at={available_at.isoformat()} precedes {domain.partition_field} "
            f"cutoff={cutoff.isoformat()}",
        )
    landed = conn.execute(
        f"""
        SELECT row_ordinal, payload_json, row_hash
          FROM {domain.landing_table}
         WHERE batch_id = ?
         ORDER BY row_ordinal
        """,
        [batch_id],
    ).fetchall()
    if not landed:
        raise DisclosureEventValidationError("EMPTY_LANDING", "landing has zero rows")
    built_at = datetime.now(timezone.utc)
    seen: set[tuple[Any, ...]] = set()
    canonical: list[dict[str, Any]] = []
    for row_ordinal, payload_json, row_hash in landed:
        payload = json.loads(str(payload_json))
        if sha256_text(stable_json(payload)) != str(row_hash):
            raise DisclosureEventValidationError(
                "ROW_HASH_MISMATCH", f"row_ordinal={row_ordinal}"
            )
        provider = domain.validate_provider_row(payload, partition)
        key = tuple(provider[name] for name in domain.grain)
        if key in seen:
            raise DisclosureEventValidationError(
                "DUPLICATE_GRAIN", f"duplicate grain={key}"
            )
        seen.add(key)
        canonical.append(
            {
                **provider,
                "available_at": available_at,
                "ingest_batch_id": batch_id,
                "source_row_hash": str(row_hash),
                "contract_version": contract_version,
                "config_hash": config_hash,
                "built_at": built_at,
            }
        )
    return tuple(canonical)


def accept_disclosure_event_batch(
    conn,
    domain: DisclosureEventDomain,
    batch_id: str,
    *,
    contract_version: str,
    contract_hash: str,
    config_hash: str,
    after_step: Callable[[str], None] | None = None,
    error_type: type[Exception] = DisclosureEventError,
    merge_grains: bool = False,
) -> DisclosureEventAcceptanceOutcome:
    """Tx-B: validate landing, then atomically replace canonical + pointer.

    ``merge_grains=True`` inserts new grains only (org late-filing growth).
    Default still replaces the configured delete scope — first fill / ops replace.
    """

    ensure_disclosure_event_schema(conn, domain, error_type=error_type)
    batch = _load_batch(conn, domain, batch_id, error_type=error_type)
    status = str(batch["status"])
    partition = partition_yyyymmdd(
        batch["partition_value"],
        field=domain.partition_field,
        error_type=error_type,
    )
    if status == "ACCEPTED":
        pointer = conn.execute(
            f"""
            SELECT 1 FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND partition_value = ?
            """,
            [domain.dataset_id, partition],
        ).fetchone()
        if pointer is None:
            raise error_type("accepted batch missing accepted_partition pointer")
        if batch["canonical_row_count"] is None or batch["canonical_hash"] is None:
            raise error_type("accepted batch missing immutable canonical evidence")
        return DisclosureEventAcceptanceOutcome(
            status="ACCEPTED",
            batch_id=batch_id,
            partition_value=partition,
            row_count=int(batch["canonical_row_count"]),
            content_hash=str(batch["canonical_hash"]),
        )
    if status == "REJECTED":
        return DisclosureEventAcceptanceOutcome(
            status="REJECTED",
            batch_id=batch_id,
            partition_value=partition,
            rejection_code=str(
                conn.execute(
                    f"SELECT rejection_code FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?",
                    [batch_id],
                ).fetchone()[0]
            ),
        )
    if status != "LANDED":
        raise error_type(f"batch status={status!r} not acceptible")
    if str(batch["contract_hash"]) != contract_hash:
        raise error_type("landed contract_hash drift vs handoff")
    if str(batch["config_hash"]) != config_hash:
        raise error_type("landed config_hash drift vs handoff")

    available_at = aware_instant(
        batch["available_at"], "available_at", error_type=error_type
    )
    try:
        canonical = _candidate_rows(
            conn,
            domain,
            batch_id,
            partition,
            available_at=available_at,
            contract_version=contract_version,
            config_hash=config_hash,
        )
    except DisclosureEventValidationError as exc:
        return _reject(conn, domain, batch_id, code=exc.code, detail=exc.detail)

    if merge_grains:
        existing_keys = _existing_canonical_keys(conn, domain, canonical)
        grain = domain.grain
        canonical = tuple(
            row
            for row in canonical
            if tuple(row[name] for name in grain) not in existing_keys
        )

    content_hash = _canonical_content_hash(domain, canonical)
    row_count = len(canonical)
    observed_at = aware_instant(
        batch["observed_at"], "observed_at", error_type=error_type
    )
    accepted_at = datetime.now(timezone.utc)
    if accepted_at < available_at:
        accepted_at = available_at
    field_names = [str(f["name"]) for f in domain.schema_contract["fields"]]
    insert_cols = ", ".join(field_names)
    insert_placeholders = ", ".join("?" for _ in field_names)
    values = [tuple(row[name] for name in field_names) for row in canonical]
    partition_col = domain.partition_field

    conn.execute("BEGIN TRANSACTION")
    try:
        if not merge_grains:
            if domain.canonical_delete_scope == "report_dates_in_batch":
                report_dates = sorted({str(row["report_date"]) for row in canonical})
                delete_placeholders = ", ".join("?" for _ in report_dates)
                conn.execute(
                    f"""
                    DELETE FROM {domain.canonical_table}
                     WHERE {partition_col} = ?
                       AND report_date IN ({delete_placeholders})
                    """,
                    [partition, *report_dates],
                )
            else:
                conn.execute(
                    f"DELETE FROM {domain.canonical_table} WHERE {partition_col} = ?",
                    [partition],
                )
        _call(after_step, "after_canonical_delete")
        if values:
            conn.executemany(
                f"INSERT INTO {domain.canonical_table} ({insert_cols}) VALUES ({insert_placeholders})",
                values,
            )
        _call(after_step, "after_canonical_insert")
        # Batch evidence stays batch-scoped; accepted pointer is partition-scoped.
        # org_holding merges multiple report_date into one available_date partition —
        # pointer row_count/content_hash must cover the full merged canonical set.
        if merge_grains or domain.canonical_delete_scope == "report_dates_in_batch":
            pointer_row_count, pointer_content_hash = partition_accepted_pointer_stats(
                conn, domain, partition
            )
        else:
            pointer_row_count, pointer_content_hash = row_count, content_hash
        if pointer_row_count <= 0:
            raise error_type(
                f"{domain.domain} accept left empty canonical partition={partition}"
            )
        conn.execute(
            f"""
            INSERT INTO {ACCEPTED_TABLE} (
                dataset_id, partition_value, batch_id, contract_version,
                contract_hash, config_hash, row_count, content_hash,
                observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dataset_id, partition_value) DO UPDATE SET
                batch_id = excluded.batch_id,
                contract_version = excluded.contract_version,
                contract_hash = excluded.contract_hash,
                config_hash = excluded.config_hash,
                row_count = excluded.row_count,
                content_hash = excluded.content_hash,
                observed_at = excluded.observed_at,
                available_at = excluded.available_at,
                accepted_at = excluded.accepted_at
            """,
            [
                domain.dataset_id,
                partition,
                batch_id,
                contract_version,
                contract_hash,
                config_hash,
                pointer_row_count,
                pointer_content_hash,
                observed_at,
                available_at,
                accepted_at,
            ],
        )
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'ACCEPTED',
                   validated_at = CURRENT_TIMESTAMP,
                   accepted_at = ?,
                   canonical_row_count = ?,
                   canonical_hash = ?,
                   rejection_code = NULL,
                   rejection_detail = NULL
             WHERE batch_id = ?
            """,
            [accepted_at, row_count, content_hash, batch_id],
        )
        _call(after_step, "after_accept_update")
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
    _call(after_step, "after_accept_commit")
    return DisclosureEventAcceptanceOutcome(
        status="ACCEPTED",
        batch_id=batch_id,
        partition_value=partition,
        row_count=row_count,
        content_hash=content_hash,
    )


__all__ = [
    "DisclosureEventAcceptanceOutcome",
    "DisclosureEventDomain",
    "DisclosureEventError",
    "DisclosureEventLandingBatch",
    "DisclosureEventValidationError",
    "accept_disclosure_event_batch",
    "aware_instant",
    "ensure_disclosure_event_schema",
    "event_cutoff_shanghai",
    "land_disclosure_event_batch",
    "partition_accepted_pointer_stats",
    "partition_yyyymmdd",
    "require_disclosure_handoff",
]
