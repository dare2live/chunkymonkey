"""Trusted, fail-closed reader for the accepted SSE trading calendar.

The public boundary intentionally accepts only a decision time.  Database,
registry, generation and table identity are all resolved inside the boundary so
callers cannot substitute an unproved raw/dim surface for accepted evidence.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_acceptance import (
    CalendarAcceptanceError,
    validate_landed_calendar_batch,
)
from services.data_sources.calendar_contract import calendar_contract_for_spec
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    verify_calendar_acceptance_schema,
)


class CalendarTruthUnavailable(RuntimeError):
    """No accepted calendar truth can be proved for the requested decision time."""

    def __init__(self, status: str, reason: str):
        if status not in {"BLOCKED", "NOT_EVALUATED"}:
            raise ValueError(f"invalid calendar truth status={status!r}")
        self.status = status
        self.reason = reason
        super().__init__(f"{status}: {reason}")


@dataclass(frozen=True)
class CalendarTruthEvidence:
    dataset_id: str
    generation_id: str
    batch_id: str
    contract_version: str
    contract_hash: str
    config_hash: str
    row_count: int
    content_hash: str
    observed_at: datetime
    available_at: datetime
    accepted_at: datetime
    usable_at: datetime
    coverage_start: date
    coverage_end: date


@dataclass(frozen=True)
class CalendarTruth:
    """Immutable accepted calendar generation and its proof envelope."""

    _all_dates: tuple[date, ...]
    _open_dates: tuple[date, ...]
    _open_flags: tuple[int, ...]
    evidence: CalendarTruthEvidence

    def _covered(self, value: Any) -> date:
        day = _as_date(value, field="calendar query date")
        if day < self.evidence.coverage_start or day > self.evidence.coverage_end:
            raise CalendarTruthUnavailable(
                "BLOCKED",
                "calendar_query_outside_accepted_coverage "
                f"date={day.isoformat()} coverage="
                f"{self.evidence.coverage_start.isoformat()}.."
                f"{self.evidence.coverage_end.isoformat()}",
            )
        return day

    def is_open(self, value: Any) -> bool:
        day = self._covered(value)
        position = bisect_left(self._all_dates, day)
        if position >= len(self._all_dates) or self._all_dates[position] != day:
            raise CalendarTruthUnavailable(
                "BLOCKED",
                f"accepted_calendar_has_internal_gap date={day.isoformat()}",
            )
        return bool(self._open_flags[position])

    def previous_open(self, value: Any) -> date | None:
        day = self._covered(value)
        position = bisect_left(self._open_dates, day)
        return None if position == 0 else self._open_dates[position - 1]

    def open_dates(self, start: Any, end: Any) -> tuple[date, ...]:
        first = self._covered(start)
        last = self._covered(end)
        if first > last:
            raise ValueError("calendar range start must be on or before end")
        left = bisect_left(self._open_dates, first)
        right = bisect_right(self._open_dates, last)
        return self._open_dates[left:right]


_POINTER_FIELDS = (
    "dataset_id",
    "partition_value",
    "batch_id",
    "contract_version",
    "contract_hash",
    "config_hash",
    "row_count",
    "content_hash",
    "observed_at",
    "available_at",
    "accepted_at",
)

_BATCH_FIELDS = (
    "batch_id",
    "dataset_id",
    "contract_version",
    "contract_hash",
    "config_hash",
    "writer_id",
    "partition_value",
    "source_name",
    "status",
    "canonical_row_count",
    "canonical_hash",
    "observed_at",
    "available_at",
    "landed_at",
    "validated_at",
    "accepted_at",
)

_CANONICAL_FIELDS = (
    "generation_id",
    "exchange",
    "cal_date",
    "is_open",
    "pretrade_date",
    "source_fragment_ordinal",
    "source_row_ordinal",
    "source_row_hash",
    "available_at",
    "contract_version",
    "config_hash",
    "built_at",
)


def _load_live_registry_snapshot() -> dict[str, Any]:
    from services.data_sources.sync_runner import load_registry

    return load_registry()


def _contract_from_live_registry():
    from services.data_sources.sync_runner import domain_spec

    registry = _load_live_registry_snapshot()
    spec = domain_spec(registry, "trade_cal")
    return calendar_contract_for_spec(spec)


def _open_live_tushare_raw_readonly():
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    path = get_database_manifest().path_for("tushare_raw")
    return connect(str(path), read_only=True)


def _as_aware_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CalendarTruthUnavailable(
                "BLOCKED", f"invalid_{field}={value!r}"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CalendarTruthUnavailable(
            "BLOCKED", f"{field}_must_be_timezone_aware"
        )
    return value.astimezone(timezone.utc)


def _as_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        compact = value.strip().replace("-", "")
        if len(compact) == 8 and compact.isdigit():
            try:
                return datetime.strptime(compact, "%Y%m%d").date()
            except ValueError as exc:
                raise ValueError(
                    f"{field} must be a valid date, got {value!r}"
                ) from exc
    raise ValueError(f"{field} must be a valid date, got {value!r}")


def _rows(conn, sql: str, params: list[Any] | None = None) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row) for row in conn.execute(sql, params or []).fetchall())


def _mapping(fields: tuple[str, ...], row: tuple[Any, ...]) -> dict[str, Any]:
    if len(fields) != len(row):
        raise CalendarTruthUnavailable("BLOCKED", "calendar_evidence_row_shape_mismatch")
    return dict(zip(fields, row, strict=True))


def _same_instant(left: Any, right: Any, *, field: str) -> bool:
    return _as_aware_datetime(left, field=field) == _as_aware_datetime(right, field=field)


def _require_formal_schema(conn) -> None:
    expected = {
        ACCEPTED_TABLE,
        INGEST_BATCH_TABLE,
        FRAGMENT_TABLE,
        LANDING_TABLE,
        CANONICAL_TABLE,
    }
    try:
        actual = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_catalog = current_database()
                   AND table_schema = 'main'
                   AND table_name IN (?, ?, ?, ?, ?)
                """,
                sorted(expected),
            ).fetchall()
        }
    except Exception as exc:
        raise CalendarTruthUnavailable(
            "BLOCKED", f"calendar_schema_inventory_failed: {str(exc)[:300]}"
        ) from exc
    if not actual:
        raise CalendarTruthUnavailable("NOT_EVALUATED", "no_accepted_calendar_schema")
    if actual != expected:
        raise CalendarTruthUnavailable(
            "BLOCKED", f"partial_accepted_calendar_schema missing={sorted(expected - actual)}"
        )
    try:
        verify_calendar_acceptance_schema(conn)
    except Exception as exc:
        raise CalendarTruthUnavailable(
            "BLOCKED", f"calendar_acceptance_schema_invalid: {str(exc)[:500]}"
        ) from exc


