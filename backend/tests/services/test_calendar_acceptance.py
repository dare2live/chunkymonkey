"""Adversarial tests for the SSE calendar landing/acceptance boundary."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import inspect

import pytest

import services.data_sources.calendar_acceptance as acceptance_module
import services.data_sources.calendar_landing as landing_module
from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_acceptance import (
    CalendarAcceptanceError,
    CalendarFragmentCapture,
    CalendarLandingBatch,
    accept_calendar_batch,
    land_calendar_batch,
    validate_landed_calendar_batch,
)
from services.data_sources.calendar_contract import calendar_contract_for_spec
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    ensure_calendar_acceptance_schema,
)
from services.duck_adapter import connect


OBSERVED_AT = datetime(1990, 12, 19, 8, 0, tzinfo=timezone.utc)
TRUSTED_NOW = OBSERVED_AT + timedelta(minutes=5)


def _spec() -> dict:
    return {
        "domain": "trade_cal",
        # 2026-08-31 授权换源: baostock -> calendar_rule (日历改规则推导, 不再取数)。
        # 表/库名相关字段依设计不动 —— 换 adapter 不改表。
        "source": "calendar_rule",
        "api": "query_trade_dates",
        "target_db": "tushare_raw",
        "target_table": "raw_tushare_trade_cal",
        "grain": ["exchange", "cal_date"],
        "batch_mode": "full_refresh",
        "fixed_params": {"exchange": "SSE"},
        "page_limit": 6000,
        "write_mode": "replace_snapshot",
        "population_scope": {
            "kind": "external_aggregate",
            "venue_field": "exchange",
            "venue_ids": ["SSE"],
            "population_label": "sse_trading_calendar",
            "method": "calendar_rule_weekday_minus_holidays",
            "unit": "calendar_day_status",
        },
        "calendar_generation": {
            "contract_version": "1",
            "coverage_start": "19901219",
            "required_through_rule": "observed_year_end",
            "timezone": "Asia/Shanghai",
            "availability": {
                "axis": "provider_response",
                "rule": "response_completed",
                "at": "response_completed_at",
            },
            "canonicalization_version": "1",
        },
    }


def _contract_with_page_limit(page_limit: int):
    """Factory-owned contract; page_limit is the only adjustable policy field."""

    spec = _spec()
    spec["page_limit"] = page_limit
    return calendar_contract_for_spec(spec)


@pytest.fixture
def contract():
    # The production contract's first observed generation is only thirteen
    # natural days, keeping tests cheap without weakening fixed policy.
    return calendar_contract_for_spec(_spec())


@pytest.fixture
def conn():
    database = connect(":memory:")
    ensure_calendar_acceptance_schema(database)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(autouse=True)
def trusted_writer_clock(monkeypatch):
    monkeypatch.setattr(acceptance_module, "_now_utc", lambda: TRUSTED_NOW)


def _rows(contract, *, observed_at=OBSERVED_AT) -> list[dict]:
    start = datetime.strptime(contract.coverage_start, "%Y%m%d").date()
    end = contract.required_through(observed_at)
    result: list[dict] = []
    previous_open: date | None = None
    cursor = start
    while cursor <= end:
        is_open = int(cursor.weekday() < 5)
        result.append(
            {
                "exchange": "SSE",
                "cal_date": cursor.strftime("%Y%m%d"),
                "is_open": str(is_open),
                "pretrade_date": previous_open.strftime("%Y%m%d")
                if previous_open
                else None,
            }
        )
        if is_open:
            previous_open = cursor
        cursor += timedelta(days=1)
    return result


def _batch(
    contract,
    batch_id: str,
    *,
    rows: list[dict] | None = None,
    observed_at=OBSERVED_AT,
    fragments: tuple[CalendarFragmentCapture, ...] | None = None,
) -> CalendarLandingBatch:
    if fragments is None:
        provider_rows = _rows(contract, observed_at=observed_at) if rows is None else rows
        captures = []
        for ordinal, offset in enumerate(range(0, len(provider_rows), contract.page_limit)):
            page = provider_rows[offset : offset + contract.page_limit]
            captures.append(
                CalendarFragmentCapture(
                    ordinal,
                    contract.request_for_page(observed_at, offset),
                    page,
                    "success",
                    observed_at,
                )
            )
        if not captures:
            captures.append(
                CalendarFragmentCapture(
                    0,
                    contract.request_for_page(observed_at, 0),
                    [],
                    "success",
                    observed_at,
                )
            )
        fragments = tuple(captures)
    return CalendarLandingBatch(batch_id, observed_at, fragments)


def _land_and_accept(conn, contract, batch_id="ok", *, rows=None):
    land_calendar_batch(conn, _batch(contract, batch_id, rows=rows), contract)
    return accept_calendar_batch(conn, batch_id, contract)


def test_open_and_closed_full_generation_is_accepted_and_idempotently_reproved(
    conn, contract
):
    outcome = _land_and_accept(conn, contract)

    assert outcome.status == "ACCEPTED"
    assert outcome.row_count == 13
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == 13
    pointer = conn.execute(
        f"SELECT partition_value, batch_id, content_hash FROM {ACCEPTED_TABLE}"
    ).fetchone()
    assert tuple(pointer) == ("ok", "ok", outcome.content_hash)
    assert accept_calendar_batch(conn, "ok", contract) == outcome
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == 13


def test_writer_public_api_does_not_allow_time_or_evidence_injection():
    assert tuple(inspect.signature(accept_calendar_batch).parameters) == (
        "conn",
        "batch_id",
        "contract",
    )
    assert tuple(inspect.signature(land_calendar_batch).parameters) == (
        "conn",
        "batch",
        "contract",
    )


def test_identical_business_content_has_parity_hash_across_append_only_generations(
    conn, contract
):
    first = _land_and_accept(conn, contract, "generation-one")
    second = _land_and_accept(conn, contract, "generation-two")

    assert first.content_hash == second.content_hash
    assert conn.execute(f"SELECT COUNT(*) FROM {ACCEPTED_TABLE}").fetchone()[0] == 2
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == 26


def test_idempotent_ack_fails_closed_when_acceptance_time_evidence_diverges(
    conn, contract
):
    _land_and_accept(conn, contract, "time-tamper")
    conn.execute(
        f"UPDATE {ACCEPTED_TABLE} SET accepted_at = accepted_at + INTERVAL 1 SECOND "
        "WHERE batch_id = 'time-tamper'"
    )

    with pytest.raises(CalendarAcceptanceError, match="acceptance-time evidence drift"):
        accept_calendar_batch(conn, "time-tamper", contract)


def test_terminal_empty_fragment_is_valid_for_exact_page_multiple(conn, contract):
    divisible = _contract_with_page_limit(13)
    rows = _rows(divisible)
    fragments = []
    for ordinal, offset in enumerate(range(0, len(rows), divisible.page_limit)):
        fragments.append(
            CalendarFragmentCapture(
                ordinal,
                divisible.request_for_page(OBSERVED_AT, offset),
                rows[offset : offset + divisible.page_limit],
                "completed",
                OBSERVED_AT,
            )
        )
    offset = len(rows)
    fragments.append(
        CalendarFragmentCapture(
            len(fragments),
            divisible.request_for_page(OBSERVED_AT, offset),
            [],
            "empty",
            OBSERVED_AT,
        )
    )

    land_calendar_batch(
        conn, _batch(divisible, "terminal-empty", fragments=tuple(fragments)), divisible
    )
    validated = validate_landed_calendar_batch(
        conn, "terminal-empty", divisible, TRUSTED_NOW
    )

    assert validated.row_count == 13


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "COVERAGE_MISMATCH"),
        ("duplicate", "DUPLICATE_GRAIN"),
        ("open_only", "OPEN_CLOSED_COMPLETENESS"),
        ("pretrade", "PRETRADE_CHAIN"),
        ("extra_field", "PROVIDER_FIELDS"),
        ("wrong_exchange", "WRONG_EXCHANGE"),
    ],
)
def test_semantically_invalid_generation_is_atomically_rejected(
    conn, contract, mutation, code
):
    rows = _rows(contract)
    if mutation == "missing":
        rows.pop(3)
    elif mutation == "duplicate":
        rows.append(dict(rows[3]))
    elif mutation == "open_only":
        previous = None
        for row in rows:
            row["is_open"] = "1"
            row["pretrade_date"] = previous
            previous = row["cal_date"]
    elif mutation == "pretrade":
        rows[5]["pretrade_date"] = None
    elif mutation == "extra_field":
        rows[5]["vendor_extra"] = "must-not-normalise"
    elif mutation == "wrong_exchange":
        rows[5]["exchange"] = "BSE"
    batch_id = f"reject-{mutation}"
    land_calendar_batch(conn, _batch(contract, batch_id, rows=rows), contract)

    outcome = accept_calendar_batch(conn, batch_id, contract)

    assert (outcome.status, outcome.rejection_code) == ("REJECTED", code)
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == 0
    assert conn.execute(f"SELECT COUNT(*) FROM {ACCEPTED_TABLE}").fetchone()[0] == 0


def test_zero_rows_and_missing_terminal_page_fail_closed(conn, contract):
    zero = _batch(contract, "zero", rows=[])
    land_calendar_batch(conn, zero, contract)
    assert accept_calendar_batch(conn, "zero", contract).rejection_code == "ZERO_ROWS"

    exact = _contract_with_page_limit(13)
    full_page = _rows(exact)
    land_calendar_batch(conn, _batch(exact, "no-terminal", rows=full_page), exact)
    assert (
        accept_calendar_batch(conn, "no-terminal", exact).rejection_code
        == "MISSING_TERMINAL_FRAGMENT"
    )


@pytest.mark.parametrize(
    ("outcome", "error_type", "code"),
    [
        ("permission", None, "PROVIDER_PERMISSION"),
        ("captcha", None, "PROVIDER_CAPTCHA"),
        ("failed", "timeout", "FRAGMENT_FAILED"),
    ],
)
def test_provider_failure_classes_are_preserved_and_rejected(
    conn, contract, outcome, error_type, code
):
    fragment = CalendarFragmentCapture(
        0,
        contract.request_for_page(OBSERVED_AT, 0),
        [],
        outcome,
        OBSERVED_AT,
        error_type=error_type,
        error_detail="provider did not return calendar rows",
    )
    batch_id = f"provider-{outcome}"
    land_calendar_batch(conn, _batch(contract, batch_id, fragments=(fragment,)), contract)

    rejected = accept_calendar_batch(conn, batch_id, contract)

    assert rejected.rejection_code == code
    stored = conn.execute(
        f"SELECT outcome, error_type FROM {FRAGMENT_TABLE} WHERE batch_id = ?",
        [batch_id],
    ).fetchone()
    assert stored[0] == "FAILED"


def test_future_calendar_rows_are_allowed_but_future_observation_is_not(conn, contract):
    land_calendar_batch(conn, _batch(contract, "future-rows"), contract)
    validated = validate_landed_calendar_batch(
        conn, "future-rows", contract, OBSERVED_AT
    )
    assert validated.canonical_rows[-1].cal_date == date(1990, 12, 31)

    future_observed = OBSERVED_AT + timedelta(days=1)
    land_calendar_batch(
        conn,
        _batch(contract, "future-observation", observed_at=future_observed),
        contract,
    )
    monkeypatch_clock = OBSERVED_AT
    acceptance_module._now_utc = lambda: monkeypatch_clock
    rejected = accept_calendar_batch(conn, "future-observation", contract)
    assert rejected.rejection_code == "FUTURE_OBSERVATION"


def test_observation_boundary_must_equal_latest_fragment_completion(conn, contract):
    rows = _rows(contract)
    fragments = tuple(
        CalendarFragmentCapture(
            ordinal,
            contract.request_for_page(OBSERVED_AT, offset),
            rows[offset : offset + contract.page_limit],
            "success",
            OBSERVED_AT - timedelta(seconds=1),
        )
        for ordinal, offset in enumerate(range(0, len(rows), contract.page_limit))
    )

    with pytest.raises(CalendarAcceptanceError, match="max fragment completed_at"):
        land_calendar_batch(
            conn, _batch(contract, "bad-observed", fragments=fragments), contract
        )
    assert conn.execute(f"SELECT COUNT(*) FROM {INGEST_BATCH_TABLE}").fetchone()[0] == 0


@pytest.mark.parametrize(
    "kill_step",
    ("tx_a_after_batch", "tx_a_after_fragments", "tx_a_after_rows"),
)
def test_tx_a_kill_rolls_back_batch_fragments_and_rows(
    conn, contract, monkeypatch, kill_step
):
    def kill(step):
        if step == kill_step:
            raise RuntimeError("kill tx-a")

    monkeypatch.setattr(landing_module, "_TEST_KILL_HOOK", kill)
    with pytest.raises(RuntimeError, match="kill tx-a"):
        land_calendar_batch(conn, _batch(contract, "kill-a"), contract)

    for table in (INGEST_BATCH_TABLE, FRAGMENT_TABLE, LANDING_TABLE):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize(
    "kill_step",
    (
        "tx_b_after_validation",
        "tx_b_after_canonical",
        "tx_b_after_pointer",
        "tx_b_after_batch",
    ),
)
def test_tx_b_kill_rolls_back_canonical_pointer_and_status(
    conn, contract, monkeypatch, kill_step
):
    land_calendar_batch(conn, _batch(contract, "kill-b"), contract)

    def kill(step):
        if step == kill_step:
            raise RuntimeError("kill tx-b")

    monkeypatch.setattr(acceptance_module, "_TEST_KILL_HOOK", kill)
    with pytest.raises(RuntimeError, match="kill tx-b"):
        accept_calendar_batch(conn, "kill-b", contract)

    assert conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = 'kill-b'"
    ).fetchone()[0] == "LANDED"
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == 0
    assert conn.execute(f"SELECT COUNT(*) FROM {ACCEPTED_TABLE}").fetchone()[0] == 0


def test_tx_b_validation_and_publish_share_one_rollback_boundary(
    conn, contract, monkeypatch
):
    land_calendar_batch(conn, _batch(contract, "snapshot-b"), contract)
    original = conn.execute(
        f"SELECT payload_json FROM {LANDING_TABLE} "
        "WHERE batch_id = 'snapshot-b' AND fragment_ordinal = 0 AND row_ordinal = 0"
    ).fetchone()[0]

    def tamper_then_kill(step):
        if step == "tx_b_after_validation":
            conn.execute(
                f"UPDATE {LANDING_TABLE} SET payload_json = '{{}}' "
                "WHERE batch_id = 'snapshot-b' AND fragment_ordinal = 0 "
                "AND row_ordinal = 0"
            )
            raise RuntimeError("kill validation snapshot")

    monkeypatch.setattr(acceptance_module, "_TEST_KILL_HOOK", tamper_then_kill)
    with pytest.raises(RuntimeError, match="kill validation snapshot"):
        accept_calendar_batch(conn, "snapshot-b", contract)

    assert conn.execute(
        f"SELECT payload_json FROM {LANDING_TABLE} "
        "WHERE batch_id = 'snapshot-b' AND fragment_ordinal = 0 AND row_ordinal = 0"
    ).fetchone()[0] == original
    assert conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = 'snapshot-b'"
    ).fetchone()[0] == "LANDED"
    assert conn.execute(f"SELECT COUNT(*) FROM {CANONICAL_TABLE}").fetchone()[0] == 0
    assert conn.execute(f"SELECT COUNT(*) FROM {ACCEPTED_TABLE}").fetchone()[0] == 0


def test_landing_preserves_duplicate_and_stored_hash_cannot_self_attest(conn, contract):
    rows = _rows(contract)
    rows.append(dict(rows[3]))
    land_calendar_batch(conn, _batch(contract, "duplicate-kept", rows=rows), contract)
    duplicated_date = rows[3]["cal_date"]
    payload_count = conn.execute(
        f"SELECT COUNT(*) FROM {LANDING_TABLE} "
        "WHERE batch_id = ? AND json_extract_string(payload_json, '$.cal_date') = ?",
        ["duplicate-kept", duplicated_date],
    ).fetchone()[0]
    assert payload_count == 2

    conn.execute(
        f"UPDATE {INGEST_BATCH_TABLE} SET payload_hash = ? WHERE batch_id = ?",
        ["f" * 64, "duplicate-kept"],
    )
    rejected = accept_calendar_batch(conn, "duplicate-kept", contract)
    assert rejected.rejection_code == "BATCH_EVIDENCE_MISMATCH"


def test_unknown_outcome_typo_is_not_silently_classified(conn, contract):
    fragment = CalendarFragmentCapture(
        0,
        contract.request_for_page(OBSERVED_AT, 0),
        [],
        "succes",
        OBSERVED_AT,
    )
    with pytest.raises(CalendarAcceptanceError, match="unknown calendar fragment outcome"):
        land_calendar_batch(conn, _batch(contract, "typo", fragments=(fragment,)), contract)


def test_request_and_pagination_evidence_are_recomputed(conn, contract):
    rows = _rows(contract)
    first = CalendarFragmentCapture(
        0,
        {**contract.request_for_page(OBSERVED_AT, 0), "exchange": "BSE"},
        rows,
        "success",
        OBSERVED_AT,
    )
    land_calendar_batch(conn, _batch(contract, "bad-request", fragments=(first,)), contract)
    rejected = accept_calendar_batch(conn, "bad-request", contract)
    assert rejected.rejection_code == "REQUEST_MISMATCH"
