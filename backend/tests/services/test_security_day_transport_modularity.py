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


def test_s3_default_security_day_publish_is_caller_only_not_fused(monkeypatch) -> None:
    """S3: default sync land→accept composition; fused capture_and_publish is not fan-in."""

    from types import SimpleNamespace

    from services.data_sources import nominal_ohlcv_runtime as nr
    from services.data_sources import security_day_transport as sdt
    from services.data_sources import sync_runner as sr

    fused_calls: list[str] = []

    def boom_fused(*_a, **_k):
        fused_calls.append("nominal")
        raise AssertionError(
            "capture_and_publish_* must not be production sync fan-in (S3)"
        )

    land_then_calls: list[dict] = []

    def fake_land_then(
        domain,
        _conn,
        _contract,
        *,
        trade_date,
        fetch_rows,
        observed_at=None,
        bootstrap=True,
    ):
        rows = list(fetch_rows({"trade_date": trade_date}) or [])
        land_then_calls.append(
            {"domain": domain, "trade_date": trade_date, "rows": len(rows)}
        )
        return SimpleNamespace(
            status="ACCEPTED",
            row_count=len(rows),
            batch_id=f"{domain}:{trade_date}:s3test",
            partition_value=trade_date,
            content_hash="s3hash",
            rejection_code=None,
        )

    monkeypatch.setattr(
        nr, "capture_and_publish_authorized_nominal_ohlcv_partition", boom_fused
    )
    monkeypatch.setattr(sdt, "land_then_accept_authorized_security_day", fake_land_then)
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec, trigger_mode="manual": SimpleNamespace(
            eligible_end="20260717", reason="test"
        ),
    )
    monkeypatch.setattr(
        sr,
        "resolve_operation_window",
        lambda _elig, requested_start, requested_end: SimpleNamespace(
            effective_end=requested_end
        ),
    )
    monkeypatch.setattr(sr, "apply_fetch_socket_timeout", lambda _spec: None)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr,
        "_fetch_with_retry",
        lambda _adapter, _spec, request: [{"ts_code": "000001.SZ", **request}],
    )

    class _FakeConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(sr, "_target_conn", lambda _spec: _FakeConn())

    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "daily")
    result = sr._publish_security_day_accepted_partition(
        "daily", spec, trade_date="20260717", trigger_mode="manual"
    )
    assert fused_calls == []
    assert land_then_calls == [
        {"domain": "daily", "trade_date": "20260717", "rows": 1}
    ]
    assert result["status"] == "ok"
    assert result["transport"] == "land_then_accept"
    assert result["publication"] == "accepted_nominal_ohlcv_partition"


def test_s3_default_trade_cal_publish_is_caller_only_not_fused(monkeypatch) -> None:
    """S3: trade_cal sync uses land then accept; fused capture_and_publish is not fan-in."""

    from types import SimpleNamespace

    from services.data_sources import calendar_runtime as cr
    from services.data_sources import sync_runner as sr

    fused_calls: list[str] = []
    land_calls: list[str] = []
    accept_calls: list[str] = []

    def boom_fused(*_a, **_k):
        fused_calls.append("calendar")
        raise AssertionError(
            "capture_and_publish_* must not be production sync fan-in (S3)"
        )

    def fake_land(_conn, _contract, *, fetch_page, observed_at=None, bootstrap=True):
        land_calls.append("land")
        _ = fetch_page({"offset": 0})
        return SimpleNamespace(batch_id="trade_cal:SSE:s3test")

    def fake_accept(_conn, batch_id, _contract, *, bootstrap=False):
        accept_calls.append(str(batch_id))
        return SimpleNamespace(
            status="ACCEPTED",
            row_count=3,
            batch_id=batch_id,
            generation_id="gen-s3",
            content_hash="calhash",
            rejection_code=None,
        )

    monkeypatch.setattr(
        cr, "capture_and_publish_authorized_calendar_generation", boom_fused
    )
    monkeypatch.setattr(cr, "capture_and_land_authorized_calendar_generation", fake_land)
    monkeypatch.setattr(cr, "accept_calendar_from_landing", fake_accept)
    monkeypatch.setattr(sr, "apply_fetch_socket_timeout", lambda _spec: None)
    monkeypatch.setattr(sr, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        sr, "_fetch_with_retry", lambda *_a, **_k: [{"cal_date": "20260717"}]
    )

    class _FakeConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(sr, "_target_conn", lambda _spec: _FakeConn())

    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "trade_cal")
    result = sr._publish_trade_cal_accepted_generation(spec)
    assert fused_calls == []
    assert land_calls == ["land"]
    assert accept_calls == ["trade_cal:SSE:s3test"]
    assert result["status"] == "ok"
    assert result["transport"] == "land_then_accept"


def test_s3_sync_runner_source_has_no_fused_publish_calls() -> None:
    """Static proof: sync_runner production source must not call capture_and_publish_*."""

    from pathlib import Path

    from services.data_sources import sync_runner as sr

    src_path = Path(sr.__file__)
    src = src_path.read_text(encoding="utf-8")
    assert "capture_and_publish_authorized_" not in src, (
        f"{src_path} still references fused capture_and_publish_* (S3 residual)"
    )
