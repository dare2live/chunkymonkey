"""Shared accepted-evidence schema contract tests."""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from services.data_sources.accepted_schema import (
    ACCEPTED_PARTITION_DDL,
    ACCEPTED_TABLE,
    INGEST_BATCH_TABLE,
    AcceptedEvidenceSchemaError,
    ensure_accepted_evidence_schema,
    verify_accepted_evidence_schema,
)
from services.data_sources.margin_schema import (
    ACCEPTED_TABLE as MARGIN_ACCEPTED_TABLE,
    INGEST_BATCH_TABLE as MARGIN_INGEST_BATCH_TABLE,
    MarginAcceptanceError,
    ensure_margin_acceptance_schema,
)
from services.duck_adapter import connect


def _accepted_values(*, batch_id: str, partition_value: str, row_count: int):
    at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    return [
        "tier0.test.dataset",
        partition_value,
        batch_id,
        "1",
        "a" * 64,
        "b" * 64,
        row_count,
        "c" * 64,
        at,
        at,
        at,
    ]


def _insert_accepted(conn, *, batch_id: str, partition_value: str, row_count: int):
    conn.execute(
        f"""
        INSERT INTO {ACCEPTED_TABLE} (
            dataset_id, partition_value, batch_id, contract_version,
            contract_hash, config_hash, row_count, content_hash,
            observed_at, available_at, accepted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _accepted_values(
            batch_id=batch_id,
            partition_value=partition_value,
            row_count=row_count,
        ),
    )


def _create_drifted_current_accepted_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE {ACCEPTED_TABLE} (
            dataset_id VARCHAR NOT NULL,
            partition_value VARCHAR NOT NULL,
            batch_id VARCHAR NOT NULL,
            contract_version VARCHAR NOT NULL,
            contract_hash VARCHAR NOT NULL,
            config_hash VARCHAR NOT NULL,
            row_count BIGINT NOT NULL,
            content_hash VARCHAR NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            available_at TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (dataset_id, partition_value)
        )
        """
    )


def _attach_valid_shadow_accepted_table(conn) -> None:
    conn.execute("ATTACH ':memory:' AS shadow")
    shadow_ddl = ACCEPTED_PARTITION_DDL.replace(
        f"CREATE TABLE IF NOT EXISTS {ACCEPTED_TABLE}",
        f"CREATE TABLE shadow.main.{ACCEPTED_TABLE}",
        1,
    )
    conn.execute(shadow_ddl)


def test_margin_reexports_shared_accepted_evidence_table_names() -> None:
    assert MARGIN_INGEST_BATCH_TABLE == INGEST_BATCH_TABLE == "ingest_batch"
    assert MARGIN_ACCEPTED_TABLE == ACCEPTED_TABLE == "accepted_partition"


def test_shared_schema_is_idempotent_and_owns_only_evidence_tables() -> None:
    conn = connect(":memory:")
    try:
        ensure_accepted_evidence_schema(conn)
        ensure_accepted_evidence_schema(conn)

        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = 'main'
                """
            ).fetchall()
        }
        assert tables == {INGEST_BATCH_TABLE, ACCEPTED_TABLE}
    finally:
        conn.close()


def test_accepted_partition_keeps_positive_rows_and_unique_batch_constraints() -> None:
    conn = connect(":memory:")
    try:
        ensure_accepted_evidence_schema(conn)
        _insert_accepted(
            conn,
            batch_id="accepted-batch",
            partition_value="20260717",
            row_count=1,
        )

        with pytest.raises(duckdb.ConstraintException, match="[Uu]nique|[Dd]uplicate"):
            _insert_accepted(
                conn,
                batch_id="accepted-batch",
                partition_value="20260718",
                row_count=1,
            )
        with pytest.raises(duckdb.ConstraintException, match="[Cc]heck|row_count"):
            _insert_accepted(
                conn,
                batch_id="empty-batch",
                partition_value="20260719",
                row_count=0,
            )
    finally:
        conn.close()


def test_shared_schema_rejects_constraint_drift_and_rolls_back_peer_creation() -> None:
    conn = connect(":memory:")
    try:
        _create_drifted_current_accepted_table(conn)

        with pytest.raises(AcceptedEvidenceSchemaError, match="unique-key drift"):
            ensure_accepted_evidence_schema(conn)

        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = 'main'
                """
            ).fetchall()
        }
        assert tables == {ACCEPTED_TABLE}
    finally:
        conn.close()


def test_verifier_cannot_borrow_constraints_from_attached_same_named_table() -> None:
    conn = connect(":memory:")
    try:
        _create_drifted_current_accepted_table(conn)
        _attach_valid_shadow_accepted_table(conn)

        with pytest.raises(AcceptedEvidenceSchemaError, match="unique-key drift"):
            ensure_accepted_evidence_schema(conn)

        shadow_constraints = conn.execute(
            """
            SELECT COUNT(*)
              FROM duckdb_constraints()
             WHERE database_name = 'shadow'
               AND table_name = ?
               AND constraint_type IN ('UNIQUE', 'CHECK')
            """,
            [ACCEPTED_TABLE],
        ).fetchone()[0]
        assert shadow_constraints == 2
    finally:
        conn.close()


def test_verifier_ignores_drift_in_attached_same_named_table() -> None:
    conn = connect(":memory:")
    try:
        ensure_accepted_evidence_schema(conn)
        conn.execute("ATTACH ':memory:' AS shadow")
        conn.execute(
            f"CREATE TABLE shadow.main.{ACCEPTED_TABLE} "
            "(dataset_id VARCHAR, partition_value VARCHAR)"
        )

        verify_accepted_evidence_schema(conn)
    finally:
        conn.close()


def test_margin_schema_translates_shared_drift_and_rolls_back_all_peer_tables() -> None:
    conn = connect(":memory:")
    try:
        _create_drifted_current_accepted_table(conn)

        with pytest.raises(MarginAcceptanceError, match="unique-key drift"):
            ensure_margin_acceptance_schema(conn)

        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_catalog = current_database()
                   AND table_schema = current_schema()
                """
            ).fetchall()
        }
        assert tables == {ACCEPTED_TABLE}
    finally:
        conn.close()
