"""Shared DuckDB schema for formal batch and accepted-partition evidence.

Domain schemas compose these two tables with their landing and canonical tables.
The standalone ensure function is useful for proving the shared contract; domain
schema bootstraps should call the create/verify helpers inside their own atomic
transaction.
"""
from __future__ import annotations

from typing import Any


INGEST_BATCH_TABLE = "ingest_batch"
ACCEPTED_TABLE = "accepted_partition"


class AcceptedEvidenceSchemaError(RuntimeError):
    """Shared acceptance evidence is missing or structurally unsafe."""


INGEST_BATCH_DDL = f"""
CREATE TABLE IF NOT EXISTS {INGEST_BATCH_TABLE} (
    batch_id VARCHAR PRIMARY KEY,
    dataset_id VARCHAR NOT NULL,
    contract_version VARCHAR NOT NULL,
    contract_hash VARCHAR NOT NULL,
    config_hash VARCHAR NOT NULL,
    writer_id VARCHAR NOT NULL,
    partition_value VARCHAR NOT NULL,
    source_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('LANDED', 'REJECTED', 'ACCEPTED')),
    request_json VARCHAR NOT NULL,
    fragment_outcomes_json VARCHAR NOT NULL,
    expected_fragment_count INTEGER NOT NULL,
    completed_fragment_count INTEGER NOT NULL,
    failed_fragment_count INTEGER NOT NULL,
    landing_row_count BIGINT NOT NULL,
    canonical_row_count BIGINT,
    payload_hash VARCHAR NOT NULL,
    canonical_hash VARCHAR,
    observed_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    landed_at TIMESTAMPTZ NOT NULL,
    validated_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ,
    rejection_code VARCHAR,
    rejection_detail VARCHAR
)
"""

ACCEPTED_PARTITION_DDL = f"""
CREATE TABLE IF NOT EXISTS {ACCEPTED_TABLE} (
    dataset_id VARCHAR NOT NULL,
    partition_value VARCHAR NOT NULL,
    batch_id VARCHAR NOT NULL UNIQUE,
    contract_version VARCHAR NOT NULL,
    contract_hash VARCHAR NOT NULL,
    config_hash VARCHAR NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count > 0),
    content_hash VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dataset_id, partition_value)
)
"""

ACCEPTED_EVIDENCE_DDL = (INGEST_BATCH_DDL, ACCEPTED_PARTITION_DDL)

_FORMAL_COLUMNS = {
    INGEST_BATCH_TABLE: {
        "batch_id", "dataset_id", "contract_version", "contract_hash", "config_hash",
        "writer_id", "partition_value", "source_name", "status", "request_json",
        "fragment_outcomes_json", "expected_fragment_count", "completed_fragment_count",
        "failed_fragment_count", "landing_row_count", "canonical_row_count", "payload_hash",
        "canonical_hash", "observed_at", "available_at", "landed_at", "validated_at",
        "accepted_at", "rejection_code", "rejection_detail",
    },
    ACCEPTED_TABLE: {
        "dataset_id", "partition_value", "batch_id", "contract_version", "contract_hash",
        "config_hash", "row_count", "content_hash", "observed_at", "available_at",
        "accepted_at",
    },
}

_TYPE_OVERRIDES = {
    INGEST_BATCH_TABLE: {
        "expected_fragment_count": "INTEGER",
        "completed_fragment_count": "INTEGER",
        "failed_fragment_count": "INTEGER",
        "landing_row_count": "BIGINT",
        "canonical_row_count": "BIGINT",
        "observed_at": "TIMESTAMP WITH TIME ZONE",
        "available_at": "TIMESTAMP WITH TIME ZONE",
        "landed_at": "TIMESTAMP WITH TIME ZONE",
        "validated_at": "TIMESTAMP WITH TIME ZONE",
        "accepted_at": "TIMESTAMP WITH TIME ZONE",
    },
    ACCEPTED_TABLE: {
        "row_count": "BIGINT",
        "observed_at": "TIMESTAMP WITH TIME ZONE",
        "available_at": "TIMESTAMP WITH TIME ZONE",
        "accepted_at": "TIMESTAMP WITH TIME ZONE",
    },
}

_PRIMARY_KEYS = {
    INGEST_BATCH_TABLE: ("batch_id",),
    ACCEPTED_TABLE: ("dataset_id", "partition_value"),
}
_UNIQUE_KEYS = {ACCEPTED_TABLE: {("batch_id",)}}
_NOT_NULL = {
    INGEST_BATCH_TABLE: {
        "batch_id", "dataset_id", "contract_version", "contract_hash", "config_hash",
        "writer_id", "partition_value", "source_name", "status", "request_json",
        "fragment_outcomes_json", "expected_fragment_count", "completed_fragment_count",
        "failed_fragment_count", "landing_row_count", "payload_hash", "observed_at",
        "available_at", "landed_at",
    },
    ACCEPTED_TABLE: set(_FORMAL_COLUMNS[ACCEPTED_TABLE]),
}
_CHECK_MARKERS = {
    INGEST_BATCH_TABLE: ("STATUSIN'LANDED','REJECTED','ACCEPTED'",),
    ACCEPTED_TABLE: ("ROW_COUNT>0",),
}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _current_namespace(conn) -> tuple[str, str]:
    row = conn.execute("SELECT current_database(), current_schema()").fetchone()
    if not row or not row[0] or not row[1]:
        raise AcceptedEvidenceSchemaError(
            "cannot resolve current DuckDB database/schema for accepted evidence"
        )
    return str(row[0]), str(row[1])


