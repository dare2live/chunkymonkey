"""Atomic landing and acceptance for one complete SSE calendar generation.

Tx-A durably records every provider fragment and row without normalising the
provider grain.  Tx-B re-reads that evidence, validates the complete generation,
and atomically appends canonical rows plus the accepted-generation pointer.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Callable

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_contract import verify_calendar_generation_contract
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    PROVIDER_FIELDS,
    verify_calendar_acceptance_schema,
)


class CalendarAcceptanceError(RuntimeError):
    """Calendar evidence cannot be read or changed safely."""


class CalendarValidationError(CalendarAcceptanceError):
    """A landed provider observation fails the calendar contract."""

    def __init__(self, code: str, detail: str):
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}")


@dataclass(frozen=True)
class CalendarFragmentCapture:
    fragment_ordinal: int
    request: Mapping[str, Any]
    rows: Iterable[Mapping[str, Any]]
    outcome: str
    completed_at: datetime | str
    error_type: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class CalendarLandingBatch:
    batch_id: str
    observed_at: datetime | str
    fragments: Iterable[CalendarFragmentCapture]


@dataclass(frozen=True)
class CalendarCanonicalRow:
    exchange: str
    cal_date: date
    is_open: int
    pretrade_date: date | None
    source_fragment_ordinal: int
    source_row_ordinal: int
    source_row_hash: str


@dataclass(frozen=True)
class ValidatedCalendarGeneration:
    batch_id: str
    observed_at: datetime
    available_at: datetime
    canonical_rows: tuple[CalendarCanonicalRow, ...]
    content_hash: str

    @property
    def generation_id(self) -> str:
        return self.batch_id

    @property
    def row_count(self) -> int:
        return len(self.canonical_rows)


@dataclass(frozen=True)
class CalendarAcceptanceOutcome:
    status: str
    batch_id: str
    generation_id: str
    row_count: int = 0
    content_hash: str | None = None
    rejection_code: str | None = None


@dataclass(frozen=True)
class _LandedFragment:
    ordinal: int
    request_offset: int
    request_limit: int
    request: dict[str, Any]
    outcome: str
    row_count: int
    fragment_hash: str
    completed_at: datetime
    error_type: str | None
    error_detail: str | None
    rows: tuple[tuple[int, dict[str, Any], str], ...]


_BATCH_FIELDS = (
    "status",
    "partition_value",
    "contract_version",
    "contract_hash",
    "config_hash",
    "writer_id",
    "source_name",
    "request_json",
    "fragment_outcomes_json",
    "expected_fragment_count",
    "completed_fragment_count",
    "failed_fragment_count",
    "landing_row_count",
    "payload_hash",
    "canonical_row_count",
    "canonical_hash",
    "observed_at",
    "available_at",
    "validated_at",
    "accepted_at",
    "rejection_code",
)

# Tests may monkeypatch this private seam.  Production callers cannot inject a
# completion flag, hash, reference, or transaction callback through public APIs.
_TEST_KILL_HOOK: Callable[[str], None] | None = None


def _kill(step: str) -> None:
    if _TEST_KILL_HOOK is not None:
        _TEST_KILL_HOOK(step)


def _now_utc() -> datetime:
    """Private trusted clock; public writers cannot inject historical time."""

    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _aware_datetime(value, "json datetime").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"unsupported JSON value type={type(value).__name__}")


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _aware_datetime(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CalendarAcceptanceError(
                f"invalid {field_name}={value!r}"
            ) from exc
    else:
        raise CalendarAcceptanceError(
            f"invalid {field_name} type={type(value).__name__}"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalendarAcceptanceError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _calendar_date(value: Any, field_name: str, *, nullable: bool = False) -> date | None:
    if nullable and (value is None or value == ""):
        return None
    if isinstance(value, datetime):
        raise CalendarValidationError("INVALID_DATE", f"{field_name} cannot be datetime")
    if isinstance(value, date):
        return value
    text = str(value or "")
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalendarValidationError(
            "INVALID_DATE", f"invalid {field_name}={value!r}"
        ) from exc


def _contract(contract: Any) -> Any:
    """Fail closed unless the contract is factory-owned and hash-attested."""

    try:
        verified = verify_calendar_generation_contract(contract)
    except (TypeError, ValueError) as exc:
        raise CalendarAcceptanceError(
            f"calendar contract wiring drift: {exc}"
        ) from exc
    if not callable(getattr(verified, "required_through", None)) or not callable(
        getattr(verified, "request_for_page", None)
    ):
        raise CalendarAcceptanceError("calendar contract derivation methods are missing")
    return verified


def _normalise_outcome(fragment: CalendarFragmentCapture) -> tuple[str, str | None, str | None]:
    token = str(fragment.outcome or "").strip().lower()
    error_type = str(fragment.error_type or "").strip() or None
    error_detail = str(fragment.error_detail or "").strip() or None
    if token in {"success", "completed", "empty"}:
        if token == "empty" and tuple(fragment.rows):
            raise CalendarAcceptanceError("empty calendar fragment cannot contain rows")
        if error_type is not None or error_detail is not None:
            raise CalendarAcceptanceError(
                "completed calendar fragment cannot contain error metadata"
            )
        return "COMPLETED", None, None
    failed_tokens = {
        "error",
        "failed",
        "permission",
        "captcha",
        "auth_error",
        "timeout",
        "connection_error",
        "schema_error",
    }
    if token not in failed_tokens:
        raise CalendarAcceptanceError(
            f"unknown calendar fragment outcome={fragment.outcome!r}"
        )
    return "FAILED", error_type or token.upper(), error_detail


def _fragment_hash(row_hashes: Iterable[str]) -> str:
    return _sha256(_stable_json(list(row_hashes)))


@dataclass(frozen=True)
class LandingStamp:
    """落地时刻的证据身份 —— 封印 (payload_hash) 唯一合法的输入。

    落地 (Tx-A) 时从活契约派生 (``from_contract``); 复验/验收时从 ingest_batch 行
    **自己的冻结值**派生 (``from_batch_row``)。它刻意是一个独立类型而不是契约对象:
    契约指纹算法一变 (2c4af4a08 把 source/api 移出 config_hash), accepted_partition /
    canonical_* 跟着当前契约重打, 而落地封印停在当时 —— 拿活契约去重算旧批次的封印
    必然 BATCH_EVIDENCE_MISMATCH (2026-09-02 活体故障的第三层)。
    见 docs/engineering_governance.md §15.6。
    """

    contract_version: str
    contract_hash: str
    config_hash: str
    writer_id: str
    source: str

    @classmethod
    def from_contract(cls, contract: Any) -> "LandingStamp":
        return cls(
            contract_version=str(contract.contract_version),
            contract_hash=str(contract.contract_hash),
            config_hash=str(contract.config_hash),
            writer_id=str(contract.writer_id),
            source=str(contract.source),
        )

    @classmethod
    def from_batch_row(cls, batch: Mapping[str, Any]) -> "LandingStamp":
        return cls(
            contract_version=str(batch["contract_version"]),
            contract_hash=str(batch["contract_hash"]),
            config_hash=str(batch["config_hash"]),
            writer_id=str(batch["writer_id"]),
            source=str(batch["source_name"]),
        )


def _batch_payload(
    *,
    batch_id: str,
    observed_at: datetime,
    stamp: LandingStamp,
    fragments: Iterable[_LandedFragment],
) -> dict[str, Any]:
    if not isinstance(stamp, LandingStamp):
        raise TypeError(
            "calendar payload seal must be computed from a LandingStamp "
            "(from_contract at landing, from_batch_row at revalidation), "
            f"never from {type(stamp).__name__}"
        )
    return {
        "dataset_id": DATASET_ID,
        "batch_id": batch_id,
        "partition_value": batch_id,
        "contract_version": stamp.contract_version,
        "contract_hash": stamp.contract_hash,
        "config_hash": stamp.config_hash,
        "writer_id": stamp.writer_id,
        "source_name": stamp.source,
        "observed_at": observed_at.isoformat(),
        "available_at": observed_at.isoformat(),
        "fragments": [
            {
                "fragment_ordinal": fragment.ordinal,
                "request_offset": fragment.request_offset,
                "request_limit": fragment.request_limit,
                "request": fragment.request,
                "outcome": fragment.outcome,
                "row_count": fragment.row_count,
                "fragment_hash": fragment.fragment_hash,
                "completed_at": fragment.completed_at.isoformat(),
                "error_type": fragment.error_type,
                "error_detail": fragment.error_detail,
                "rows": [
                    {
                        "row_ordinal": ordinal,
                        "payload": payload,
                        "row_hash": row_hash,
                    }
                    for ordinal, payload, row_hash in fragment.rows
                ],
            }
            for fragment in fragments
        ],
    }


def _batch_summary(fragments: Iterable[_LandedFragment]) -> tuple[str, str]:
    frozen = tuple(fragments)
    requests = [
        {"fragment_ordinal": item.ordinal, "request": item.request} for item in frozen
    ]
    outcomes = [
        {
            "fragment_ordinal": item.ordinal,
            "outcome": item.outcome,
            "row_count": item.row_count,
            "fragment_hash": item.fragment_hash,
            "completed_at": item.completed_at.isoformat(),
            "error_type": item.error_type,
            "error_detail": item.error_detail,
        }
        for item in frozen
    ]
    return _stable_json(requests), _stable_json(outcomes)


def _rollback(conn, primary_error: BaseException) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception as rollback_error:  # pragma: no cover - broken connection only
        primary_error.add_note(
            "ROLLBACK failed; connection state unknown: "
            f"{type(rollback_error).__name__}: {rollback_error}"
        )


def _prepare_landed_fragments(
    batch: CalendarLandingBatch,
) -> tuple[str, datetime, list[_LandedFragment]]:
    """Validate landing input shape before any schema DDL or DB I/O."""

    batch_id = str(batch.batch_id or "").strip()
    if not batch_id:
        raise CalendarAcceptanceError("batch_id must be non-empty")
    observed_at = _aware_datetime(batch.observed_at, "observed_at")
    captures = tuple(batch.fragments)
    if not captures:
        raise CalendarAcceptanceError("calendar landing requires at least one fragment")

    fragments: list[_LandedFragment] = []
    for capture in captures:
        if isinstance(capture.fragment_ordinal, bool) or not isinstance(
            capture.fragment_ordinal, int
        ):
            raise CalendarAcceptanceError("fragment_ordinal must be an integer")
        request = dict(capture.request)
        offset = request.get("offset")
        limit = request.get("limit")
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise CalendarAcceptanceError("fragment request offset must be an integer")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise CalendarAcceptanceError("fragment request limit must be an integer")
        raw_rows = tuple(dict(row) for row in capture.rows)
        # Outcome normalisation must not consume a one-shot iterable a second time.
        capture_for_outcome = CalendarFragmentCapture(
            capture.fragment_ordinal,
            request,
            raw_rows,
            capture.outcome,
            capture.completed_at,
            capture.error_type,
            capture.error_detail,
        )
        outcome, error_type, error_detail = _normalise_outcome(capture_for_outcome)
        rows: list[tuple[int, dict[str, Any], str]] = []
        for row_ordinal, row in enumerate(raw_rows):
            row_hash = _sha256(_stable_json(row))
            rows.append((row_ordinal, row, row_hash))
        completed_at = _aware_datetime(capture.completed_at, "completed_at")
        fragments.append(
            _LandedFragment(
                ordinal=capture.fragment_ordinal,
                request_offset=offset,
                request_limit=limit,
                request=request,
                outcome=outcome,
                row_count=len(rows),
                fragment_hash=_fragment_hash(row_hash for _, _, row_hash in rows),
                completed_at=completed_at,
                error_type=error_type,
                error_detail=error_detail,
                rows=tuple(rows),
            )
        )

    completed_boundary = max(item.completed_at for item in fragments)
    if observed_at != completed_boundary:
        raise CalendarAcceptanceError(
            "observed_at must equal max fragment completed_at; "
            f"observed_at={observed_at.isoformat()} "
            f"completed_at={completed_boundary.isoformat()}"
        )
    return batch_id, observed_at, fragments


def land_calendar_batch(conn, batch: CalendarLandingBatch, contract: Any) -> str:
    """Tx-A: atomically preserve every captured fragment and raw provider row.

    Input validation runs before schema verification.  Writers never bootstrap
    DDL; callers must use ``bootstrap_calendar_acceptance_schema`` / ensure.
    """

    contract = _contract(contract)
    batch_id, observed_at, fragments = _prepare_landed_fragments(batch)
    verify_calendar_acceptance_schema(conn)

    request_json, outcomes_json = _batch_summary(fragments)
    payload_hash = _sha256(
        _stable_json(
            _batch_payload(
                batch_id=batch_id,
                observed_at=observed_at,
                stamp=LandingStamp.from_contract(contract),
                fragments=fragments,
            )
        )
    )
    existing = conn.execute(
        f"SELECT dataset_id FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?", [batch_id]
    ).fetchone()
    if existing is not None:
        raise CalendarAcceptanceError(
            f"batch_id={batch_id!r} already exists; Tx-A is append-only"
        )

    landing_rows = [
        (batch_id, item.ordinal, row_ordinal, _stable_json(payload), row_hash)
        for item in fragments
        for row_ordinal, payload, row_hash in item.rows
    ]
    now = _now_utc()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"""
            INSERT INTO {INGEST_BATCH_TABLE} (
                batch_id, dataset_id, contract_version, contract_hash, config_hash,
                writer_id, partition_value, source_name, status, request_json,
                fragment_outcomes_json, expected_fragment_count,
                completed_fragment_count, failed_fragment_count, landing_row_count,
                payload_hash, observed_at, available_at, landed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'LANDED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                batch_id,
                DATASET_ID,
                str(contract.contract_version),
                str(contract.contract_hash),
                str(contract.config_hash),
                str(contract.writer_id),
                batch_id,
                str(contract.source),
                request_json,
                outcomes_json,
                len(fragments),
                sum(item.outcome == "COMPLETED" for item in fragments),
                sum(item.outcome == "FAILED" for item in fragments),
                len(landing_rows),
                payload_hash,
                observed_at,
                observed_at,
                now,
            ],
        )
        _kill("tx_a_after_batch")
        conn.executemany(
            f"""
            INSERT INTO {FRAGMENT_TABLE} (
                batch_id, fragment_ordinal, request_offset, request_limit,
                request_json, outcome, row_count, fragment_hash, completed_at,
                error_type, error_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    batch_id,
                    item.ordinal,
                    item.request_offset,
                    item.request_limit,
                    _stable_json(item.request),
                    item.outcome,
                    item.row_count,
                    item.fragment_hash,
                    item.completed_at,
                    item.error_type,
                    item.error_detail,
                )
                for item in fragments
            ],
        )
        _kill("tx_a_after_fragments")
        if landing_rows:
            conn.executemany(
                f"INSERT INTO {LANDING_TABLE} VALUES (?, ?, ?, ?, ?)", landing_rows
            )
        _kill("tx_a_after_rows")
        conn.execute("COMMIT")
    except Exception as exc:
        _rollback(conn, exc)
        raise
    return batch_id


__all__ = [
    "CalendarAcceptanceError",
    "CalendarAcceptanceOutcome",
    "CalendarCanonicalRow",
    "CalendarFragmentCapture",
    "CalendarLandingBatch",
    "CalendarValidationError",
    "ValidatedCalendarGeneration",
    "land_calendar_batch",
]
