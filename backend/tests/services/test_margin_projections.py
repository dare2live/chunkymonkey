from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("deterministic_margin_calendar")

from services.data_sources.margin_acceptance import (
    MarginFragment,
    MarginLandingBatch,
    accept_margin_batch,
    ensure_margin_acceptance_schema,
    land_margin_batch,
)
from services.data_sources.margin_projections import (
    GAP_FAILURE_TYPE,
    MarginProjectionResult,
    MarginProjectionError,
    MarginReconcileProjectionFailure,
    RECONCILE_FAILURE_TYPE,
    derive_margin_accepted_state,
    project_margin_accepted_state,
)
from services.data_sources.margin_schema import ACCEPTED_TABLE
from services.data_sources.margin_readiness import evaluate_margin_readiness
from services.data_sources.margin_state import MarginStateError, accepted_margin_partitions
from services.duck_adapter import connect
from services.source_watermarks import (
    ensure_source_watermark_schema,
    record_source_failure,
    upsert_watermark,
)


PARTITION = "20260715"


def test_projection_ready_is_derived_from_real_typed_evidence():
    ready = MarginProjectionResult(
        frontier=PARTITION,
        row_count=3,
        accepted_at=datetime(2026, 7, 16, 1, 5, tzinfo=timezone.utc),
        expected=(PARTITION,),
        accepted=(PARTITION,),
        missing=(),
        reconcile_failures=(),
    )

    assert ready.ready is True
    assert replace(ready, expected=()).ready is False
    assert replace(ready, missing=(PARTITION,)).ready is False
    assert replace(
        ready,
        reconcile_failures=(
            MarginReconcileProjectionFailure(
                partition_value=PARTITION,
                issue_codes=("FORMAL_EVIDENCE_INVALID",),
            ),
        ),
    ).ready is False


def _row(exchange: str, partition: str = PARTITION) -> dict:
    return {
        "trade_date": partition,
        "exchange_id": exchange,
        "rzye": 100,
        "rzmre": 10,
        "rzche": 8,
        "rqye": 5,
        "rqmcl": 1,
        "rzrqye": 105,
        "rqyl": 2,
    }


def _batch(
    partition: str,
    *,
    batch_id: str,
    stamp: datetime,
    exchanges: tuple[str, ...] = ("SSE", "SZSE", "BSE"),
) -> MarginLandingBatch:
    return MarginLandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=stamp,
        available_at=stamp,
        fragments=tuple(
            MarginFragment(
                exchange_id=exchange,
                request={"trade_date": partition, "exchange_id": exchange},
                rows=(_row(exchange, partition),),
            )
            for exchange in exchanges
        ),
    )


