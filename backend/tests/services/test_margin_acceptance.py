"""Tier0 margin landing -> accepted canonical transaction contract.

The fixtures mirror TuShare ``margin`` rows observed on 2026-07-15.  These
tests intentionally exercise a real in-memory DuckDB: mocks cannot prove the
atomic boundary between canonical publication and the accepted-partition
pointer.  The legacy table remains an independent shadow baseline.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("deterministic_margin_calendar")

from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_acceptance import (
    MarginAcceptanceError,
    MarginFragment,
    MarginLandingBatch,
    MarginValidationError,
    accept_margin_batch,
    ensure_margin_acceptance_schema,
    find_current_landed_margin_batch,
    land_margin_batch,
    recover_margin_batch,
    validate_margin_batch,
)
from services.data_sources.margin_schema import (
    MARGIN_SCHEMA_CONTRACT,
    MARGIN_SCHEMA_HASH,
)
from services.duck_adapter import connect


DATASET_ID = "tier0.market_data.margin_exchange_daily"
PARTITION = "20260717"
OBSERVED_AT = datetime(2026, 7, 20, 1, 5, tzinfo=timezone.utc)
AVAILABLE_AT = OBSERVED_AT
_SAMPLE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "domain_samples" / "margin.json").read_text(
        encoding="utf-8"
    )
)
_PRE_BSE_ROWS = {row["exchange_id"]: row for row in _SAMPLE["rows"]}

# Read-only sample from raw_tushare_margin for 2026-07-15.  Keep the provider
# field names and integer magnitudes: renamed toy fields would not catch a
# reversed/missing provider column or a lossy floating-point contract.
_REAL_ROWS: dict[str, dict[str, Any]] = {
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


@pytest.fixture
def conn():
    database = connect(":memory:")
    ensure_margin_acceptance_schema(database)
    yield database
    database.close()


def test_margin_wiring_rejects_compatibility_table_on_formal_surface(monkeypatch):
    from services.data_sources import contracts
    from services.data_sources import margin_acceptance as acceptance

    current = load_dataset_contract("margin")
    monkeypatch.setattr(
        contracts,
        "load_dataset_contract",
        lambda _domain: replace(current, compatibility_table="ingest_batch"),
    )

    with pytest.raises(MarginAcceptanceError, match="wiring drift"):
        acceptance._contract()


def test_planned_contract_snapshot_is_not_replaced_by_registry_reload(
    conn, monkeypatch
):
    from services.data_sources import contracts

    planned = load_dataset_contract("margin")
    drifted = replace(
        planned,
        retention="seven_years",
        config_hash="a" * 64,
        contract_hash="b" * 64,
    )
    monkeypatch.setattr(contracts, "load_dataset_contract", lambda _domain: drifted)

    batch = _batch("planned-contract-snapshot")
    land_margin_batch(conn, batch, contract=planned)
    outcome = accept_margin_batch(
        conn, batch.batch_id, contract=planned
    )

    stored = conn.execute(
        "SELECT contract_hash, config_hash FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()
    assert tuple(stored) == (planned.contract_hash, planned.config_hash)
    assert outcome.status == "ACCEPTED"


def _row(exchange_id: str, *, revision: int = 0, trade_date: str = PARTITION) -> dict[str, Any]:
    row = dict(_REAL_ROWS[exchange_id])
    row["trade_date"] = trade_date
    # Provider revisions change balances while preserving rzrqye = rzye + rqye.
    row["rzye"] += revision
    row["rzrqye"] += revision
    return row


def _batch(
    batch_id: str,
    *,
    partition: str = PARTITION,
    observed_at: datetime = OBSERVED_AT,
    available_at: datetime | None = None,
    revision: int = 0,
    exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    row_trade_dates: dict[str, str] | None = None,
    extra_fields: dict[str, dict[str, Any]] | None = None,
    duplicate_exchange: str | None = None,
    fragment_outcomes: dict[str, tuple[str, str | None, str | None]] | None = None,
) -> MarginLandingBatch:
    fragments = []
    for exchange_id in exchanges:
        row = _row(
            exchange_id,
            revision=revision,
            trade_date=(row_trade_dates or {}).get(exchange_id, partition),
        )
        row.update((extra_fields or {}).get(exchange_id, {}))
        rows = [row]
        if duplicate_exchange == exchange_id:
            rows.append(dict(row))
        outcome, error_type, error_detail = (fragment_outcomes or {}).get(
            exchange_id, ("success", None, None)
        )
        if outcome in {"empty", "error"}:
            rows = []
        fragments.append(
            MarginFragment(
                exchange_id=exchange_id,
                request={"trade_date": partition, "exchange_id": exchange_id},
                rows=rows,
                outcome=outcome,
                error_type=error_type,
                error_detail=error_detail,
            )
        )
    return MarginLandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=observed_at,
        available_at=available_at or observed_at,
        fragments=fragments,
    )


def _batch_state(conn, batch_id: str) -> tuple[str, str | None]:
    row = conn.execute(
        "SELECT status, rejection_code FROM ingest_batch WHERE batch_id = ?",
        [batch_id],
    ).fetchone()
    assert row is not None
    return row[0], row[1]


def _published_snapshot(conn) -> dict[str, tuple[tuple[Any, ...], ...]]:
    queries = {
        "canonical": """
            SELECT CAST(trade_date AS VARCHAR), exchange_id,
                   rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl,
                   ingest_batch_id
            FROM canonical_margin_exchange_daily
            ORDER BY trade_date, exchange_id
        """,
        "accepted": """
            SELECT dataset_id, partition_value, batch_id, row_count,
                   CAST(observed_at AS VARCHAR), CAST(available_at AS VARCHAR)
            FROM accepted_partition
            ORDER BY dataset_id, partition_value
        """,
    }
    return {
        name: tuple(tuple(row) for row in conn.execute(sql).fetchall())
        for name, sql in queries.items()
    }


def _accept_seed(conn, *, batch_id: str = "seed", revision: int = 0) -> None:
    seed = _batch(batch_id, revision=revision)
    land_margin_batch(conn, seed)
    accept_margin_batch(conn, seed.batch_id)
    assert _batch_state(conn, seed.batch_id) == ("ACCEPTED", None)


class _RollbackFailureConnection:
    """Execute the real rollback, then report a driver-level rollback failure."""

    def __init__(self, inner, *, fail_prefix: str | None = None, primary_error=None):
        self._inner = inner
        self._fail_prefix = fail_prefix
        self._primary_error = primary_error
        self._primary_raised = False

    def execute(self, sql, *args, **kwargs):
        normalized = str(sql).strip().upper()
        if (
            self._fail_prefix
            and not self._primary_raised
            and normalized.startswith(self._fail_prefix)
        ):
            self._primary_raised = True
            raise self._primary_error
        result = self._inner.execute(sql, *args, **kwargs)
        if normalized == "ROLLBACK":
            raise RuntimeError("injected rollback driver failure")
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.parametrize("transaction", ["schema", "land", "reject", "accept"])
def test_rollback_failure_preserves_primary_error_and_exposes_unknown_state(
    conn, transaction
):
    primary_error = RuntimeError(f"injected {transaction} primary failure")
    crash_step = None
    batch = None

    if transaction == "schema":
        guarded = _RollbackFailureConnection(
            conn, fail_prefix="CREATE TABLE", primary_error=primary_error
        )
    else:
        guarded = _RollbackFailureConnection(conn)
        exchanges = ("SSE",) if transaction == "reject" else ("SSE", "SZSE")
        batch = _batch(f"rollback-note-{transaction}", exchanges=exchanges)
        if transaction != "land":
            land_margin_batch(conn, batch)
        crash_step = {
            "land": "after_batch_insert",
            "reject": "after_rejection_update",
            "accept": "after_canonical_insert",
        }[transaction]

    def crash_after(step: str) -> None:
        if step == crash_step:
            raise primary_error

    with pytest.raises(RuntimeError) as caught:
        if transaction == "schema":
            ensure_margin_acceptance_schema(guarded)
        elif transaction == "land":
            land_margin_batch(guarded, batch, after_step=crash_after)
        else:
            accept_margin_batch(guarded, batch.batch_id, after_step=crash_after)

    assert caught.value is primary_error
    assert caught.value.__notes__ == [
        "ROLLBACK failed; connection state is unknown: "
        "RuntimeError: injected rollback driver failure"
    ]
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    if transaction == "land":
        assert conn.execute(
            "SELECT COUNT(*) FROM ingest_batch WHERE batch_id = ?", [batch.batch_id]
        ).fetchone()[0] == 0
    elif transaction in {"reject", "accept"}:
        assert _batch_state(conn, batch.batch_id) == ("LANDED", None)


def test_recovery_discovery_and_pure_validation_preserve_provider_shape(conn):
    batch = _batch("recovery-checkpoint")
    land_margin_batch(conn, batch)

    recoverable = find_current_landed_margin_batch(conn, PARTITION)
    assert recoverable is not None
    assert recoverable.batch_id == batch.batch_id
    assert recoverable.payload_hash == conn.execute(
        "SELECT payload_hash FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    validated = validate_margin_batch(conn, batch.batch_id)

    assert validated.batch_id == batch.batch_id
    assert validated.partition_value == PARTITION
    assert validated.row_count == 2
    assert [row["exchange_id"] for row in validated.legacy_rows] == [
        "SSE", "SZSE"
    ]
    assert isinstance(validated.legacy_rows[0]["rzye"], int)
    assert str(validated.canonical_rows[0]["rzye"]) == str(_REAL_ROWS["SSE"]["rzye"])
    assert _batch_state(conn, batch.batch_id) == ("LANDED", None)


def test_pure_validation_failure_does_not_mutate_checkpoint(conn):
    batch = _batch(
        "pure-validation-failure",
        fragment_outcomes={
            "SZSE": ("empty", None, None),
        },
    )
    land_margin_batch(conn, batch)

    with pytest.raises(MarginValidationError) as exc_info:
        validate_margin_batch(conn, batch.batch_id)

    assert exc_info.value.code == "ZERO_ROWS"
    assert "empty_fragments=['SZSE']" in exc_info.value.detail
    assert _batch_state(conn, batch.batch_id) == ("LANDED", None)


def test_recovery_discovery_fails_closed_on_ambiguous_landed_batches(conn):
    land_margin_batch(conn, _batch("ambiguous-a"))
    land_margin_batch(conn, _batch("ambiguous-b", observed_at=OBSERVED_AT.replace(minute=6)))

    with pytest.raises(MarginAcceptanceError, match="ambiguous LANDED"):
        find_current_landed_margin_batch(conn, PARTITION)


def test_schema_is_typed_idempotent_and_empty_is_not_ready(conn):
    ensure_margin_acceptance_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {
        "ingest_batch",
        "landing_tushare_margin",
        "canonical_margin_exchange_daily",
        "accepted_partition",
    }.issubset(tables)

    canonical_types = {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'canonical_margin_exchange_daily'
            """
        ).fetchall()
    }
    assert canonical_types["trade_date"] == "DATE"
    assert canonical_types["exchange_id"] == "VARCHAR"
    assert {
        canonical_types[column]
        for column in ("rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye", "rqyl")
    } == {"DECIMAL(38,6)"}
    semantic_fields = {
        field["name"]: field for field in MARGIN_SCHEMA_CONTRACT["fields"]
    }
    assert set(canonical_types) == set(semantic_fields)
    assert {
        column: canonical_types[column] for column in canonical_types
    } == {
        column: field["duckdb_type"] for column, field in semantic_fields.items()
    }
    assert len(MARGIN_SCHEMA_HASH) == 64
    allowed = tuple(
        semantic_fields["exchange_id"]["allowed_values"]
    )
    check_text = " ".join(
        str(row[0])
        for row in conn.execute(
            """
            SELECT constraint_text FROM duckdb_constraints()
             WHERE table_name = 'canonical_margin_exchange_daily'
               AND constraint_type = 'CHECK'
            """
        ).fetchall()
    )
    assert all(value in check_text for value in allowed)

    for table in (
        "ingest_batch",
        "landing_tushare_margin",
        "canonical_margin_exchange_daily",
        "accepted_partition",
    ):
        assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0


