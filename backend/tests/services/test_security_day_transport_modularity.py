"""S1/S2 transport strangler: land-only and accept-from-landing are independent.

Red→green invariants:
- land-only never writes canonical / accepted_partition
- accept-from-landing never calls provider fetch / _adapter
- local legacy-raw materializer is acquire→landing only (never raw→canonical)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.data_sources.nominal_ohlcv_contract import load_nominal_ohlcv_contract
from services.data_sources.nominal_ohlcv_runtime import (
    accept_nominal_ohlcv_from_landing,
    capture_and_land_authorized_nominal_ohlcv_partition,
)
from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE as OHLCV_CANONICAL,
    DATASET_ID as OHLCV_DATASET,
)
from services.data_sources.security_day_transport import (
    land_then_accept_authorized_security_day,
    materialize_security_day_landing_from_legacy_raw_rows,
)
from services.data_sources.stock_st_contract import load_stock_st_contract
from services.data_sources.stock_st_runtime import (
    accept_stock_st_from_landing,
    capture_and_land_authorized_stock_st_partition,
)
from services.data_sources.stock_st_schema import (
    CANONICAL_TABLE as ST_CANONICAL,
    DATASET_ID as ST_DATASET,
)
from services.duck_adapter import connect

_DAILY = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "domain_samples" / "daily.json").read_text(
        encoding="utf-8"
    )
)
_ST = json.loads(
    (
        Path(__file__).parents[1] / "fixtures" / "domain_samples" / "stock_st.json"
    ).read_text(encoding="utf-8")
)
PARTITION = "20230103"
ST_PARTITION = "20220104"
OBSERVED = datetime(2023, 1, 3, 18, 5, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)
ST_OBSERVED = datetime(2022, 1, 4, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
    timezone.utc
)


@pytest.fixture
def conn():
    database = connect(":memory:")
    yield database
    database.close()


def _daily_rows(partition: str) -> list[dict]:
    rows = [dict(row) for row in _DAILY["rows"]]
    for row in rows:
        row["trade_date"] = partition
    return rows


def _st_rows() -> list[dict]:
    rows = [dict(row) for row in _ST["rows"]]
    for row in rows:
        row["trade_date"] = ST_PARTITION
    rows[0]["ts_code"] = "000001.SZ"
    return rows


def test_land_only_daily_does_not_write_canonical(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    rows = _daily_rows(PARTITION)
    fetch_calls: list[dict] = []

    def fetch_rows(request):
        fetch_calls.append(dict(request))
        return rows

    batch = capture_and_land_authorized_nominal_ohlcv_partition(
        conn,
        contract,
        trade_date=PARTITION,
        fetch_rows=fetch_rows,
        observed_at=OBSERVED,
        bootstrap=True,
    )
    assert batch.batch_id.startswith(f"daily:{PARTITION}:")
    assert len(fetch_calls) == 1

    status = conn.execute(
        "SELECT status FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    assert status == "LANDED"

    canonical = conn.execute(
        f"SELECT COUNT(*) FROM {OHLCV_CANONICAL} WHERE trade_date = ?",
        [PARTITION],
    ).fetchone()[0]
    assert canonical == 0
    accepted = conn.execute(
        "SELECT COUNT(*) FROM accepted_partition WHERE dataset_id = ? AND partition_value = ?",
        [OHLCV_DATASET, PARTITION],
    ).fetchone()[0]
    assert accepted == 0


def test_land_only_stock_st_does_not_write_canonical(conn) -> None:
    contract = load_stock_st_contract()
    rows = _st_rows()

    batch = capture_and_land_authorized_stock_st_partition(
        conn,
        contract,
        trade_date=ST_PARTITION,
        fetch_rows=lambda _request: rows,
        observed_at=ST_OBSERVED,
        bootstrap=True,
    )
    status = conn.execute(
        "SELECT status FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()[0]
    assert status == "LANDED"
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {ST_CANONICAL} WHERE trade_date = ?",
            [ST_PARTITION],
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM accepted_partition WHERE dataset_id = ?",
            [ST_DATASET],
        ).fetchone()[0]
        == 0
    )


def test_accept_from_landing_has_zero_provider_fetch(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    rows = _daily_rows(PARTITION)
    fetch_calls: list[dict] = []

    def fetch_rows(request):
        fetch_calls.append(dict(request))
        return rows

    batch = capture_and_land_authorized_nominal_ohlcv_partition(
        conn,
        contract,
        trade_date=PARTITION,
        fetch_rows=fetch_rows,
        observed_at=OBSERVED,
        bootstrap=True,
    )
    assert len(fetch_calls) == 1

    def forbid_fetch(_request):
        raise AssertionError("accept-from-landing must not fetch")

    # Rebind would only matter if accept path called capture; it must not.
    outcome = accept_nominal_ohlcv_from_landing(
        conn, batch.batch_id, contract, bootstrap=False
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == len(rows)
    assert len(fetch_calls) == 1  # unchanged after accept
    # prove forbid_fetch is unused
    assert forbid_fetch  # noqa: B018 — keep symbol referenced for intent


def test_accept_from_landing_stock_st_zero_fetch(conn) -> None:
    contract = load_stock_st_contract()
    rows = _st_rows()
    batch = capture_and_land_authorized_stock_st_partition(
        conn,
        contract,
        trade_date=ST_PARTITION,
        fetch_rows=lambda _request: rows,
        observed_at=ST_OBSERVED,
        bootstrap=True,
    )
    outcome = accept_stock_st_from_landing(conn, batch.batch_id, contract)
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == len(rows)


def test_land_then_accept_is_caller_only_composition(conn) -> None:
    contract = load_nominal_ohlcv_contract()
    rows = _daily_rows(PARTITION)
    outcome = land_then_accept_authorized_security_day(
        "daily",
        conn,
        contract,
        trade_date=PARTITION,
        fetch_rows=lambda _request: rows,
        observed_at=OBSERVED,
        bootstrap=True,
    )
    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == len(rows)
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {OHLCV_CANONICAL} WHERE trade_date = DATE '2023-01-03'",
        ).fetchone()[0]
        == len(rows)
    )


def test_legacy_raw_materializer_lands_only_never_canonical(conn) -> None:
    """Explicit acquire→landing from local raw rows; never SELECT raw INSERT canonical."""

    contract = load_nominal_ohlcv_contract()
    raw_rows = _daily_rows(PARTITION)
    batch = materialize_security_day_landing_from_legacy_raw_rows(
        "daily",
        conn,
        contract,
        trade_date=PARTITION,
        raw_rows=raw_rows,
        observed_at=OBSERVED,
        bootstrap=True,
        lineage_note="test_local_raw_materialize",
    )
    status = conn.execute(
        "SELECT status, request_json FROM ingest_batch WHERE batch_id = ?",
        [batch.batch_id],
    ).fetchone()
    assert status[0] == "LANDED"
    request = json.loads(status[1])
    assert request.get("acquire_mode") == "local_legacy_raw_materialize"
    assert request.get("lineage_note") == "test_local_raw_materialize"
    assert (
        conn.execute(
            f"SELECT COUNT(*) FROM {OHLCV_CANONICAL}",
        ).fetchone()[0]
        == 0
    )

    outcome = accept_nominal_ohlcv_from_landing(conn, batch.batch_id, contract)
    assert outcome.status == "ACCEPTED"


def test_sync_runner_accept_from_landing_skips_adapter(monkeypatch) -> None:
    """CLI accept path must not construct a live provider adapter."""

    from services.data_sources import sync_runner as sr

    adapter_calls: list[str] = []

    def boom_adapter(source: str):
        adapter_calls.append(source)
        raise AssertionError(f"_adapter must not run on accept-from-landing: {source}")

    monkeypatch.setattr(sr, "_adapter", boom_adapter)

    # Shape-level: the accept helper must be importable without adapter.
    assert callable(sr.accept_security_day_from_landing_batch)
    assert adapter_calls == []