def _select_pointer(conn, contract, decision_time: datetime) -> dict[str, Any]:
    rows = _rows(
        conn,
        f"""
        SELECT {', '.join(_POINTER_FIELDS)}
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ?
           AND contract_version = ?
           AND contract_hash = ?
           AND config_hash = ?
         ORDER BY accepted_at DESC, batch_id DESC
        """,
        [
            DATASET_ID,
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
        ],
    )
    visible = []
    for row in rows:
        item = _mapping(_POINTER_FIELDS, row)
        usable_at = max(
            _as_aware_datetime(item["available_at"], field="available_at"),
            _as_aware_datetime(item["accepted_at"], field="accepted_at"),
        )
        if usable_at <= decision_time:
            visible.append((usable_at, item))
    if visible:
        # SQL order is authoritative: accepted_at, never the generation string.
        return visible[0][1]

    total = conn.execute(
        f"SELECT COUNT(*) FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
        [DATASET_ID],
    ).fetchone()[0]
    if int(total) == 0:
        raise CalendarTruthUnavailable(
            "NOT_EVALUATED", "no_accepted_calendar_generation"
        )
    if not rows:
        raise CalendarTruthUnavailable(
            "BLOCKED", "accepted_calendar_generation_does_not_match_live_contract"
        )
    raise CalendarTruthUnavailable(
        "NOT_EVALUATED", "no_accepted_calendar_generation_visible_at_decision_time"
    )


