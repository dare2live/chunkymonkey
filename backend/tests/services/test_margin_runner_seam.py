"""Production seam for the first formal Tier0 dataset.

The runner must keep its existing retry/paging/split and legacy-raw writer,
while the margin-specific seam records the exact fragment evidence and invokes
the formal landing/acceptance boundary before touching the legacy shadow.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("deterministic_margin_calendar")

from services.data_sources import margin_ingest as mi
from services.data_sources import sync_runner as sr
from services.data_sources.contracts import load_dataset_contract
from services.data_sources.margin_acceptance import (
    MarginFragment,
    MarginLandingBatch,
    land_margin_batch,
)
from services.data_sources.margin_validation import MarginValidationError
from services.duck_adapter import connect


PARTITION = "20260715"
OBSERVED_AT = datetime(2026, 7, 16, 1, 5, tzinfo=timezone.utc)
DATASET_ID = "tier0.market_data.margin_exchange_daily"


def _contract(*groups: str):
    contract = load_dataset_contract("margin")
    assert contract.batch_completeness.required_groups_for(PARTITION) == tuple(
        sorted(groups)
    )
    return contract


def _spec() -> dict:
    return {
        "domain": "margin",
        "source": "tushare",
        "api": "margin",
        "date_param": "trade_date",
        "target_table": "raw_tushare_margin",
        "grain": ["trade_date", "exchange_id"],
        "partition_by": ["trade_date"],
        "write_mode": "replace_partition",
        "split_by": {
            "param": "exchange_id",
            "values": ["SSE", "SZSE", "BSE"],
        },
        "retry": {"max_attempts": 1, "backoff_seconds": [0]},
        "min_rows_per_batch": 2,
    }


def _row(exchange_id: str) -> dict:
    return {
        "trade_date": PARTITION,
        "exchange_id": exchange_id,
        "rzye": 100,
        "rzmre": 10,
        "rzche": 8,
        "rqye": 5,
        "rqmcl": 1,
        "rzrqye": 105,
        "rqyl": 2,
    }


def _execute_margin_partition(*args, **kwargs):
    """Exercise the dataset owner with the production runner callbacks."""
    return mi.execute_partition(
        *args,
        fetch_logical_batch=sr._fetch_logical_batch,
        write_batch=sr._write_batch,
        quota_wall_classifier=sr._is_quota_wall,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)


def test_only_the_typed_margin_dataset_activates_the_formal_seam():
    spec = sr.domain_spec(sr.load_registry(), "margin")
    contract = mi.contract_for_spec(spec)

    assert contract is not None
    assert contract.dataset_id == DATASET_ID
    with pytest.raises(ValueError, match="cannot fall back"):
        mi.contract_for_spec({**spec, "dataset_contract": {}})
    with pytest.raises(ValueError, match="cannot fall back"):
        mi.contract_for_spec(
            {**spec, "dataset_contract": {"dataset_id": "tier0.other"}}
        )
    assert mi.contract_for_spec({"domain": "daily"}) is None


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"batch_mode": "full_refresh"}, "batch_mode"),
        ({"date_param": "ann_date"}, "date_param"),
        ({"write_mode": "append"}, "write_mode"),
        ({"split_by": {"param": "market", "values": ["SSE", "SZSE", "BSE"]}}, "split_by.param"),
        ({"split_by": {"param": "exchange_id", "values": ["SSE", "SZSE"]}}, "split_by.values"),
        ({"split_by": {"param": "exchange_id", "values": ["SSE", "SZSE", "BSE", "BSE"]}}, "split_by.values"),
        ({"split_by": {"param": "exchange_id", "values": ["SSE", "SZSE", "bse"]}}, "split_by.values"),
        ({"split_by": {"param": "exchange_id", "values": []}}, "split_by.values"),
    ],
)
def test_formal_margin_contract_rejects_transport_drift_before_runtime(
    mutation, expected
):
    spec = sr.domain_spec(sr.load_registry(), "margin")

    with pytest.raises(ValueError, match=expected):
        mi.contract_for_spec({**spec, **mutation})


def test_formal_margin_transport_allows_future_effective_group_during_history_expansion():
    spec = sr.domain_spec(sr.load_registry(), "margin")
    metadata = {**spec["dataset_contract"], "coverage_start": "20190102"}

    contract = mi.contract_for_spec({**spec, "dataset_contract": metadata})

    assert contract.batch_completeness.required_groups_for("20190102") == (
        "SSE",
        "SZSE",
    )
    assert contract.batch_completeness.required_groups_for("20230213") == (
        "BSE",
        "SSE",
        "SZSE",
    )


def test_public_domain_and_calendar_helpers_are_stable(monkeypatch):
    from services.data_access import resolver

    registry = {
        "defaults": {"target_db": "tushare_raw", "retry": {"max_attempts": 1}},
        "domains": {"margin": {"source": "tushare", "api": "margin"}},
    }
    seen: list[tuple[str, str]] = []

    class Calendar:
        def execute(self, _sql, params):
            seen.append(tuple(params))
            return SimpleNamespace(
                fetchall=lambda: [("20260715",), ("20260716",)]
            )

    monkeypatch.setattr(
        resolver,
        "dim_read_conn",
        lambda _conn, _table: (Calendar(), False),
    )

    spec = sr.domain_spec(registry, "margin")

    assert spec == {
        "target_db": "tushare_raw",
        "retry": {"max_attempts": 1},
        "source": "tushare",
        "api": "margin",
        "domain": "margin",
    }
    assert sr.trading_days("20260715", "20260716") == ["20260715", "20260716"]
    assert seen == [("20260715", "20260716")]


def test_eligible_end_default_path_calls_public_calendar_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end=None: calls.append((start, end))
        or ["20260715", "20260716", "20260717"],
    )

    result = sr.eligible_end_date(
        {
            "data_start": "20260715",
            "available_after": "t+1",
            "availability_policy": {
                "axis": "trading_day",
                "rule": "next_trading_session_at",
                "at": "09:00",
            },
        },
        now=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert result.eligible_end == "20260716"
    assert result.pending_today is True
    assert calls == [("20260715", "20260717")]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (sr.BatchCompletenessError("partial"), "batch_incomplete"),
        (mi.LegacyWriteError("legacy"), "legacy_write_failed"),
        (mi.MarginReconcileError("reconcile"), "reconcile_failed"),
        (sr.QuotaExhaustedError("quota"), "quota_halt"),
    ],
)
def test_formal_margin_adapter_returns_typed_failure_outcomes(
    monkeypatch, error, kind
):
    monkeypatch.setattr(
        mi,
        "execute_partition",
        lambda **_kwargs: (_ for _ in ()).throw(error),
    )

    outcome = mi.execute_partition_outcome(
        conn=object(),
        adapter=object(),
        spec=_spec(),
        params={"trade_date": PARTITION},
        contract=_contract("SSE", "SZSE", "BSE"),
        effective_min_rows=2,
        fetch_logical_batch=sr._fetch_logical_batch,
        write_batch=sr._write_batch,
        quota_wall_classifier=sr._is_quota_wall,
        authorization_error_type=sr.TuShareAuthorizationError,
        quota_error_type=sr.QuotaExhaustedError,
    )

    assert isinstance(outcome, mi.FormalMarginPartitionOutcome)
    assert outcome.kind == kind
    assert outcome.rows == 0
    assert outcome.error is error


def test_margin_partition_validates_then_writes_legacy_before_formal_accept(monkeypatch):
    calls: list[object] = []
    landed = []

    class Adapter:
        def fetch_raw(self, _api, **params):
            calls.append(("fetch", dict(params)))
            return [_row(params["exchange_id"])]

    def fake_land(_conn, batch, **_kwargs):
        calls.append("land")
        landed.append(batch)
        return batch.batch_id

    def fake_validate(_conn, batch_id, **_kwargs):
        calls.append(("validate", batch_id))
        return SimpleNamespace(
            batch_id=batch_id,
            partition_value=PARTITION,
            legacy_rows=tuple(_row(exchange) for exchange in ("SSE", "SZSE", "BSE")),
        )

    def fake_accept(_conn, batch_id, **_kwargs):
        calls.append(("accept", batch_id))
        return SimpleNamespace(status="ACCEPTED", rejection_code=None)

    def fake_write(_conn, _spec_arg, rows, **kwargs):
        calls.append(("raw", [dict(row) for row in rows], kwargs))
        return len(rows)

    def fake_reconcile(_conn, partition, **_kwargs):
        calls.append(("reconcile", partition))
        return SimpleNamespace(ok=True, issues=())

    monkeypatch.setattr(mi, "land_margin_batch", fake_land)
    monkeypatch.setattr(
        mi, "find_current_landed_margin_batch", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(mi, "validate_margin_batch", fake_validate)
    monkeypatch.setattr(mi, "accept_margin_batch", fake_accept)
    monkeypatch.setattr(mi, "reconcile_margin_partition", fake_reconcile)
    monkeypatch.setattr(sr, "_write_batch", fake_write)

    written = _execute_margin_partition(
        object(),
        Adapter(),
        _spec(),
        {"trade_date": PARTITION},
        contract=_contract("SSE", "SZSE", "BSE"),
        effective_min_rows=2,
        observed_at=OBSERVED_AT,
        batch_id="margin-seam-success",
    )

    assert written == 3
    assert [call[0] for call in calls if isinstance(call, tuple) and call[0] == "fetch"] == [
        "fetch", "fetch", "fetch"
    ]
    assert calls.index("land") < next(
        i for i, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "validate"
    ) < next(
        i for i, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "raw"
    ) < next(
        i for i, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "accept"
    ) < next(
        i for i, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "reconcile"
    )
    fragments = list(landed[0].fragments)
    assert [fragment.exchange_id for fragment in fragments] == ["SSE", "SZSE", "BSE"]
    assert all(fragment.outcome == "success" for fragment in fragments)
    assert all(fragment.request["trade_date"] == PARTITION for fragment in fragments)


@pytest.mark.parametrize(
    ("failure", "expected_outcome", "expected_error"),
    [
        ("empty", "empty", None),
        ("timeout", "error", "timeout"),
        ("connection", "error", "connection"),
    ],
)
def test_margin_fetch_failure_is_landed_and_blocks_legacy_raw(
    monkeypatch, failure, expected_outcome, expected_error
):
    landed = []
    raw_calls = []

    class Adapter:
        def fetch_raw(self, _api, **_params):
            if failure == "empty":
                return []
            if failure == "timeout":
                raise TimeoutError("provider timed out")
            raise ConnectionError("provider connection refused")

    monkeypatch.setattr(
        mi,
        "land_margin_batch",
        lambda _conn, batch, **_kwargs: landed.append(batch) or batch.batch_id,
    )
    monkeypatch.setattr(
        mi, "find_current_landed_margin_batch", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        mi,
        "validate_margin_batch",
        lambda _conn, _batch_id, **_kwargs: (_ for _ in ()).throw(
            MarginValidationError(
                "ZERO_ROWS" if failure == "empty" else "FRAGMENT_FAILED", failure
            )
        ),
    )
    monkeypatch.setattr(
        mi,
        "accept_margin_batch",
        lambda _conn, _batch_id, **_kwargs: SimpleNamespace(
            status="REJECTED", rejection_code="ZERO_ROWS" if failure == "empty" else "FRAGMENT_FAILED"
        ),
    )
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *_args, **_kwargs: raw_calls.append(True),
    )

    with pytest.raises(sr.BatchCompletenessError):
        _execute_margin_partition(
            object(),
            Adapter(),
            _spec(),
            {"trade_date": PARTITION},
            contract=_contract("SSE", "SZSE", "BSE"),
            effective_min_rows=2,
            observed_at=OBSERVED_AT,
            batch_id=f"margin-seam-{failure}",
        )

    assert raw_calls == []
    fragments = list(landed[0].fragments)
    assert [fragment.exchange_id for fragment in fragments] == ["SSE", "SZSE", "BSE"]
    assert fragments[0].outcome == expected_outcome
    assert fragments[0].error_type == expected_error
    assert [fragment.error_type for fragment in fragments[1:]] == [
        "not_attempted", "not_attempted"
    ]


def test_existing_landed_batch_is_validated_and_published_without_provider_refetch(monkeypatch):
    calls: list[str] = []

    class NoProvider:
        def fetch_raw(self, _api, **_params):
            pytest.fail("recoverable LANDED batch must suppress provider fetch")

    prepared = SimpleNamespace(
        batch_id="existing-landed",
        partition_value=PARTITION,
        legacy_rows=tuple(_row(exchange) for exchange in ("SSE", "SZSE", "BSE")),
    )
    monkeypatch.setattr(
        mi,
        "find_current_landed_margin_batch",
        lambda *_args, **_kwargs: calls.append("find") or "existing-landed",
    )
    monkeypatch.setattr(
        mi,
        "validate_margin_batch",
        lambda _conn, batch_id, **_kwargs: calls.append("validate") or prepared,
    )
    monkeypatch.setattr(
        mi,
        "land_margin_batch",
        lambda *_args, **_kwargs: pytest.fail("existing landing must not be duplicated"),
    )
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *_args, **_kwargs: calls.append("raw") or 3,
    )
    monkeypatch.setattr(
        mi,
        "accept_margin_batch",
        lambda _conn, batch_id, **_kwargs: calls.append("accept")
        or SimpleNamespace(status="ACCEPTED", rejection_code=None),
    )
    monkeypatch.setattr(
        mi,
        "reconcile_margin_partition",
        lambda _conn, _partition, **_kwargs: calls.append("reconcile")
        or SimpleNamespace(ok=True, issues=()),
    )

    written = _execute_margin_partition(
        object(),
        NoProvider(),
        _spec(),
        {"trade_date": PARTITION},
        contract=_contract("SSE", "SZSE", "BSE"),
        effective_min_rows=2,
        observed_at=OBSERVED_AT,
    )

    assert written == 3
    assert calls == ["find", "validate", "raw", "accept", "reconcile"]


def test_legacy_raw_failure_leaves_landed_unaccepted_for_next_recovery(monkeypatch):
    calls: list[str] = []
    prepared = SimpleNamespace(
        batch_id="existing-landed",
        partition_value=PARTITION,
        legacy_rows=tuple(_row(exchange) for exchange in ("SSE", "SZSE", "BSE")),
    )
    monkeypatch.setattr(
        mi,
        "find_current_landed_margin_batch",
        lambda *_args, **_kwargs: "existing-landed",
    )
    monkeypatch.setattr(
        mi,
        "validate_margin_batch",
        lambda *_args, **_kwargs: calls.append("validate") or prepared,
    )
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("raw unavailable")),
    )
    monkeypatch.setattr(
        mi,
        "accept_margin_batch",
        lambda *_args, **_kwargs: calls.append("accept"),
    )

    with pytest.raises(mi.LegacyWriteError):
        _execute_margin_partition(
            object(),
            object(),
            _spec(),
            {"trade_date": PARTITION},
            contract=_contract("SSE", "SZSE", "BSE"),
            effective_min_rows=2,
            observed_at=OBSERVED_AT,
        )

    assert calls == ["validate"]


def _land_actual_batch(conn, batch_id: str) -> None:
    land_margin_batch(
        conn,
        MarginLandingBatch(
            batch_id=batch_id,
            partition_value=PARTITION,
            observed_at=OBSERVED_AT,
            available_at=OBSERVED_AT,
            fragments=tuple(
                MarginFragment(
                    exchange_id=exchange,
                    rows=[_row(exchange)],
                    request={"trade_date": PARTITION, "exchange_id": exchange},
                )
                for exchange in ("SSE", "SZSE", "BSE")
            ),
        ),
    )


def test_actual_fresh_fetch_closes_landing_raw_and_accepted_in_order():
    conn = connect(":memory:")
    calls: list[str] = []

    class Adapter:
        def fetch_raw(self, _api, **params):
            calls.append(params["exchange_id"])
            return [_row(params["exchange_id"])]

    try:
        spec = sr.domain_spec(sr.load_registry(), "margin")
        spec["retry"] = {"max_attempts": 1, "backoff_seconds": [0]}
        spec["rate_limit"] = {}
        written = _execute_margin_partition(
            conn,
            Adapter(),
            spec,
            {"trade_date": PARTITION},
            contract=mi.contract_for_spec(spec),
            effective_min_rows=2,
            observed_at=OBSERVED_AT,
            batch_id="actual-fresh",
        )

        assert written == 3
        assert calls == ["SSE", "SZSE", "BSE"]
        assert conn.execute("SELECT status FROM ingest_batch").fetchone()[0] == "ACCEPTED"
        assert conn.execute("SELECT COUNT(*) FROM raw_tushare_margin").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 1
    finally:
        conn.close()


def test_actual_shadow_mismatch_blocks_partition_completion(monkeypatch):
    conn = connect(":memory:")
    original_write = sr._write_batch

    class Adapter:
        def fetch_raw(self, _api, **params):
            return [_row(params["exchange_id"])]

    def corrupting_shadow_write(*args, **kwargs):
        written = original_write(*args, **kwargs)
        args[0].execute(
            "UPDATE raw_tushare_margin SET rzye = rzye + 1 WHERE exchange_id = 'SSE'"
        )
        return written

    try:
        spec = sr.domain_spec(sr.load_registry(), "margin")
        spec["retry"] = {"max_attempts": 1, "backoff_seconds": [0]}
        spec["rate_limit"] = {}
        monkeypatch.setattr(sr, "_write_batch", corrupting_shadow_write)

        with pytest.raises(mi.MarginReconcileError, match="VALUE_MISMATCH"):
            _execute_margin_partition(
                conn,
                Adapter(),
                spec,
                {"trade_date": PARTITION},
                contract=mi.contract_for_spec(spec),
                effective_min_rows=2,
                observed_at=OBSERVED_AT,
                batch_id="actual-shadow-mismatch",
            )

        assert conn.execute("SELECT status FROM ingest_batch").fetchone()[0] == "ACCEPTED"
        assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 1
    finally:
        conn.close()


def test_actual_terminal_empty_is_rejected_without_creating_legacy_raw():
    conn = connect(":memory:")

    class EmptyAdapter:
        def fetch_raw(self, _api, **_params):
            return []

    try:
        spec = sr.domain_spec(sr.load_registry(), "margin")
        spec["retry"] = {"max_attempts": 1, "backoff_seconds": [0]}
        spec["rate_limit"] = {}
        with pytest.raises(sr.BatchCompletenessError):
            _execute_margin_partition(
                conn,
                EmptyAdapter(),
                spec,
                {"trade_date": PARTITION},
                contract=mi.contract_for_spec(spec),
                effective_min_rows=2,
                observed_at=OBSERVED_AT,
                batch_id="actual-empty",
            )

        status, rejection, detail = conn.execute(
            "SELECT status, rejection_code, rejection_detail FROM ingest_batch"
        ).fetchone()
        assert status == "REJECTED"
        assert rejection == "ZERO_ROWS"
        assert "empty_fragments=['SSE']" in detail
        assert "not_attempted=['SZSE', 'BSE']" in detail
        assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'raw_tushare_margin'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("failure", "error_type", "exception_type"),
    [
        ("authorization", "authorization", "authorization"),
        ("quota", "quota", "quota"),
        ("timeout", "timeout", "batch"),
        ("connection", "connection", "batch"),
    ],
)
def test_actual_provider_failure_flows_through_trace_to_durable_rejection(
    failure, error_type, exception_type
):
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    conn = connect(":memory:")

    class FailedAdapter:
        def fetch_raw(self, _api, **_params):
            if failure == "authorization":
                raise TuShareAuthorizationError("auth_denied")
            if failure == "quota":
                raise RuntimeError("今日请求已达上限")
            if failure == "timeout":
                raise TimeoutError("provider timed out")
            raise ConnectionError("provider connection refused")

    expected_exception = {
        "authorization": TuShareAuthorizationError,
        "quota": sr.QuotaExhaustedError,
        "batch": sr.BatchCompletenessError,
    }[exception_type]
    try:
        spec = sr.domain_spec(sr.load_registry(), "margin")
        spec["retry"] = {"max_attempts": 1, "backoff_seconds": [0]}
        spec["rate_limit"] = {}
        with pytest.raises(expected_exception):
            _execute_margin_partition(
                conn,
                FailedAdapter(),
                spec,
                {"trade_date": PARTITION},
                contract=mi.contract_for_spec(spec),
                effective_min_rows=2,
                observed_at=OBSERVED_AT,
                batch_id=f"actual-{failure}",
            )

        status, rejection, detail, outcomes_json = conn.execute(
            "SELECT status, rejection_code, rejection_detail, "
            "fragment_outcomes_json FROM ingest_batch"
        ).fetchone()
        assert (status, rejection) == ("REJECTED", "FRAGMENT_FAILED")
        assert f"SSE:{error_type}" in detail
        assert "not_attempted=['SZSE', 'BSE']" in detail
        outcomes = json.loads(outcomes_json)
        assert outcomes[0]["error_type"] == error_type
        assert [item["error_type"] for item in outcomes[1:]] == [
            "not_attempted",
            "not_attempted",
        ]
        for table in (
            "canonical_margin_exchange_daily",
            "accepted_partition",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'raw_tushare_margin'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_actual_landed_checkpoint_recovers_without_provider_and_closes_both_surfaces():
    conn = connect(":memory:")
    try:
        _land_actual_batch(conn, "actual-recovery")

        class NoProvider:
            def fetch_raw(self, _api, **_params):
                pytest.fail("durable Tx-A checkpoint must be reused without provider I/O")

        spec = sr.domain_spec(sr.load_registry(), "margin")
        written = _execute_margin_partition(
            conn,
            NoProvider(),
            spec,
            {"trade_date": PARTITION},
            contract=mi.contract_for_spec(spec),
            effective_min_rows=2,
        )

        assert written == 3
        assert conn.execute("SELECT status FROM ingest_batch").fetchone()[0] == "ACCEPTED"
        assert conn.execute("SELECT COUNT(*) FROM raw_tushare_margin").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM canonical_margin_exchange_daily").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 1
    finally:
        conn.close()


def test_actual_raw_failure_keeps_checkpoint_landed_and_pointer_absent(monkeypatch):
    conn = connect(":memory:")
    try:
        _land_actual_batch(conn, "actual-raw-failure")
        spec = sr.domain_spec(sr.load_registry(), "margin")
        monkeypatch.setattr(
            sr,
            "_write_batch",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("raw unavailable")),
        )

        with pytest.raises(mi.LegacyWriteError):
            _execute_margin_partition(
                conn,
                object(),
                spec,
                {"trade_date": PARTITION},
                contract=mi.contract_for_spec(spec),
                effective_min_rows=2,
            )

        assert conn.execute("SELECT status FROM ingest_batch").fetchone()[0] == "LANDED"
        assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM canonical_margin_exchange_daily").fetchone()[0] == 0
    finally:
        conn.close()


def test_drain_recovers_raw_ahead_landed_checkpoint_from_accepted_gap_without_provider():
    """Crash after raw commit cannot be hidden by the legacy table on restart."""
    conn = connect(":memory:")
    try:
        _land_actual_batch(conn, "raw-ahead-landed")
        spec = sr.domain_spec(sr.load_registry(), "margin")
        sr._write_batch(
            conn,
            spec,
            [_row(exchange) for exchange in ("SSE", "SZSE", "BSE")],
            effective_min_rows=2,
            expected_partition={"trade_date": PARTITION},
        )

        class NoProvider:
            def fetch_raw(self, _api, **_params):
                pytest.fail("accepted gap with LANDED evidence must recover without provider")

        result = sr.drain_domain(
            "margin",
            registry=sr.load_registry(),
            conn=conn,
            adapter=NoProvider(),
            expected_trading_days=[PARTITION],
            record=False,
        )

        assert result["status"] == "drained"
        assert result["gap_days"] == 1
        assert conn.execute("SELECT status FROM ingest_batch").fetchone()[0] == "ACCEPTED"
        assert conn.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 1
    finally:
        conn.close()


def test_drain_rechecks_accepted_shadow_parity_without_refetching_provider():
    """Accepted coverage cannot make a missing legacy shadow look clean."""

    conn = connect(":memory:")
    try:
        batch_id = "accepted-without-shadow"
        _land_actual_batch(conn, batch_id)
        mi.validate_margin_batch(conn, batch_id)
        assert mi.accept_margin_batch(conn, batch_id).status == "ACCEPTED"

        class NoProvider:
            def fetch_raw(self, _api, **_params):
                pytest.fail("accepted partition parity checks must not refetch provider")

        results = [
            sr.drain_domain(
                "margin",
                registry=sr.load_registry(),
                conn=conn,
                adapter=NoProvider(),
                expected_trading_days=[PARTITION],
                record=False,
            )
            for _ in range(2)
        ]

        assert [result["gap_days"] for result in results] == [0, 0]
        assert [result["refilled_days"] for result in results] == [0, 0]
        assert [result["status"] for result in results] == ["partial", "partial"]
        assert [result["still_failed"] for result in results] == [
            [PARTITION],
            [PARTITION],
        ]
    finally:
        conn.close()


def test_run_and_drain_share_the_same_margin_partition_executor(monkeypatch):
    contract = replace(
        _contract("SSE", "SZSE", "BSE"), coverage_start="20240102"
    )
    spec = {
        **_spec(),
        "batch_mode": "by_trade_date",
        "data_start": "20240102",
        "available_after": "t+1",
        "dataset_contract": {"dataset_id": DATASET_ID},
    }
    registry = {
        "defaults": {"target_db": "tushare_raw"},
        "domains": {"margin": spec},
    }
    calls: list[str] = []

    class Conn:
        def close(self):
            pass

    conn = Conn()
    adapter = object()

    monkeypatch.setattr(mi, "contract_for_spec", lambda _spec_arg: contract)
    monkeypatch.setattr(sr, "_adapter", lambda _source: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec_arg: conn)
    monkeypatch.setattr(sr, "_last_watermark_date", lambda *_args: None)
    monkeypatch.setattr(sr, "trading_days", lambda *_args: ["20240102"])
    monkeypatch.setattr(sr, "complete_batch_dates", lambda *_args: set())
    monkeypatch.setattr(mi, "accepted_dates", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility("20240102", False, "test"),
    )
    monkeypatch.setattr(
        mi,
        "accepted_frontier",
        lambda *_args, **_kwargs: SimpleNamespace(
            last_date="20240102", row_count=3, last_success_at=OBSERVED_AT
        ),
    )
    monkeypatch.setattr(sr, "_record_outcome", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mi,
        "project_ops_state",
        lambda *_args, **_kwargs: SimpleNamespace(
            frontier="20240102", row_count=3, accepted_at=OBSERVED_AT
        ),
    )
    monkeypatch.setattr(
        mi,
        "execute_partition_outcome",
        lambda **kwargs: calls.append(kwargs["params"]["trade_date"])
        or mi.FormalMarginPartitionOutcome(kind="accepted", rows=3),
    )
    monkeypatch.setattr(
        mi,
        "execute_partition",
        lambda *_args, **_kwargs: pytest.fail(
            "run and drain must both use the shared formal-margin adapter"
        ),
    )
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *_args, **_kwargs: pytest.fail("formal margin must not bypass its executor"),
    )

    run_result = sr.run_domain(
        "margin", backfill=True, start="20240102", end="20240102", registry=registry
    )
    drain_result = sr.drain_domain(
        "margin",
        registry=registry,
        conn=conn,
        adapter=adapter,
        expected_trading_days=["20240102"],
        record=False,
    )

    assert calls == ["20240102", "20240102"]
    assert run_result["ok"] is True and run_result["rows"] == 3
    assert drain_result["status"] == "drained" and drain_result["refilled_rows"] == 3


def test_explicit_margin_end_cannot_cross_current_eligible_horizon(monkeypatch):
    """显式回放窗口也必须先过 publication gate，未发布分区不得触发 writer/provider。"""
    contract = replace(
        _contract("SSE", "SZSE", "BSE"), coverage_start="20260715"
    )
    registry = {
        "defaults": {"target_db": "tushare_raw"},
        "domains": {
            "margin": {
                **_spec(),
                "batch_mode": "by_trade_date",
                "data_start": "20260715",
                "available_after": "t+1",
                "dataset_contract": {"dataset_id": DATASET_ID},
            }
        },
    }

    monkeypatch.setattr(mi, "contract_for_spec", lambda _spec_arg: contract)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility(
            "20260716", True, "t_plus_one_awaiting_next_trading_day"
        ),
    )
    monkeypatch.setattr(sr, "trading_days", lambda *_args: ["20260717"])
    monkeypatch.setattr(
        sr,
        "_target_conn",
        lambda _spec_arg: pytest.fail("future explicit end reached the writer boundary"),
    )

    with pytest.raises(ValueError, match="eligible horizon"):
        sr.run_domain(
            "margin",
            backfill=True,
            start="20260717",
            end="20260717",
            registry=registry,
        )


def test_margin_drain_injected_dates_cannot_bypass_eligible_horizon(monkeypatch):
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility(
            "20260716", True, "next_trading_session_awaiting_session"
        ),
    )
    monkeypatch.setattr(
        sr,
        "_target_conn",
        lambda _spec_arg: pytest.fail("future injected drain reached DB boundary"),
    )

    with pytest.raises(ValueError, match="eligible horizon"):
        sr.drain_domain(
            "margin",
            registry=sr.load_registry(),
            expected_trading_days=["20260717"],
            record=False,
        )


def test_margin_drain_injected_dates_must_each_be_real_trading_sessions(monkeypatch):
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility("20260720", False, "published"),
    )
    monkeypatch.setattr(
        sr, "trading_days", lambda *_args: ["20260717", "20260720"]
    )
    monkeypatch.setattr(
        sr,
        "_target_conn",
        lambda _spec_arg: pytest.fail("non-session injection reached DB boundary"),
    )

    with pytest.raises(ValueError, match="not trading sessions.*20260718"):
        sr.drain_domain(
            "margin",
            registry=sr.load_registry(),
            expected_trading_days=["20260717", "20260718", "20260720"],
            record=False,
        )


@pytest.mark.parametrize("mode", ["run", "drain"])
def test_mid_run_margin_authorization_failure_projects_accepted_state_before_exit(
    monkeypatch, mode
):
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    contract = _contract("SSE", "SZSE", "BSE")
    spec = {
        **_spec(),
        "batch_mode": "by_trade_date",
        "data_start": PARTITION,
        "available_after": "t+1",
        "dataset_contract": {"dataset_id": DATASET_ID},
    }
    registry = {
        "defaults": {"target_db": "tushare_raw"},
        "domains": {"margin": spec},
    }
    projected = []

    class Conn:
        def close(self):
            pass

    conn = Conn()
    monkeypatch.setattr(mi, "contract_for_spec", lambda _spec_arg: contract)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_target_conn", lambda _spec_arg: conn)
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility(PARTITION, False, "test"),
    )
    monkeypatch.setattr(sr, "trading_days", lambda *_args: [PARTITION])
    monkeypatch.setattr(mi, "accepted_dates", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(
        mi,
        "execute_partition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TuShareAuthorizationError("auth_denied")
        ),
    )
    monkeypatch.setattr(
        mi,
        "project_ops_state",
        lambda _conn, expected, **kwargs: projected.append(
            (list(expected), kwargs.get("provider_succeeded"))
        ),
    )

    with pytest.raises(TuShareAuthorizationError):
        if mode == "run":
            sr.run_domain(
                "margin",
                backfill=True,
                start=PARTITION,
                end=PARTITION,
                registry=registry,
            )
        else:
            sr.drain_domain(
                "margin",
                registry=registry,
                conn=conn,
                adapter=object(),
                expected_trading_days=[PARTITION],
                record=True,
            )

    assert projected == [([PARTITION], False)]


def test_incremental_margin_at_accepted_eligible_frontier_makes_zero_provider_calls(
    monkeypatch,
):
    """The single-day closed interval must not refetch its accepted endpoint."""
    contract = _contract("SSE", "SZSE", "BSE")
    spec = {
        **_spec(),
        "batch_mode": "by_trade_date",
        "data_start": PARTITION,
        "available_after": "t+1",
        "dataset_contract": {"dataset_id": DATASET_ID},
    }
    registry = {
        "defaults": {"target_db": "tushare_raw"},
        "domains": {"margin": spec},
    }
    projected: list[list[str]] = []

    class Conn:
        def close(self):
            pass

    monkeypatch.setattr(mi, "contract_for_spec", lambda _spec_arg: contract)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_target_conn", lambda _spec_arg: Conn())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility(PARTITION, False, "test"),
    )
    monkeypatch.setattr(sr, "trading_days", lambda *_args: [PARTITION])
    monkeypatch.setattr(
        mi,
        "accepted_frontier",
        lambda *_args, **_kwargs: SimpleNamespace(
            last_date=PARTITION, row_count=3, last_success_at=OBSERVED_AT
        ),
    )
    monkeypatch.setattr(
        mi,
        "execute_partition",
        lambda *_args, **_kwargs: pytest.fail(
            "accepted eligible frontier must suppress provider/executor"
        ),
    )
    monkeypatch.setattr(
        mi,
        "project_ops_state",
        lambda _conn, expected, **_kwargs: projected.append(list(expected))
        or SimpleNamespace(frontier=PARTITION, row_count=3, accepted_at=OBSERVED_AT),
    )
    monkeypatch.setattr(
        sr,
        "_record_outcome",
        lambda *_args, **_kwargs: pytest.fail(
            "formal margin must not use generic monotonic outcome projection"
        ),
    )

    result = sr.run_domain("margin", registry=registry)

    assert result["batches"] == 0
    assert result["last_date"] == PARTITION
    assert projected == [[PARTITION]]


def test_incremental_margin_reports_internal_accepted_gap_without_refetch(
    monkeypatch,
):
    """A latest frontier cannot hide an earlier missing AcceptedPartition."""

    latest = "20260716"
    contract = _contract("SSE", "SZSE", "BSE")
    spec = {
        **_spec(),
        "batch_mode": "by_trade_date",
        "data_start": PARTITION,
        "available_after": "t+1",
        "dataset_contract": {"dataset_id": DATASET_ID},
    }
    registry = {
        "defaults": {"target_db": "tushare_raw"},
        "domains": {"margin": spec},
    }

    class Conn:
        def close(self):
            pass

    monkeypatch.setattr(mi, "contract_for_spec", lambda _spec_arg: contract)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(sr, "_target_conn", lambda _spec_arg: Conn())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility(latest, False, "test"),
    )
    monkeypatch.setattr(sr, "trading_days", lambda *_args: [PARTITION, latest])
    monkeypatch.setattr(
        mi,
        "accepted_frontier",
        lambda *_args, **_kwargs: SimpleNamespace(
            last_date=latest, row_count=3, last_success_at=OBSERVED_AT
        ),
    )
    monkeypatch.setattr(
        mi,
        "execute_partition_outcome",
        lambda **_kwargs: pytest.fail(
            "default incremental mode must report the gap, not refetch it"
        ),
    )
    monkeypatch.setattr(
        mi,
        "project_ops_state",
        lambda _conn, _expected, **_kwargs: SimpleNamespace(
            frontier=latest,
            row_count=3,
            accepted_at=OBSERVED_AT,
            missing=(PARTITION,),
            reconcile_failures=(),
        ),
    )

    result = sr.run_domain("margin", registry=registry)

    assert result["ok"] is False
    assert result["failed_batches"] == 1


def test_single_domain_cli_exits_one_for_a_projected_accepted_gap(monkeypatch):
    registry = {"domains": {"margin": {"sync_policy": "manual_only"}}}
    monkeypatch.setattr(sr, "load_registry", lambda: registry)
    monkeypatch.setattr(sr, "_calendar_preflight", lambda _domains: None)
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda *_args, **_kwargs: {
            "domain": "margin",
            "failed_batches": 1,
            "ok": False,
        },
    )
    args = SimpleNamespace(
        all_due=False,
        domain="margin",
        drain=False,
        max_dates=None,
        backfill=False,
        start=None,
        end=None,
        resume=False,
    )

    assert sr._main_unlocked(args) == 1


def test_ops_projection_failure_rebuilds_without_refetching_accepted_partition(
    monkeypatch,
):
    """Accepted Tx-B survives a later cross-database projection failure."""
    raw = connect(":memory:")

    class NoClose:
        def __getattr__(self, name):
            return getattr(raw, name)

        def close(self):
            pass

    class Adapter:
        def __init__(self):
            self.calls: list[str] = []

        def fetch_raw(self, _api, **params):
            self.calls.append(params["exchange_id"])
            return [_row(params["exchange_id"])]

    adapter = Adapter()
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["retry"] = {"max_attempts": 1, "backoff_seconds": [0]}
    spec["rate_limit"] = {}
    registry = {
        "defaults": {},
        "domains": {"margin": spec},
    }
    projections = 0

    def flaky_projection(_conn, expected, **_kwargs):
        nonlocal projections
        projections += 1
        if projections == 1:
            raise RuntimeError("smartmoney projection unavailable")
        frontier = mi.accepted_frontier(spec, conn=raw)
        return SimpleNamespace(
            frontier=frontier.last_date,
            row_count=frontier.row_count,
            accepted_at=frontier.last_success_at,
        )

    monkeypatch.setattr(sr, "_adapter", lambda _source: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda _spec_arg: NoClose())
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec_arg: sr.DomainEligibility(PARTITION, False, "test"),
    )
    monkeypatch.setattr(sr, "trading_days", lambda *_args: [PARTITION])
    monkeypatch.setattr(mi, "project_ops_state", flaky_projection)
    monkeypatch.setattr(
        sr,
        "_record_outcome",
        lambda *_args, **_kwargs: pytest.fail(
            "formal margin must not fall back to generic outcome recording"
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="projection unavailable"):
            sr.run_domain("margin", registry=registry)

        assert raw.execute("SELECT COUNT(*) FROM accepted_partition").fetchone()[0] == 1
        assert adapter.calls == ["SSE", "SZSE", "BSE"]

        result = sr.run_domain("margin", registry=registry)

        assert result["batches"] == 0
        assert result["last_date"] == PARTITION
        assert adapter.calls == ["SSE", "SZSE", "BSE"]
        assert projections == 2
    finally:
        raw.close()
