"""Frozen ``ingest_batch`` landing stamps must never be compared to a live contract.

Background (2026-09-01 contract-fingerprint change): ``source``/``api`` were
removed from ``config_hash``'s payload, so the fingerprint algorithm changed.
``accepted_partition`` and ``canonical_*`` rows get *restamped* on every
sync run to follow the live contract fingerprint. ``ingest_batch.contract_hash
/ config_hash / source_name``, by contrast, are a **frozen evidence seal**:
``payload_hash`` is a sha256 over those three columns plus the landed rows, so
they must never change once a batch lands. After the fingerprint algorithm
changed, a batch's stored stamps therefore legitimately (and permanently)
differ from the pointer's and from what ``load_dataset_contract("margin")``
computes today. Runtime code that asserts
``ingest_batch.contract_hash == contract.contract_hash`` (or
``== accepted_partition.contract_hash``, or
``ingest_batch.source_name == contract.source``) mistakes a frozen seal for a
live wiring check and is a bug: it would fail-closed forever on every batch
landed before 2026-09-01, even though nothing about them is actually wrong.
``contract_version`` and ``writer_id`` are different — they are human-declared
identities, not a hash of the fingerprint algorithm, so they are still
compared. The payload seal itself (``payload_hash`` recomputed from the
batch's *own* stored values) and the pointer-vs-live-contract check are also
still enforced; only "batch frozen stamp vs live/pointer stamp" is not.

The fixture here is fully self-contained: an in-memory DuckDB
(``services.duck_adapter.connect(":memory:")``) bootstrapped by
``ensure_margin_acceptance_schema``, plus a copy of the minimal
``MarginLandingBatch``/``MarginFragment`` builders used elsewhere in this
test package (not imported from another test module, per instructions).

Tests 2-5 encode the TARGET (post-fix) behaviour and were RED before the
2026-09-02 production fix that stops comparing
``ingest_batch.contract_hash/config_hash/source_name`` against the live
contract or the pointer. That is expected and is the point of this file:
their pre-fix failures are the evidence that these tests are not vacuous.
Tests 1, 6, 7, 8 are controls that must pass both before and after the fix.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    validate_margin_batch,
)
from services.data_sources.margin_state import (
    MarginStateError,
    accepted_margin_dates,
    latest_accepted_margin_frontier,
)
from services.data_sources.stamp_checks import _recompute_payload_hash_margin
from services.data_sources.stamp_types import DOMAIN_BY_DATASET_ID
from services.duck_adapter import connect


DATASET_ID = "tier0.market_data.margin_exchange_daily"
PARTITION = "20260717"
OBSERVED_AT = datetime(2026, 7, 20, 1, 5, tzinfo=timezone.utc)

STALE_CONTRACT_HASH = "1" * 64
STALE_CONFIG_HASH = "2" * 64
STALE_SOURCE_NAME = "retired_vendor"

_BATCH_ROW_COLUMNS = (
    "batch_id",
    "partition_value",
    "source_name",
    "contract_version",
    "contract_hash",
    "config_hash",
    "request_json",
    "fragment_outcomes_json",
    "observed_at",
    "available_at",
    "landing_row_count",
)


@pytest.fixture
def conn():
    database = connect(":memory:")
    ensure_margin_acceptance_schema(database)
    yield database
    database.close()


def _row(exchange_id: str, partition: str = PARTITION) -> dict[str, Any]:
    return {
        "trade_date": partition,
        "exchange_id": exchange_id,
        "rzye": 100,
        "rzmre": 10,
        "rzche": 8,
        "rqye": 5,
        "rqmcl": 1,
        "rzrqye": 105,
        "rqyl": 2,
    }


def _batch(
    batch_id: str,
    *,
    partition: str = PARTITION,
    observed_at: datetime = OBSERVED_AT,
    available_at: datetime | None = None,
) -> MarginLandingBatch:
    return MarginLandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=observed_at,
        available_at=available_at or observed_at,
        fragments=tuple(
            MarginFragment(
                exchange_id=exchange_id,
                request={"trade_date": partition, "exchange_id": exchange_id},
                rows=(_row(exchange_id, partition),),
            )
            for exchange_id in ("SSE", "SZSE")
        ),
    )


def _batch_row(conn, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT {", ".join(_BATCH_ROW_COLUMNS)}
          FROM ingest_batch
         WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()
    assert row is not None, f"no ingest_batch row for batch_id={batch_id!r}"
    return dict(zip(_BATCH_ROW_COLUMNS, row, strict=True))


def _reseal(conn, batch_id: str) -> None:
    """Recompute payload_hash from the batch's OWN currently-stored stamps.

    This mirrors production ``check_ingest_batch_derivation`` /
    ``_recompute_payload_hash_margin`` exactly: same landing table, same
    request/outcome JSON, same recompute helper. It does not reimplement the
    hash formula.
    """
    row_dict = _batch_row(conn, batch_id)
    domain = DOMAIN_BY_DATASET_ID[DATASET_ID]
    recomputed, error = _recompute_payload_hash_margin(conn, domain, row_dict)
    assert error is None, f"reseal recompute failed: {error}"
    conn.execute(
        "UPDATE ingest_batch SET payload_hash = ? WHERE batch_id = ?",
        [recomputed, batch_id],
    )


def _make_batch_stamps_stale(conn, batch_id: str) -> None:
    """Simulate a batch landed before the 2026-09-01 fingerprint-algorithm change.

    Overwrites the frozen contract_hash/config_hash/source_name with stale
    values (leaving contract_version/writer_id untouched, since those are
    human-declared identities that do NOT change with the fingerprint
    algorithm), then re-seals payload_hash so the row is an internally
    self-consistent "old-format" checkpoint rather than a corrupted one.
    """
    conn.execute(
        """
        UPDATE ingest_batch
           SET contract_hash = ?, config_hash = ?, source_name = ?
         WHERE batch_id = ?
        """,
        [STALE_CONTRACT_HASH, STALE_CONFIG_HASH, STALE_SOURCE_NAME, batch_id],
    )
    _reseal(conn, batch_id)


# ---------------------------------------------------------------------------
# 1. Sanity check of the reseal recipe itself (must pass before AND after).
# ---------------------------------------------------------------------------


def test_reseal_recipe_reproduces_production_seal(conn):
    batch = _batch("reseal-recipe-check")
    land_margin_batch(conn, batch)

    row_dict = _batch_row(conn, batch.batch_id)
    domain = DOMAIN_BY_DATASET_ID[DATASET_ID]
    recomputed, error = _recompute_payload_hash_margin(conn, domain, row_dict)
    assert error is None, error

    stored = conn.execute(
        "SELECT payload_hash FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    assert recomputed == stored


# ---------------------------------------------------------------------------
# 2-5. Target behaviour. RED before the 2026-09-02 fix (see module docstring).
# ---------------------------------------------------------------------------


def test_stale_landed_stamps_are_still_a_recoverable_checkpoint(conn):
    batch = _batch("stale-landed-checkpoint")
    land_margin_batch(conn, batch)
    _make_batch_stamps_stale(conn, batch.batch_id)

    recoverable = find_current_landed_margin_batch(conn, PARTITION)

    assert recoverable is not None
    assert recoverable.batch_id == batch.batch_id


def test_stale_landed_stamps_still_validate(conn):
    batch = _batch("stale-landed-validate")
    land_margin_batch(conn, batch)
    _make_batch_stamps_stale(conn, batch.batch_id)

    validated = validate_margin_batch(conn, batch.batch_id)

    assert validated.batch_id == batch.batch_id
    assert validated.row_count == 2
    assert len(validated.canonical_rows) == 2


def test_stale_landed_stamps_accept_and_pointer_is_stamped_with_live_contract(conn):
    batch = _batch("stale-landed-accept")
    land_margin_batch(conn, batch)
    _make_batch_stamps_stale(conn, batch.batch_id)

    outcome = accept_margin_batch(conn, batch.batch_id)

    assert outcome.status == "ACCEPTED"

    live_contract = load_dataset_contract("margin")
    pointer_stamps = conn.execute(
        "SELECT contract_hash, config_hash FROM accepted_partition WHERE partition_value = ?",
        [PARTITION],
    ).fetchone()
    assert tuple(pointer_stamps) == (
        live_contract.contract_hash,
        live_contract.config_hash,
    )

    frozen_stamps = conn.execute(
        "SELECT contract_hash, config_hash, source_name FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()
    assert tuple(frozen_stamps) == (
        STALE_CONTRACT_HASH,
        STALE_CONFIG_HASH,
        STALE_SOURCE_NAME,
    )


def test_stale_stamps_on_accepted_batch_do_not_block_accepted_state_reads(conn):
    batch = _batch("stale-after-accept")
    land_margin_batch(conn, batch)
    outcome = accept_margin_batch(conn, batch.batch_id)
    assert outcome.status == "ACCEPTED"

    _make_batch_stamps_stale(conn, batch.batch_id)

    dates = accepted_margin_dates(conn)
    assert PARTITION in dates

    frontier = latest_accepted_margin_frontier(conn)
    assert frontier is not None


# ---------------------------------------------------------------------------
# 6-8. Negative controls: must pass BOTH before and after the fix.
# ---------------------------------------------------------------------------


def test_seal_is_still_enforced_when_stamps_change_without_reseal(conn):
    """Tampering the frozen stamps WITHOUT re-sealing must still be caught.

    Pre-fix this may raise MarginValidationError(code="CONTRACT_DRIFT")
    instead of LANDING_HASH_MISMATCH, because the (buggy) wiring check runs
    before the payload-hash recompute and short-circuits it. That would mean
    this control is only meaningful post-fix -- do not weaken the assertion
    to paper over that; report it instead.
    """
    batch = _batch("seal-still-enforced")
    land_margin_batch(conn, batch)

    conn.execute(
        """
        UPDATE ingest_batch
           SET contract_hash = ?, config_hash = ?, source_name = ?
         WHERE batch_id = ?
        """,
        [STALE_CONTRACT_HASH, STALE_CONFIG_HASH, STALE_SOURCE_NAME, batch.batch_id],
    )

    with pytest.raises(MarginValidationError) as excinfo:
        validate_margin_batch(conn, batch.batch_id)

    assert excinfo.value.code == "LANDING_HASH_MISMATCH"


def test_pointer_stamp_drift_is_still_blocking(conn):
    batch = _batch("pointer-stamp-drift")
    land_margin_batch(conn, batch)
    assert accept_margin_batch(conn, batch.batch_id).status == "ACCEPTED"

    conn.execute(
        "UPDATE accepted_partition SET config_hash = ? WHERE batch_id = ?",
        ["0" * 64, batch.batch_id],
    )

    with pytest.raises(MarginStateError):
        accepted_margin_dates(conn)


def test_declared_identity_is_still_compared(conn):
    batch = _batch("declared-identity-still-compared")
    land_margin_batch(conn, batch)

    conn.execute(
        "UPDATE ingest_batch SET contract_version = '99' WHERE batch_id = ?",
        [batch.batch_id],
    )

    with pytest.raises(MarginAcceptanceError):
        find_current_landed_margin_batch(conn, PARTITION)