def _seed_legacy(raw, partition: str = PARTITION) -> None:
    raw.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_tushare_margin(
            trade_date VARCHAR,
            exchange_id VARCHAR,
            rzye DECIMAL(38,6),
            rzmre DECIMAL(38,6),
            rzche DECIMAL(38,6),
            rqye DECIMAL(38,6),
            rqmcl DECIMAL(38,6),
            rzrqye DECIMAL(38,6),
            rqyl DECIMAL(38,6)
        )
        """
    )
    raw.execute("DELETE FROM raw_tushare_margin WHERE trade_date=?", [partition])
    raw.executemany(
        "INSERT INTO raw_tushare_margin VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [tuple(_row(exchange, partition).values()) for exchange in ("SSE", "SZSE", "BSE")],
    )


def _accept(
    raw,
    partition: str = PARTITION,
    *,
    batch_id: str = "projected-accepted",
    legacy: bool = True,
    stamp: datetime | None = None,
) -> None:
    stamp = stamp or datetime(2026, 7, 16, 1, 5, tzinfo=timezone.utc)
    batch = _batch(
        partition,
        batch_id=batch_id,
        stamp=stamp,
    )
    land_margin_batch(raw, batch)
    assert accept_margin_batch(raw, batch.batch_id).status == "ACCEPTED"
    if legacy:
        _seed_legacy(raw, partition)


class _QueryCountingConnection:
    """Count read statements without weakening the real DuckDB execution path."""

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


class _SchemaProbeFailure:
    def execute(self, statement: str, parameters=None):
        raise RuntimeError("forced schema probe failure")


def test_schema_probe_failure_cannot_masquerade_as_no_formal_tables():
    broken = _SchemaProbeFailure()

    with pytest.raises(MarginStateError, match="schema evidence query failed"):
        accepted_margin_partitions(broken)
    with pytest.raises(MarginProjectionError, match="schema evidence query failed"):
        derive_margin_accepted_state(broken, [PARTITION])


def _full_read_counts(partitions: tuple[str, ...]) -> tuple[int, int]:
    raw = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(raw)
        for partition in partitions:
            next_session = datetime.strptime(partition, "%Y%m%d") + timedelta(days=1)
            while next_session.weekday() >= 5:
                next_session += timedelta(days=1)
            _accept(
                raw,
                partition,
                batch_id=f"query-scale-{partition}",
                stamp=next_session.replace(
                    hour=1,
                    minute=5,
                    tzinfo=timezone.utc,
                ),
            )
        counted = _QueryCountingConnection(raw)

        result = derive_margin_accepted_state(counted, partitions)

        assert result.accepted == partitions
        assert result.reconcile_failures == ()
        projection_count = len(counted.statements)

        readiness_conn = _QueryCountingConnection(raw)
        readiness = evaluate_margin_readiness(
            readiness_conn,
            partitions,
            eligible_end=partitions[-1],
            eligibility_reason="published",
        )
        assert readiness.ready is True
        return projection_count, len(readiness_conn.statements)
    finally:
        raw.close()


def test_projection_read_count_is_bounded_by_surfaces_not_partition_count():
    partitions = []
    cursor = datetime(2026, 7, 15)
    while len(partitions) < 20:
        if cursor.weekday() < 5:
            partitions.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)

    one_partition = _full_read_counts(("20260715",))
    twenty_partitions = _full_read_counts(tuple(partitions))

    assert twenty_partitions == one_partition == (6, 6)


def _seed_old_raw_watermark(ops) -> None:
    ensure_source_watermark_schema(ops)
    upsert_watermark(
        ops,
        {
            "data_domain": "sync:margin",
            "source_name": "tushare",
            "source_tier": 2,
            "last_success_at": "2099-12-31T00:00:00+00:00",
            "last_data_date": "20991231",
            "row_count": 999,
            "parser_version": "legacy_raw",
        },
    )


def _gap_payload(ops) -> dict:
    row = ops.execute(
        "SELECT last_error FROM mart_data_source_failure_queue "
        "WHERE data_domain='sync:margin' AND error_type=?",
        [GAP_FAILURE_TYPE],
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


def test_projection_clears_legacy_false_green_when_no_partition_is_accepted():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        raw.execute("CREATE TABLE raw_tushare_margin(trade_date VARCHAR)")
        raw.execute("INSERT INTO raw_tushare_margin VALUES ('20991231')")
        _seed_old_raw_watermark(ops)

        result = project_margin_accepted_state(raw, ops, [PARTITION])

        assert result.frontier is None
        watermark = ops.execute(
            "SELECT last_data_date, row_count, last_success_at, parser_version "
            "FROM mart_data_source_watermark WHERE data_domain='sync:margin'"
        ).fetchone()
        assert tuple(watermark[index] for index in range(3)) == (None, 0, None)
        assert str(watermark[3]).startswith("margin_accepted_contract_")
        failure = ops.execute(
            "SELECT error_type, last_error, status FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin'"
        ).fetchone()
        assert failure[0] == GAP_FAILURE_TYPE
        assert PARTITION in failure[1]
        assert failure[2] == "open"
    finally:
        raw.close()
        ops.close()


def test_projection_replaces_old_watermark_with_exact_accepted_fact_and_resolves_gap():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(raw)
        _accept(raw)
        _seed_old_raw_watermark(ops)
        first = project_margin_accepted_state(raw, ops, [PARTITION, "20260716"])
        assert first.accepted == (PARTITION,)
        assert first.missing == ("20260716",)
        first_payload = _gap_payload(ops)
        assert first_payload["expected_count"] == 2
        assert first_payload["accepted_count"] == 1
        assert first_payload["missing_sample"] == ["20260716"]
        assert ops.execute(
            "SELECT status FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' AND error_type=?",
            [GAP_FAILURE_TYPE],
        ).fetchone()[0] == "open"

        result = project_margin_accepted_state(raw, ops, [PARTITION])

        assert result.frontier == PARTITION
        assert result.row_count == 3
        watermark = ops.execute(
            "SELECT last_data_date, row_count, last_success_at "
            "FROM mart_data_source_watermark WHERE data_domain='sync:margin'"
        ).fetchone()
        assert tuple(watermark[index] for index in range(2)) == (PARTITION, 3)
        accepted_at = raw.execute(
            f"SELECT accepted_at FROM {ACCEPTED_TABLE} WHERE partition_value=?",
            [PARTITION],
        ).fetchone()[0]
        assert str(watermark[2]) == str(accepted_at).split("+")[0]
        status = ops.execute(
            "SELECT status FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' AND error_type=?",
            [GAP_FAILURE_TYPE],
        ).fetchone()[0]
        assert status == "resolved"
    finally:
        raw.close()
        ops.close()


def test_projection_persists_and_rebuilds_accepted_shadow_reconcile_failure():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(raw)
        _accept(raw, legacy=False)

        first = project_margin_accepted_state(raw, ops, [PARTITION])
        assert [failure.partition_value for failure in first.reconcile_failures] == [
            PARTITION
        ]
        row = ops.execute(
            "SELECT status, occurrence_count, last_error "
            "FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' AND error_type=?",
            [RECONCILE_FAILURE_TYPE],
        ).fetchone()
        assert row[0] == "open"
        assert row[1] == 1
        assert json.loads(row[2])["failures"][0]["partition"] == PARTITION

        second = project_margin_accepted_state(raw, ops, [PARTITION])
        assert second.reconcile_failures
        repeated = ops.execute(
            "SELECT status, occurrence_count FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' AND error_type=?",
            [RECONCILE_FAILURE_TYPE],
        ).fetchone()
        assert tuple(repeated[index] for index in range(2)) == ("open", 2)

        _seed_legacy(raw)
        repaired = project_margin_accepted_state(raw, ops, [PARTITION])
        assert repaired.reconcile_failures == ()
        assert ops.execute(
            "SELECT status FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' AND error_type=?",
            [RECONCILE_FAILURE_TYPE],
        ).fetchone()[0] == "resolved"
    finally:
        raw.close()
        ops.close()


def test_projection_creates_a_missing_watermark_row_from_empty_ops_state():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        result = project_margin_accepted_state(raw, ops, [PARTITION])

        assert result.frontier is None
        row = ops.execute(
            "SELECT source_name, source_tier, last_data_date, row_count, "
            "last_success_at, parser_version "
            "FROM mart_data_source_watermark WHERE data_domain='sync:margin'"
        ).fetchone()
        assert tuple(row[index] for index in range(5)) == (
            "tushare",
            2,
            None,
            0,
            None,
        )
        assert str(row[5]).startswith("margin_accepted_contract_")
    finally:
        raw.close()
        ops.close()


def test_projection_creates_the_only_margin_watermark_and_removes_stale_keys():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        ensure_source_watermark_schema(ops)
        for source_name, source_tier in (("legacy_raw", 1), ("tushare", 9)):
            upsert_watermark(
                ops,
                {
                    "data_domain": "sync:margin",
                    "source_name": source_name,
                    "source_tier": source_tier,
                    "last_success_at": "2099-12-31T00:00:00+00:00",
                    "last_data_date": "20991231",
                    "row_count": 999,
                    "parser_version": "legacy_raw",
                },
            )

        result = project_margin_accepted_state(raw, ops, [PARTITION])

        assert result.frontier is None
        rows = ops.execute(
            "SELECT source_name, source_tier, last_data_date, row_count, last_success_at "
            "FROM mart_data_source_watermark WHERE data_domain='sync:margin'"
        ).fetchall()
        assert [tuple(row[index] for index in range(5)) for row in rows] == [
            ("tushare", 2, None, 0, None)
        ]
    finally:
        raw.close()
        ops.close()


def test_projection_expected_minus_accepted_gap_uses_latest_landed_then_rejected_evidence():
    raw = connect(":memory:")
    ops = connect(":memory:")
    missing_partition = "20260716"
    try:
        ensure_margin_acceptance_schema(raw)
        older = _batch(
            missing_partition,
            batch_id="older-incomplete",
            stamp=datetime(2026, 7, 17, 1, 5, tzinfo=timezone.utc),
            exchanges=("SSE",),
        )
        land_margin_batch(raw, older)
        older_rejection = accept_margin_batch(raw, older.batch_id)
        assert older_rejection.status == "REJECTED"
        raw.execute(
            "UPDATE ingest_batch SET landed_at=? WHERE batch_id=?",
            [datetime(2026, 7, 16, tzinfo=timezone.utc), older.batch_id],
        )
        incomplete = _batch(
            missing_partition,
            batch_id="latest-incomplete",
            stamp=datetime(2026, 7, 17, 1, 5, tzinfo=timezone.utc),
            exchanges=("SSE",),
        )
        land_margin_batch(raw, incomplete)
        raw.execute(
            "UPDATE ingest_batch SET landed_at=? WHERE batch_id=?",
            [datetime(2026, 7, 17, tzinfo=timezone.utc), incomplete.batch_id],
        )

        result = project_margin_accepted_state(raw, ops, [PARTITION, missing_partition])

        assert result.expected == (PARTITION, missing_partition)
        assert result.accepted == ()
        assert result.missing == (PARTITION, missing_partition)
        payload = _gap_payload(ops)
        assert payload["missing_count"] == 2
        assert payload["missing_sample"] == [PARTITION, missing_partition]
        no_batch, landed = payload["latest_ingest_evidence"]
        assert no_batch == {"partition": PARTITION, "state": "NO_INGEST_BATCH"}
        assert landed["partition"] == missing_partition
        assert landed["batch_id"] == "latest-incomplete"
        assert landed["state"] == "LANDED"
        assert landed["rejection_code"] is None
        assert landed["rejection_detail"] is None
        assert landed["fragment_outcomes"]
        assert landed["landed_at"]

        rejection = accept_margin_batch(raw, incomplete.batch_id)
        assert rejection.status == "REJECTED"
        project_margin_accepted_state(raw, ops, [PARTITION, missing_partition])

        evidence = _gap_payload(ops)["latest_ingest_evidence"][1]
        assert evidence["batch_id"] == incomplete.batch_id
        assert evidence["state"] == "REJECTED"
        assert evidence["rejection_code"] == rejection.rejection_code
        assert evidence["rejection_detail"]
    finally:
        raw.close()
        ops.close()


def test_gap_failure_payload_stays_valid_json_for_many_missing_partitions():
    raw = connect(":memory:")
    ops = connect(":memory:")
    expected = [
        (datetime(2026, 7, 15) + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(20)
    ]
    try:
        project_margin_accepted_state(raw, ops, expected)

        row = ops.execute(
            "SELECT last_error FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' AND error_type=?",
            [GAP_FAILURE_TYPE],
        ).fetchone()
        assert row is not None
        assert len(row[0]) <= 1000
        payload = json.loads(row[0])
        assert payload["missing_count"] == 20
        assert payload["earliest_missing"] == expected[0]
        assert payload["latest_missing"] == expected[-1]
        assert 0 < len(payload["latest_ingest_evidence"]) < 20
        assert all(
            item["state"] == "NO_INGEST_BATCH"
            for item in payload["latest_ingest_evidence"]
        )
    finally:
        raw.close()
        ops.close()


def test_projection_keeps_quota_failure_separate_from_the_accepted_gap():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        project_margin_accepted_state(
            raw,
            ops,
            [PARTITION],
            quota_error="provider quota exhausted",
        )
        open_types = {
            row[0]
            for row in ops.execute(
                "SELECT error_type FROM mart_data_source_failure_queue "
                "WHERE data_domain='sync:margin' AND status='open'"
            ).fetchall()
        }
        assert open_types == {GAP_FAILURE_TYPE, "sync_quota_halt"}

        project_margin_accepted_state(
            raw,
            ops,
            [PARTITION],
            provider_succeeded=True,
        )

        statuses = {
            row[0]: row[1]
            for row in ops.execute(
                "SELECT error_type, status FROM mart_data_source_failure_queue "
                "WHERE data_domain='sync:margin'"
            ).fetchall()
        }
        assert statuses[GAP_FAILURE_TYPE] == "open"
        assert statuses["sync_quota_halt"] == "resolved"
    finally:
        raw.close()
        ops.close()


def test_projection_keeps_runtime_failure_separate_until_accepted_gap_closes():
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        record_source_failure(
            ops,
            data_domain="sync:margin",
            source_name="tushare",
            source_tier=2,
            error_type="sync_batch_failed",
            last_error="provider runtime failed",
        )

        project_margin_accepted_state(raw, ops, [PARTITION])

        open_types = {
            row[0]
            for row in ops.execute(
                "SELECT error_type FROM mart_data_source_failure_queue "
                "WHERE data_domain='sync:margin' AND status='open'"
            ).fetchall()
        }
        assert open_types == {GAP_FAILURE_TYPE, "sync_batch_failed"}

        ensure_margin_acceptance_schema(raw)
        _accept(raw)
        project_margin_accepted_state(raw, ops, [PARTITION])

        statuses = {
            row[0]: row[1]
            for row in ops.execute(
                "SELECT error_type, status FROM mart_data_source_failure_queue "
                "WHERE data_domain='sync:margin'"
            ).fetchall()
        }
        assert statuses[GAP_FAILURE_TYPE] == "resolved"
        assert statuses["sync_batch_failed"] == "resolved"
    finally:
        raw.close()
        ops.close()


def test_projection_coverage_start_is_an_obligation_not_an_acceptance_ban():
    raw = connect(":memory:")
    ops = connect(":memory:")
    historical = "20260714"
    try:
        ensure_margin_acceptance_schema(raw)
        _accept(raw, historical, batch_id="historical-before-coverage")

        result = project_margin_accepted_state(raw, ops, [historical, PARTITION])

        assert result.expected == (PARTITION,)
        assert result.accepted == ()
        assert result.missing == (PARTITION,)
        assert result.frontier is None
        assert raw.execute(
            f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE partition_value=?",
            [historical],
        ).fetchone()[0] == 1
        assert _gap_payload(ops)["missing_sample"] == [PARTITION]
    finally:
        raw.close()
        ops.close()


class _FailOnNthFailureQueueDrop:
    def __init__(self, delegate, *, fail_on: int) -> None:
        self._delegate = delegate
        self._fail_on = fail_on
        self._drop_count = 0

    def executescript(self, sql):
        return self._delegate.executescript(sql)

    def execute(self, sql, params=None):
        result = self._delegate.execute(sql, params)
        if "DROP TABLE IF EXISTS mart_data_source_failure_queue" in sql:
            self._drop_count += 1
            if self._drop_count == self._fail_on:
                raise RuntimeError("injected Ops projection failure")
        return result

    def executemany(self, sql, params):
        return self._delegate.executemany(sql, params)

    def commit(self):
        return self._delegate.commit()


def _ops_projection_snapshot(ops) -> dict[str, object]:
    watermarks = ops.execute(
        "SELECT data_domain, source_name, source_tier, last_data_date, row_count, "
        "parser_version FROM mart_data_source_watermark ORDER BY 1, 2, 3"
    ).fetchall()
    queue_exists = bool(ops.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name='mart_data_source_failure_queue'"
    ).fetchone()[0])
    failures = (
        ops.execute(
            "SELECT failure_id, data_domain, source_name, source_tier, stock_code, "
            "error_type, last_error, status, first_seen_at, last_seen_at, retry_after, "
            "occurrence_count, resolved_at FROM mart_data_source_failure_queue "
            "ORDER BY failure_id"
        ).fetchall()
        if queue_exists
        else None
    )
    return {
        "watermarks": [tuple(row[index] for index in range(6)) for row in watermarks],
        "queue_exists": queue_exists,
        "failures": (
            [tuple(row[index] for index in range(13)) for row in failures]
            if failures is not None
            else None
        ),
    }


@pytest.mark.parametrize(
    ("quota_error", "fail_on_drop", "legacy", "expected"),
    [
        (None, 1, True, [PARTITION, "20260716"]),
        ("provider quota exhausted", 2, True, [PARTITION, "20260716"]),
        ("provider quota exhausted", 3, True, [PARTITION, "20260716"]),
        (None, 2, True, [PARTITION]),
        (None, 3, False, [PARTITION]),
    ],
)
def test_cross_database_projection_failure_is_atomic_and_rebuildable_from_raw_truth(
    quota_error: str | None,
    fail_on_drop: int,
    legacy: bool,
    expected: list[str],
):
    raw = connect(":memory:")
    ops = connect(":memory:")
    try:
        ensure_margin_acceptance_schema(raw)
        _accept(raw, legacy=legacy)
        _seed_old_raw_watermark(ops)
        record_source_failure(
            ops,
            data_domain="sync:other",
            source_name="tushare",
            source_tier=2,
            error_type="preexisting_failure",
            last_error="must survive a failed margin projection",
        )
        before = _ops_projection_snapshot(ops)

        with pytest.raises(RuntimeError, match="injected Ops projection failure"):
            project_margin_accepted_state(
                raw,
                _FailOnNthFailureQueueDrop(ops, fail_on=fail_on_drop),
                expected,
                quota_error=quota_error,
            )

        assert _ops_projection_snapshot(ops) == before
        assert raw.execute(
            f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE partition_value=?",
            [PARTITION],
        ).fetchone()[0] == 1

        result = project_margin_accepted_state(raw, ops, [PARTITION, "20260716"])

        assert result.frontier == PARTITION
        assert result.missing == ("20260716",)
        watermark = ops.execute(
            "SELECT last_data_date, row_count FROM mart_data_source_watermark "
            "WHERE data_domain='sync:margin'"
        ).fetchone()
        assert tuple(watermark[index] for index in range(2)) == (PARTITION, 3)
        assert _gap_payload(ops)["missing_sample"] == ["20260716"]
    finally:
        raw.close()
        ops.close()