def _load_and_verify_batch(conn, pointer: dict[str, Any], contract) -> dict[str, Any]:
    rows = _rows(
        conn,
        f"SELECT {', '.join(_BATCH_FIELDS)} FROM {INGEST_BATCH_TABLE} "
        "WHERE batch_id = ? AND dataset_id = ?",
        [pointer["batch_id"], DATASET_ID],
    )
    if len(rows) != 1:
        raise CalendarTruthUnavailable(
            "BLOCKED", "accepted_calendar_pointer_batch_cardinality_mismatch"
        )
    batch = _mapping(_BATCH_FIELDS, rows[0])
    exact = {
        "batch_id": pointer["batch_id"],
        "dataset_id": pointer["dataset_id"],
        "contract_version": pointer["contract_version"],
        "contract_hash": pointer["contract_hash"],
        "config_hash": pointer["config_hash"],
        "partition_value": pointer["partition_value"],
        "status": "ACCEPTED",
        "canonical_row_count": pointer["row_count"],
        "canonical_hash": pointer["content_hash"],
        "writer_id": contract.writer_id,
        "source_name": contract.source,
    }
    mismatches = [name for name, value in exact.items() if batch[name] != value]
    for field in ("observed_at", "available_at", "accepted_at"):
        if not _same_instant(batch[field], pointer[field], field=field):
            mismatches.append(field)
    if batch["validated_at"] is None:
        mismatches.append("validated_at")
    else:
        observed_at = _as_aware_datetime(batch["observed_at"], field="observed_at")
        available_at = _as_aware_datetime(batch["available_at"], field="available_at")
        landed_at = _as_aware_datetime(batch["landed_at"], field="landed_at")
        validated_at = _as_aware_datetime(batch["validated_at"], field="validated_at")
        accepted_at = _as_aware_datetime(batch["accepted_at"], field="accepted_at")
        if observed_at != available_at:
            mismatches.append("availability_rule")
        if not observed_at <= landed_at <= validated_at <= accepted_at:
            mismatches.append("time_chain")
    if pointer["partition_value"] != pointer["batch_id"]:
        mismatches.append("generation_identity")
    if mismatches:
        raise CalendarTruthUnavailable(
            "BLOCKED",
            f"accepted_calendar_pointer_batch_mismatch fields={sorted(set(mismatches))}",
        )
    return batch


def _canonical_tuple(row: Any) -> tuple[Any, ...]:
    return (
        str(row.exchange),
        _as_date(row.cal_date, field="canonical cal_date"),
        int(row.is_open),
        None if row.pretrade_date is None else _as_date(
            row.pretrade_date, field="canonical pretrade_date"
        ),
        int(row.source_fragment_ordinal),
        int(row.source_row_ordinal),
        str(row.source_row_hash),
    )


def _load_and_verify_canonical(
    conn,
    pointer: dict[str, Any],
    contract,
    validated,
) -> tuple[tuple[date, ...], tuple[date, ...], tuple[int, ...]]:
    generation_id = str(pointer["partition_value"])
    stored = _rows(
        conn,
        f"""
        SELECT {', '.join(_CANONICAL_FIELDS)}
          FROM {CANONICAL_TABLE}
         WHERE generation_id = ?
         ORDER BY cal_date, exchange
        """,
        [generation_id],
    )
    if len(stored) != int(pointer["row_count"]):
        raise CalendarTruthUnavailable(
            "BLOCKED", "accepted_calendar_canonical_row_count_mismatch"
        )
    expected = tuple(sorted(
        (_canonical_tuple(row) for row in validated.canonical_rows),
        key=lambda row: (row[1], row[0]),
    ))
    actual_semantics: list[tuple[Any, ...]] = []
    for raw in stored:
        item = _mapping(_CANONICAL_FIELDS, raw)
        if (
            str(item["generation_id"]) != generation_id
            or str(item["contract_version"]) != str(contract.contract_version)
            or str(item["config_hash"]) != str(contract.config_hash)
            or item["built_at"] is None
            or not _same_instant(
                item["built_at"], pointer["accepted_at"], field="built_at"
            )
            or not _same_instant(
                item["available_at"], pointer["available_at"], field="available_at"
            )
        ):
            raise CalendarTruthUnavailable(
                "BLOCKED", "accepted_calendar_canonical_lineage_mismatch"
            )
        actual_semantics.append((
            str(item["exchange"]),
            _as_date(item["cal_date"], field="canonical cal_date"),
            int(item["is_open"]),
            None if item["pretrade_date"] is None else _as_date(
                item["pretrade_date"], field="canonical pretrade_date"
            ),
            int(item["source_fragment_ordinal"]),
            int(item["source_row_ordinal"]),
            str(item["source_row_hash"]),
        ))
    if tuple(actual_semantics) != expected:
        raise CalendarTruthUnavailable(
            "BLOCKED", "accepted_calendar_canonical_content_or_lineage_mismatch"
        )
    if (
        validated.batch_id != pointer["batch_id"]
        or validated.row_count != int(pointer["row_count"])
        or validated.content_hash != pointer["content_hash"]
        or not _same_instant(
            validated.observed_at, pointer["observed_at"], field="observed_at"
        )
        or not _same_instant(
            validated.available_at, pointer["available_at"], field="available_at"
        )
    ):
        raise CalendarTruthUnavailable(
            "BLOCKED", "accepted_calendar_recomputed_evidence_mismatch"
        )

    all_dates = tuple(row[1] for row in actual_semantics)
    flags = tuple(row[2] for row in actual_semantics)
    if len(set(all_dates)) != len(all_dates):
        raise CalendarTruthUnavailable(
            "BLOCKED", "accepted_calendar_has_duplicate_dates"
        )
    open_dates = tuple(day for day, flag in zip(all_dates, flags, strict=True) if flag == 1)
    return all_dates, open_dates, flags


