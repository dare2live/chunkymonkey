"""Read-only shadow reconciliation for the accepted margin partition.

The seed uses the real landing/acceptance boundary.  Mutations happen only in
an isolated in-memory DuckDB and deliberately model each false-green surface
the reconciler must reject.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_evidence import load_margin_evidence_snapshot
from services.data_sources.margin_acceptance import (
    MarginFragment,
    MarginLandingBatch,
    MARGIN_FIELDS,
    accept_margin_batch,
    ensure_margin_acceptance_schema,
    land_margin_batch,
)
from services.data_sources.margin_reconcile import (
    MarginReconcileCode,
    MarginReconcileStatus,
    _reconcile_margin_partitions_snapshot,
    reconcile_margin_partition,
    reconcile_margin_partitions,
)
from services.data_sources.margin_validation import _batch_payload_hash
from services.duck_adapter import connect


pytestmark = pytest.mark.usefixtures("deterministic_margin_calendar")


DATASET_ID = "tier0.market_data.margin_exchange_daily"
PARTITION = "20260717"
AVAILABLE_AT = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
SECOND_PARTITION = "20260720"
SECOND_AVAILABLE_AT = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)

_ROWS: dict[str, dict[str, Any]] = {
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


def _append_legacy_shadow(database, rows: dict[str, dict[str, Any]]) -> None:
    """Append legacy rows explicitly; acceptance is not the writer under test."""
    database.executemany(
        f"""
        INSERT INTO raw_tushare_margin ({', '.join(MARGIN_FIELDS)}, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [tuple(row[field] for field in MARGIN_FIELDS) for row in rows.values()],
    )


def _replace_legacy_shadow(database, rows: dict[str, dict[str, Any]]) -> None:
    """Populate legacy explicitly; acceptance must not be the writer under test."""
    database.execute("DELETE FROM raw_tushare_margin")
    _append_legacy_shadow(database, rows)


def _rows_for(partition: str) -> dict[str, dict[str, Any]]:
    return {
        exchange_id: {**row, "trade_date": partition}
        for exchange_id, row in _ROWS.items()
    }


