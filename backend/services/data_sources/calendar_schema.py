"""Fixed DuckDB schema for accepted SSE trading-calendar generations."""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any

from services.data_sources.accepted_schema import (
    ACCEPTED_PARTITION_DDL,
    INGEST_BATCH_DDL,
    verify_accepted_evidence_schema,
)


DATASET_ID = "tier0.reference.sse_trading_calendar_generation"
CONTRACT_VERSION = "1"
WRITER_ID = "services.data_sources.calendar_acceptance"
FRAGMENT_TABLE = "landing_tushare_trade_cal_fragment"
LANDING_TABLE = "landing_tushare_trade_cal"
CANONICAL_TABLE = "canonical_sse_trading_calendar_generation"
PROVIDER_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")


class CalendarSchemaError(RuntimeError):
    """The fixed calendar acceptance schema is missing or has drifted."""


_CALENDAR_SCHEMA_PAYLOAD: dict[str, Any] = {
    "dataset_id": DATASET_ID,
    "contract_version": CONTRACT_VERSION,
    "owner": "tier0.reference",
    "writer_id": WRITER_ID,
    "logical_grain": ["exchange", "cal_date"],
    "physical_grain": ["generation_id", "exchange", "cal_date"],
    "duplicate_policy": "reject_before_canonicalization",
    "transport_evidence": {
        # 2026-09-01: source/api 不进 schema (进而不进 CALENDAR_SCHEMA_HASH)。schema
        # 描述的是"证据长什么样/怎么验"(字段/哈希算法/分页终止规则/fragment 身份),
        # 不是"从哪取"——那是传输轴, formal_boundaries.py 开篇即 "Transport axis only.
        # Business tiers must not own these seams."。同型判据已在 config_hash 层翻车过:
        # nominal_ohlcv/stock_st 的 config_hash 曾含 source/api, 换源(tushare->通达信)
        # 被误判成契约变更, 拒读 2,986 个既有分区 (docs/engineering_governance.md §15.5)。
        # trade_cal 自己的 source 已换了两次 (tushare -> baostock -> calendar_rule) 而
        # 这份证据捕获协议一字未变, 不该让它跟着漂移。registry/adapter 一致性由
        # calendar_contract.py 的 _EXPECTED_PROVIDER_TRANSPORT 独立守卫, 不依赖这个 hash。
        "provider_fields": list(PROVIDER_FIELDS),
        "landing_population": "provider_response",
        "provider_row_hash": "sha256_stable_json_exact_provider_row",
        "fragment_hash": "sha256_stable_json_array_provider_row_hashes",
        "batch_payload_hash": "sha256_stable_json_landed_transport_evidence",
        "future_observation": "reject_against_trusted_now",
        "fragment_identity": ["batch_id", "fragment_ordinal", "request_offset"],
        "pagination": {
            "offset_rule": "fragment_ordinal_times_request_limit",
            "terminal_rule": "first_completed_fragment_with_row_count_less_than_request_limit",
            "full_page_multiple_rule": "one_zero_row_completed_terminal_fragment",
            "after_terminal": "forbidden",
            "failed_fragment": "reject_generation",
        },
    },
    "completeness": {
        "venue": "SSE",
        "coverage_start": "19901219",
        "required_through_rule": "observed_year_end",
        "natural_day_rule": "every_inclusive_calendar_date_exactly_once",
        "status_domain": [0, 1],
        "preserve_open_and_closed": True,
        "positive_generation": True,
        "duplicate_policy": "reject_before_normalization",
        "pretrade_chain": "previous_open_date_within_coverage_else_null",
    },
    "canonicalization": {
        "version": "1",
        "provider_field_order": list(PROVIDER_FIELDS),
        "sort_by": ["exchange", "cal_date"],
        "date_encoding": "ISO-8601",
        "source_row_hash": "sha256_stable_json_exact_provider_row",
        "content_hash": {
            "algorithm": "sha256_stable_json_array",
            "fields": list(PROVIDER_FIELDS),
            "sort_by": ["exchange", "cal_date"],
            "excludes": ["generation", "time", "contract", "config", "lineage"],
        },
    },
    "time_semantics": {
        "event_time": "cal_date",
        "effective_time": "cal_date",
        "observed_time": "generation_response_completed_at=max(fragment.completed_at)",
        "available_time": "generation_response_completed_at=max(fragment.completed_at)",
        "built_time": "canonical.built_at",
        "publication_availability": {
            "axis": "provider_response",
            "rule": "response_completed",
            "at": "response_completed_at",
        },
    },
    "lineage": {
        "input_snapshot": "generation_id",
        "source_batch": "ingest_batch.batch_id",
        "source_row": [
            "source_fragment_ordinal",
            "source_row_ordinal",
            "source_row_hash",
        ],
        "definition": "contract_version",
        "configuration": "config_hash",
    },
    "criticality": "blocking",
    "failure_policy": "fail_closed",
    "allowed_fallbacks": [],
    "consumers": ["services.data_sources.calendar_reader"],
    "retention": "permanent_accepted_generations",
    "rebuild_policy": "replay_landing_or_refetch_full_generation",
    "retirement_condition": "replacement_contract_and_consumer_cutover",
    "tables": {
        FRAGMENT_TABLE: {
            "primary_key": ["batch_id", "fragment_ordinal"],
            "unique": [["batch_id", "request_offset"]],
            "checks": [
                "FRAGMENT_ORDINAL>=0",
                "REQUEST_OFFSET>=0",
                "REQUEST_LIMIT>0",
                "OUTCOMEIN'COMPLETED','FAILED'",
                "ROW_COUNT>=0",
                "REGEXP_FULL_MATCHFRAGMENT_HASH,'[0-9A-F]{64}'",
                "OUTCOME='COMPLETED'ANDERROR_TYPEISNULLANDERROR_DETAILISNULLOR"
                "OUTCOME='FAILED'ANDERROR_TYPEISNOTNULL",
            ],
            "columns": [
                ["batch_id", "VARCHAR", False],
                ["fragment_ordinal", "INTEGER", False],
                ["request_offset", "BIGINT", False],
                ["request_limit", "INTEGER", False],
                ["request_json", "VARCHAR", False],
                ["outcome", "VARCHAR", False],
                ["row_count", "BIGINT", False],
                ["fragment_hash", "VARCHAR", False],
                ["completed_at", "TIMESTAMP WITH TIME ZONE", False],
                ["error_type", "VARCHAR", True],
                ["error_detail", "VARCHAR", True],
            ],
        },
        LANDING_TABLE: {
            "primary_key": ["batch_id", "fragment_ordinal", "row_ordinal"],
            "unique": [],
            "checks": [
                "FRAGMENT_ORDINAL>=0",
                "ROW_ORDINAL>=0",
                "REGEXP_FULL_MATCHROW_HASH,'[0-9A-F]{64}'",
            ],
            "columns": [
                ["batch_id", "VARCHAR", False],
                ["fragment_ordinal", "INTEGER", False],
                ["row_ordinal", "INTEGER", False],
                ["payload_json", "VARCHAR", False],
                ["row_hash", "VARCHAR", False],
            ],
        },
        CANONICAL_TABLE: {
            "primary_key": ["generation_id", "exchange", "cal_date"],
            "unique": [],
            "checks": [
                "EXCHANGE='SSE'",
                "IS_OPENIN0,1",
                "SOURCE_FRAGMENT_ORDINAL>=0",
                "SOURCE_ROW_ORDINAL>=0",
                "REGEXP_FULL_MATCHSOURCE_ROW_HASH,'[0-9A-F]{64}'",
                f"CONTRACT_VERSION='{CONTRACT_VERSION}'",
                "REGEXP_FULL_MATCHCONFIG_HASH,'[0-9A-F]{64}'",
            ],
            "columns": [
                ["generation_id", "VARCHAR", False],
                ["exchange", "VARCHAR", False],
                ["cal_date", "DATE", False],
                ["is_open", "TINYINT", False],
                ["pretrade_date", "DATE", True],
                ["source_fragment_ordinal", "INTEGER", False],
                ["source_row_ordinal", "INTEGER", False],
                ["source_row_hash", "VARCHAR", False],
                ["available_at", "TIMESTAMP WITH TIME ZONE", False],
                ["contract_version", "VARCHAR", False],
                ["config_hash", "VARCHAR", False],
                ["built_at", "TIMESTAMP WITH TIME ZONE", False],
            ],
        },
    },
}