def open_calendar_truth(decision_time) -> CalendarTruth:
    """Open one proved calendar generation visible at ``decision_time``.

    There is deliberately no fallback to a legacy provider mirror or an
    open-day serve projection. Missing or contradictory accepted evidence is a
    blocking result, never an empty/healthy calendar.
    """

    cutoff = _as_aware_datetime(decision_time, field="decision_time")
    try:
        contract = _contract_from_live_registry()
    except CalendarTruthUnavailable:
        raise
    except Exception as exc:
        raise CalendarTruthUnavailable(
            "BLOCKED", f"live_calendar_contract_invalid: {str(exc)[:500]}"
        ) from exc

    conn = None
    transaction_open = False
    primary_error: BaseException | None = None
    try:
        conn = _open_live_tushare_raw_readonly()
        conn.execute("BEGIN TRANSACTION")
        transaction_open = True
        _require_formal_schema(conn)
        pointer = _select_pointer(conn, contract, cutoff)
        _load_and_verify_batch(conn, pointer, contract)
        try:
            validated = validate_landed_calendar_batch(
                conn,
                str(pointer["batch_id"]),
                contract,
                trusted_now=cutoff,
            )
        except CalendarAcceptanceError as exc:
            raise CalendarTruthUnavailable(
                "BLOCKED", f"accepted_calendar_landing_invalid: {str(exc)[:500]}"
            ) from exc
        except Exception as exc:
            raise CalendarTruthUnavailable(
                "BLOCKED", f"accepted_calendar_revalidation_failed: {str(exc)[:500]}"
            ) from exc
        all_dates, open_dates, flags = _load_and_verify_canonical(
            conn, pointer, contract, validated
        )
        if not all_dates:
            raise CalendarTruthUnavailable(
                "BLOCKED", "accepted_calendar_generation_is_empty"
            )
        coverage_start = _as_date(contract.coverage_start, field="coverage_start")
        if all_dates[0] != coverage_start:
            raise CalendarTruthUnavailable(
                "BLOCKED", "accepted_calendar_coverage_start_mismatch"
            )
        observed_at = _as_aware_datetime(pointer["observed_at"], field="observed_at")
        available_at = _as_aware_datetime(pointer["available_at"], field="available_at")
        accepted_at = _as_aware_datetime(pointer["accepted_at"], field="accepted_at")
        evidence = CalendarTruthEvidence(
            dataset_id=str(pointer["dataset_id"]),
            generation_id=str(pointer["partition_value"]),
            batch_id=str(pointer["batch_id"]),
            contract_version=str(pointer["contract_version"]),
            contract_hash=str(pointer["contract_hash"]),
            config_hash=str(pointer["config_hash"]),
            row_count=int(pointer["row_count"]),
            content_hash=str(pointer["content_hash"]),
            observed_at=observed_at,
            available_at=available_at,
            accepted_at=accepted_at,
            usable_at=max(available_at, accepted_at),
            coverage_start=all_dates[0],
            coverage_end=all_dates[-1],
        )
        return CalendarTruth(all_dates, open_dates, flags, evidence)
    except CalendarTruthUnavailable as exc:
        primary_error = exc
        raise
    except Exception as exc:
        primary_error = CalendarTruthUnavailable(
            "BLOCKED", f"calendar_truth_read_failed: {str(exc)[:500]}"
        )
        raise primary_error from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if conn is not None:
            cleanup_errors: list[BaseException] = []
            if transaction_open:
                try:
                    conn.execute("ROLLBACK")
                except Exception as exc:
                    cleanup_errors.append(exc)
            try:
                conn.close()
            except Exception as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                detail = "calendar_truth_cleanup_failed: " + "; ".join(
                    f"{type(error).__name__}: {error}" for error in cleanup_errors
                )
                if primary_error is not None:
                    primary_error.add_note(detail)
                else:
                    raise CalendarTruthUnavailable("BLOCKED", detail) from cleanup_errors[0]


__all__ = [
    "CalendarTruth",
    "CalendarTruthEvidence",
    "CalendarTruthUnavailable",
    "open_calendar_truth",
]