def _seed_accepted_partition(
    database,
    partition: str,
    *,
    available_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Create coherent evidence through the production landing/accept boundary."""
    rows = _rows_for(partition)
    batch_id = f"accepted-margin-{partition}"
    batch = MarginLandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=available_at,
        available_at=available_at,
        fragments=tuple(
            MarginFragment(
                exchange_id=exchange_id,
                request={
                    "trade_date": partition,
                    "exchange_id": exchange_id,
                },
                rows=(row,),
            )
            for exchange_id, row in rows.items()
        ),
    )
    land_margin_batch(database, batch)
    assert accept_margin_batch(database, batch_id).status == "ACCEPTED"
    return rows


def _land_unresolved_partition(
    database,
    batch_id: str,
    *,
    partition: str = PARTITION,
    available_at: datetime | None = None,
) -> None:
    rows = _rows_for(partition)
    stamp = available_at or (AVAILABLE_AT + timedelta(minutes=1))
    land_margin_batch(
        database,
        MarginLandingBatch(
            batch_id=batch_id,
            partition_value=partition,
            observed_at=stamp,
            available_at=stamp,
            fragments=tuple(
                MarginFragment(
                    exchange_id=exchange_id,
                    request={
                        "trade_date": partition,
                        "exchange_id": exchange_id,
                    },
                    rows=(row,),
                )
                for exchange_id, row in rows.items()
            ),
        ),
    )


def _seed_accepted_facts(database) -> None:
    _seed_accepted_partition(database, PARTITION, available_at=AVAILABLE_AT)


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


def test_public_reconcile_apis_expose_no_proof_bypass():
    from services.data_sources import (
        margin_legacy_reconcile,
        margin_readiness,
        margin_state,
    )

    assert "_accepted_proof" not in inspect.signature(
        reconcile_margin_partition
    ).parameters
    assert "_accepted_proof" not in inspect.signature(
        reconcile_margin_partitions
    ).parameters
    assert "evidence_snapshot" not in inspect.signature(
        reconcile_margin_partition
    ).parameters
    assert "evidence_snapshot" not in inspect.signature(
        reconcile_margin_partitions
    ).parameters
    assert "evidence_snapshot" not in inspect.signature(
        margin_state.accepted_margin_partitions
    ).parameters
    assert "evidence_snapshot" not in inspect.signature(
        margin_state.load_margin_accepted_state
    ).parameters
    assert "accepted_state" not in inspect.signature(
        margin_readiness.evaluate_margin_readiness
    ).parameters
    assert not hasattr(margin_state, "prove_margin_partitions")
    for alias in (
        "append_margin_reconcile_issue",
        "build_margin_reconcile_report",
        "compare_margin_legacy_partition",
        "margin_snapshot_schema_issues",
        "normalize_margin_partition",
    ):
        assert not hasattr(margin_legacy_reconcile, alias)


def test_cross_connection_snapshot_cannot_make_tampered_evidence_green(conn):
    from services.data_sources import margin_state

    contract = load_dataset_contract("margin")
    stale_clean = load_margin_evidence_snapshot(
        conn,
        contract=contract,
        partition_value=PARTITION,
        include_legacy=True,
    )
    other = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(other)
        _ensure_legacy_shadow(other)
        _seed_accepted_facts(other)
        _replace_legacy_shadow(other, _ROWS)
        other.execute(
            "UPDATE landing_tushare_margin SET row_hash = 'tampered' "
            "WHERE batch_id = 'accepted-margin-20260717' AND row_ordinal = 1"
        )

        report = reconcile_margin_partition(other, PARTITION)
        assert _codes(report) == {MarginReconcileCode.FORMAL_EVIDENCE_INVALID}
        with pytest.raises(TypeError):
            reconcile_margin_partition(
                other,
                PARTITION,
                evidence_snapshot=stale_clean,
            )
        with pytest.raises(TypeError):
            margin_state.accepted_margin_partitions(
                other,
                evidence_snapshot=stale_clean,
            )
    finally:
        other.close()


class _ReadOnlySpy:
    """Reject SQL writes so a green report is also a proof of read-only use."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.statements: list[str] = []

    def execute(self, statement: str, parameters=None):
        normalized = " ".join(statement.split()).upper()
        self.statements.append(normalized)
        assert normalized.startswith(("SELECT ", "WITH "))
        if parameters is None:
            return self.wrapped.execute(statement)
        return self.wrapped.execute(statement, parameters)


class _CountingRows:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.visits = 0

    def __iter__(self):
        for row in self.rows:
            self.visits += 1
            yield row


def test_proof_comparison_visits_each_master_row_a_constant_number_of_times(
    conn, monkeypatch
):
    from services.data_sources import margin_reconcile

    partitions = [PARTITION]
    cursor = datetime.strptime(PARTITION, "%Y%m%d") + timedelta(days=1)
    while len(partitions) < 20:
        if cursor.weekday() < 5:
            partition = cursor.strftime("%Y%m%d")
            next_session = cursor + timedelta(days=1)
            while next_session.weekday() >= 5:
                next_session += timedelta(days=1)
            rows = _seed_accepted_partition(
                conn,
                partition,
                available_at=next_session.replace(
                    hour=1,
                    tzinfo=timezone.utc,
                ),
            )
            _append_legacy_shadow(conn, rows)
            partitions.append(partition)
        cursor += timedelta(days=1)

    contract = load_dataset_contract("margin")
    snapshot = load_margin_evidence_snapshot(
        conn,
        contract=contract,
        include_legacy=True,
    )
    counted_snapshot_rows = _CountingRows(snapshot.accepted_rows)
    counted_proof_rows: list[_CountingRows] = []
    real_prove = margin_reconcile._prove_margin_partitions_snapshot

    def _counted_prove(*args, **kwargs):
        proofs = real_prove(*args, **kwargs)
        counted = _CountingRows(proofs.accepted)
        counted_proof_rows.append(counted)
        return replace(proofs, accepted=counted)

    monkeypatch.setattr(
        margin_reconcile,
        "_prove_margin_partitions_snapshot",
        _counted_prove,
    )
    reports = _reconcile_margin_partitions_snapshot(
        conn,
        tuple(partitions),
        contract=contract,
        snapshot=replace(
            snapshot,
            accepted_rows=counted_snapshot_rows,
        ),
    )

    assert all(report.status is MarginReconcileStatus.PARITY for report in reports)
    assert counted_snapshot_rows.visits == 3 * len(partitions)
    assert len(counted_proof_rows) == 1
    assert counted_proof_rows[0].visits == len(partitions)


def test_exact_parity_is_green_and_reconcile_attempts_no_writes(conn):
    spy = _ReadOnlySpy(conn)

    report = reconcile_margin_partition(spy, PARTITION)

    assert report.status is MarginReconcileStatus.PARITY
    assert report.ok is True
    assert report.dataset_id == DATASET_ID
    assert report.partition_value == PARTITION
    assert report.accepted_batch_id == "accepted-margin-20260717"
    assert report.accepted_row_count == 2
    assert report.canonical_row_count == 2
    assert report.legacy_row_count == 2
    assert report.issues == ()
    assert spy.statements


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE ingest_batch SET config_hash = 'bad' WHERE batch_id = 'accepted-margin-20260717'",
        "UPDATE canonical_margin_exchange_daily SET rzye = rzye + 1 WHERE exchange_id = 'SSE'",
        "UPDATE raw_tushare_margin SET rzye = rzye + 1 WHERE exchange_id = 'SSE'",
    ],
)
def test_set_based_reconcile_preserves_single_partition_failure_semantics(conn, tamper):
    conn.execute(tamper)

    single = reconcile_margin_partition(conn, PARTITION)
    batched = reconcile_margin_partitions(conn, [PARTITION])[0]

    assert batched == single