def _hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


CALENDAR_SCHEMA_HASH = _hash(_CALENDAR_SCHEMA_PAYLOAD)


def calendar_schema_contract_payload() -> dict[str, Any]:
    """Return a detached review copy of the code-owned semantic contract."""

    return json.loads(json.dumps(_CALENDAR_SCHEMA_PAYLOAD))


_DDL = (
    INGEST_BATCH_DDL,
    f"""
    CREATE TABLE IF NOT EXISTS {FRAGMENT_TABLE} (
        batch_id VARCHAR NOT NULL,
        fragment_ordinal INTEGER NOT NULL CHECK (fragment_ordinal >= 0),
        request_offset BIGINT NOT NULL CHECK (request_offset >= 0),
        request_limit INTEGER NOT NULL CHECK (request_limit > 0),
        request_json VARCHAR NOT NULL,
        outcome VARCHAR NOT NULL CHECK (outcome IN ('COMPLETED', 'FAILED')),
        row_count BIGINT NOT NULL CHECK (row_count >= 0),
        fragment_hash VARCHAR NOT NULL
            CHECK (regexp_full_match(fragment_hash, '[0-9a-f]{{64}}')),
        completed_at TIMESTAMPTZ NOT NULL,
        error_type VARCHAR,
        error_detail VARCHAR,
        PRIMARY KEY (batch_id, fragment_ordinal),
        UNIQUE (batch_id, request_offset),
        CHECK (
            (outcome = 'COMPLETED' AND error_type IS NULL AND error_detail IS NULL)
            OR (outcome = 'FAILED' AND error_type IS NOT NULL)
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {LANDING_TABLE} (
        batch_id VARCHAR NOT NULL,
        fragment_ordinal INTEGER NOT NULL CHECK (fragment_ordinal >= 0),
        row_ordinal INTEGER NOT NULL CHECK (row_ordinal >= 0),
        payload_json VARCHAR NOT NULL,
        row_hash VARCHAR NOT NULL
            CHECK (regexp_full_match(row_hash, '[0-9a-f]{{64}}')),
        PRIMARY KEY (batch_id, fragment_ordinal, row_ordinal)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {CANONICAL_TABLE} (
        generation_id VARCHAR NOT NULL,
        exchange VARCHAR NOT NULL CHECK (exchange = 'SSE'),
        cal_date DATE NOT NULL,
        is_open TINYINT NOT NULL CHECK (is_open IN (0, 1)),
        pretrade_date DATE,
        source_fragment_ordinal INTEGER NOT NULL
            CHECK (source_fragment_ordinal >= 0),
        source_row_ordinal INTEGER NOT NULL CHECK (source_row_ordinal >= 0),
        source_row_hash VARCHAR NOT NULL
            CHECK (regexp_full_match(source_row_hash, '[0-9a-f]{{64}}')),
        available_at TIMESTAMPTZ NOT NULL,
        contract_version VARCHAR NOT NULL
            CHECK (contract_version = '{CONTRACT_VERSION}'),
        config_hash VARCHAR NOT NULL
            CHECK (regexp_full_match(config_hash, '[0-9a-f]{{64}}')),
        built_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (generation_id, exchange, cal_date)
    )
    """,
    ACCEPTED_PARTITION_DDL,
)

