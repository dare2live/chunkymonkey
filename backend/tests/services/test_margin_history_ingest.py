"""History migration must prove legacy parity before formal publication.

The history seam is deliberately different from the incremental compatibility
path: it may read the existing legacy partition, but it must never rewrite it.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.usefixtures("deterministic_margin_calendar")

from services.data_sources import margin_history as mh
from services.data_sources import margin_history_ingest as mhi
from services.data_sources import margin_history_runtime
from services.data_sources import margin_ingest as mi
from services.data_sources import sync_runner as sr
from services.data_sources.margin_acceptance import (
    MarginFragment,
    MarginLandingBatch,
    ensure_margin_acceptance_schema,
    land_margin_batch,
)
from services.data_sources.margin_legacy_reconcile import (
    MarginReconcileCode,
    compare_margin_history_rows,
)
from services.data_sources.margin_projections import project_margin_accepted_state
from services.data_sources.margin_reconcile import reconcile_margin_partition
from services.data_sources.margin_schema import MARGIN_FIELDS
from services.duck_adapter import connect


PARTITION = "20260715"
PRE_BSE_PARTITION = "20190102"
PRE_BSE_SECOND = "20190103"
OBSERVED_AT = datetime(2026, 7, 16, 1, 5, tzinfo=timezone.utc)


def _row(
    exchange_id: str,
    *,
    partition: str = PARTITION,
    revision: int = 0,
) -> dict:
    rzye = 100 + revision
    rqye = 5
    return {
        "trade_date": partition,
        "exchange_id": exchange_id,
        "rzye": rzye,
        "rzmre": 10,
        "rzche": 8,
        "rqye": rqye,
        "rqmcl": 1,
        "rzrqye": rzye + rqye,
        "rqyl": 2,
    }


def _spec_and_contract():
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["retry"] = {"max_attempts": 1, "backoff_seconds": [0]}
    spec["rate_limit"] = {}
    return spec, mi.contract_for_spec(spec)


def _history_plan(conn, contract):
    ensure_margin_acceptance_schema(conn)
    report = reconcile_margin_partition(conn, PARTITION, contract=contract)
    return mh.build_margin_history_plan(
        mh.MarginHistoryRequest(PARTITION, PARTITION, 1),
        configured_max_dates=1,
        trading_dates=(PARTITION,),
        reconcile_reports=(report,),
        dataset_id=contract.dataset_id,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
    )


def _action_checkpoint(conn, contract):
    checkpoint = _history_plan(conn, contract).checkpoints[0]
    assert checkpoint.kind in {
        mh.MarginHistoryCheckpointKind.SELECTED,
        mh.MarginHistoryCheckpointKind.REPAIR,
    }
    return checkpoint


def _seed_legacy(conn, rows: list[dict]) -> None:
    conn.execute(
        """
        CREATE TABLE raw_tushare_margin (
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
    conn.executemany(
        f"""
        INSERT INTO raw_tushare_margin ({', '.join(MARGIN_FIELDS)}, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [tuple(row[field] for field in MARGIN_FIELDS) for row in rows],
    )


class _DmlAuditConnection:
    """Transparent connection proxy recording writes to the legacy table."""

    def __init__(self, conn):
        self._conn = conn
        self.legacy_dml: list[str] = []

    def execute(self, sql, *args, **kwargs):
        normalized = " ".join(str(sql).upper().split())
        if "RAW_TUSHARE_MARGIN" in normalized and normalized.startswith(
            ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "CREATE ", "ALTER ", "DROP ")
        ):
            self.legacy_dml.append(normalized)
        return self._conn.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        normalized = " ".join(str(sql).upper().split())
        if "RAW_TUSHARE_MARGIN" in normalized:
            self.legacy_dml.append(normalized)
        return self._conn.executemany(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _Provider:
    def __init__(self, *, revision: int = 0):
        self.calls: list[str] = []
        self.revision = revision

    def fetch_raw(self, _api, **params):
        exchange = params["exchange_id"]
        self.calls.append(exchange)
        return [_row(exchange, revision=self.revision)]


class _NoProvider:
    def fetch_raw(self, _api, **_params):  # pragma: no cover - assertion surface
        raise AssertionError("durable evidence must recover without provider I/O")


def _execute(
    conn,
    adapter,
    *,
    batch_id: str,
    observed_at: datetime = OBSERVED_AT,
    checkpoint=None,
):
    spec, contract = _spec_and_contract()
    return mhi.execute_history_partition(
        conn=conn,
        adapter=adapter,
        spec=spec,
        params={"trade_date": PARTITION},
        fetch_logical_batch=sr._fetch_logical_batch,
        quota_wall_classifier=sr._is_quota_wall,
        contract=contract,
        observed_at=observed_at,
        batch_id=batch_id,
        checkpoint=checkpoint or _action_checkpoint(conn, contract),
    )


def test_history_match_accepts_without_any_legacy_dml():
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    conn = _DmlAuditConnection(raw)
    provider = _Provider()
    try:
        before = [
            tuple(row)
            for row in raw.execute(
                f"SELECT {', '.join(MARGIN_FIELDS)} FROM raw_tushare_margin ORDER BY exchange_id"
            ).fetchall()
        ]

        outcome = _execute(conn, provider, batch_id="history-match")

        after = [
            tuple(row)
            for row in raw.execute(
                f"SELECT {', '.join(MARGIN_FIELDS)} FROM raw_tushare_margin ORDER BY exchange_id"
            ).fetchall()
        ]
        assert outcome.kind is mhi.MarginHistoryIngestKind.ACCEPTED
        assert outcome.partition_value == PARTITION
        assert outcome.batch_id == "history-match"
        assert outcome.row_count == 3
        assert outcome.content_hash == outcome.candidate_hash == outcome.legacy_hash
        assert outcome.issue_codes == ()
        assert provider.calls == ["SSE", "SZSE", "BSE"]
        assert conn.legacy_dml == []
        assert after == before
        assert raw.execute("SELECT status FROM ingest_batch").fetchone()[0] == "ACCEPTED"
    finally:
        raw.close()


def test_public_history_runtime_pre_bse_uses_two_exchange_contract(tmp_path):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(
        raw,
        [
            _row(exchange, partition=PRE_BSE_PARTITION)
            for exchange in ("SSE", "SZSE")
        ],
    )
    ensure_margin_acceptance_schema(raw)
    raw.close()
    spec, contract = _spec_and_contract()
    provider_calls: list[str] = []
    audit_connections: list[_DmlAuditConnection] = []

    class PreBseProvider:
        def fetch_raw(self, _api, **params):
            exchange = params["exchange_id"]
            if exchange == "BSE":
                pytest.fail("pre-BSE history request must not call the BSE fragment")
            provider_calls.append(exchange)
            return [_row(exchange, partition=PRE_BSE_PARTITION)]

    def target_conn_factory(_spec):
        audited = _DmlAuditConnection(connect(str(raw_path)))
        audit_connections.append(audited)
        return audited

    def sessions(start: str, end: str) -> list[str]:
        if start == end == PRE_BSE_PARTITION:
            return [PRE_BSE_PARTITION]
        return []

    result = margin_history_runtime.run_margin_history_domain(
        "margin",
        spec,
        contract=contract,
        request=mh.MarginHistoryRequest(
            PRE_BSE_PARTITION,
            PRE_BSE_PARTITION,
            1,
        ),
        eligibility=sr.DomainEligibility(PRE_BSE_PARTITION, False, "test"),
        target_conn_factory=target_conn_factory,
        adapter_factory=lambda _source: PreBseProvider(),
        trading_days=sessions,
        fetch_logical_batch=sr._fetch_logical_batch,
        quota_wall_classifier=sr._is_quota_wall,
        ops_conn_factory=lambda: connect(str(ops_path)),
        authorization_error_type=sr.TuShareAuthorizationError,
        quota_error_type=sr.QuotaExhaustedError,
    )

    assert result["status"] == "CHUNK_ACCEPTED"
    assert result["accepted_dates"] == [PRE_BSE_PARTITION]
    assert result["rows"] == 2
    assert provider_calls == ["SSE", "SZSE"]
    assert audit_connections[0].legacy_dml == []


def test_history_quota_halt_keeps_first_acceptance_and_resumable_evidence(tmp_path):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(
        raw,
        [
            _row(exchange, partition=partition)
            for partition in (PRE_BSE_PARTITION, PRE_BSE_SECOND)
            for exchange in ("SSE", "SZSE")
        ],
    )
    ensure_margin_acceptance_schema(raw)
    raw.close()
    spec, base_contract = _spec_and_contract()
    contract = replace(base_contract, coverage_start=PRE_BSE_PARTITION)

    class QuotaAfterFirstDay:
        def fetch_raw(self, _api, **params):
            if params["trade_date"] == PRE_BSE_SECOND:
                raise RuntimeError("今日请求已达上限")
            return [
                _row(
                    params["exchange_id"],
                    partition=params["trade_date"],
                )
            ]

    def sessions(start: str, end: str) -> list[str]:
        return [
            day
            for day in (PRE_BSE_PARTITION, PRE_BSE_SECOND)
            if start <= day <= end
        ]

    with pytest.raises(sr.QuotaExhaustedError) as caught:
        margin_history_runtime.run_margin_history_domain(
            "margin",
            spec,
            contract=contract,
            request=mh.MarginHistoryRequest(
                PRE_BSE_PARTITION,
                PRE_BSE_SECOND,
                2,
            ),
            eligibility=sr.DomainEligibility(PRE_BSE_SECOND, False, "test"),
            target_conn_factory=lambda _spec: connect(str(raw_path)),
            adapter_factory=lambda _source: QuotaAfterFirstDay(),
            trading_days=sessions,
            fetch_logical_batch=sr._fetch_logical_batch,
            quota_wall_classifier=sr._is_quota_wall,
            ops_conn_factory=lambda: connect(str(ops_path)),
            authorization_error_type=sr.TuShareAuthorizationError,
            quota_error_type=sr.QuotaExhaustedError,
        )

    partial = caught.value.history_result
    raw_check = connect(str(raw_path), read_only=True)
    ops_check = connect(str(ops_path), read_only=True)
    try:
        accepted_dates = [
            row[0]
            for row in raw_check.execute(
                "SELECT partition_value FROM accepted_partition "
                "WHERE dataset_id=? ORDER BY partition_value",
                [contract.dataset_id],
            ).fetchall()
        ]
        quota_status = ops_check.execute(
            "SELECT status FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' "
            "AND error_type='sync_quota_halt'"
        ).fetchone()[0]
    finally:
        raw_check.close()
        ops_check.close()

    assert accepted_dates == [PRE_BSE_PARTITION]
    assert partial.accepted_dates == (PRE_BSE_PARTITION,)
    assert partial.attempted_dates == (PRE_BSE_PARTITION, PRE_BSE_SECOND)
    assert partial.failed_dates == (PRE_BSE_SECOND,)
    assert partial.next_start == PRE_BSE_SECOND
    assert partial.failures[0].code == "account_halt"
    assert quota_status == "open"


def test_history_conflict_stays_landed_and_recovers_without_provider():
    raw = connect(":memory:")
    legacy = [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")]
    legacy[0] = _row("SSE", revision=1)
    _seed_legacy(raw, legacy)
    conn = _DmlAuditConnection(raw)
    provider = _Provider()
    try:
        first = _execute(conn, provider, batch_id="history-conflict")
        second = _execute(conn, _NoProvider(), batch_id="ignored-on-recovery")

        assert first.kind is mhi.MarginHistoryIngestKind.LEGACY_CONFLICT
        assert second.kind is mhi.MarginHistoryIngestKind.LEGACY_CONFLICT
        assert first.batch_id == second.batch_id == "history-conflict"
        assert first.candidate_hash == second.candidate_hash
        assert first.legacy_hash == second.legacy_hash
        assert first.candidate_hash != first.legacy_hash
        assert MarginReconcileCode.VALUE_MISMATCH in first.issue_codes
        assert provider.calls == ["SSE", "SZSE", "BSE"]
        assert conn.legacy_dml == []
        assert raw.execute("SELECT status FROM ingest_batch").fetchone()[0] == "LANDED"
        assert raw.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] == 1
    finally:
        raw.close()


def test_planned_landing_reuse_is_bound_to_payload_hash():
    raw = connect(":memory:")
    legacy = [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")]
    legacy[0] = _row("SSE", revision=1)
    _seed_legacy(raw, legacy)
    try:
        first = _execute(raw, _Provider(), batch_id="payload-bound")
        payload_hash = raw.execute(
            "SELECT payload_hash FROM ingest_batch WHERE batch_id='payload-bound'"
        ).fetchone()[0]
        spec, contract = _spec_and_contract()
        checkpoint = _action_checkpoint(raw, contract)
        assert checkpoint.recoverable_landing_payload_hash == payload_hash

        with pytest.raises(RuntimeError, match="LANDED checkpoint drifted"):
            mhi.execute_history_partition(
                conn=raw,
                adapter=_NoProvider(),
                spec=spec,
                params={"trade_date": PARTITION},
                fetch_logical_batch=sr._fetch_logical_batch,
                quota_wall_classifier=sr._is_quota_wall,
                contract=contract,
                checkpoint=replace(
                    checkpoint,
                    recoverable_landing_payload_hash="wrong-payload-hash",
                ),
            )

        recovered = mhi.execute_history_partition(
            conn=raw,
            adapter=_NoProvider(),
            spec=spec,
            params={"trade_date": PARTITION},
            fetch_logical_batch=sr._fetch_logical_batch,
            quota_wall_classifier=sr._is_quota_wall,
            contract=contract,
            checkpoint=checkpoint,
        )

        assert recovered.kind is mhi.MarginHistoryIngestKind.LEGACY_CONFLICT
        assert recovered.batch_id == first.batch_id
        assert raw.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] == 1
    finally:
        raw.close()


def test_history_accept_ack_loss_and_next_process_are_proof_recoverable(monkeypatch):
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    conn = _DmlAuditConnection(raw)
    actual_accept = mhi.accept_margin_batch

    def commit_then_lose_ack(*args, **kwargs):
        actual_accept(*args, **kwargs)
        raise ConnectionError("ACK lost after Tx-B commit")

    monkeypatch.setattr(mhi, "accept_margin_batch", commit_then_lose_ack)
    try:
        accepted = _execute(conn, _Provider(), batch_id="history-ack-loss")
        monkeypatch.setattr(mhi, "accept_margin_batch", actual_accept)
        _spec, contract = _spec_and_contract()
        plan = _history_plan(conn, contract)

        def forbidden_executor(_partition):
            pytest.fail("accepted history checkpoint must be skipped without provider")

        recovered = mh.execute_margin_history_plan(plan, forbidden_executor)

        assert accepted.kind is mhi.MarginHistoryIngestKind.ACCEPTED
        assert recovered.skipped_dates == (PARTITION,)
        assert recovered.accepted_evidence[0].batch_id == accepted.batch_id
        assert recovered.accepted_evidence[0].content_hash == accepted.content_hash
        assert conn.legacy_dml == []
        assert raw.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] == 1
    finally:
        raw.close()


def test_history_accept_programming_error_before_commit_keeps_original_traceback(
    monkeypatch,
):
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])

    def programming_error_before_commit(*_args, **_kwargs):
        raise RuntimeError("programming bug before Tx-B")

    monkeypatch.setattr(mhi, "accept_margin_batch", programming_error_before_commit)
    try:
        with pytest.raises(RuntimeError, match="programming bug before Tx-B"):
            _execute(raw, _Provider(), batch_id="history-precommit-error")

        assert raw.execute(
            "SELECT status FROM ingest_batch WHERE batch_id='history-precommit-error'"
        ).fetchone()[0] == "LANDED"
        assert raw.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 0
    finally:
        raw.close()


def test_history_repair_fetches_after_valid_accepted_legacy_only_mismatch():
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    conn = _DmlAuditConnection(raw)
    try:
        baseline = _execute(conn, _Provider(), batch_id="history-baseline")
        raw.execute(
            "UPDATE raw_tushare_margin SET rzye = rzye + 1, rzrqye = rzrqye + 1"
        )
        repair_provider = _Provider(revision=1)

        repaired = _execute(
            conn,
            repair_provider,
            batch_id="history-repair",
            observed_at=OBSERVED_AT + timedelta(days=1),
        )

        assert baseline.kind is mhi.MarginHistoryIngestKind.ACCEPTED
        assert repaired.kind is mhi.MarginHistoryIngestKind.ACCEPTED
        assert repaired.batch_id == "history-repair"
        assert repair_provider.calls == ["SSE", "SZSE", "BSE"]
        assert conn.legacy_dml == []
        assert raw.execute(
            "SELECT batch_id FROM accepted_partition WHERE partition_value = ?",
            [PARTITION],
        ).fetchone()[0] == "history-repair"
    finally:
        raw.close()


def test_planned_repair_rejects_accepted_pointer_drift_before_provider():
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    try:
        baseline = _execute(raw, _Provider(), batch_id="pointer-baseline")
        raw.execute(
            "UPDATE raw_tushare_margin SET rzye = rzye + 1, rzrqye = rzrqye + 1"
        )
        spec, contract = _spec_and_contract()
        checkpoint = _action_checkpoint(raw, contract)

        with pytest.raises(RuntimeError, match="accepted checkpoint drifted"):
            mhi.execute_history_partition(
                conn=raw,
                adapter=_NoProvider(),
                spec=spec,
                params={"trade_date": PARTITION},
                fetch_logical_batch=sr._fetch_logical_batch,
                quota_wall_classifier=sr._is_quota_wall,
                contract=contract,
                checkpoint=replace(
                    checkpoint,
                    accepted_batch_id="different-pointer",
                    accepted_content_hash=baseline.content_hash,
                ),
            )

        assert raw.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] == 1
    finally:
        raw.close()


def test_history_repair_conflict_reuses_landed_without_provider():
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    conn = _DmlAuditConnection(raw)
    try:
        _execute(conn, _Provider(), batch_id="history-baseline")
        raw.execute(
            "UPDATE raw_tushare_margin SET rzye = rzye + 1, rzrqye = rzrqye + 1"
        )
        provider = _Provider()

        first = _execute(
            conn,
            provider,
            batch_id="history-repair-conflict",
            observed_at=OBSERVED_AT + timedelta(days=1),
        )
        second = _execute(
            conn,
            _NoProvider(),
            batch_id="must-not-be-used",
            observed_at=OBSERVED_AT + timedelta(days=2),
        )

        assert first.kind is mhi.MarginHistoryIngestKind.LEGACY_CONFLICT
        assert second.kind is mhi.MarginHistoryIngestKind.LEGACY_CONFLICT
        assert first.batch_id == second.batch_id == "history-repair-conflict"
        assert provider.calls == ["SSE", "SZSE", "BSE"]
        assert conn.legacy_dml == []
        statuses = raw.execute(
            "SELECT status, COUNT(*) FROM ingest_batch GROUP BY status ORDER BY status"
        ).fetchall()
        assert [tuple(row) for row in statuses] == [("ACCEPTED", 1), ("LANDED", 1)]
        assert raw.execute(
            "SELECT batch_id FROM accepted_partition WHERE partition_value = ?",
            [PARTITION],
        ).fetchone()[0] == "history-baseline"
    finally:
        raw.close()


def test_public_history_runtime_reuses_conflict_landing_without_provider(
    tmp_path, monkeypatch
):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    try:
        _execute(raw, _Provider(), batch_id="runtime-baseline")
        raw.execute(
            "UPDATE raw_tushare_margin SET rzye = rzye + 1, rzrqye = rzrqye + 1"
        )
    finally:
        raw.close()

    spec, contract = _spec_and_contract()
    provider = _Provider()
    side_effects: list[str] = []

    def prove_timeout_before_adapter(_spec):
        side_effects.append("timeout")
        return 120.0

    def ordered_adapter(_source):
        assert side_effects == ["timeout"]
        side_effects.append("adapter")
        return provider

    monkeypatch.setattr(
        margin_history_runtime,
        "apply_fetch_socket_timeout",
        prove_timeout_before_adapter,
    )
    common = {
        "domain": "margin",
        "spec": spec,
        "contract": contract,
        "request": margin_history_runtime.history.MarginHistoryRequest(
            PARTITION, PARTITION, 1
        ),
        "eligibility": sr.DomainEligibility(PARTITION, False, "test"),
        "target_conn_factory": lambda _spec: connect(str(raw_path)),
        "trading_days": lambda _start, _end: [PARTITION],
        "fetch_logical_batch": sr._fetch_logical_batch,
        "quota_wall_classifier": sr._is_quota_wall,
        "ops_conn_factory": lambda: connect(str(ops_path)),
        "authorization_error_type": sr.TuShareAuthorizationError,
        "quota_error_type": sr.QuotaExhaustedError,
    }

    first = margin_history_runtime.run_margin_history_domain(
        **common,
        adapter_factory=ordered_adapter,
    )

    def forbidden_timeout(_spec):
        pytest.fail("planned LANDED reuse must not mutate provider timeout state")

    monkeypatch.setattr(
        margin_history_runtime,
        "apply_fetch_socket_timeout",
        forbidden_timeout,
    )

    def forbidden_adapter(_source):
        pytest.fail("planned LANDED reuse must not construct a provider adapter")

    second = margin_history_runtime.run_margin_history_domain(
        **common,
        adapter_factory=forbidden_adapter,
    )

    assert first["status"] == second["status"] == "FAILED"
    assert first["failures"][0]["code"] == "legacy_conflict"
    assert second["failures"][0]["code"] == "legacy_conflict"
    assert len(first["failures"][0]["evidence_hash"]) == 64
    assert len(second["failures"][0]["evidence_hash"]) == 64
    assert first["execution_dates"] == second["execution_dates"] == [PARTITION]
    assert second["repair_dates"] == [PARTITION]
    assert provider.calls == ["SSE", "SZSE", "BSE"]
    assert side_effects == ["timeout", "adapter"]
    check = connect(str(raw_path), read_only=True)
    try:
        assert check.execute(
            "SELECT COUNT(*) FROM ingest_batch WHERE status='LANDED'"
        ).fetchone()[0] == 1
    finally:
        check.close()


def test_public_history_runtime_rejects_checkpoint_drift_before_adapter(
    tmp_path, monkeypatch
):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    ensure_margin_acceptance_schema(raw)
    raw.close()
    spec, contract = _spec_and_contract()

    def drifted_checkpoint(*_args, **_kwargs):
        raise mhi.MarginHistoryCheckpointDrift(
            "checkpoint changed after planning",
            evidence={"surface": "accepted", "partition": PARTITION},
        )

    def forbidden(*_args, **_kwargs):
        pytest.fail("checkpoint drift crossed into provider setup")

    monkeypatch.setattr(
        mhi,
        "prove_history_execution_checkpoint",
        drifted_checkpoint,
    )
    monkeypatch.setattr(
        margin_history_runtime,
        "apply_fetch_socket_timeout",
        forbidden,
    )

    result = margin_history_runtime.run_margin_history_domain(
        "margin",
        spec,
        contract=contract,
        request=mh.MarginHistoryRequest(PARTITION, PARTITION, 1),
        eligibility=sr.DomainEligibility(PARTITION, False, "test"),
        target_conn_factory=lambda _spec: connect(str(raw_path)),
        adapter_factory=forbidden,
        trading_days=lambda _start, _end: [PARTITION],
        fetch_logical_batch=sr._fetch_logical_batch,
        quota_wall_classifier=sr._is_quota_wall,
        ops_conn_factory=lambda: connect(str(ops_path)),
        authorization_error_type=sr.TuShareAuthorizationError,
        quota_error_type=sr.QuotaExhaustedError,
    )

    assert result["status"] == "FAILED"
    assert result["failures"][0]["code"] == "checkpoint_drift"
    assert len(result["failures"][0]["evidence_hash"]) == 64


def test_history_parity_with_unresolved_landed_remains_blocked():
    raw = connect(":memory:")
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    conn = _DmlAuditConnection(raw)
    try:
        _execute(conn, _Provider(), batch_id="history-baseline")
        _spec, contract = _spec_and_contract()
        land_margin_batch(
            raw,
            MarginLandingBatch(
                batch_id="unexpected-landed",
                partition_value=PARTITION,
                observed_at=OBSERVED_AT + timedelta(days=1),
                available_at=OBSERVED_AT + timedelta(days=1),
                fragments=tuple(
                    MarginFragment(
                        exchange_id=exchange,
                        request={"trade_date": PARTITION, "exchange_id": exchange},
                        rows=[_row(exchange)],
                    )
                    for exchange in ("SSE", "SZSE", "BSE")
                ),
                source=contract.source,
                contract_version=contract.contract_version,
            ),
            contract=contract,
        )

        plan = _history_plan(conn, contract)

        result = mh.execute_margin_history_plan(
            plan,
            lambda _partition: pytest.fail(
                "PARITY + LANDED must block before provider execution"
            ),
        )

        assert plan.blocked_dates == (PARTITION,)
        assert result.blocked_partition == PARTITION
        assert result.failures[0].code == "checkpoint_blocked"
    finally:
        raw.close()


def test_public_history_runtime_blocks_parity_with_landed_before_provider(tmp_path):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    try:
        _execute(raw, _Provider(), batch_id="runtime-parity")
        spec, contract = _spec_and_contract()
        land_margin_batch(
            raw,
            MarginLandingBatch(
                batch_id="runtime-unexpected-landed",
                partition_value=PARTITION,
                observed_at=OBSERVED_AT + timedelta(days=1),
                available_at=OBSERVED_AT + timedelta(days=1),
                fragments=tuple(
                    MarginFragment(
                        exchange_id=exchange,
                        request={
                            "trade_date": PARTITION,
                            "exchange_id": exchange,
                        },
                        rows=[_row(exchange)],
                    )
                    for exchange in ("SSE", "SZSE", "BSE")
                ),
                source=contract.source,
                contract_version=contract.contract_version,
            ),
            contract=contract,
        )
    finally:
        raw.close()

    def forbidden_adapter(_source):
        pytest.fail("PARITY + LANDED crossed the public runtime into provider I/O")

    result = margin_history_runtime.run_margin_history_domain(
        "margin",
        spec,
        contract=contract,
        request=mh.MarginHistoryRequest(PARTITION, PARTITION, 1),
        eligibility=sr.DomainEligibility(PARTITION, False, "test"),
        target_conn_factory=lambda _spec: connect(str(raw_path)),
        adapter_factory=forbidden_adapter,
        trading_days=lambda _start, _end: [PARTITION],
        fetch_logical_batch=sr._fetch_logical_batch,
        quota_wall_classifier=sr._is_quota_wall,
        ops_conn_factory=lambda: connect(str(ops_path)),
        authorization_error_type=sr.TuShareAuthorizationError,
        quota_error_type=sr.QuotaExhaustedError,
    )

    assert result["status"] == "BLOCKED"
    assert result["blocked_partition"] == PARTITION
    assert result["execution_dates"] == []
    assert result["failures"][0]["code"] == "checkpoint_blocked"


def test_landed_recovery_does_not_claim_provider_success_or_clear_quota(tmp_path):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    spec, contract = _spec_and_contract()
    raw = connect(str(raw_path))
    ops = connect(str(ops_path))
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    try:
        land_margin_batch(
            raw,
            MarginLandingBatch(
                batch_id="quota-neutral-recovery",
                partition_value=PARTITION,
                observed_at=OBSERVED_AT,
                available_at=OBSERVED_AT,
                fragments=tuple(
                    MarginFragment(
                        exchange_id=exchange,
                        request={
                            "trade_date": PARTITION,
                            "exchange_id": exchange,
                        },
                        rows=[_row(exchange)],
                    )
                    for exchange in ("SSE", "SZSE", "BSE")
                ),
                source=contract.source,
                contract_version=contract.contract_version,
            ),
            contract=contract,
        )
        project_margin_accepted_state(
            raw,
            ops,
            [PARTITION],
            contract=contract,
            quota_error="pre-existing quota wall",
        )
    finally:
        raw.close()
        ops.close()

    def forbidden_adapter(_source):
        pytest.fail("durable LANDED recovery must not construct a provider adapter")

    result = margin_history_runtime.run_margin_history_domain(
        "margin",
        spec,
        contract=contract,
        request=mh.MarginHistoryRequest(PARTITION, PARTITION, 1),
        eligibility=sr.DomainEligibility(PARTITION, False, "test"),
        target_conn_factory=lambda _spec: connect(str(raw_path)),
        adapter_factory=forbidden_adapter,
        trading_days=lambda _start, _end: [PARTITION],
        fetch_logical_batch=sr._fetch_logical_batch,
        quota_wall_classifier=sr._is_quota_wall,
        ops_conn_factory=lambda: connect(str(ops_path)),
        authorization_error_type=sr.TuShareAuthorizationError,
        quota_error_type=sr.QuotaExhaustedError,
    )

    check = connect(str(ops_path), read_only=True)
    try:
        quota_status = check.execute(
            "SELECT status FROM mart_data_source_failure_queue "
            "WHERE data_domain='sync:margin' "
            "AND error_type='sync_quota_halt'"
        ).fetchone()[0]
    finally:
        check.close()

    assert result["status"] == "CHUNK_ACCEPTED"
    assert result["accepted_dates"] == [PARTITION]
    assert quota_status == "open"


def test_public_history_runtime_classifies_marked_provider_timeout(tmp_path):
    raw_path = tmp_path / "raw.duckdb"
    ops_path = tmp_path / "ops.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    ensure_margin_acceptance_schema(raw)
    raw.close()
    spec, contract = _spec_and_contract()

    def timed_out_fetch(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    result = margin_history_runtime.run_margin_history_domain(
        "margin",
        spec,
        contract=contract,
        request=mh.MarginHistoryRequest(PARTITION, PARTITION, 1),
        eligibility=sr.DomainEligibility(PARTITION, False, "test"),
        target_conn_factory=lambda _spec: connect(str(raw_path)),
        adapter_factory=lambda _source: object(),
        trading_days=lambda _start, _end: [PARTITION],
        fetch_logical_batch=timed_out_fetch,
        quota_wall_classifier=sr._is_quota_wall,
        ops_conn_factory=lambda: connect(str(ops_path)),
        authorization_error_type=sr.TuShareAuthorizationError,
        quota_error_type=sr.QuotaExhaustedError,
    )

    assert result["status"] == "FAILED"
    assert result["failures"][0]["code"] == "provider_timeout"
    assert len(result["failures"][0]["evidence_hash"]) == 64


def test_public_history_runtime_propagates_unmarked_connection_failure(
    tmp_path, monkeypatch
):
    raw_path = tmp_path / "raw.duckdb"
    raw = connect(str(raw_path))
    _seed_legacy(raw, [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")])
    ensure_margin_acceptance_schema(raw)
    raw.close()
    spec, contract = _spec_and_contract()
    monkeypatch.setattr(
        mhi,
        "execute_history_partition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ConnectionError("target DB connection broke")
        ),
    )

    with pytest.raises(ConnectionError, match="target DB connection broke"):
        margin_history_runtime.run_margin_history_domain(
            "margin",
            spec,
            contract=contract,
            request=mh.MarginHistoryRequest(PARTITION, PARTITION, 1),
            eligibility=sr.DomainEligibility(PARTITION, False, "test"),
            target_conn_factory=lambda _spec: connect(str(raw_path)),
            adapter_factory=lambda _source: object(),
            trading_days=lambda _start, _end: [PARTITION],
            fetch_logical_batch=sr._fetch_logical_batch,
            quota_wall_classifier=sr._is_quota_wall,
            ops_conn_factory=lambda: pytest.fail(
                "unmarked runtime failure reached Ops projection"
            ),
            authorization_error_type=sr.TuShareAuthorizationError,
            quota_error_type=sr.QuotaExhaustedError,
        )


def test_history_comparison_reuses_decimal_null_and_grain_semantics():
    candidate = [_row("SSE"), _row("SZSE")]
    decimal_legacy = [
        {
            **row,
            **{
                field: Decimal(str(row[field])).quantize(Decimal("0.000000"))
                for field in MARGIN_FIELDS[2:]
            },
        }
        for row in candidate
    ]
    assert compare_margin_history_rows(PARTITION, candidate, decimal_legacy).ok

    null_mismatch = [{**decimal_legacy[0], "rqyl": None}, decimal_legacy[1]]
    missing = decimal_legacy[:1]
    extra = [*decimal_legacy, _row("BSE")]
    duplicate = [*decimal_legacy, dict(decimal_legacy[0])]

    assert MarginReconcileCode.NULL_MISMATCH in compare_margin_history_rows(
        PARTITION, candidate, null_mismatch
    ).issue_codes
    assert MarginReconcileCode.LEGACY_ROW_MISSING in compare_margin_history_rows(
        PARTITION, candidate, missing
    ).issue_codes
    assert MarginReconcileCode.LEGACY_ROW_EXTRA in compare_margin_history_rows(
        PARTITION, candidate, extra
    ).issue_codes
    assert MarginReconcileCode.LEGACY_DUPLICATE_GRAIN in compare_margin_history_rows(
        PARTITION, candidate, duplicate
    ).issue_codes
