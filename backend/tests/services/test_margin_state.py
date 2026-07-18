from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("deterministic_margin_calendar")

from services.data_sources.margin_acceptance import (
    MarginFragment,
    MarginLandingBatch,
    accept_margin_batch,
    ensure_margin_acceptance_schema,
    land_margin_batch,
)
from services.data_sources.margin_state import (
    MarginStateError,
    accepted_margin_dates,
    evaluate_margin_readiness,
    latest_accepted_margin_frontier,
    load_margin_accepted_state,
    missing_accepted_margin_dates,
)
from services.data_sources.margin_schema import MARGIN_FIELDS
from services.data_sources import margin_validation
from services.data_sources.margin_validation import canonical_content_hash
from services.duck_adapter import connect


PARTITION = "20260715"


def _row(exchange: str) -> dict:
    return {
        "trade_date": PARTITION,
        "exchange_id": exchange,
        "rzye": 100,
        "rzmre": 10,
        "rzche": 8,
        "rqye": 5,
        "rqmcl": 1,
        "rzrqye": 105,
        "rqyl": 2,
    }


def _accept(conn) -> None:
    available = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
    batch = MarginLandingBatch(
        batch_id="accepted-state",
        partition_value=PARTITION,
        observed_at=available,
        available_at=available,
        fragments=tuple(
            MarginFragment(
                exchange_id=exchange,
                request={"trade_date": PARTITION, "exchange_id": exchange},
                rows=(_row(exchange),),
            )
            for exchange in ("SSE", "SZSE", "BSE")
        ),
    )
    land_margin_batch(conn, batch)
    assert accept_margin_batch(conn, batch.batch_id).status == "ACCEPTED"


def test_raw_rows_never_count_as_accepted_state():
    conn = connect(":memory:")
    try:
        conn.execute("CREATE TABLE raw_tushare_margin(trade_date VARCHAR)")
        conn.execute("INSERT INTO raw_tushare_margin VALUES (?)", [PARTITION])

        assert accepted_margin_dates(conn) == set()
        assert latest_accepted_margin_frontier(conn) is None
        assert missing_accepted_margin_dates(conn, ["20260714", PARTITION]) == [PARTITION]
    finally:
        conn.close()


def test_frontier_is_derived_from_proven_accepted_partition():
    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        _accept(conn)

        assert accepted_margin_dates(conn) == {PARTITION}
        assert missing_accepted_margin_dates(conn, ["20260714", PARTITION]) == []
        frontier = latest_accepted_margin_frontier(conn)
        assert frontier is not None
        assert frontier.last_date == PARTITION
        assert frontier.row_count == 3
        assert frontier.last_success_at is not None
    finally:
        conn.close()


def test_typed_accepted_state_and_readiness_share_one_proven_snapshot():
    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        _accept(conn)

        state = load_margin_accepted_state(conn)
        assert state.dates == frozenset({PARTITION})
        assert state.batch_by_partition == {PARTITION: "accepted-state"}
        assert state.frontier is not None
        assert state.frontier.last_date == PARTITION

        readiness = evaluate_margin_readiness(
            conn,
            [PARTITION, "20260716"],
            eligible_end="20260716",
            eligibility_reason="t_plus_one",
            reconcile=False,
            accepted_state=state,
        )
        assert readiness.accepted_state is state
        assert readiness.expected == (PARTITION, "20260716")
        assert readiness.missing == ("20260716",)
        assert readiness.unexpected == ()
        assert readiness.reconcile_failures == ()
        assert readiness.ready is False
    finally:
        conn.close()


def test_readiness_reconcile_failure_is_typed_and_blocking(monkeypatch):
    from services.data_sources import margin_reconcile

    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        _accept(conn)
        issue = SimpleNamespace(code=SimpleNamespace(value="VALUE_MISMATCH"))
        monkeypatch.setattr(
            margin_reconcile,
            "reconcile_margin_partition",
            lambda _conn, _partition, **_kwargs: SimpleNamespace(
                ok=False, issues=(issue,)
            ),
        )

        readiness = evaluate_margin_readiness(
            conn,
            [PARTITION],
            eligible_end=PARTITION,
            eligibility_reason="published",
        )

        assert readiness.ready is False
        assert readiness.reconcile_failures[0].partition_value == PARTITION
        assert readiness.reconcile_failures[0].issue_codes == ("VALUE_MISMATCH",)
    finally:
        conn.close()