_COLUMNS: dict[str, dict[str, str]] = {
    table: {str(name): str(kind) for name, kind, _nullable in contract["columns"]}
    for table, contract in _CALENDAR_SCHEMA_PAYLOAD["tables"].items()
}
_PRIMARY_KEYS = {
    table: tuple(str(column) for column in contract["primary_key"])
    for table, contract in _CALENDAR_SCHEMA_PAYLOAD["tables"].items()
}
_UNIQUE_KEYS = {
    table: {
        tuple(str(column) for column in columns) for columns in contract["unique"]
    }
    for table, contract in _CALENDAR_SCHEMA_PAYLOAD["tables"].items()
}
_NOT_NULL = {
    table: {
        str(name)
        for name, _kind, nullable in contract["columns"]
        if not bool(nullable)
    }
    for table, contract in _CALENDAR_SCHEMA_PAYLOAD["tables"].items()
}
_CHECK_MARKERS = {
    table: tuple(str(marker) for marker in contract["checks"])
    for table, contract in _CALENDAR_SCHEMA_PAYLOAD["tables"].items()
}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _namespace(conn) -> tuple[str, str]:
    row = conn.execute("SELECT current_database(), current_schema()").fetchone()
    if not row or not row[0] or not row[1]:
        raise CalendarSchemaError("cannot resolve current DuckDB database/schema")
    return str(row[0]), str(row[1])


