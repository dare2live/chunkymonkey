"""Fixed DuckDB schema contract for the formal TuShare margin boundary.

The schema is intentionally domain-specific.  It owns only the formal landing,
canonical, batch-evidence and accepted-pointer tables; the legacy shadow table
remains owned by ``sync_runner`` throughout the strangler phase.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any

from services.data_sources.accepted_schema import (
    ACCEPTED_PARTITION_DDL,
    ACCEPTED_TABLE,
    INGEST_BATCH_DDL,
    INGEST_BATCH_TABLE,
    verify_accepted_evidence_schema,
)


DATASET_ID = "tier0.market_data.margin_exchange_daily"
LANDING_TABLE = "landing_tushare_margin"
CANONICAL_TABLE = "canonical_margin_exchange_daily"
MARGIN_SCHEMA_ID = "tier0.market_data.margin_exchange_daily.canonical"
MARGIN_SCHEMA_VERSION = "1"

def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze(item) for item in value)
    return value


def schema_contract_hash(payload: Mapping[str, Any]) -> str:
    """Hash the semantic schema contract with stable JSON serialization."""

    if not isinstance(payload, Mapping):
        raise TypeError("schema contract payload must be a mapping")
    blob = json.dumps(
        _plain(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


# This is the code-owned semantic contract for the published canonical dataset.
# The DuckDB verifier below derives its canonical column/type/null/key expectations
# from the same payload, so the manifest cannot drift silently from the DDL.
_MARGIN_SCHEMA_PAYLOAD: dict[str, Any] = {
    "schema_id": MARGIN_SCHEMA_ID,
    "schema_version": MARGIN_SCHEMA_VERSION,
    "dataset_id": DATASET_ID,
    "canonical_table": CANONICAL_TABLE,
    "primary_key": ["trade_date", "exchange_id"],
    "duplicate_policy": "reject",
    "fields": [
        {
            "name": "trade_date",
            "duckdb_type": "DATE",
            "nullable": False,
            "unit": "calendar_date",
            "null_semantics": "forbidden",
            "origin": "provider",
            "role": "event_and_effective_time",
        },
        {
            "name": "exchange_id",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "exchange_identifier",
            "null_semantics": "forbidden",
            "origin": "provider",
            "allowed_values": ["SSE", "SZSE", "BSE"],
        },
        {
            "name": "rzye",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "rzmre",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "rzche",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "rqye",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": True,
            "unit": "CNY",
            "null_semantics": "provider_unknown_or_not_reported; never_zero_fill",
            "origin": "provider",
        },
        {
            "name": "rqmcl",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": True,
            "unit": "share",
            "null_semantics": "provider_unknown_or_not_reported; never_zero_fill",
            "origin": "provider",
        },
        {
            "name": "rzrqye",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "rqyl",
            "duckdb_type": "DECIMAL(38,6)",
            "nullable": True,
            "unit": "share",
            "null_semantics": "provider_unknown_or_not_reported; never_zero_fill",
            "origin": "provider",
        },
        {
            "name": "available_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_timestamp",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "availability_time",
        },
        {
            "name": "ingest_batch_id",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "batch_identifier",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "source_batch",
        },
        {
            "name": "source_row_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "landing_row_lineage",
        },
        {
            "name": "contract_version",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "version_identifier",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "definition_version",
        },
        {
            "name": "config_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "definition_and_policy_hash",
        },
        {
            "name": "built_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_timestamp",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "built_time",
        },
    ],
    "time_semantics": {
        "event_time": {
            "field": "canonical_margin_exchange_daily.trade_date",
            "meaning": "exchange trading date",
        },
        "effective_time": {
            "field": "canonical_margin_exchange_daily.trade_date",
            "meaning": "margin balance effective trading date",
        },
        "observed_time": {
            "field": "ingest_batch.observed_at",
            "meaning": "first completed provider observation at adapter boundary",
        },
        "available_time": {
            "field": "canonical_margin_exchange_daily.available_at",
            "derivation": "ingest_batch.observed_at",
            "constraint": "available_at_equals_observed_at",
            "reason": "provider publication timestamp unavailable",
        },
        "built_time": {
            "field": "canonical_margin_exchange_daily.built_at",
            "meaning": "canonical transaction materialization time",
        },
    },
    "lineage": {
        "landing": {
            "table": LANDING_TABLE,
            "primary_key": ["batch_id", "fragment_ordinal", "row_ordinal"],
            "payload_column": "payload_json",
            "row_hash": {
                "column": "row_hash",
                "algorithm": "sha256",
                "input": "stable_json(payload_json)",
            },
        },
        "input_snapshot": {
            "batch_table": INGEST_BATCH_TABLE,
            "batch_hash_column": "payload_hash",
            "inputs": ["request_json", "fragment_outcomes_json", "landing row_hashes"],
        },
        "source_batch": {
            "canonical_field": "ingest_batch_id",
            "target": "ingest_batch.batch_id",
        },
        "definition": {
            "version_field": "contract_version",
            "hash_field": "config_hash",
            "hash_includes": ["schema_hash", "typed dataset policy"],
        },
        "accepted_pointer": {
            "table": ACCEPTED_TABLE,
            "join": "accepted_partition.batch_id = ingest_batch.batch_id",
            "proof": ["contract_hash", "config_hash", "row_count", "content_hash"],
        },
    },
}
MARGIN_SCHEMA_CONTRACT: Mapping[str, Any] = _freeze(_MARGIN_SCHEMA_PAYLOAD)
MARGIN_SCHEMA_HASH = schema_contract_hash(MARGIN_SCHEMA_CONTRACT)


_CANONICAL_FIELD_CONTRACTS = tuple(MARGIN_SCHEMA_CONTRACT["fields"])
_CANONICAL_FIELD_BY_NAME = {
    str(field["name"]): field for field in _CANONICAL_FIELD_CONTRACTS
}
MARGIN_FIELDS = tuple(
    name
    for name, field in _CANONICAL_FIELD_BY_NAME.items()
    if str(field["origin"]) == "provider"
)
NUMERIC_FIELDS = tuple(
    name
    for name in MARGIN_FIELDS
    if str(_CANONICAL_FIELD_BY_NAME[name]["duckdb_type"]).startswith("DECIMAL(")
)
NON_NULL_NUMERIC_FIELDS = tuple(
    name
    for name in NUMERIC_FIELDS
    if not bool(_CANONICAL_FIELD_BY_NAME[name]["nullable"])
)


def margin_schema_contract_payload() -> dict[str, Any]:
    """Return a mutable copy for review/tests without exposing live constants."""

    return _plain(MARGIN_SCHEMA_CONTRACT)


class MarginAcceptanceError(RuntimeError):
    """Acceptance control state is contradictory or cannot publish safely."""


def _canonical_column_sql(field: Mapping[str, Any]) -> str:
    name = str(field["name"])
    parts = [name, str(field["duckdb_type"])]
    if not bool(field["nullable"]):
        parts.append("NOT NULL")
    allowed = tuple(str(value) for value in field.get("allowed_values", ()))
    if allowed:
        values = ", ".join(f"'{value}'" for value in allowed)
        parts.append(f"CHECK ({name} IN ({values}))")
    return " ".join(parts)


_CANONICAL_COLUMNS_SQL = ",\n        ".join(
    _canonical_column_sql(field) for field in _CANONICAL_FIELD_CONTRACTS
)
_CANONICAL_PRIMARY_KEY_SQL = ", ".join(MARGIN_SCHEMA_CONTRACT["primary_key"])


_DDL = (
    INGEST_BATCH_DDL,
    f"""
    CREATE TABLE IF NOT EXISTS {LANDING_TABLE} (
        batch_id VARCHAR NOT NULL,
        fragment_exchange_id VARCHAR NOT NULL,
        fragment_ordinal INTEGER NOT NULL,
        row_ordinal INTEGER NOT NULL,
        request_json VARCHAR NOT NULL,
        payload_json VARCHAR NOT NULL,
        row_hash VARCHAR NOT NULL,
        PRIMARY KEY (batch_id, fragment_ordinal, row_ordinal)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {CANONICAL_TABLE} (
        {_CANONICAL_COLUMNS_SQL},
        PRIMARY KEY ({_CANONICAL_PRIMARY_KEY_SQL})
    )
    """,
    ACCEPTED_PARTITION_DDL,
)

_FORMAL_COLUMNS = {
    LANDING_TABLE: {
        "batch_id", "fragment_exchange_id", "fragment_ordinal", "row_ordinal",
        "request_json", "payload_json", "row_hash",
    },
    CANONICAL_TABLE: {
        *_CANONICAL_FIELD_BY_NAME,
    },
}

_FORMAL_TYPE_OVERRIDES = {
    LANDING_TABLE: {
        "fragment_ordinal": "INTEGER",
        "row_ordinal": "INTEGER",
    },
    CANONICAL_TABLE: {
        name: str(field["duckdb_type"])
        for name, field in _CANONICAL_FIELD_BY_NAME.items()
        if str(field["duckdb_type"]) != "VARCHAR"
    },
}

_EXPECTED_PRIMARY_KEYS = {
    LANDING_TABLE: ("batch_id", "fragment_ordinal", "row_ordinal"),
    CANONICAL_TABLE: tuple(MARGIN_SCHEMA_CONTRACT["primary_key"]),
}
_EXPECTED_UNIQUE_KEYS: dict[str, set[tuple[str, ...]]] = {}
_EXPECTED_NOT_NULL = {
    LANDING_TABLE: set(_FORMAL_COLUMNS[LANDING_TABLE]),
    CANONICAL_TABLE: {
        name
        for name, field in _CANONICAL_FIELD_BY_NAME.items()
        if not bool(field["nullable"])
    },
}
_EXPECTED_CHECK_MARKERS = {
    CANONICAL_TABLE: tuple(
        f"{name.upper()}IN"
        + ",".join(f"'{str(value).upper()}'" for value in field["allowed_values"])
        for name, field in _CANONICAL_FIELD_BY_NAME.items()
        if field.get("allowed_values")
    ),
}


def _columns(conn, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE {table}").fetchall()
    }


def _constraint_contract(conn, table: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT constraint_type, constraint_text, constraint_column_names
          FROM duckdb_constraints()
         WHERE table_name = ?
        """,
        [table],
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


def ensure_margin_acceptance_schema(conn) -> None:
    """Create and verify the fixed v1 schema inside one transaction."""
    conn.execute("BEGIN TRANSACTION")
    try:
        for statement in _DDL:
            conn.execute(statement)
        verify_accepted_evidence_schema(conn, error_type=MarginAcceptanceError)
        for table, expected in _FORMAL_COLUMNS.items():
            actual_types = _columns(conn, table)
            actual = set(actual_types)
            if actual != expected:
                raise MarginAcceptanceError(
                    f"{table} schema drift: missing={sorted(expected - actual)} "
                    f"extra={sorted(actual - expected)}"
                )
            expected_types = _FORMAL_TYPE_OVERRIDES.get(table, {})
            mismatched = {
                column: (actual_types[column], expected_types.get(column, "VARCHAR"))
                for column in expected
                if actual_types[column] != expected_types.get(column, "VARCHAR")
            }
            if mismatched:
                raise MarginAcceptanceError(f"{table} type drift: {mismatched}")
            constraints = _constraint_contract(conn, table)
            expected_primary = {_EXPECTED_PRIMARY_KEYS[table]}
            if constraints["primary_keys"] != expected_primary:
                raise MarginAcceptanceError(
                    f"{table} primary-key drift: actual={constraints['primary_keys']} "
                    f"expected={expected_primary}"
                )
            expected_unique = _EXPECTED_UNIQUE_KEYS.get(table, set())
            if constraints["unique_keys"] != expected_unique:
                raise MarginAcceptanceError(
                    f"{table} unique-key drift: actual={constraints['unique_keys']} "
                    f"expected={expected_unique}"
                )
            expected_not_null = _EXPECTED_NOT_NULL[table]
            if constraints["not_null"] != expected_not_null:
                raise MarginAcceptanceError(
                    f"{table} nullability drift: actual={sorted(constraints['not_null'])} "
                    f"expected={sorted(expected_not_null)}"
                )
            expected_checks = _EXPECTED_CHECK_MARKERS.get(table, ())
            checks = constraints["checks"]
            if len(checks) != len(expected_checks) or any(
                not any(marker in check for check in checks)
                for marker in expected_checks
            ):
                raise MarginAcceptanceError(
                    f"{table} check-constraint drift: actual={checks} "
                    f"expected_markers={expected_checks}"
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


__all__ = [
    "ACCEPTED_TABLE",
    "CANONICAL_TABLE",
    "DATASET_ID",
    "INGEST_BATCH_TABLE",
    "LANDING_TABLE",
    "MARGIN_FIELDS",
    "MARGIN_SCHEMA_CONTRACT",
    "MARGIN_SCHEMA_HASH",
    "MARGIN_SCHEMA_ID",
    "MARGIN_SCHEMA_VERSION",
    "MarginAcceptanceError",
    "NON_NULL_NUMERIC_FIELDS",
    "NUMERIC_FIELDS",
    "ensure_margin_acceptance_schema",
    "margin_schema_contract_payload",
    "schema_contract_hash",
]
