"""Read-only shadow reconciliation for the accepted margin partition.

The seed uses the real landing/acceptance boundary.  Mutations happen only in
an isolated in-memory DuckDB and deliberately model each false-green surface
the reconciler must reject.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_acceptance import (
    MARGIN_FIELDS,
    canonical_content_hash,
    ensure_margin_acceptance_schema,
)
from services.data_sources.margin_reconcile import (
    MarginReconcileCode,
    MarginReconcileStatus,
    reconcile_margin_partition,
)
from services.duck_adapter import connect


DATASET_ID = "tier0.market_data.margin_exchange_daily"
PARTITION = "20260715"
AVAILABLE_AT = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
OBSERVED_AT = datetime(2026, 7, 16, 1, 5, tzinfo=timezone.utc)

_ROWS: dict[str, dict[str, Any]] = {
    "BSE": {
        "trade_date": PARTITION,
        "exchange_id": "BSE",
        "rzye": 8_741_512_642,
        "rzmre": 570_023_780,
        "rzche": 667_187_196,
        "rqye": 41_976,
        "rqmcl": 200,
        "rzrqye": 8_741_554_618,
        "rqyl": 1_980,
    },
    "SSE": {
        "trade_date": PARTITION,
        "exchange_id": "SSE",
        "rzye": 1_444_782_928_188,
        "rzmre": 121_624_335_799,
        "rzche": 128_803_072_971,
        "rqye": 13_759_732_441,
        "rqmcl": 69_845_154,
        "rzrqye": 1_458_542_660_629,
        "rqyl": 2_462_232_437,
    },
    "SZSE": {
        "trade_date": PARTITION,
        "exchange_id": "SZSE",
        "rzye": 1_412_829_773_105,
        "rzmre": 111_460_081_117,
        "rzche": 115_860_077_759,
        "rqye": 7_381_899_720,
        "rqmcl": 36_212_724,
        "rzrqye": 1_420_211_672_825,
        "rqyl": 880_676_935,
    },
}


def _ensure_legacy_shadow(database) -> None:
    """Model the independent legacy sync_runner-owned compatibility surface."""
    database.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_tushare_margin (
            trade_date VARCHAR NOT NULL,
            exchange_id VARCHAR NOT NULL,
            rzye DECIMAL(38, 6),
            rzmre DECIMAL(38, 6),
            rzche DECIMAL(38, 6),
            rqye DECIMAL(38, 6),
            rqmcl DECIMAL(38, 6),
            rzrqye DECIMAL(38, 6),
            rqyl DECIMAL(38, 6),
            built_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def _replace_legacy_shadow(database, rows: dict[str, dict[str, Any]]) -> None:
    """Populate legacy explicitly; acceptance must not be the writer under test."""
    database.execute("DELETE FROM raw_tushare_margin")
    database.executemany(
        f"""
        INSERT INTO raw_tushare_margin ({', '.join(MARGIN_FIELDS)}, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [tuple(row[field] for field in MARGIN_FIELDS) for row in rows.values()],
    )