def test_formal_schema_does_not_create_or_own_legacy_shadow(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }

    assert "raw_tushare_margin" not in tables


def test_schema_bootstrap_rolls_back_new_tables_when_existing_schema_is_wrong():
    database = connect(":memory:")
    database.execute("CREATE TABLE canonical_margin_exchange_daily (trade_date VARCHAR)")

    with pytest.raises(RuntimeError, match="schema drift"):
        ensure_margin_acceptance_schema(database)

    tables = {
        row[0]
        for row in database.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert tables == {"canonical_margin_exchange_daily"}
    database.close()


def test_schema_gate_rejects_constraint_drift_even_when_columns_and_types_match():
    database = connect(":memory:")
    database.execute(
        """
        CREATE TABLE canonical_margin_exchange_daily (
            trade_date DATE NOT NULL,
            exchange_id VARCHAR NOT NULL,
            rzye DECIMAL(38, 6) NOT NULL,
            rzmre DECIMAL(38, 6) NOT NULL,
            rzche DECIMAL(38, 6) NOT NULL,
            rqye DECIMAL(38, 6),
            rqmcl DECIMAL(38, 6),
            rzrqye DECIMAL(38, 6) NOT NULL,
            rqyl DECIMAL(38, 6),
            available_at TIMESTAMPTZ NOT NULL,
            ingest_batch_id VARCHAR NOT NULL,
            source_row_hash VARCHAR NOT NULL,
            contract_version VARCHAR NOT NULL,
            config_hash VARCHAR NOT NULL,
            built_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    with pytest.raises(RuntimeError, match="primary-key drift"):
        ensure_margin_acceptance_schema(database)

    tables = {
        row[0]
        for row in database.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert tables == {"canonical_margin_exchange_daily"}
    database.close()


def test_tx_a_lands_every_fragment_verbatim_without_publishing(conn):
    batch = _batch(
        "land-verbatim",
        extra_fields={"SSE": {"vendor_extra": {"trace": "kept", "attempt": 2}}},
    )

    land_margin_batch(conn, batch)

    status, partition_value = conn.execute(
        "SELECT status, partition_value FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()
    assert (status, partition_value) == ("LANDED", PARTITION)
    landed = conn.execute(
        """
        SELECT fragment_exchange_id, row_ordinal, payload_json
        FROM landing_tushare_margin
        WHERE batch_id = ?
        ORDER BY fragment_exchange_id, row_ordinal
        """,
        [batch.batch_id],
    ).fetchall()
    assert len(landed) == 2
    payload_by_exchange = {row[0]: json.loads(row[2]) for row in landed}
    assert payload_by_exchange["SZSE"] == _row("SZSE")
    assert payload_by_exchange["SSE"] == {
        **_row("SSE"),
        "vendor_extra": {"trace": "kept", "attempt": 2},
    }
    assert _published_snapshot(conn) == {
        "canonical": (),
        "accepted": (),
    }


@pytest.mark.parametrize("kill_step", ["after_batch_insert", "after_landing_insert"])
def test_tx_a_kill_points_roll_back_batch_and_landing(conn, kill_step):
    batch = _batch(f"tx-a-{kill_step}")

    def crash_after(step: str) -> None:
        if step == kill_step:
            raise RuntimeError(f"injected crash at {step}")

    with pytest.raises(RuntimeError, match=kill_step):
        land_margin_batch(conn, batch, after_step=crash_after)

    assert conn.execute(
        "SELECT COUNT(*) FROM ingest_batch WHERE batch_id = ?", [batch.batch_id]
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM landing_tushare_margin WHERE batch_id = ?", [batch.batch_id]
    ).fetchone()[0] == 0
    assert _published_snapshot(conn) == {"canonical": (), "accepted": ()}


def test_tx_a_commit_ack_loss_leaves_recoverable_landed_evidence(conn):
    batch = _batch("tx-a-ack-lost")

    def lose_ack(step: str) -> None:
        if step == "after_landing_commit":
            raise RuntimeError("landing acknowledgement lost")

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        land_margin_batch(conn, batch, after_step=lose_ack)

    assert _batch_state(conn, batch.batch_id) == ("LANDED", None)
    assert conn.execute(
        "SELECT COUNT(*) FROM landing_tushare_margin WHERE batch_id = ?", [batch.batch_id]
    ).fetchone()[0] == 2
    recover_margin_batch(conn, batch.batch_id)
    assert _batch_state(conn, batch.batch_id) == ("ACCEPTED", None)


def test_tx_a_retry_is_idempotent_only_for_the_same_complete_envelope(conn):
    batch = _batch("tx-a-idempotent")
    land_margin_batch(conn, batch)

    land_margin_batch(conn, _batch("tx-a-idempotent"))

    assert conn.execute(
        "SELECT COUNT(*) FROM ingest_batch WHERE batch_id = ?", [batch.batch_id]
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM landing_tushare_margin WHERE batch_id = ?", [batch.batch_id]
    ).fetchone()[0] == 2

    changed_envelope = _batch(
        "tx-a-idempotent",
        observed_at=datetime(2026, 7, 20, 1, 6, tzinfo=timezone.utc),
    )
    with pytest.raises(RuntimeError, match="different payload"):
        land_margin_batch(conn, changed_envelope)


@pytest.mark.parametrize(
    ("outcome", "error_type", "expected_code"),
    [
        ("empty", None, "ZERO_ROWS"),
        ("error", "authorization", "FRAGMENT_FAILED"),
        ("error", "quota", "FRAGMENT_FAILED"),
        ("error", "timeout", "FRAGMENT_FAILED"),
        ("error", "connection", "FRAGMENT_FAILED"),
        ("error", "provider_error", "FRAGMENT_FAILED"),
    ],
)
def test_fragment_failure_outcomes_are_durable_and_never_publish(
    conn, outcome, error_type, expected_code
):
    batch = _batch(
        f"fragment-{outcome}-{error_type or 'zero'}",
        fragment_outcomes={
            "SZSE": (outcome, error_type, "sanitized provider failure")
            if error_type
            else (outcome, None, None)
        },
    )

    land_margin_batch(conn, batch)

    evidence = conn.execute(
        """
        SELECT status, expected_fragment_count, completed_fragment_count,
               failed_fragment_count, landing_row_count, fragment_outcomes_json
          FROM ingest_batch WHERE batch_id = ?
        """,
        [batch.batch_id],
    ).fetchone()
    assert tuple(evidence)[:5] == (
        "LANDED",
        2,
        1 if outcome == "error" else 2,
        1 if outcome == "error" else 0,
        1,
    )
    persisted = json.loads(evidence[5])
    szse = next(item for item in persisted if item["exchange_id"] == "SZSE")
    assert szse["status"] == outcome
    assert szse["error_type"] == error_type

    accept_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("REJECTED", expected_code)
    assert _published_snapshot(conn) == {"canonical": (), "accepted": ()}


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("schema_drift", "SCHEMA_DRIFT"),
        ("wrong_partition", "WRONG_PARTITION"),
        ("unexpected_bse", "UNEXPECTED_GROUP"),
        ("duplicate_grain", "DUPLICATE_GRAIN"),
    ],
)
def test_invalid_batch_is_rejected_without_mutating_published_state(conn, case, expected_code):
    _accept_seed(conn, batch_id=f"seed-{case}")
    before = _published_snapshot(conn)
    common = {
        "batch_id": f"invalid-{case}",
        "observed_at": datetime(2026, 7, 20, 1, 10, tzinfo=timezone.utc),
        "revision": 10_000,
    }
    if case == "schema_drift":
        candidate = _batch(**common, extra_fields={"SSE": {"unexpected_vendor_column": 1}})
    elif case == "wrong_partition":
        candidate = _batch(**common, row_trade_dates={"SSE": "20260714"})
    elif case == "unexpected_bse":
        candidate = _batch(
            **common,
            exchanges=("SSE", "SZSE", "BSE"),
            # BSE rows still need provider shape for landing; use SZSE magnitudes.
            extra_fields={},
        )
        # Inject a synthetic BSE fragment via exchanges — _row needs BSE key.

    else:
        candidate = _batch(**common, duplicate_exchange="SSE")

    land_margin_batch(conn, candidate)
    accept_margin_batch(conn, candidate.batch_id)

    assert _batch_state(conn, candidate.batch_id) == ("REJECTED", expected_code)
    assert _published_snapshot(conn) == before


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("payload", "LANDING_HASH_MISMATCH"),
        ("row_hash", "LANDING_HASH_MISMATCH"),
        ("batch_count", "LANDING_ENVELOPE_DRIFT"),
        ("outcome_count", "LANDING_ENVELOPE_DRIFT"),
    ],
)
def test_committed_landing_tamper_is_rejected(conn, tamper, expected_code):
    batch = _batch(f"tamper-{tamper}")
    land_margin_batch(conn, batch)
    if tamper == "payload":
        conn.execute(
            "UPDATE landing_tushare_margin SET payload_json = '{}' "
            "WHERE batch_id = ? AND fragment_exchange_id = 'SSE'",
            [batch.batch_id],
        )
    elif tamper == "row_hash":
        conn.execute(
            "UPDATE landing_tushare_margin SET row_hash = repeat('0', 64) "
            "WHERE batch_id = ? AND fragment_exchange_id = 'SSE'",
            [batch.batch_id],
        )
    elif tamper == "batch_count":
        conn.execute(
            "UPDATE ingest_batch SET landing_row_count = landing_row_count + 1 "
            "WHERE batch_id = ?",
            [batch.batch_id],
        )
    else:
        raw = conn.execute(
            "SELECT fragment_outcomes_json FROM ingest_batch WHERE batch_id = ?",
            [batch.batch_id],
        ).fetchone()[0]
        outcomes = json.loads(raw)
        outcomes[0]["row_count"] += 1
        conn.execute(
            "UPDATE ingest_batch SET fragment_outcomes_json = ? WHERE batch_id = ?",
            [json.dumps(outcomes, ensure_ascii=False), batch.batch_id],
        )

    accept_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("REJECTED", expected_code)
    assert _published_snapshot(conn) == {"canonical": (), "accepted": ()}


@pytest.mark.parametrize(
    ("kill_step", "durable"),
    [("after_rejection_update", False), ("after_rejection_commit", True)],
)
def test_rejection_transaction_kill_and_ack_loss_are_recoverable(conn, kill_step, durable):
    # Incomplete required groups → durable REJECTED path under test.
    batch = _batch(f"reject-{kill_step}", exchanges=("SSE",))
    land_margin_batch(conn, batch)

    def crash_after(step: str) -> None:
        if step == kill_step:
            raise RuntimeError(f"injected crash at {step}")

    with pytest.raises(RuntimeError, match=kill_step):
        accept_margin_batch(conn, batch.batch_id, after_step=crash_after)

    assert _batch_state(conn, batch.batch_id) == (
        ("REJECTED", "MISSING_REQUIRED_GROUP")
        if durable
        else ("LANDED", None)
    )
    outcome = recover_margin_batch(conn, batch.batch_id)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "MISSING_REQUIRED_GROUP"
    assert _published_snapshot(conn) == {"canonical": (), "accepted": ()}


def test_valid_v3_partition_publishes_exact_sse_szse(conn):
    batch = _batch("valid-three-exchanges")

    land_margin_batch(conn, batch)
    accept_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("ACCEPTED", None)
    canonical = conn.execute(
        """
        SELECT exchange_id, rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl,
               ingest_batch_id
        FROM canonical_margin_exchange_daily
        WHERE trade_date = DATE '2026-07-17'
        ORDER BY exchange_id
        """
    ).fetchall()
    assert [tuple(row) for row in canonical] == [
        tuple(_REAL_ROWS[exchange][column] for column in (
            "exchange_id", "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye", "rqyl"
        )) + (batch.batch_id,)
        for exchange in ("SSE", "SZSE")
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'raw_tushare_margin'"
    ).fetchone()[0] == 0
    accepted = conn.execute(
        """
        SELECT batch_id, row_count
        FROM accepted_partition
        WHERE dataset_id = ? AND partition_value = ?
        """,
        [DATASET_ID, PARTITION],
    ).fetchone()
    assert tuple(accepted) == (batch.batch_id, 2)


def test_pre_bse_partition_accepts_exact_two_exchange_fixture(conn):
    partition = "20190102"
    batch = MarginLandingBatch(
        batch_id="valid-two-exchanges",
        partition_value=partition,
        observed_at=datetime(2019, 1, 3, 1, 20, tzinfo=timezone.utc),
        available_at=datetime(2019, 1, 3, 1, 20, tzinfo=timezone.utc),
        fragments=[
            MarginFragment(
                exchange_id=exchange_id,
                request={"trade_date": partition, "exchange_id": exchange_id},
                rows=[dict(_PRE_BSE_ROWS[exchange_id])],
            )
            for exchange_id in ("SSE", "SZSE")
        ],
    )

    land_margin_batch(conn, batch)
    accept_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("ACCEPTED", None)
    assert conn.execute(
        "SELECT LIST(exchange_id ORDER BY exchange_id) FROM canonical_margin_exchange_daily"
    ).fetchone()[0] == ["SSE", "SZSE"]
    assert conn.execute(
        "SELECT row_count FROM accepted_partition WHERE partition_value = ?", [partition]
    ).fetchone()[0] == 2


@pytest.mark.parametrize(
    ("field_overrides", "expected_code"),
    [
        ({"rzye": -1}, "INVALID_NUMERIC"),
        ({"rzrqye": _REAL_ROWS["SSE"]["rzrqye"] + 1}, "INCONSISTENT_TOTAL"),
    ],
)
def test_invalid_numeric_semantics_are_rejected(conn, field_overrides, expected_code):
    batch = _batch(
        f"invalid-{expected_code.lower()}",
        extra_fields={"SSE": field_overrides},
    )
    land_margin_batch(conn, batch)

    accept_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("REJECTED", expected_code)
    assert _published_snapshot(conn) == {"canonical": (), "accepted": ()}


def test_nullable_provider_values_remain_null_not_zero(conn):
    batch = _batch(
        "nullable-provider-values",
        extra_fields={"SZSE": {"rqye": None, "rqmcl": None, "rqyl": None}},
    )
    land_margin_batch(conn, batch)

    accept_margin_batch(conn, batch.batch_id)

    canonical = conn.execute(
        "SELECT rqye, rqmcl, rqyl FROM canonical_margin_exchange_daily WHERE exchange_id = 'SZSE'"
    ).fetchone()
    assert tuple(canonical) == (None, None, None)


def test_formal_acceptance_never_mutates_exact_live_legacy_schema():
    database = connect(":memory:")
    database.execute(
        """
        CREATE TABLE raw_tushare_margin (
            trade_date VARCHAR, exchange_id VARCHAR,
            rzye BIGINT, rzmre BIGINT, rzche BIGINT, rqye BIGINT,
            rqmcl BIGINT, rzrqye BIGINT, rqyl BIGINT, built_at VARCHAR
        )
        """
    )
    database.execute(
        "INSERT INTO raw_tushare_margin VALUES "
        "('20260717', 'SSE', 1, 2, 3, 4, 5, 5, 6, 'legacy-shadow')"
    )
    ensure_margin_acceptance_schema(database)
    before = tuple(
        tuple(row)
        for row in database.execute("SELECT * FROM raw_tushare_margin").fetchall()
    )

    fractional_batch = _batch(
        "formal-decimal-shadow",
        extra_fields={
            "SSE": {
                "rzye": _REAL_ROWS["SSE"]["rzye"] + 0.5,
                "rzrqye": _REAL_ROWS["SSE"]["rzrqye"] + 0.5,
            }
        },
    )
    land_margin_batch(database, fractional_batch)
    accept_margin_batch(database, fractional_batch.batch_id)

    assert _batch_state(database, fractional_batch.batch_id) == ("ACCEPTED", None)
    assert tuple(
        tuple(row)
        for row in database.execute("SELECT * FROM raw_tushare_margin").fetchall()
    ) == before
    database.close()


@pytest.mark.parametrize(
    "change",
    [
        {"available_at": datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)},
        {"observed_at": datetime(2026, 7, 16, 0, 59, tzinfo=timezone.utc)},
        {"observed_at": datetime(2026, 7, 16, 1, 5)},
        {"partition_value": "20260230"},
        {"source": "other"},
        {"contract_version": "0"},
    ],
)
def test_landing_rejects_invalid_availability_or_contract_identity(conn, change):
    values = {
        "batch_id": "invalid-landing-identity",
        "partition_value": PARTITION,
        "observed_at": OBSERVED_AT,
        "available_at": AVAILABLE_AT,
        "fragments": _batch("source-fragments").fragments,
        "source": "tushare",
        "contract_version": "2",
    }
    values.update(change)

    with pytest.raises(RuntimeError):
        land_margin_batch(conn, MarginLandingBatch(**values))

    assert conn.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] == 0


@pytest.mark.parametrize(
    "observed_at",
    [
        datetime(2026, 7, 18, 1, 5, tzinfo=timezone.utc),  # Saturday 09:05 CST
        datetime(2026, 7, 20, 0, 59, tzinfo=timezone.utc),  # Monday 08:59 CST
    ],
)
def test_acceptance_rejects_observation_before_next_trading_session_cutoff(
    conn, observed_at
):
    batch = _batch(
        "premature-policy-cutoff",
        partition="20260717",
        observed_at=observed_at,
    )
    land_margin_batch(conn, batch)

    with pytest.raises(MarginValidationError) as caught:
        validate_margin_batch(conn, batch.batch_id)
    assert caught.value.code == "PREMATURE_PUBLICATION"

    outcome = accept_margin_batch(conn, batch.batch_id)
    assert outcome.status == "REJECTED"
    assert outcome.rejection_code == "PREMATURE_PUBLICATION"
    assert conn.execute(
        "SELECT COUNT(*) FROM canonical_margin_exchange_daily"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 0


def test_acceptance_allows_exact_next_trading_session_cutoff(conn):
    batch = _batch(
        "exact-policy-cutoff",
        partition="20260717",
        observed_at=datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc),
    )
    land_margin_batch(conn, batch)

    outcome = accept_margin_batch(conn, batch.batch_id)

    assert outcome.status == "ACCEPTED"
    assert outcome.partition_value == "20260717"


def test_recovery_reproves_publication_cutoff_for_already_accepted_batch(
    conn, monkeypatch
):
    from services.data_sources import margin_validation

    batch = _batch(
        "recovery-policy-reproof",
        partition="20260717",
        observed_at=datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc),
    )
    land_margin_batch(conn, batch)
    assert accept_margin_batch(conn, batch.batch_id).status == "ACCEPTED"
    monkeypatch.setattr(
        margin_validation,
        "load_margin_publication_sessions",
        lambda _partition, **_kwargs: ("20260717", "20260721"),
    )

    with pytest.raises(MarginAcceptanceError, match="violates current policy"):
        recover_margin_batch(conn, batch.batch_id)


def test_schema_fingerprint_drift_fails_before_creating_formal_tables(monkeypatch):
    from services.data_sources import contracts

    current = load_dataset_contract("margin")
    monkeypatch.setattr(
        contracts,
        "load_dataset_contract",
        lambda _domain: replace(current, schema_hash="0" * 64),
    )
    database = connect(":memory:")
    try:
        with pytest.raises(MarginAcceptanceError, match="wiring drift"):
            land_margin_batch(database, _batch("fingerprint-no-side-effect"))
        assert database.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchone()[0] == 0
    finally:
        database.close()


def test_recover_rejects_current_schema_fingerprint_drift(conn, monkeypatch):
    from services.data_sources import contracts

    batch = _batch("recover-schema-drift")
    land_margin_batch(conn, batch)
    assert accept_margin_batch(conn, batch.batch_id).status == "ACCEPTED"
    current = load_dataset_contract("margin")
    monkeypatch.setattr(
        contracts,
        "load_dataset_contract",
        lambda _domain: replace(current, schema_hash="0" * 64),
    )

    with pytest.raises(MarginAcceptanceError, match="wiring drift"):
        recover_margin_batch(conn, batch.batch_id)


@pytest.mark.parametrize(
    "kill_step",
    [
        "after_canonical_delete",
        "after_canonical_insert",
        "after_accepted_upsert",
        "after_batch_update",
    ],
)
def test_tx_b_kill_points_roll_back_every_published_surface(conn, kill_step):
    _accept_seed(conn, batch_id=f"old-{kill_step}")
    candidate = _batch(
        f"new-{kill_step}",
        observed_at=datetime(2026, 7, 20, 1, 10, tzinfo=timezone.utc),
        revision=50_000,
    )
    land_margin_batch(conn, candidate)
    before = _published_snapshot(conn)
    seen: list[str] = []

    def crash_after(step: str) -> None:
        seen.append(step)
        if step == kill_step:
            raise RuntimeError(f"injected crash at {step}")

    with pytest.raises(RuntimeError, match=kill_step):
        accept_margin_batch(conn, candidate.batch_id, after_step=crash_after)

    assert kill_step in seen
    assert _batch_state(conn, candidate.batch_id) == ("LANDED", None)
    assert _published_snapshot(conn) == before
    assert conn.execute(
        "SELECT COUNT(*) FROM landing_tushare_margin WHERE batch_id = ?",
        [candidate.batch_id],
    ).fetchone()[0] == 2


def test_recover_accepts_a_durable_landed_batch(conn):
    batch = _batch("recover-landed")
    land_margin_batch(conn, batch)
    assert _batch_state(conn, batch.batch_id) == ("LANDED", None)

    recover_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("ACCEPTED", None)
    accepted = conn.execute(
        "SELECT batch_id, row_count FROM accepted_partition WHERE partition_value = ?",
        [PARTITION],
    ).fetchone()
    assert tuple(accepted) == (batch.batch_id, 2)


def test_recover_is_idempotent_when_commit_succeeded_but_ack_was_lost(conn):
    batch = _batch("ack-lost")
    land_margin_batch(conn, batch)

    def lose_ack(step: str) -> None:
        if step == "after_commit":
            raise RuntimeError("commit acknowledgement lost")

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        accept_margin_batch(conn, batch.batch_id, after_step=lose_ack)

    # The callback runs after COMMIT: durable state is already accepted even
    # though the caller observed an exception.
    assert _batch_state(conn, batch.batch_id) == ("ACCEPTED", None)
    before = _published_snapshot(conn)
    counts_before = {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in (
            "ingest_batch",
            "landing_tushare_margin",
            "canonical_margin_exchange_daily",
            "accepted_partition",
        )
    }

    recover_margin_batch(conn, batch.batch_id)

    assert _batch_state(conn, batch.batch_id) == ("ACCEPTED", None)
    assert _published_snapshot(conn) == before
    assert {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in counts_before
    } == counts_before


def test_older_observation_cannot_overwrite_newer_accepted_partition(conn):
    newer = _batch(
        "newer-observation",
        observed_at=datetime(2026, 7, 20, 1, 10, tzinfo=timezone.utc),
        revision=20_000,
    )
    land_margin_batch(conn, newer)
    accept_margin_batch(conn, newer.batch_id)
    before = _published_snapshot(conn)

    older = _batch(
        "older-observation",
        observed_at=datetime(2026, 7, 20, 1, 5, tzinfo=timezone.utc),
        revision=90_000,
    )
    land_margin_batch(conn, older)
    accept_margin_batch(conn, older.batch_id)

    assert _batch_state(conn, older.batch_id) == ("REJECTED", "STALE_OBSERVED_AT")
    assert _published_snapshot(conn) == before


def test_equal_observed_at_with_different_content_is_order_independent_conflict(conn):
    first = _batch("equal-observation-first", revision=10)
    land_margin_batch(conn, first)
    accept_margin_batch(conn, first.batch_id)
    before = _published_snapshot(conn)

    conflicting = _batch("equal-observation-conflict", revision=20)
    land_margin_batch(conn, conflicting)
    accept_margin_batch(conn, conflicting.batch_id)

    assert _batch_state(conn, conflicting.batch_id) == (
        "REJECTED",
        "OBSERVATION_CONFLICT",
    )
    assert _published_snapshot(conn) == before