def _columns(
    conn,
    table: str,
    *,
    database: str,
    schema: str,
) -> dict[str, str]:
    relation = ".".join(
        _quote_identifier(part) for part in (database, schema, table)
    )
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    }


def _constraint_contract(
    conn,
    table: str,
    *,
    database: str,
    schema: str,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT constraint_type, constraint_text, constraint_column_names
          FROM duckdb_constraints()
         WHERE database_name = ? AND schema_name = ? AND table_name = ?
        """,
        [database, schema, table],
    ).fetchall()
    primary_keys: set[tuple[str, ...]] = set()
    unique_keys: set[tuple[str, ...]] = set()
    not_null: set[str] = set()
    checks: list[str] = []
    for constraint_type, constraint_text, columns in rows:
        kind = str(constraint_type).upper()
        key = tuple(str(column) for column in (columns or []))
        if kind == "PRIMARY KEY":
            primary_keys.add(key)
        elif kind == "UNIQUE":
            unique_keys.add(key)
        elif kind == "NOT NULL" and len(key) == 1:
            not_null.add(key[0])
        elif kind == "CHECK":
            checks.append(
                "".join(
                    character
                    for character in str(constraint_text).upper()
                    if not character.isspace() and character not in "()"
                )
            )
    return {
        "primary_keys": primary_keys,
        "unique_keys": unique_keys,
        "not_null": not_null,
        "checks": tuple(checks),
    }


def create_accepted_evidence_tables(conn) -> None:
    """Create the shared tables without owning the surrounding transaction."""

    for statement in ACCEPTED_EVIDENCE_DDL:
        conn.execute(statement)


def verify_accepted_evidence_schema(
    conn,
    *,
    error_type: type[RuntimeError] = AcceptedEvidenceSchemaError,
) -> None:
    """Fail closed if either shared table drifts from the fixed contract."""

    database, schema = _current_namespace(conn)
    for table, expected in _FORMAL_COLUMNS.items():
        actual_types = _columns(
            conn,
            table,
            database=database,
            schema=schema,
        )
        actual = set(actual_types)
        if actual != expected:
            raise error_type(
                f"{table} schema drift: missing={sorted(expected - actual)} "
                f"extra={sorted(actual - expected)}"
            )
        expected_types = _TYPE_OVERRIDES.get(table, {})
        mismatched = {
            column: (actual_types[column], expected_types.get(column, "VARCHAR"))
            for column in expected
            if actual_types[column] != expected_types.get(column, "VARCHAR")
        }
        if mismatched:
            raise error_type(f"{table} type drift: {mismatched}")
        constraints = _constraint_contract(
            conn,
            table,
            database=database,
            schema=schema,
        )
        expected_primary = {_PRIMARY_KEYS[table]}
        if constraints["primary_keys"] != expected_primary:
            raise error_type(
                f"{table} primary-key drift: actual={constraints['primary_keys']} "
                f"expected={expected_primary}"
            )
        expected_unique = _UNIQUE_KEYS.get(table, set())
        if constraints["unique_keys"] != expected_unique:
            raise error_type(
                f"{table} unique-key drift: actual={constraints['unique_keys']} "
                f"expected={expected_unique}"
            )
        expected_not_null = _NOT_NULL[table]
        if constraints["not_null"] != expected_not_null:
            raise error_type(
                f"{table} nullability drift: actual={sorted(constraints['not_null'])} "
                f"expected={sorted(expected_not_null)}"
            )
        expected_checks = _CHECK_MARKERS.get(table, ())
        checks = constraints["checks"]
        if len(checks) != len(expected_checks) or any(
            not any(marker in check for check in checks)
            for marker in expected_checks
        ):
            raise error_type(
                f"{table} check-constraint drift: actual={checks} "
                f"expected_markers={expected_checks}"
            )


def ensure_accepted_evidence_schema(conn) -> None:
    """Create and verify the shared evidence tables atomically."""

    conn.execute("BEGIN TRANSACTION")
    try:
        create_accepted_evidence_tables(conn)
        verify_accepted_evidence_schema(conn)
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


__all__ = [
    "ACCEPTED_EVIDENCE_DDL",
    "ACCEPTED_PARTITION_DDL",
    "ACCEPTED_TABLE",
    "INGEST_BATCH_DDL",
    "INGEST_BATCH_TABLE",
    "AcceptedEvidenceSchemaError",
    "create_accepted_evidence_tables",
    "ensure_accepted_evidence_schema",
    "verify_accepted_evidence_schema",
]