def _seed_accepted_facts(database) -> None:
    """Create a coherent accepted pointer and canonical partition directly."""
    batch_id = "accepted-margin-20260715"
    candidate_rows = [
        {
            field: (
                Decimal(str(row[field]))
                if field in MARGIN_FIELDS[2:] and row[field] is not None
                else row[field]
            )
            for field in MARGIN_FIELDS
        }
        for row in _ROWS.values()
    ]
    content_hash = canonical_content_hash(candidate_rows)
    database.execute(
        """
        INSERT INTO ingest_batch (
            batch_id, dataset_id, contract_version, contract_hash, config_hash,
            writer_id, partition_value, source_name, status, request_json,
            fragment_outcomes_json, expected_fragment_count, completed_fragment_count,
            failed_fragment_count, landing_row_count, canonical_row_count, payload_hash,
            canonical_hash, observed_at, available_at, landed_at, validated_at, accepted_at
        ) VALUES (
            ?, ?, '1', 'contract-hash-v1', 'config-hash-v1',
            'services.data_sources.margin_acceptance', ?, 'tushare', 'ACCEPTED', '{}',
            '[]', 3, 3, 0, 3, 3, 'payload-hash-v1', ?, ?, ?, ?, ?, ?
        )
        """,
        [
            batch_id,
            DATASET_ID,
            PARTITION,
            content_hash,
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT,
            OBSERVED_AT,
            OBSERVED_AT,
        ],
    )
    database.executemany(
        """
        INSERT INTO landing_tushare_margin (
            batch_id, fragment_exchange_id, fragment_ordinal, row_ordinal,
            request_json, payload_json, row_hash
        ) VALUES (?, ?, ?, 1, '{}', ?, ?)
        """,
        [
            (
                batch_id,
                exchange_id,
                fragment_ordinal,
                json.dumps(row, sort_keys=True, separators=(",", ":")),
                f"source-row-hash-{exchange_id}",
            )
            for fragment_ordinal, (exchange_id, row) in enumerate(_ROWS.items(), start=1)
        ],
    )
    database.executemany(
        f"""
        INSERT INTO canonical_margin_exchange_daily (
            {', '.join(MARGIN_FIELDS)}, available_at, ingest_batch_id,
            source_row_hash, contract_version, config_hash, built_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1', 'config-hash-v1', ?
        )
        """,
        [
            (
                f"{PARTITION[:4]}-{PARTITION[4:6]}-{PARTITION[6:]}",
                *(row[field] for field in MARGIN_FIELDS[1:]),
                AVAILABLE_AT,
                batch_id,
                f"source-row-hash-{exchange_id}",
                OBSERVED_AT,
            )
            for exchange_id, row in _ROWS.items()
        ],
    )
    database.execute(
        """
        INSERT INTO accepted_partition (
            dataset_id, partition_value, batch_id, contract_version, contract_hash,
            config_hash, row_count, content_hash, observed_at, available_at, accepted_at
        ) VALUES (?, ?, ?, '1', 'contract-hash-v1', 'config-hash-v1', 3, ?, ?, ?, ?)
        """,
        [
            DATASET_ID,
            PARTITION,
            batch_id,
            content_hash,
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT,
        ],
    )
    # The green fixture must represent the current typed contract.  Using two
    # mutually consistent fake hashes would let stale accepted evidence pass a
    # shadow reconcile after a contract/config change.
    contract = load_dataset_contract("margin")
    database.execute(
        """
        UPDATE ingest_batch
           SET contract_version = ?, contract_hash = ?, config_hash = ?
         WHERE batch_id = ?
        """,
        [
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
            batch_id,
        ],
    )
    database.execute(
        """
        UPDATE accepted_partition
           SET contract_version = ?, contract_hash = ?, config_hash = ?
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
            DATASET_ID,
            PARTITION,
        ],
    )
    database.execute(
        """
        UPDATE canonical_margin_exchange_daily
           SET contract_version = ?, config_hash = ?
         WHERE trade_date = CAST(? AS DATE)
        """,
        [
            contract.contract_version,
            contract.config_hash,
            f"{PARTITION[:4]}-{PARTITION[4:6]}-{PARTITION[6:]}",
        ],
    )


@pytest.fixture
def conn():
    database = connect(":memory:")
    ensure_margin_acceptance_schema(database)
    _ensure_legacy_shadow(database)
    _seed_accepted_facts(database)
    _replace_legacy_shadow(database, _ROWS)
    yield database
    database.close()


def _codes(report) -> set[MarginReconcileCode]:
    return {issue.code for issue in report.issues}


class _ReadOnlySpy:
    """Reject SQL writes so a green report is also a proof of read-only use."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.statements: list[str] = []

    def execute(self, statement: str, parameters=None):
        normalized = " ".join(statement.split()).upper()
        self.statements.append(normalized)
        assert normalized.startswith(("SELECT ", "DESCRIBE ", "WITH ", "EXPLAIN "))
        if parameters is None:
            return self.wrapped.execute(statement)
        return self.wrapped.execute(statement, parameters)


def test_exact_parity_is_green_and_reconcile_attempts_no_writes(conn):
    spy = _ReadOnlySpy(conn)

    report = reconcile_margin_partition(spy, PARTITION)

    assert report.status is MarginReconcileStatus.PARITY
    assert report.ok is True
    assert report.dataset_id == DATASET_ID
    assert report.partition_value == PARTITION
    assert report.accepted_batch_id == "accepted-margin-20260715"
    assert report.accepted_row_count == 3
    assert report.canonical_row_count == 3
    assert report.legacy_row_count == 3
    assert report.issues == ()
    assert spy.statements


def test_mutually_consistent_stale_acceptance_fails_current_contract_gate(conn):
    """Two matching stale hashes are not evidence for the current contract."""
    conn.execute(
        """
        UPDATE ingest_batch
           SET contract_version = 'stale-v0',
               contract_hash = 'stale-contract',
               config_hash = 'stale-config'
         WHERE batch_id = 'accepted-margin-20260715'
        """
    )
    conn.execute(
        """
        UPDATE accepted_partition
           SET contract_version = 'stale-v0',
               contract_hash = 'stale-contract',
               config_hash = 'stale-config'
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [DATASET_ID, PARTITION],
    )
    conn.execute(
        """
        UPDATE canonical_margin_exchange_daily
           SET contract_version = 'stale-v0', config_hash = 'stale-config'
         WHERE trade_date = CAST(? AS DATE)
        """,
        [f"{PARTITION[:4]}-{PARTITION[4:6]}-{PARTITION[6:]}"],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert report.status is MarginReconcileStatus.FAILED
    assert MarginReconcileCode.CURRENT_CONTRACT_MISMATCH in _codes(report)
    assert MarginReconcileCode.ACCEPTANCE_EVIDENCE_MISMATCH not in _codes(report)


@pytest.mark.parametrize(
    "partition",
    ["", "2026--0715", "20261301", "not-a-date"],
)
def test_invalid_partition_fails_closed_without_querying(conn, partition):
    spy = _ReadOnlySpy(conn)

    report = reconcile_margin_partition(spy, partition)

    assert report.status is MarginReconcileStatus.FAILED
    assert _codes(report) == {MarginReconcileCode.INVALID_PARTITION}
    assert spy.statements == []


def test_missing_accepted_partition_fails_closed(conn):
    conn.execute(
        "DELETE FROM accepted_partition WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, PARTITION],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.ACCEPTED_PARTITION_MISSING in _codes(report)
    assert report.status is MarginReconcileStatus.FAILED


def test_pointer_to_nonaccepted_batch_fails_closed(conn):
    conn.execute(
        "UPDATE ingest_batch SET status = 'LANDED' WHERE batch_id = ?",
        ["accepted-margin-20260715"],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.BATCH_NOT_ACCEPTED in _codes(report)


def test_pointer_to_wrong_batch_partition_fails_closed(conn):
    conn.execute(
        "UPDATE ingest_batch SET partition_value = '20260714' WHERE batch_id = ?",
        ["accepted-margin-20260715"],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.BATCH_PARTITION_MISMATCH in _codes(report)


def test_canonical_row_from_nonaccepted_batch_fails_closed(conn):
    conn.execute(
        "UPDATE canonical_margin_exchange_daily "
        "SET ingest_batch_id = 'unaccepted-shadow', config_hash = 'wrong-config' "
        "WHERE exchange_id = 'BSE'"
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.CANONICAL_BATCH_MISMATCH in _codes(report)
    assert MarginReconcileCode.CANONICAL_EVIDENCE_MISMATCH in _codes(report)


def test_missing_legacy_row_is_typed(conn):
    conn.execute(
        "DELETE FROM raw_tushare_margin WHERE trade_date = ? AND exchange_id = 'BSE'",
        [PARTITION],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.LEGACY_ROW_MISSING in _codes(report)
    assert report.legacy_row_count == 2


def test_extra_legacy_row_is_typed(conn):
    conn.execute(
        """
        INSERT INTO raw_tushare_margin
        SELECT trade_date, 'XSE', rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl,
               built_at
          FROM raw_tushare_margin
         WHERE exchange_id = 'BSE'
        """
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.LEGACY_ROW_EXTRA in _codes(report)
    assert report.legacy_row_count == 4


def test_duplicate_legacy_grain_is_typed_and_not_silently_collapsed(conn):
    conn.execute(
        """
        INSERT INTO raw_tushare_margin
        SELECT * FROM raw_tushare_margin WHERE exchange_id = 'BSE'
        """
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.LEGACY_DUPLICATE_GRAIN in _codes(report)
    assert report.legacy_row_count == 4


def test_required_column_schema_drift_is_typed(conn):
    conn.execute("ALTER TABLE raw_tushare_margin DROP COLUMN rqyl")

    report = reconcile_margin_partition(conn, PARTITION)

    assert _codes(report) == {MarginReconcileCode.SCHEMA_MISMATCH}


def test_absent_legacy_compatibility_source_fails_closed(conn):
    conn.execute("DROP TABLE raw_tushare_margin")

    report = reconcile_margin_partition(conn, PARTITION)

    assert _codes(report) == {MarginReconcileCode.SCHEMA_MISMATCH}


def test_value_mismatch_is_typed_with_field_and_grain(conn):
    conn.execute(
        "UPDATE raw_tushare_margin SET rzye = rzye + 1 WHERE exchange_id = 'SSE'"
    )

    report = reconcile_margin_partition(conn, PARTITION)

    issues = [
        issue for issue in report.issues
        if issue.code is MarginReconcileCode.VALUE_MISMATCH
    ]
    assert len(issues) == 1
    assert issues[0].field == "rzye"
    assert issues[0].grain == (PARTITION, "SSE")


def test_null_mismatch_is_distinct_from_value_mismatch(conn):
    conn.execute(
        "UPDATE raw_tushare_margin SET rqyl = NULL WHERE exchange_id = 'SZSE'"
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.NULL_MISMATCH in _codes(report)
    assert MarginReconcileCode.VALUE_MISMATCH not in _codes(report)


def test_accepted_count_evidence_mismatch_is_typed(conn):
    conn.execute(
        "UPDATE accepted_partition SET row_count = 4 "
        "WHERE dataset_id = ? AND partition_value = ?",
        [DATASET_ID, PARTITION],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.ACCEPTANCE_EVIDENCE_MISMATCH in _codes(report)
    assert MarginReconcileCode.CANONICAL_COUNT_MISMATCH in _codes(report)


def test_lockstep_canonical_and_legacy_tamper_cannot_fake_parity(conn):
    for table in ("canonical_margin_exchange_daily", "raw_tushare_margin"):
        conn.execute(f"UPDATE {table} SET rzye = rzye + 1 WHERE exchange_id = 'SSE'")

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.CANONICAL_CONTENT_MISMATCH in _codes(report)
    assert MarginReconcileCode.VALUE_MISMATCH not in _codes(report)
