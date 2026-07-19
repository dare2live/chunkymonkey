"""Fail-closed tests for the trusted accepted-calendar read boundary."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_acceptance import (
    CalendarFragmentCapture,
    CalendarLandingBatch,
    accept_calendar_batch,
    land_calendar_batch,
)
from services.data_sources.calendar_contract import calendar_contract_for_spec
from services.data_sources.calendar_reader import (
    CalendarTruthUnavailable,
    open_calendar_truth,
)
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    LANDING_TABLE,
    ensure_calendar_acceptance_schema,
)
from services.data_sources import calendar_acceptance, calendar_landing, calendar_reader
from services.data_sources.sync_runner import domain_spec, load_registry
from services.duck_adapter import connect


UTC = timezone.utc
OBSERVED = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
AVAILABLE = OBSERVED
FIRST_ACCEPTED = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
SECOND_ACCEPTED = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
START = date(1990, 12, 19)
FUTURE = date(2026, 12, 31)


@dataclass(frozen=True)
class _ValidatedRow:
    exchange: str
    cal_date: date
    is_open: int
    pretrade_date: date | None
    source_fragment_ordinal: int
    source_row_ordinal: int
    source_row_hash: str


@dataclass(frozen=True)
class _ValidatedGeneration:
    batch_id: str
    observed_at: datetime
    available_at: datetime
    canonical_rows: tuple[_ValidatedRow, ...]
    content_hash: str

    @property
    def row_count(self) -> int:
        return len(self.canonical_rows)


def _contract_and_registry():
    registry = load_registry()
    return calendar_contract_for_spec(domain_spec(registry, "trade_cal")), registry


def _validated(batch_id: str, *, second: bool = False) -> _ValidatedGeneration:
    marker = "b" if second else "a"
    rows = (
        _ValidatedRow("SSE", START, 1, None, 0, 0, marker * 64),
        _ValidatedRow("SSE", FUTURE, 1 if second else 0, START, 0, 1, marker * 63 + "c"),
    )
    return _ValidatedGeneration(
        batch_id=batch_id,
        observed_at=OBSERVED,
        available_at=AVAILABLE,
        canonical_rows=rows,
        content_hash=("2" if second else "1") * 64,
    )


def _insert_generation(
    conn,
    contract,
    validated: _ValidatedGeneration,
    *,
    accepted_at: datetime,
) -> None:
    generation_id = validated.batch_id
    conn.execute(
        f"""
        INSERT INTO {INGEST_BATCH_TABLE} (
            batch_id, dataset_id, contract_version, contract_hash, config_hash,
            writer_id, partition_value, source_name, status, request_json,
            fragment_outcomes_json, expected_fragment_count,
            completed_fragment_count, failed_fragment_count, landing_row_count,
            canonical_row_count, payload_hash, canonical_hash, observed_at,
            available_at, landed_at, validated_at, accepted_at,
            rejection_code, rejection_detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', ?, ?, 1, 1, 0, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, NULL, NULL)
        """,
        [
            generation_id,
            DATASET_ID,
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
            contract.writer_id,
            generation_id,
            contract.source,
            "{}",
            "[]",
            validated.row_count,
            validated.row_count,
            "f" * 64,
            validated.content_hash,
            OBSERVED,
            AVAILABLE,
            AVAILABLE,
            accepted_at,
            accepted_at,
        ],
    )
    conn.execute(
        f"""
        INSERT INTO {ACCEPTED_TABLE} (
            dataset_id, partition_value, batch_id, contract_version,
            contract_hash, config_hash, row_count, content_hash, observed_at,
            available_at, accepted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            DATASET_ID,
            generation_id,
            generation_id,
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
            validated.row_count,
            validated.content_hash,
            OBSERVED,
            AVAILABLE,
            accepted_at,
        ],
    )
    conn.executemany(
        f"""
        INSERT INTO {CANONICAL_TABLE} (
            generation_id, exchange, cal_date, is_open, pretrade_date,
            source_fragment_ordinal, source_row_ordinal, source_row_hash,
            available_at, contract_version, config_hash, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [
                generation_id,
                row.exchange,
                row.cal_date,
                row.is_open,
                row.pretrade_date,
                row.source_fragment_ordinal,
                row.source_row_ordinal,
                row.source_row_hash,
                AVAILABLE,
                contract.contract_version,
                contract.config_hash,
                accepted_at,
            ]
            for row in validated.canonical_rows
        ],
    )


def _build_db(path: Path, generations: tuple[tuple[_ValidatedGeneration, datetime], ...]):
    contract, _registry = _contract_and_registry()
    conn = connect(str(path))
    try:
        ensure_calendar_acceptance_schema(conn)
        for validated, accepted_at in generations:
            _insert_generation(
                conn, contract, validated, accepted_at=accepted_at
            )
    finally:
        conn.close()
    return contract


def _patch_live(
    monkeypatch,
    path: Path,
    validated_by_batch: dict[str, _ValidatedGeneration],
) -> list[tuple[str, datetime]]:
    _contract, registry = _contract_and_registry()
    calls: list[tuple[str, datetime]] = []
    monkeypatch.setattr(
        calendar_reader,
        "_load_live_registry_snapshot",
        lambda: deepcopy(registry),
    )
    monkeypatch.setattr(
        calendar_reader,
        "_open_live_tushare_raw_readonly",
        lambda: connect(str(path), read_only=True),
    )

    def _validate(conn, batch_id, contract, trusted_now):
        del conn, contract
        calls.append((batch_id, trusted_now))
        return validated_by_batch[batch_id]

    monkeypatch.setattr(
        calendar_reader, "validate_landed_calendar_batch", _validate
    )
    return calls


def _patch_live_factories(monkeypatch, path: Path) -> None:
    _contract, registry = _contract_and_registry()
    monkeypatch.setattr(
        calendar_reader,
        "_load_live_registry_snapshot",
        lambda: deepcopy(registry),
    )
    monkeypatch.setattr(
        calendar_reader,
        "_open_live_tushare_raw_readonly",
        lambda: connect(str(path), read_only=True),
    )


def _real_provider_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous_open: date | None = None
    current = START
    while current <= date(1990, 12, 31):
        is_open = int(current.weekday() < 5)
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": is_open,
                "pretrade_date": previous_open.strftime("%Y%m%d")
                if previous_open
                else None,
            }
        )
        if is_open:
            previous_open = current
        current += timedelta(days=1)
    return rows


def _land_and_accept_real_generation(path: Path, monkeypatch) -> str:
    contract, _registry = _contract_and_registry()
    observed_at = datetime(1990, 12, 19, 8, 0, tzinfo=UTC)
    batch_id = "real-generation-1990"
    conn = connect(str(path))
    try:
        # Land and accept must share a frozen clock. Using wall-clock landed_at
        # on the same UTC day as FIRST_ACCEPTED makes time_chain fail after 10:00Z.
        landed_at = FIRST_ACCEPTED - timedelta(minutes=30)
        monkeypatch.setattr(calendar_landing, "_now_utc", lambda: landed_at)
        monkeypatch.setattr(calendar_acceptance, "_now_utc", lambda: FIRST_ACCEPTED)
        land_calendar_batch(
            conn,
            CalendarLandingBatch(
                batch_id=batch_id,
                observed_at=observed_at,
                fragments=(
                    CalendarFragmentCapture(
                        fragment_ordinal=0,
                        request=contract.request_for_page(observed_at, 0),
                        rows=_real_provider_rows(),
                        outcome="completed",
                        completed_at=observed_at,
                    ),
                ),
            ),
            contract,
        )
        outcome = accept_calendar_batch(conn, batch_id, contract)
        assert outcome.status == "ACCEPTED"
        assert outcome.row_count == 13
    finally:
        conn.close()
    return batch_id


def test_public_boundary_only_accepts_decision_time() -> None:
    signature = inspect.signature(open_calendar_truth)
    assert tuple(signature.parameters) == ("decision_time",)
    for forbidden in ({"conn": object()}, {"path": "/tmp/x"}, {"ref": "latest"}):
        with pytest.raises(TypeError):
            open_calendar_truth(FIRST_ACCEPTED, **forbidden)


def test_real_landing_acceptance_and_reader_prove_the_same_generation(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "real-calendar.duckdb"
    batch_id = _land_and_accept_real_generation(db_path, monkeypatch)
    _patch_live_factories(monkeypatch, db_path)

    truth = open_calendar_truth(FIRST_ACCEPTED)
    assert truth.evidence.batch_id == batch_id
    assert truth.evidence.row_count == 13
    assert truth.evidence.coverage_start == START
    assert truth.evidence.coverage_end == date(1990, 12, 31)
    assert truth.is_open(date(1990, 12, 31))


def test_reader_rollback_failure_is_blocking_not_silently_swallowed(
    tmp_path, monkeypatch
) -> None:
    generation = _validated("cleanup-failure")
    db_path = tmp_path / "calendar.duckdb"
    _build_db(db_path, ((generation, FIRST_ACCEPTED),))
    _patch_live(monkeypatch, db_path, {generation.batch_id: generation})

    class RollbackFailConnection:
        def __init__(self):
            self.inner = connect(str(db_path), read_only=True)

        def execute(self, sql, *args, **kwargs):
            if sql == "ROLLBACK":
                raise RuntimeError("synthetic rollback failure")
            return self.inner.execute(sql, *args, **kwargs)

        def close(self):
            self.inner.close()

    monkeypatch.setattr(
        calendar_reader,
        "_open_live_tushare_raw_readonly",
        RollbackFailConnection,
    )

    with pytest.raises(
        CalendarTruthUnavailable,
        match="calendar_truth_cleanup_failed.*synthetic rollback failure",
    ):
        open_calendar_truth(FIRST_ACCEPTED)


def test_reader_cleanup_failure_does_not_mask_base_exception(
    monkeypatch,
) -> None:
    _contract, registry = _contract_and_registry()
    monkeypatch.setattr(
        calendar_reader,
        "_load_live_registry_snapshot",
        lambda: deepcopy(registry),
    )

    class InterruptingConnection:
        def execute(self, sql, *_args, **_kwargs):
            if sql == "ROLLBACK":
                raise RuntimeError("synthetic rollback failure")
            return self

        def close(self):
            return None

    monkeypatch.setattr(
        calendar_reader,
        "_open_live_tushare_raw_readonly",
        InterruptingConnection,
    )
    monkeypatch.setattr(
        calendar_reader,
        "_require_formal_schema",
        lambda _conn: (_ for _ in ()).throw(KeyboardInterrupt("cancelled")),
    )

    with pytest.raises(KeyboardInterrupt, match="cancelled") as caught:
        open_calendar_truth(FIRST_ACCEPTED)
    assert any(
        "calendar_truth_cleanup_failed" in note
        and "synthetic rollback failure" in note
        for note in getattr(caught.value, "__notes__", ())
    )


def test_bad_landing_blocks_even_when_pointer_and_canonical_stay_self_consistent(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "tampered-landing.duckdb"
    batch_id = _land_and_accept_real_generation(db_path, monkeypatch)
    conn = connect(str(db_path))
    try:
        payload = conn.execute(
            f"SELECT payload_json FROM {LANDING_TABLE} "
            "WHERE batch_id = ? AND fragment_ordinal = 0 AND row_ordinal = 0",
            [batch_id],
        ).fetchone()[0]
        forged = json.loads(payload)
        forged["is_open"] = 0
        conn.execute(
            f"UPDATE {LANDING_TABLE} SET payload_json = ? "
            "WHERE batch_id = ? AND fragment_ordinal = 0 AND row_ordinal = 0",
            [json.dumps(forged, sort_keys=True), batch_id],
        )
    finally:
        conn.close()
    _patch_live_factories(monkeypatch, db_path)

    with pytest.raises(CalendarTruthUnavailable) as caught:
        open_calendar_truth(FIRST_ACCEPTED)
    assert caught.value.status == "BLOCKED"
    assert "landing_invalid" in caught.value.reason


def test_generation_is_invisible_before_usable_at_and_visible_after(
    tmp_path, monkeypatch
) -> None:
    generation = _validated("generation-1")
    db_path = tmp_path / "calendar.duckdb"
    _build_db(db_path, ((generation, FIRST_ACCEPTED),))
    calls = _patch_live(monkeypatch, db_path, {generation.batch_id: generation})

    with pytest.raises(CalendarTruthUnavailable) as caught:
        open_calendar_truth(FIRST_ACCEPTED - timedelta(microseconds=1))
    assert caught.value.status == "NOT_EVALUATED"
    assert calls == []

    truth = open_calendar_truth(FIRST_ACCEPTED)
    assert truth.evidence.usable_at == FIRST_ACCEPTED
    assert truth.is_open(START)
    assert calls == [(generation.batch_id, FIRST_ACCEPTED)]


def test_multiple_generations_are_selected_by_accepted_at_as_of_not_identity(
    tmp_path, monkeypatch
) -> None:
    # Lexical identities deliberately sort opposite to acceptance chronology.
    old = _validated("z-old")
    new = _validated("a-new", second=True)
    db_path = tmp_path / "calendar.duckdb"
    _build_db(
        db_path,
        ((old, FIRST_ACCEPTED), (new, SECOND_ACCEPTED)),
    )
    _patch_live(
        monkeypatch,
        db_path,
        {old.batch_id: old, new.batch_id: new},
    )

    assert open_calendar_truth(FIRST_ACCEPTED).evidence.generation_id == "z-old"
    assert open_calendar_truth(SECOND_ACCEPTED).evidence.generation_id == "a-new"


def test_raw_rows_without_accepted_generation_do_not_fallback(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "calendar.duckdb"
    _build_db(db_path, ())
    conn = connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE raw_tushare_trade_cal "
            "(exchange VARCHAR, cal_date VARCHAR, is_open INTEGER)"
        )
        conn.execute(
            "INSERT INTO raw_tushare_trade_cal VALUES ('SSE', '20260719', 1)"
        )
    finally:
        conn.close()
    calls = _patch_live(monkeypatch, db_path, {})

    with pytest.raises(CalendarTruthUnavailable) as caught:
        open_calendar_truth(FIRST_ACCEPTED)
    assert caught.value.status == "NOT_EVALUATED"
    assert "no_accepted" in caught.value.reason
    assert calls == []


@pytest.mark.parametrize(
    "mutation",
    ("canonical", "hash", "count", "lineage", "built_at", "time_chain"),
)
def test_canonical_hash_count_and_lineage_tampering_blocks(
    tmp_path, monkeypatch, mutation
) -> None:
    generation = _validated("generation-1")
    db_path = tmp_path / f"calendar-{mutation}.duckdb"
    _build_db(db_path, ((generation, FIRST_ACCEPTED),))
    conn = connect(str(db_path))
    try:
        if mutation == "canonical":
            conn.execute(
                f"UPDATE {CANONICAL_TABLE} SET is_open = 0 "
                "WHERE generation_id = ? AND cal_date = ?",
                [generation.batch_id, START],
            )
        elif mutation == "hash":
            conn.execute(
                f"UPDATE {ACCEPTED_TABLE} SET content_hash = ? WHERE batch_id = ?",
                ["9" * 64, generation.batch_id],
            )
            conn.execute(
                f"UPDATE {INGEST_BATCH_TABLE} SET canonical_hash = ? WHERE batch_id = ?",
                ["9" * 64, generation.batch_id],
            )
        elif mutation == "count":
            conn.execute(
                f"UPDATE {ACCEPTED_TABLE} SET row_count = 3 WHERE batch_id = ?",
                [generation.batch_id],
            )
            conn.execute(
                f"UPDATE {INGEST_BATCH_TABLE} SET canonical_row_count = 3 "
                "WHERE batch_id = ?",
                [generation.batch_id],
            )
        elif mutation == "lineage":
            conn.execute(
                f"UPDATE {CANONICAL_TABLE} SET source_row_hash = ? "
                "WHERE generation_id = ? AND cal_date = ?",
                ["d" * 64, generation.batch_id, START],
            )
        elif mutation == "built_at":
            conn.execute(
                f"UPDATE {CANONICAL_TABLE} SET built_at = ? "
                "WHERE generation_id = ? AND cal_date = ?",
                [FIRST_ACCEPTED + timedelta(seconds=1), generation.batch_id, START],
            )
        else:
            conn.execute(
                f"UPDATE {INGEST_BATCH_TABLE} SET landed_at = ? WHERE batch_id = ?",
                [FIRST_ACCEPTED + timedelta(seconds=1), generation.batch_id],
            )
    finally:
        conn.close()
    _patch_live(monkeypatch, db_path, {generation.batch_id: generation})

    with pytest.raises(CalendarTruthUnavailable) as caught:
        open_calendar_truth(FIRST_ACCEPTED)
    assert caught.value.status == "BLOCKED"


def test_announced_future_calendar_date_is_queryable_but_outside_coverage_blocks(
    tmp_path, monkeypatch
) -> None:
    generation = _validated("generation-2", second=True)
    db_path = tmp_path / "calendar.duckdb"
    _build_db(db_path, ((generation, FIRST_ACCEPTED),))
    _patch_live(monkeypatch, db_path, {generation.batch_id: generation})

    truth = open_calendar_truth(FIRST_ACCEPTED)
    assert truth.is_open(FUTURE)
    assert FUTURE in truth.open_dates(START, FUTURE)
    assert truth.previous_open(FUTURE) == START
    with pytest.raises(CalendarTruthUnavailable, match="outside_accepted_coverage"):
        truth.is_open(date(2027, 1, 1))
