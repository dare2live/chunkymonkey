"""Fixed DuckDB schema tests for accepted calendar generations."""
from __future__ import annotations

import duckdb
import pytest

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    PROVIDER_FIELDS,
    CalendarSchemaError,
    calendar_schema_contract_payload,
    ensure_calendar_acceptance_schema,
    verify_calendar_acceptance_schema,
)
from services.duck_adapter import connect


def _tables(conn) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_catalog = current_database()
               AND table_schema = current_schema()
            """
        ).fetchall()
    }


def test_semantic_contract_carries_required_publication_ownership_and_lifecycle() -> None:
    payload = calendar_schema_contract_payload()

    assert payload["owner"] == "tier0.reference"
    assert payload["writer_id"] == "services.data_sources.calendar_acceptance"
    assert payload["logical_grain"] == ["exchange", "cal_date"]
    assert payload["physical_grain"] == ["generation_id", "exchange", "cal_date"]
    assert "OUTCOMEIN'COMPLETED','FAILED'" in payload["tables"][FRAGMENT_TABLE][
        "checks"
    ]
    assert tuple(payload["transport_evidence"]["provider_fields"]) == PROVIDER_FIELDS
    assert payload["transport_evidence"]["provider_row_hash"] == (
        "sha256_stable_json_exact_provider_row"
    )
    assert payload["transport_evidence"]["fragment_hash"] == (
        "sha256_stable_json_array_provider_row_hashes"
    )
    assert payload["transport_evidence"]["future_observation"] == (
        "reject_against_trusted_now"
    )
    assert payload["transport_evidence"]["pagination"] == {
        "offset_rule": "fragment_ordinal_times_request_limit",
        "terminal_rule": (
            "first_completed_fragment_with_row_count_less_than_request_limit"
        ),
        "full_page_multiple_rule": "one_zero_row_completed_terminal_fragment",
        "after_terminal": "forbidden",
        "failed_fragment": "reject_generation",
    }
    assert payload["completeness"] == {
        "venue": "SSE",
        "coverage_start": "19901219",
        "required_through_rule": "observed_year_end",
        "natural_day_rule": "every_inclusive_calendar_date_exactly_once",
        "status_domain": [0, 1],
        "preserve_open_and_closed": True,
        "positive_generation": True,
        "duplicate_policy": "reject_before_normalization",
        "pretrade_chain": "previous_open_date_within_coverage_else_null",
    }
    assert payload["canonicalization"] == {
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
    }
    assert payload["time_semantics"] == {
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
    }
    assert payload["criticality"] == "blocking"
    assert payload["failure_policy"] == "fail_closed"
    assert payload["allowed_fallbacks"] == []
    assert payload["consumers"] == ["services.data_sources.calendar_reader"]
    assert payload["retention"] == "permanent_accepted_generations"
    assert payload["rebuild_policy"] == "replay_landing_or_refetch_full_generation"
    assert payload["retirement_condition"] == (
        "replacement_contract_and_consumer_cutover"
    )

    payload["time_semantics"]["event_time"] = "tampered"
    assert calendar_schema_contract_payload()["time_semantics"]["event_time"] == (
        "cal_date"
    )


def test_calendar_schema_is_idempotent_and_owns_fixed_tables() -> None:
    conn = connect(":memory:")
    try:
        ensure_calendar_acceptance_schema(conn)
        ensure_calendar_acceptance_schema(conn)
        assert _tables(conn) == {
            INGEST_BATCH_TABLE,
            ACCEPTED_TABLE,
            FRAGMENT_TABLE,
            LANDING_TABLE,
            CANONICAL_TABLE,
        }
        verify_calendar_acceptance_schema(conn)
    finally:
        conn.close()


def test_read_only_verifier_translates_missing_shared_tables() -> None:
    conn = connect(":memory:")
    try:
        with pytest.raises(CalendarSchemaError, match="shared accepted-evidence"):
            verify_calendar_acceptance_schema(conn)
    finally:
        conn.close()


def test_fragment_constraints_reject_duplicate_offset_and_invalid_outcome() -> None:
    conn = connect(":memory:")
    try:
        ensure_calendar_acceptance_schema(conn)
        values = [
            "batch-1", 0, 0, 6000, "{}", "COMPLETED", 1, "a" * 64,
            "2026-07-19T10:00:00+08:00", None, None,
        ]
        conn.execute(
            f"INSERT INTO {FRAGMENT_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        duplicate = list(values)
        duplicate[1] = 1
        with pytest.raises(duckdb.ConstraintException, match="[Uu]nique|[Dd]uplicate"):
            conn.execute(
                f"INSERT INTO {FRAGMENT_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                duplicate,
            )
        invalid = list(values)
        invalid[0] = "batch-2"
        invalid[5] = "IGNORED"
        with pytest.raises(duckdb.ConstraintException, match="[Cc]heck|outcome"):
            conn.execute(
                f"INSERT INTO {FRAGMENT_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                invalid,
            )
    finally:
        conn.close()


def test_schema_bootstrap_fails_closed_and_rolls_back_peer_creation() -> None:
    conn = connect(":memory:")
    try:
        conn.execute(
            f"CREATE TABLE {FRAGMENT_TABLE} "
            "(batch_id VARCHAR, fragment_ordinal INTEGER)"
        )
        with pytest.raises(CalendarSchemaError, match="schema drift"):
            ensure_calendar_acceptance_schema(conn)
        assert _tables(conn) == {FRAGMENT_TABLE}
    finally:
        conn.close()


def test_verifier_cannot_borrow_attached_same_name_constraints() -> None:
    conn = connect(":memory:")
    try:
        ensure_calendar_acceptance_schema(conn)
        conn.execute(f"DROP TABLE {FRAGMENT_TABLE}")
        conn.execute(
            f"CREATE TABLE {FRAGMENT_TABLE} "
            "(batch_id VARCHAR, fragment_ordinal INTEGER)"
        )
        conn.execute("ATTACH ':memory:' AS shadow")
        conn.execute(
            f"CREATE TABLE shadow.main.{FRAGMENT_TABLE} ("
            "batch_id VARCHAR NOT NULL, fragment_ordinal INTEGER NOT NULL, "
            "request_offset BIGINT NOT NULL, request_limit INTEGER NOT NULL, "
            "request_json VARCHAR NOT NULL, outcome VARCHAR NOT NULL, "
            "row_count BIGINT NOT NULL, fragment_hash VARCHAR, "
            "completed_at TIMESTAMPTZ NOT NULL, error_type VARCHAR, "
            "error_detail VARCHAR, PRIMARY KEY(batch_id, fragment_ordinal), "
            "UNIQUE(batch_id, request_offset))"
        )

        with pytest.raises(CalendarSchemaError, match="schema drift"):
            verify_calendar_acceptance_schema(conn)
    finally:
        conn.close()


def test_verifier_rejects_check_constraint_drift() -> None:
    conn = connect(":memory:")
    try:
        ensure_calendar_acceptance_schema(conn)
        conn.execute(f"DROP TABLE {CANONICAL_TABLE}")
        conn.execute(
            f"""
            CREATE TABLE {CANONICAL_TABLE} (
                generation_id VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                cal_date DATE NOT NULL,
                is_open TINYINT NOT NULL,
                pretrade_date DATE,
                source_fragment_ordinal INTEGER NOT NULL,
                source_row_ordinal INTEGER NOT NULL,
                source_row_hash VARCHAR NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                contract_version VARCHAR NOT NULL,
                config_hash VARCHAR NOT NULL,
                built_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (generation_id, exchange, cal_date)
            )
            """
        )

        with pytest.raises(CalendarSchemaError, match="check-constraint drift"):
            verify_calendar_acceptance_schema(conn)
    finally:
        conn.close()