def test_readiness_uses_one_injected_contract_snapshot(monkeypatch):
    from services.data_sources import contracts, margin_reconcile, margin_state
    from services.data_sources.contracts import load_dataset_contract

    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        _accept(conn)
        planned = load_dataset_contract("margin")
        monkeypatch.setattr(
            margin_state,
            "load_dataset_contract",
            lambda _domain: pytest.fail("readiness reloaded contract B"),
        )
        monkeypatch.setattr(
            contracts,
            "load_dataset_contract",
            lambda _domain: pytest.fail("accepted state reloaded contract C"),
        )
        monkeypatch.setattr(
            margin_reconcile,
            "load_dataset_contract",
            lambda _domain: pytest.fail("reconcile reloaded contract D"),
        )
        reconciled_contracts = []
        monkeypatch.setattr(
            margin_reconcile,
            "reconcile_margin_partition",
            lambda _conn, _partition, **kwargs: reconciled_contracts.append(
                kwargs.get("contract")
            )
            or SimpleNamespace(ok=True, issues=()),
        )

        readiness = evaluate_margin_readiness(
            conn,
            [PARTITION],
            contract=planned,
            eligible_end=PARTITION,
            eligibility_reason="published",
        )

        assert readiness.ready is True
        assert readiness.expected == (PARTITION,)
        assert readiness.accepted_state.dates == frozenset({PARTITION})
        assert len(reconciled_contracts) == 1
        assert reconciled_contracts[0] is planned
    finally:
        conn.close()


def test_state_projection_and_readiness_reprove_publication_calendar(monkeypatch):
    from services.data_sources import margin_acceptance as acceptance
    from services.data_sources.margin_projections import derive_margin_accepted_state

    partition = "20260717"
    available = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
    batch = MarginLandingBatch(
        batch_id="accepted-before-calendar-revision",
        partition_value=partition,
        observed_at=available,
        available_at=available,
        fragments=tuple(
            MarginFragment(
                exchange_id=exchange,
                request={"trade_date": partition, "exchange_id": exchange},
                rows=({**_row(exchange), "trade_date": partition},),
            )
            for exchange in ("SSE", "SZSE", "BSE")
        ),
    )
    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        land_margin_batch(conn, batch)
        assert accept_margin_batch(conn, batch.batch_id).status == "ACCEPTED"
        assert accepted_margin_dates(conn) == {partition}

        # Simulate a corrected calendar proving Monday was not the successor
        # session. Every accepted-state consumer must now fail closed.
        monkeypatch.setattr(
            margin_validation,
            "load_margin_publication_sessions",
            lambda _partition, **_kwargs: (partition, "20260721"),
        )
        with pytest.raises(MarginStateError, match="PREMATURE_PUBLICATION"):
            accepted_margin_dates(conn)
        with pytest.raises(MarginStateError, match="PREMATURE_PUBLICATION"):
            derive_margin_accepted_state(conn, [partition])
        with pytest.raises(MarginStateError, match="PREMATURE_PUBLICATION"):
            evaluate_margin_readiness(
                conn,
                [partition],
                eligible_end=partition,
                eligibility_reason="published",
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE ingest_batch SET canonical_hash = 'bad' WHERE batch_id = 'accepted-state'",
        "DELETE FROM canonical_margin_exchange_daily WHERE exchange_id = 'BSE'",
        "UPDATE canonical_margin_exchange_daily SET rzye = rzye + 1 WHERE exchange_id = 'SSE'",
        "UPDATE landing_tushare_margin SET payload_json = '{}' WHERE row_ordinal = 1",
        "UPDATE canonical_margin_exchange_daily SET available_at = available_at + INTERVAL 1 SECOND WHERE exchange_id = 'SSE'",
    ],
)
def test_accepted_projection_fails_closed_on_evidence_tamper(tamper: str):
    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        _accept(conn)
        conn.execute(tamper)

        with pytest.raises(MarginStateError):
            accepted_margin_dates(conn)
    finally:
        conn.close()


def test_accepted_projection_rebuilds_from_landing_not_lockstep_mutable_hashes():
    conn = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(conn)
        _accept(conn)
        conn.execute(
            "UPDATE canonical_margin_exchange_daily SET rzye = rzye + 1, "
            "rzrqye = rzrqye + 1 WHERE exchange_id = 'SSE'"
        )
        rows = conn.execute(
            f"""
            SELECT {', '.join(MARGIN_FIELDS)}
              FROM canonical_margin_exchange_daily
             ORDER BY trade_date, exchange_id
            """
        ).fetchall()
        changed = [
            {
                field: (
                    value.strftime("%Y%m%d") if field == "trade_date" else value
                )
                for field, value in zip(MARGIN_FIELDS, row, strict=True)
            }
            for row in rows
        ]
        forged_hash = canonical_content_hash(changed)
        conn.execute(
            "UPDATE ingest_batch SET canonical_hash = ? WHERE batch_id = 'accepted-state'",
            [forged_hash],
        )
        conn.execute(
            "UPDATE accepted_partition SET content_hash = ? WHERE batch_id = 'accepted-state'",
            [forged_hash],
        )

        with pytest.raises(MarginStateError, match="landing content mismatch"):
            accepted_margin_dates(conn)
    finally:
        conn.close()
