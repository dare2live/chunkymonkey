"""A2 adversarial tests: live-capable accepted calendar runtime + dim demotion."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.calendar_builder import DIM_IS_ACCEPTED_TRUTH, DIM_ROLE
from services.data_sources.calendar_acceptance import (
    CalendarAcceptanceError,
    CalendarFragmentCapture,
    CalendarLandingBatch,
)
from services.data_sources.calendar_contract import calendar_contract_for_spec
from services.data_sources.calendar_reader import (
    CalendarTruthUnavailable,
    open_calendar_truth,
)
from services.data_sources.calendar_runtime import (
    CalendarRuntimeError,
    DIM_TRADING_CALENDAR_ROLE,
    bootstrap_calendar_acceptance_schema,
    capture_and_publish_authorized_calendar_generation,
    dim_is_accepted_calendar_truth,
    publish_accepted_calendar_generation,
    refuse_legacy_calendar_raw_write,
    runtime_surface,
)
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    CalendarSchemaError,
    FRAGMENT_TABLE,
    LANDING_TABLE,
)
from services.data_sources.sync_runner import domain_spec, load_registry
from services.duck_adapter import connect


OBSERVED_AT = datetime(1990, 12, 19, 8, 0, tzinfo=timezone.utc)


def _contract():
    return calendar_contract_for_spec(domain_spec(load_registry(), "trade_cal"))


def _rows(contract, *, observed_at=OBSERVED_AT) -> list[dict]:
    start = datetime.strptime(contract.coverage_start, "%Y%m%d").date()
    end = contract.required_through(observed_at)
    result: list[dict] = []
    previous_open: date | None = None
    cursor = start
    while cursor <= end:
        is_open = 1 if cursor.weekday() < 5 else 0
        result.append(
            {
                "exchange": "SSE",
                "cal_date": cursor.strftime("%Y%m%d"),
                "is_open": is_open,
                "pretrade_date": (
                    None if previous_open is None else previous_open.strftime("%Y%m%d")
                ),
            }
        )
        if is_open:
            previous_open = cursor
        cursor += timedelta(days=1)
    return result


def _batch(contract, batch_id: str, *, rows=None):
    page = contract.page_limit
    provider_rows = rows if rows is not None else _rows(contract)
    fragments = []
    for index in range(0, max(len(provider_rows), 1), page):
        chunk = provider_rows[index : index + page]
        if index > 0 and not chunk:
            break
        fragments.append(
            CalendarFragmentCapture(
                fragment_ordinal=len(fragments),
                request=contract.request_for_page(OBSERVED_AT, index),
                rows=chunk,
                outcome="COMPLETED",
                completed_at=OBSERVED_AT,
            )
        )
    if len(provider_rows) % page == 0:
        fragments.append(
            CalendarFragmentCapture(
                fragment_ordinal=len(fragments),
                request=contract.request_for_page(
                    OBSERVED_AT, len(fragments) * page
                ),
                rows=(),
                outcome="COMPLETED",
                completed_at=OBSERVED_AT,
            )
        )
    return CalendarLandingBatch(batch_id, OBSERVED_AT, tuple(fragments))


def test_dim_markers_declare_serve_projection_not_accepted_truth() -> None:
    assert DIM_IS_ACCEPTED_TRUTH is False
    assert dim_is_accepted_calendar_truth() is False
    assert DIM_ROLE == DIM_TRADING_CALENDAR_ROLE == "serve_projection_open_days_only"
    surface = runtime_surface()
    assert surface["dim_is_accepted_truth"] is False
    assert surface["legacy_raw_write"] == "forbidden"


def test_dim_and_legacy_raw_alone_do_not_satisfy_calendar_truth(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "dim-only.duckdb"
    conn = connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE raw_tushare_trade_cal "
            "(exchange VARCHAR, cal_date VARCHAR, is_open INTEGER)"
        )
        conn.execute(
            "INSERT INTO raw_tushare_trade_cal VALUES ('SSE', '20260719', 1)"
        )
        conn.execute(
            "CREATE TABLE dim_trading_calendar "
            "(trade_date VARCHAR PRIMARY KEY, is_trading INTEGER)"
        )
        conn.execute(
            "INSERT INTO dim_trading_calendar VALUES ('2026-07-19', 1)"
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "services.data_sources.calendar_reader._open_live_tushare_raw_readonly",
        lambda: connect(str(db_path), read_only=True),
    )
    monkeypatch.setattr(
        "services.data_sources.calendar_reader._contract_from_live_registry",
        _contract,
    )

    with pytest.raises(CalendarTruthUnavailable) as caught:
        open_calendar_truth(datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc))
    assert caught.value.status == "NOT_EVALUATED"
    assert "no_accepted" in caught.value.reason or "schema" in caught.value.reason


def test_invalid_landing_does_not_bootstrap_schema() -> None:
    contract = _contract()
    conn = connect(":memory:")
    try:
        with pytest.raises(CalendarAcceptanceError, match="batch_id must be non-empty"):
            publish_accepted_calendar_generation(
                conn,
                CalendarLandingBatch("", OBSERVED_AT, ()),
                contract,
            )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert LANDING_TABLE not in tables
        assert CANONICAL_TABLE not in tables
        assert FRAGMENT_TABLE not in tables
    finally:
        conn.close()


def test_valid_landing_without_bootstrap_fails_closed_no_ddl() -> None:
    contract = _contract()
    # Tiny page so fixture generation stays cheap under the production coverage
    # start; runtime path still requires an already-bootstrapped schema.
    from copy import deepcopy

    from services.data_sources.calendar_contract import calendar_contract_for_spec

    spec = deepcopy(domain_spec(load_registry(), "trade_cal"))
    spec["page_limit"] = 20
    contract = calendar_contract_for_spec(spec)
    conn = connect(":memory:")
    try:
        with pytest.raises(CalendarSchemaError):
            publish_accepted_calendar_generation(
                conn, _batch(contract, "needs-schema"), contract
            )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert not tables.intersection({LANDING_TABLE, CANONICAL_TABLE, FRAGMENT_TABLE})
    finally:
        conn.close()


def test_publish_accepted_generation_end_to_end_with_explicit_bootstrap() -> None:
    from copy import deepcopy

    spec = deepcopy(domain_spec(load_registry(), "trade_cal"))
    # Keep coverage cheap: override only through factory page_limit; coverage
    # remains production start but required_through for OBSERVED_AT is 1990-12-31.
    contract = calendar_contract_for_spec(spec)
    conn = connect(":memory:")
    try:
        outcome = publish_accepted_calendar_generation(
            conn,
            _batch(contract, "live-capable"),
            contract,
            bootstrap=True,
        )
        assert outcome.status == "ACCEPTED"
        assert outcome.row_count > 0
        assert (
            conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0]
            == outcome.row_count
        )
        open_days = conn.execute(
            f"SELECT COUNT(*) FROM {CANONICAL_TABLE} WHERE is_open = 1"
        ).fetchone()[0]
        closed_days = conn.execute(
            f"SELECT COUNT(*) FROM {CANONICAL_TABLE} WHERE is_open = 0"
        ).fetchone()[0]
        assert open_days > 0 and closed_days > 0
    finally:
        conn.close()


def test_refuse_legacy_raw_write_is_hard_wall() -> None:
    with pytest.raises(CalendarRuntimeError, match="legacy_calendar_raw_write_forbidden"):
        refuse_legacy_calendar_raw_write(detail="test")


def test_enabled_trade_cal_uses_formal_publish_never_legacy_raw(monkeypatch) -> None:
    import services.data_sources.sync_runner as sr

    registry = sr.load_registry()
    called = {"publish": 0}

    def _fake_publish(spec):
        called["publish"] += 1
        assert spec["domain"] == "trade_cal"
        return {
            "domain": "trade_cal",
            "status": "ok",
            "batches": 1,
            "rows": 12,
            "failed_batches": 0,
            "publication": "accepted_calendar_generation",
        }

    monkeypatch.setattr(sr, "_publish_trade_cal_accepted_generation", _fake_publish)
    for name in ("_write_batch", "_smartmoney_conn"):
        monkeypatch.setattr(
            sr,
            name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)),
        )

    result = sr.run_domain("trade_cal", registry=registry)
    assert called["publish"] == 1
    assert result["publication"] == "accepted_calendar_generation"
    surface = runtime_surface()
    assert surface["legacy_raw_write"] == "forbidden"
    assert surface["provider_sync"] == "authorized_manual_generation"


def test_provider_nan_pretrade_normalizes_to_null_before_landing() -> None:
    from services.data_sources.calendar_runtime import _project_provider_row

    projected = _project_provider_row(
        {
            "exchange": "SSE",
            "cal_date": "19901219",
            "is_open": 1,
            "pretrade_date": float("nan"),
            "extra": "drop-me",
        }
    )
    assert projected == {
        "exchange": "SSE",
        "cal_date": "19901219",
        "is_open": 1,
        "pretrade_date": None,
    }


def test_capture_and_publish_authorized_generation_from_fetch_pages() -> None:
    contract = _contract()
    provider_rows = _rows(contract)
    page = contract.page_limit
    pages = [
        provider_rows[index : index + page]
        for index in range(0, len(provider_rows), page)
    ]
    if len(pages[-1]) == page:
        pages.append([])
    cursor = {"i": 0}

    def fetch_page(request):
        assert request["offset"] == cursor["i"] * page
        chunk = pages[cursor["i"]]
        cursor["i"] += 1
        return chunk

    conn = connect(":memory:")
    try:
        outcome = capture_and_publish_authorized_calendar_generation(
            conn,
            contract,
            fetch_page=fetch_page,
            observed_at=OBSERVED_AT,
            bootstrap=True,
        )
        assert outcome.status == "ACCEPTED"
        assert outcome.row_count == len(provider_rows)
        assert outcome.batch_id.startswith("trade_cal:SSE:")
    finally:
        conn.close()


def test_bootstrap_is_explicit_entrypoint() -> None:
    conn = connect(":memory:")
    try:
        bootstrap_calendar_acceptance_schema(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert {LANDING_TABLE, CANONICAL_TABLE, FRAGMENT_TABLE}.issubset(tables)
    finally:
        conn.close()
