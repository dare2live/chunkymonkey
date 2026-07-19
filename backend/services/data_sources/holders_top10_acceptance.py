"""E0 formal land→validate→accept for miaoxiang holders_top10 (tracer).

Requires disclosure execution handoff before any write.  Legacy
``fact_top10_holder_period`` direct writes remain NONCONFORMING strangler until
cutover; this module never publishes DatasetSnapshot readiness.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
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
from services.data_sources.holders_top10_contract import (
    HoldersTop10Contract,
    load_holders_top10_contract,
    verify_holders_top10_contract,
)
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    GRAIN,
    LANDING_TABLE,
    PROVIDER_FIELDS,
    SCHEMA_CONTRACT,
    SOURCE,
    WRITER_ID,
)
from services.data_sources.security_day_partition import (
    sha256_text,
    stable_json,
)


class HoldersTop10AcceptanceError(RuntimeError):
    """holders_top10 formal acceptance cannot proceed safely."""


class HoldersTop10ValidationError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class HoldersTop10LandingBatch:
    batch_id: str
    partition_value: str
    observed_at: datetime | str
    available_at: datetime | str | None
    rows: Iterable[Mapping[str, Any]]
    request: Mapping[str, Any]
    source: str = SOURCE
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True)
class HoldersTop10AcceptanceOutcome:
    status: str
    batch_id: str
    partition_value: str
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None


def _require_handoff(
    contract: HoldersTop10Contract,
    handoff: HoldersTop10Contract | None,
) -> HoldersTop10Contract:
    if handoff is None or handoff is not contract:
        raise HoldersTop10AcceptanceError(
            "holders_top10 formal land/accept requires disclosure execution_handoff "
            "(propagate_disclosure_execution_contract); naked writes are forbidden"
        )
    return verify_holders_top10_contract(handoff)


def _partition(value: Any) -> str:
    compact = str(value or "").replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise HoldersTop10AcceptanceError(f"invalid notice_date partition={value!r}")
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise HoldersTop10AcceptanceError(
            f"invalid notice_date partition={value!r}"
        ) from exc
    return compact


def _aware(value: datetime | str | None, field: str) -> datetime:
    if value is None:
        raise HoldersTop10AcceptanceError(f"{field} is required (fail closed)")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HoldersTop10AcceptanceError(f"invalid {field}={value!r}") from exc
    else:
        raise HoldersTop10AcceptanceError(f"invalid {field} type={type(value).__name__}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HoldersTop10AcceptanceError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _notice_cutoff(partition: str) -> datetime:
    """Earliest legal visibility: notice_date 00:00 Asia/Shanghai."""

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


def ensure_holders_top10_acceptance_schema(conn) -> None:
    fields = tuple(SCHEMA_CONTRACT["fields"])
    columns_sql = ",\n        ".join(_canonical_column_sql(field) for field in fields)
    pk_sql = ", ".join(SCHEMA_CONTRACT["primary_key"])
    ddl = (
        INGEST_BATCH_DDL,
        f"""
        CREATE TABLE IF NOT EXISTS {LANDING_TABLE} (
            batch_id VARCHAR NOT NULL,
            row_ordinal INTEGER NOT NULL,
            request_json VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            row_hash VARCHAR NOT NULL,
            PRIMARY KEY (batch_id, row_ordinal)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {CANONICAL_TABLE} (
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
        verify_accepted_evidence_schema(
            conn, error_type=HoldersTop10AcceptanceError
        )
        landing_cols = set(_columns(conn, LANDING_TABLE))
        if landing_cols != expected_landing:
            raise HoldersTop10AcceptanceError(
                f"{LANDING_TABLE} schema drift: "
                f"missing={sorted(expected_landing - landing_cols)} "
                f"extra={sorted(landing_cols - expected_landing)}"
            )
        canonical_cols = set(_columns(conn, CANONICAL_TABLE))
        if canonical_cols != expected_canonical:
            raise HoldersTop10AcceptanceError(
                f"{CANONICAL_TABLE} schema drift: "
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


def _validate_provider_row(
    row: Mapping[str, Any], *, partition: str
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise HoldersTop10ValidationError("INVALID_ROW", "provider row must be a mapping")
    missing = [name for name in PROVIDER_FIELDS if name not in row]
    if missing:
        raise HoldersTop10ValidationError(
            "MISSING_FIELDS", f"missing provider fields: {missing}"
        )
    notice = row.get("notice_date")
    if notice is None or str(notice).strip() == "":
        raise HoldersTop10ValidationError(
            "MISSING_NOTICE_DATE", "notice_date is required for availability axis"
        )
    notice_compact = _partition(notice)
    if notice_compact != partition:
        raise HoldersTop10ValidationError(
            "PARTITION_MISMATCH",
            f"row notice_date={notice_compact} partition={partition}",
        )
    stock = str(row.get("stock_code") or "").strip()
    if not stock:
        raise HoldersTop10ValidationError("INVALID_STOCK", "stock_code required")
    report = _partition(row.get("report_date"))
    holder_set = str(row.get("holder_set") or "").strip()
    if not holder_set:
        raise HoldersTop10ValidationError("EMPTY_TEXT", "holder_set cannot be empty")
    holder_name = str(row.get("holder_name") or "").strip()
    if not holder_name:
        raise HoldersTop10ValidationError("EMPTY_TEXT", "holder_name cannot be empty")
    try:
        holder_rank = int(row["holder_rank"])
        row_seq = int(row["row_seq"])
    except (TypeError, ValueError) as exc:
        raise HoldersTop10ValidationError(
            "INVALID_NUMERIC", "holder_rank/row_seq must be int"
        ) from exc
    ratio = row.get("hold_ratio_float")
    if ratio is not None:
        try:
            ratio = float(ratio)
        except (TypeError, ValueError) as exc:
            raise HoldersTop10ValidationError(
                "INVALID_NUMERIC", f"hold_ratio_float={ratio!r}"
            ) from exc
    is_exit = row.get("is_exit_row")
    if not isinstance(is_exit, bool):
        raise HoldersTop10ValidationError(
            "INVALID_EXIT_FLAG", "is_exit_row must be bool"
        )
    return {
        "stock_code": stock,
        "report_date": report,
        "holder_set": holder_set,
        "holder_rank": holder_rank,
        "row_seq": row_seq,
        "holder_name": holder_name,
        "hold_ratio_float": ratio,
        "notice_date": notice_compact,
        "is_exit_row": is_exit,
    }


def land_holders_top10_batch(
    conn,
    batch: HoldersTop10LandingBatch,
    contract: HoldersTop10Contract,
    *,
    handoff: HoldersTop10Contract | None = None,
    after_step: Callable[[str], None] | None = None,
) -> str:
    """Tx-A: persist provider rows without universe filtering."""

    contract = _require_handoff(contract, handoff)
    ensure_holders_top10_acceptance_schema(conn)
    batch_id = str(batch.batch_id or "").strip()
    if not batch_id:
        raise HoldersTop10AcceptanceError("batch_id must be non-empty")
    partition = _partition(batch.partition_value)
    if str(batch.contract_version) != contract.contract_version:
        raise HoldersTop10AcceptanceError(
            f"batch contract_version={batch.contract_version!r} "
            f"current={contract.contract_version!r}"
        )
    if str(batch.source) != SOURCE:
        raise HoldersTop10AcceptanceError(
            f"batch source={batch.source!r} current={SOURCE!r}"
        )
    observed_at = _aware(batch.observed_at, "observed_at")
    available_at = _aware(batch.available_at, "available_at")
    if observed_at != available_at:
        raise HoldersTop10AcceptanceError(
            "available_at must equal observed_at for holders_top10 tracer "
            "(provider publication clock unavailable independently)"
        )
    rows = list(batch.rows)
    if not rows:
        raise HoldersTop10AcceptanceError("holders_top10 landing rejects empty rows")
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
                "contract_hash": contract.contract_hash,
                "config_hash": contract.config_hash,
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
        raise HoldersTop10AcceptanceError(
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
                DATASET_ID,
                contract.contract_version,
                contract.contract_hash,
                contract.config_hash,
                WRITER_ID,
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
            INSERT INTO {LANDING_TABLE} (
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


def _load_batch(conn, batch_id: str) -> dict[str, Any]:
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
        raise HoldersTop10AcceptanceError(f"unknown batch_id={batch_id!r}")
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
    batch_id: str,
    *,
    code: str,
    detail: str,
) -> HoldersTop10AcceptanceOutcome:
    batch = _load_batch(conn, batch_id)
    partition = _partition(batch["partition_value"])
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
    return HoldersTop10AcceptanceOutcome(
        status="REJECTED",
        batch_id=batch_id,
        partition_value=partition,
        rejection_code=code,
    )


def _canonical_content_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    fields = list(GRAIN) + ["holder_name", "hold_ratio_float", "notice_date"]
    payload = [
        {name: row[name] for name in fields}
        for row in sorted(
            rows,
            key=lambda item: tuple(str(item[k]) for k in GRAIN),
        )
    ]
    return sha256_text(stable_json(payload))


def _candidate_rows(
    conn,
    batch_id: str,
    partition: str,
    *,
    available_at: datetime,
    contract: HoldersTop10Contract,
) -> tuple[dict[str, Any], ...]:
    cutoff = _notice_cutoff(partition)
    if available_at < cutoff:
        raise HoldersTop10ValidationError(
            "FORGED_AVAILABLE_AT",
            f"available_at={available_at.isoformat()} precedes notice_date "
            f"cutoff={cutoff.isoformat()}",
        )
    landed = conn.execute(
        f"""
        SELECT row_ordinal, payload_json, row_hash
          FROM {LANDING_TABLE}
         WHERE batch_id = ?
         ORDER BY row_ordinal
        """,
        [batch_id],
    ).fetchall()
    if not landed:
        raise HoldersTop10ValidationError("EMPTY_LANDING", "landing has zero rows")
    built_at = datetime.now(timezone.utc)
    seen: set[tuple[Any, ...]] = set()
    canonical: list[dict[str, Any]] = []
    for row_ordinal, payload_json, row_hash in landed:
        payload = json.loads(str(payload_json))
        if sha256_text(stable_json(payload)) != str(row_hash):
            raise HoldersTop10ValidationError(
                "ROW_HASH_MISMATCH", f"row_ordinal={row_ordinal}"
            )
        provider = _validate_provider_row(payload, partition=partition)
        key = tuple(provider[name] for name in GRAIN)
        if key in seen:
            raise HoldersTop10ValidationError(
                "DUPLICATE_GRAIN", f"duplicate grain={key}"
            )
        seen.add(key)
        canonical.append(
            {
                **provider,
                "available_at": available_at,
                "ingest_batch_id": batch_id,
                "source_row_hash": str(row_hash),
                "contract_version": contract.contract_version,
                "config_hash": contract.config_hash,
                "built_at": built_at,
            }
        )
    return tuple(canonical)


def accept_holders_top10_batch(
    conn,
    batch_id: str,
    contract: HoldersTop10Contract,
    *,
    handoff: HoldersTop10Contract | None = None,
    after_step: Callable[[str], None] | None = None,
) -> HoldersTop10AcceptanceOutcome:
    """Tx-B: validate landing, then atomically replace canonical + pointer."""

    contract = _require_handoff(contract, handoff)
    ensure_holders_top10_acceptance_schema(conn)
    batch = _load_batch(conn, batch_id)
    status = str(batch["status"])
    partition = _partition(batch["partition_value"])
    if status == "ACCEPTED":
        pointer = conn.execute(
            f"""
            SELECT row_count, content_hash FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND partition_value = ? AND batch_id = ?
            """,
            [DATASET_ID, partition, batch_id],
        ).fetchone()
        if pointer is None:
            raise HoldersTop10AcceptanceError(
                "accepted batch missing accepted_partition pointer"
            )
        return HoldersTop10AcceptanceOutcome(
            status="ACCEPTED",
            batch_id=batch_id,
            partition_value=partition,
            row_count=int(pointer[0]),
            content_hash=str(pointer[1]),
        )
    if status == "REJECTED":
        return HoldersTop10AcceptanceOutcome(
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
        raise HoldersTop10AcceptanceError(f"batch status={status!r} not acceptible")
    if str(batch["contract_hash"]) != contract.contract_hash:
        raise HoldersTop10AcceptanceError("landed contract_hash drift vs handoff")
    if str(batch["config_hash"]) != contract.config_hash:
        raise HoldersTop10AcceptanceError("landed config_hash drift vs handoff")

    available_at = _aware(batch["available_at"], "available_at")
    try:
        canonical = _candidate_rows(
            conn,
            batch_id,
            partition,
            available_at=available_at,
            contract=contract,
        )
    except HoldersTop10ValidationError as exc:
        return _reject(conn, batch_id, code=exc.code, detail=exc.detail)

    content_hash = _canonical_content_hash(canonical)
    row_count = len(canonical)
    observed_at = _aware(batch["observed_at"], "observed_at")
    accepted_at = datetime.now(timezone.utc)
    if accepted_at < available_at:
        accepted_at = available_at
    field_names = [str(f["name"]) for f in SCHEMA_CONTRACT["fields"]]
    insert_cols = ", ".join(field_names)
    placeholders = ", ".join("?" for _ in field_names)
    values = [tuple(row[name] for name in field_names) for row in canonical]

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"DELETE FROM {CANONICAL_TABLE} WHERE notice_date = ?",
            [partition],
        )
        _call(after_step, "after_canonical_delete")
        conn.executemany(
            f"INSERT INTO {CANONICAL_TABLE} ({insert_cols}) VALUES ({placeholders})",
            values,
        )
        _call(after_step, "after_canonical_insert")
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
                DATASET_ID,
                partition,
                batch_id,
                contract.contract_version,
                contract.contract_hash,
                contract.config_hash,
                row_count,
                content_hash,
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
    return HoldersTop10AcceptanceOutcome(
        status="ACCEPTED",
        batch_id=batch_id,
        partition_value=partition,
        row_count=row_count,
        content_hash=content_hash,
    )


def publish_accepted_holders_top10_partition(
    conn,
    batch: HoldersTop10LandingBatch,
    contract: HoldersTop10Contract | None = None,
) -> HoldersTop10AcceptanceOutcome:
    """Land + accept with mandatory disclosure execution handoff."""

    from services.data_sources.formal_execution import (
        propagate_disclosure_execution_contract,
    )

    contract = verify_holders_top10_contract(contract or load_holders_top10_contract())
    handed = propagate_disclosure_execution_contract("holders_top10", contract)
    land_holders_top10_batch(conn, batch, handed, handoff=handed)
    return accept_holders_top10_batch(conn, batch.batch_id, handed, handoff=handed)


def runtime_surface() -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        "writer_id": WRITER_ID,
        "production_write": "formal_default_legacy_mirror",
        "legacy_direct_write": "nonconforming_escape_hatch",
        "dataset_snapshot": "blocked_until_e0_cutover",
        "provider_sync": "fixture_or_authorized_manual_only",
    }


__all__ = [
    "HoldersTop10AcceptanceError",
    "HoldersTop10AcceptanceOutcome",
    "HoldersTop10LandingBatch",
    "HoldersTop10ValidationError",
    "accept_holders_top10_batch",
    "ensure_holders_top10_acceptance_schema",
    "land_holders_top10_batch",
    "publish_accepted_holders_top10_partition",
    "runtime_surface",
]