def test_batch_reconcile_rejects_snapshot_scope_drift(conn):
    contract = load_dataset_contract("margin")
    snapshot = load_margin_evidence_snapshot(
        conn,
        contract=contract,
        partition_value=PARTITION,
        include_legacy=True,
    )
    wrong_scope = replace(snapshot, partition_value="20260720")

    report = _reconcile_margin_partitions_snapshot(
        conn,
        (PARTITION,),
        contract=contract,
        snapshot=wrong_scope,
    )[0]

    assert _codes(report) == {MarginReconcileCode.QUERY_ERROR}


def test_scoped_snapshot_rejects_rows_outside_its_declared_partition(conn):
    second_rows = _seed_accepted_partition(
        conn,
        SECOND_PARTITION,
        available_at=SECOND_AVAILABLE_AT,
    )
    _append_legacy_shadow(conn, second_rows)
    contract = load_dataset_contract("margin")
    all_partitions = load_margin_evidence_snapshot(
        conn,
        contract=contract,
        partition_value=None,
        include_legacy=True,
    )
    contaminated_scope = replace(all_partitions, partition_value=PARTITION)

    report = _reconcile_margin_partitions_snapshot(
        conn,
        (PARTITION,),
        contract=contract,
        snapshot=contaminated_scope,
    )[0]

    assert report.status is MarginReconcileStatus.FAILED
    assert _codes(report) == {MarginReconcileCode.QUERY_ERROR}


def test_batch_reconcile_rejects_snapshot_load_error(conn):
    contract = load_dataset_contract("margin")
    snapshot = load_margin_evidence_snapshot(
        conn,
        contract=contract,
        partition_value=PARTITION,
        include_legacy=True,
    )
    broken = replace(snapshot, load_error="forced incoherent evidence read")

    report = _reconcile_margin_partitions_snapshot(
        conn,
        (PARTITION,),
        contract=contract,
        snapshot=broken,
    )[0]

    assert _codes(report) == {MarginReconcileCode.QUERY_ERROR}


def test_reconcile_exposes_content_bound_current_landing(conn):
    _land_unresolved_partition(conn, "current-unresolved")

    report = reconcile_margin_partition(conn, PARTITION)

    assert _codes(report) == {MarginReconcileCode.UNRESOLVED_LANDING}
    assert report.recoverable_landing_batch_id == "current-unresolved"
    assert report.recoverable_landing_payload_hash == conn.execute(
        "SELECT payload_hash FROM ingest_batch WHERE batch_id='current-unresolved'"
    ).fetchone()[0]
    assert report.unresolved_landing_batch_ids == ("current-unresolved",)