def _columns(conn, table: str, database: str, schema: str) -> dict[str, str]:
    relation = ".".join(
        _quote_identifier(part) for part in (database, schema, table)
    )
    try:
        rows = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    except Exception as exc:
        raise CalendarSchemaError(f"{table} schema drift: table is missing") from exc
    return {str(row[0]): str(row[1]).upper() for row in rows}


def _constraints(conn, table: str, database: str, schema: str) -> dict[str, Any]:
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


def verify_calendar_acceptance_schema(conn) -> None:
    """Fail closed unless shared and calendar tables match the fixed schema."""

    try:
        verify_accepted_evidence_schema(conn, error_type=CalendarSchemaError)
    except CalendarSchemaError:
        raise
    except Exception as exc:
        raise CalendarSchemaError(
            "shared accepted-evidence schema drift: table is missing or unreadable"
        ) from exc
    database, schema = _namespace(conn)
    for table, expected_types in _COLUMNS.items():
        actual_types = _columns(conn, table, database, schema)
        if set(actual_types) != set(expected_types):
            raise CalendarSchemaError(
                f"{table} schema drift: missing={sorted(set(expected_types) - set(actual_types))} "
                f"extra={sorted(set(actual_types) - set(expected_types))}"
            )
        mismatched = {
            column: (actual_types[column], expected_types[column])
            for column in expected_types
            if actual_types[column] != expected_types[column]
        }
        if mismatched:
            raise CalendarSchemaError(f"{table} type drift: {mismatched}")

        actual = _constraints(conn, table, database, schema)
        expected_primary = {_PRIMARY_KEYS[table]}
        if actual["primary_keys"] != expected_primary:
            raise CalendarSchemaError(
                f"{table} primary-key drift: actual={actual['primary_keys']} "
                f"expected={expected_primary}"
            )
        if actual["unique_keys"] != _UNIQUE_KEYS[table]:
            raise CalendarSchemaError(
                f"{table} unique-key drift: actual={actual['unique_keys']} "
                f"expected={_UNIQUE_KEYS[table]}"
            )
        if actual["not_null"] != _NOT_NULL[table]:
            raise CalendarSchemaError(
                f"{table} nullability drift: actual={sorted(actual['not_null'])} "
                f"expected={sorted(_NOT_NULL[table])}"
            )
        markers = _CHECK_MARKERS[table]
        checks = actual["checks"]
        if len(checks) != len(markers) or any(
            not any(marker in check for check in checks) for marker in markers
        ):
            raise CalendarSchemaError(
                f"{table} check-constraint drift: actual={checks} "
                f"expected_markers={markers}"
            )


def ensure_calendar_acceptance_schema(conn) -> None:
    """Create and verify the complete schema in one transaction."""

    conn.execute("BEGIN TRANSACTION")
    try:
        for statement in _DDL:
            conn.execute(statement)
        verify_calendar_acceptance_schema(conn)
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
    "CALENDAR_SCHEMA_HASH",
    "CANONICAL_TABLE",
    "CONTRACT_VERSION",
    "DATASET_ID",
    "FRAGMENT_TABLE",
    "LANDING_TABLE",
    "PROVIDER_FIELDS",
    "WRITER_ID",
    "CalendarSchemaError",
    "calendar_schema_contract_payload",
    "ensure_calendar_acceptance_schema",
    "verify_calendar_acceptance_schema",
]