def test_reconcile_marks_stale_or_ambiguous_landing_nonrecoverable(conn):
    _land_unresolved_partition(conn, "stale-unresolved")
    conn.execute(
        "UPDATE ingest_batch SET contract_hash='stale' "
        "WHERE batch_id='stale-unresolved'"
    )

    stale = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.UNRESOLVED_LANDING in _codes(stale)
    assert stale.recoverable_landing_batch_id is None
    assert stale.recoverable_landing_payload_hash is None
    assert stale.unresolved_landing_batch_ids == ("stale-unresolved",)

    conn.execute("DELETE FROM landing_tushare_margin WHERE batch_id='stale-unresolved'")
    conn.execute("DELETE FROM ingest_batch WHERE batch_id='stale-unresolved'")
    _land_unresolved_partition(conn, "ambiguous-a")
    _land_unresolved_partition(
        conn,
        "ambiguous-b",
        available_at=AVAILABLE_AT + timedelta(minutes=2),
    )

    ambiguous = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.UNRESOLVED_LANDING in _codes(ambiguous)
    assert ambiguous.recoverable_landing_batch_id is None
    assert ambiguous.recoverable_landing_payload_hash is None
    assert ambiguous.unresolved_landing_batch_ids == (
        "ambiguous-a",
        "ambiguous-b",
    )


def test_reconcile_reproves_retained_landing_lineage(conn):
    conn.execute(
        "UPDATE landing_tushare_margin SET row_hash = 'bad' "
        "WHERE batch_id = 'accepted-margin-20260717' AND row_ordinal = 1"
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert _codes(report) == {MarginReconcileCode.FORMAL_EVIDENCE_INVALID}


def test_batch_reconcile_isolates_formal_proof_failure_to_its_partition(conn):
    second_rows = _seed_accepted_partition(
        conn,
        SECOND_PARTITION,
        available_at=SECOND_AVAILABLE_AT,
    )
    _append_legacy_shadow(conn, second_rows)
    conn.execute(
        "UPDATE landing_tushare_margin SET row_hash = 'bad' "
        "WHERE batch_id = ? AND row_ordinal = 1",
        [f"accepted-margin-{SECOND_PARTITION}"],
    )

    reports = {
        report.partition_value: report
        for report in reconcile_margin_partitions(
            conn,
            [PARTITION, SECOND_PARTITION],
        )
    }

    assert reports[PARTITION].status is MarginReconcileStatus.PARITY
    assert reports[PARTITION].issues == ()
    assert reports[SECOND_PARTITION].status is MarginReconcileStatus.FAILED
    assert (
        MarginReconcileCode.FORMAL_EVIDENCE_INVALID
        in _codes(reports[SECOND_PARTITION])
    )


def test_coherent_premature_publication_cannot_fake_parity(conn):
    """Even rehashed, mutually matching evidence must obey the PIT cutoff."""
    premature = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    batch = conn.execute(
        """
        SELECT source_name, contract_version, contract_hash, config_hash,
               request_json, fragment_outcomes_json
          FROM ingest_batch
         WHERE batch_id = 'accepted-margin-20260717'
        """
    ).fetchone()
    row_signatures = [
        f"{fragment_ordinal}:{row_ordinal}:{row_hash}"
        for fragment_ordinal, row_ordinal, row_hash in conn.execute(
            """
            SELECT fragment_ordinal, row_ordinal, row_hash
              FROM landing_tushare_margin
             WHERE batch_id = 'accepted-margin-20260717'
             ORDER BY fragment_ordinal, row_ordinal
            """
        ).fetchall()
    ]
    payload_hash = _batch_payload_hash(
        partition=PARTITION,
        source=str(batch[0]),
        contract_version=str(batch[1]),
        contract_hash=str(batch[2]),
        config_hash=str(batch[3]),
        observed_at=premature,
        available_at=premature,
        requests=json.loads(str(batch[4])),
        outcomes=json.loads(str(batch[5])),
        row_signatures=row_signatures,
    )
    conn.execute(
        """
        UPDATE ingest_batch
           SET observed_at = ?, available_at = ?, payload_hash = ?
         WHERE batch_id = 'accepted-margin-20260717'
        """,
        [premature, premature, payload_hash],
    )
    conn.execute(
        """
        UPDATE accepted_partition
           SET observed_at = ?, available_at = ?
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [premature, premature, DATASET_ID, PARTITION],
    )
    conn.execute(
        """
        UPDATE canonical_margin_exchange_daily
           SET available_at = ?
         WHERE trade_date = CAST(? AS DATE)
        """,
        [premature, f"{PARTITION[:4]}-{PARTITION[4:6]}-{PARTITION[6:]}"],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    formal = [
        issue for issue in report.issues
        if issue.code is MarginReconcileCode.FORMAL_EVIDENCE_INVALID
    ]
    assert len(formal) == 1
    assert "PREMATURE_PUBLICATION" in formal[0].detail


def test_public_reconcile_types_publication_calendar_loader_failure(
    conn,
    monkeypatch,
):
    from services.data_sources import margin_validation

    def fail_calendar_load(_partition, *, limit=None):
        raise RuntimeError("forced publication calendar failure")

    monkeypatch.setattr(
        margin_validation,
        "load_margin_publication_sessions",
        fail_calendar_load,
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert report.status is MarginReconcileStatus.FAILED
    assert report.issues


def test_mutually_consistent_stale_acceptance_fails_current_contract_gate(conn):
    """Stale-generation pointers are invisible to the current contract filter."""
    conn.execute(
        """
        UPDATE ingest_batch
           SET contract_version = 'stale-v0',
               contract_hash = 'stale-contract',
               config_hash = 'stale-config'
         WHERE batch_id = 'accepted-margin-20260717'
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
    # v3 evidence reads filter by current contract_version/hash, so stale
    # pointers look missing rather than mismatched-in-scope.
    assert MarginReconcileCode.ACCEPTED_PARTITION_MISSING in _codes(report)
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
        ["accepted-margin-20260717"],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.BATCH_NOT_ACCEPTED in _codes(report)


def test_pointer_to_wrong_batch_partition_fails_closed(conn):
    conn.execute(
        "UPDATE ingest_batch SET partition_value = '20260714' WHERE batch_id = ?",
        ["accepted-margin-20260717"],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.BATCH_PARTITION_MISMATCH in _codes(report)


def test_canonical_row_from_nonaccepted_batch_fails_closed(conn):
    conn.execute(
        "UPDATE canonical_margin_exchange_daily "
        "SET ingest_batch_id = 'unaccepted-shadow', config_hash = 'wrong-config' "
        "WHERE exchange_id = 'SZSE'"
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.CANONICAL_BATCH_MISMATCH in _codes(report)
    assert MarginReconcileCode.CANONICAL_EVIDENCE_MISMATCH in _codes(report)


def test_missing_legacy_row_is_typed(conn):
    conn.execute(
        "DELETE FROM raw_tushare_margin WHERE trade_date = ? AND exchange_id = 'SZSE'",
        [PARTITION],
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.LEGACY_ROW_MISSING in _codes(report)
    assert report.legacy_row_count == 1


def test_extra_legacy_row_is_typed(conn):
    conn.execute(
        """
        INSERT INTO raw_tushare_margin
        SELECT trade_date, 'XSE', rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl,
               built_at
          FROM raw_tushare_margin
         WHERE exchange_id = 'SZSE'
        """
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.LEGACY_ROW_EXTRA in _codes(report)
    assert report.legacy_row_count == 3


def test_duplicate_legacy_grain_is_typed_and_not_silently_collapsed(conn):
    conn.execute(
        """
        INSERT INTO raw_tushare_margin
        SELECT * FROM raw_tushare_margin WHERE exchange_id = 'SZSE'
        """
    )

    report = reconcile_margin_partition(conn, PARTITION)

    assert MarginReconcileCode.LEGACY_DUPLICATE_GRAIN in _codes(report)
    assert report.legacy_row_count == 3


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("accepted_partition", "observed_at"),
        ("ingest_batch", "writer_id"),
        ("landing_tushare_margin", "request_json"),
        ("canonical_margin_exchange_daily", "source_row_hash"),
    ],
    ids=("accepted-pointer", "ingest-batch", "landing", "canonical"),
)
def test_formal_required_column_schema_drift_stays_typed(conn, table, column):
    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    report = reconcile_margin_partition(conn, PARTITION)

    assert report.status is MarginReconcileStatus.FAILED
    assert _codes(report) == {MarginReconcileCode.SCHEMA_MISMATCH}


def test_legacy_required_column_schema_drift_is_typed(conn):
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
