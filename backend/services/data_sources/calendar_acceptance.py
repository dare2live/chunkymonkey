"""Validate landed SSE calendar evidence and atomically publish Tx-B."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Callable

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.calendar_landing import (
    _BATCH_FIELDS,
    _LandedFragment,
    _aware_datetime,
    _batch_payload,
    _batch_summary,
    _calendar_date,
    _contract,
    _fragment_hash,
    _sha256,
    _stable_json,
    CalendarAcceptanceError,
    CalendarAcceptanceOutcome,
    CalendarCanonicalRow,
    CalendarFragmentCapture,
    CalendarLandingBatch,
    CalendarValidationError,
    LandingStamp,
    ValidatedCalendarGeneration,
    land_calendar_batch,
)
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    PROVIDER_FIELDS,
    verify_calendar_acceptance_schema,
)

# Tx-B has its own private failure-injection seam. Tx-A owns the corresponding
# seam in calendar_landing; neither seam is part of the public writer API.
_TEST_KILL_HOOK: Callable[[str], None] | None = None


def _kill(step: str) -> None:
    if _TEST_KILL_HOOK is not None:
        _TEST_KILL_HOOK(step)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _rollback(conn, primary_error: BaseException) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception as rollback_error:  # pragma: no cover - broken connection only
        primary_error.add_note(
            "ROLLBACK failed; connection state unknown: "
            f"{type(rollback_error).__name__}: {rollback_error}"
        )


def _load_batch(conn, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT status, partition_value, contract_version, contract_hash, config_hash,
               writer_id, source_name, request_json, fragment_outcomes_json,
               expected_fragment_count, completed_fragment_count,
               failed_fragment_count, landing_row_count, payload_hash,
               canonical_row_count, canonical_hash, observed_at, available_at,
               validated_at, accepted_at, rejection_code
          FROM {INGEST_BATCH_TABLE}
         WHERE batch_id = ? AND dataset_id = ?
        """,
        [batch_id, DATASET_ID],
    ).fetchone()
    if row is None:
        raise CalendarAcceptanceError(f"unknown calendar batch_id={batch_id!r}")
    return dict(zip(_BATCH_FIELDS, row, strict=True))


def _loaded_fragments(conn, batch_id: str) -> tuple[_LandedFragment, ...]:
    fragment_rows = conn.execute(
        f"""
        SELECT fragment_ordinal, request_offset, request_limit, request_json,
               outcome, row_count, fragment_hash, completed_at,
               error_type, error_detail
          FROM {FRAGMENT_TABLE}
         WHERE batch_id = ? ORDER BY fragment_ordinal
        """,
        [batch_id],
    ).fetchall()
    raw_rows = conn.execute(
        f"""
        SELECT fragment_ordinal, row_ordinal, payload_json, row_hash
          FROM {LANDING_TABLE}
         WHERE batch_id = ? ORDER BY fragment_ordinal, row_ordinal
        """,
        [batch_id],
    ).fetchall()
    grouped: dict[int, list[tuple[int, dict[str, Any], str]]] = {}
    for fragment_ordinal, row_ordinal, payload_json, row_hash in raw_rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CalendarValidationError(
                "LANDING_JSON", f"invalid landing JSON fragment={fragment_ordinal}"
            ) from exc
        if not isinstance(payload, dict):
            raise CalendarValidationError(
                "LANDING_JSON", "calendar provider row must be a JSON object"
            )
        grouped.setdefault(int(fragment_ordinal), []).append(
            (int(row_ordinal), payload, str(row_hash))
        )
    fragments: list[_LandedFragment] = []
    for row in fragment_rows:
        try:
            request = json.loads(row[3])
        except (TypeError, json.JSONDecodeError) as exc:
            raise CalendarValidationError(
                "REQUEST_JSON", f"invalid request JSON fragment={row[0]}"
            ) from exc
        if not isinstance(request, dict):
            raise CalendarValidationError("REQUEST_JSON", "request must be a JSON object")
        fragments.append(
            _LandedFragment(
                ordinal=int(row[0]),
                request_offset=int(row[1]),
                request_limit=int(row[2]),
                request=request,
                outcome=str(row[4]),
                row_count=int(row[5]),
                fragment_hash=str(row[6]),
                completed_at=_aware_datetime(row[7], "completed_at"),
                error_type=str(row[8]) if row[8] is not None else None,
                error_detail=str(row[9]) if row[9] is not None else None,
                rows=tuple(grouped.pop(int(row[0]), [])),
            )
        )
    if grouped:
        raise CalendarValidationError(
            "ORPHAN_LANDING_ROWS", f"rows reference missing fragments={sorted(grouped)}"
        )
    return tuple(fragments)


def _validate_batch_identity_and_time(
    batch_id: str,
    batch: dict[str, Any],
    contract: Any,
    trusted_now: datetime,
) -> datetime:
    # 2026-09-02: 只比**声明身份** (partition/batch 同一、contract_version、writer_id)。
    # contract_hash / config_hash / source_name 是落地时刻的冻结证据 (封印派生), 指纹算法
    # 一变或换源之后必然与现算契约不等, 与之比相等 = 把正确状态判成 CONTRACT_DRIFT
    # (2026-09-02 活体故障第二层)。"批次有没有被动过"由下方封印重算 (按批次自己的
    # LandingStamp) 回答, "指针是不是当前契约的"由 reader 的指针选取回答。
    # 见 docs/engineering_governance.md §15.6。
    expected_wiring = (
        batch_id,
        str(contract.contract_version),
        str(contract.writer_id),
    )
    actual_wiring = (
        str(batch["partition_value"]),
        str(batch["contract_version"]),
        str(batch["writer_id"]),
    )
    if actual_wiring != expected_wiring:
        raise CalendarValidationError(
            "CONTRACT_DRIFT", f"landed={actual_wiring!r} current={expected_wiring!r}"
        )
    observed_at = _aware_datetime(batch["observed_at"], "observed_at")
    available_at = _aware_datetime(batch["available_at"], "available_at")
    if observed_at > trusted_now:
        raise CalendarValidationError(
            "FUTURE_OBSERVATION",
            f"observed_at={observed_at.isoformat()} trusted_now={trusted_now.isoformat()}",
        )
    if available_at != observed_at:
        raise CalendarValidationError(
            "AVAILABILITY_DRIFT", "available_at must equal response observed_at"
        )
    return observed_at


def _validate_fragment_chain(
    batch_id: str,
    batch: dict[str, Any],
    fragments: tuple[_LandedFragment, ...],
    contract: Any,
    observed_at: datetime,
) -> None:
    if not fragments:
        raise CalendarValidationError("MISSING_FRAGMENTS", "no fragment evidence")
    completed_boundary = max(item.completed_at for item in fragments)
    if observed_at != completed_boundary:
        raise CalendarValidationError(
            "OBSERVATION_BOUNDARY",
            "observed_at must equal max fragment completed_at",
        )
    ordinals = [item.ordinal for item in fragments]
    if ordinals != list(range(len(fragments))):
        raise CalendarValidationError(
            "FRAGMENT_SEQUENCE", f"actual fragment ordinals={ordinals!r}"
        )
    limit = int(contract.page_limit)
    total_rows = 0
    for index, fragment in enumerate(fragments):
        expected_offset = index * limit
        expected_request = dict(contract.request_for_page(observed_at, expected_offset))
        if (
            fragment.request_offset != expected_offset
            or fragment.request_limit != limit
            or fragment.request != expected_request
        ):
            raise CalendarValidationError(
                "REQUEST_MISMATCH", f"fragment={index} request is not contract-derived"
            )
        if fragment.outcome != "COMPLETED":
            error = str(fragment.error_type or "").lower()
            code = (
                "PROVIDER_CAPTCHA"
                if "captcha" in error
                else "PROVIDER_PERMISSION"
                if "permission" in error or "auth" in error
                else "FRAGMENT_FAILED"
            )
            raise CalendarValidationError(code, f"fragment={index} error={fragment.error_type}")
        if fragment.error_type is not None or fragment.error_detail is not None:
            raise CalendarValidationError(
                "FRAGMENT_EVIDENCE", f"completed fragment={index} carries error metadata"
            )
        row_ordinals = [row[0] for row in fragment.rows]
        if row_ordinals != list(range(len(fragment.rows))):
            raise CalendarValidationError(
                "ROW_SEQUENCE", f"fragment={index} row ordinals={row_ordinals!r}"
            )
        recomputed_hashes: list[str] = []
        for _row_ordinal, payload, stored_hash in fragment.rows:
            actual_hash = _sha256(_stable_json(payload))
            if stored_hash != actual_hash:
                raise CalendarValidationError(
                    "ROW_HASH_MISMATCH", f"fragment={index} landing row hash drift"
                )
            recomputed_hashes.append(actual_hash)
        if fragment.row_count != len(fragment.rows) or fragment.fragment_hash != _fragment_hash(
            recomputed_hashes
        ):
            raise CalendarValidationError(
                "FRAGMENT_EVIDENCE", f"fragment={index} count/hash drift"
            )
        if index < len(fragments) - 1 and fragment.row_count != limit:
            raise CalendarValidationError(
                "PAGE_SIZE_MISMATCH", f"non-terminal fragment={index} is not full"
            )
        if index == len(fragments) - 1 and fragment.row_count >= limit:
            raise CalendarValidationError(
                "MISSING_TERMINAL_FRAGMENT", "last page must be shorter than page_limit"
            )
        total_rows += fragment.row_count
    if total_rows <= 0:
        raise CalendarValidationError("ZERO_ROWS", "calendar generation has no rows")

    request_json, outcomes_json = _batch_summary(fragments)
    # 封印按批次**自己**的落地戳重算 (不是现算契约): 证明的是"这行没被动过", 不是
    # "这行是当前契约落的" —— 后者在重打指纹之后是个必假的问题。
    recomputed_payload_hash = _sha256(
        _stable_json(
            _batch_payload(
                batch_id=batch_id,
                observed_at=observed_at,
                stamp=LandingStamp.from_batch_row(batch),
                fragments=fragments,
            )
        )
    )
    evidence_actual = (
        str(batch["request_json"]),
        str(batch["fragment_outcomes_json"]),
        int(batch["expected_fragment_count"]),
        int(batch["completed_fragment_count"]),
        int(batch["failed_fragment_count"]),
        int(batch["landing_row_count"]),
        str(batch["payload_hash"]),
    )
    evidence_expected = (
        request_json,
        outcomes_json,
        len(fragments),
        len(fragments),
        0,
        total_rows,
        recomputed_payload_hash,
    )
    if evidence_actual != evidence_expected:
        raise CalendarValidationError(
            "BATCH_EVIDENCE_MISMATCH", "batch summary/hash does not match durable fragments"
        )


def _canonicalize_provider_rows(
    fragments: tuple[_LandedFragment, ...],
) -> tuple[CalendarCanonicalRow, ...]:
    canonical: list[CalendarCanonicalRow] = []
    grains: set[tuple[str, date]] = set()
    for fragment in fragments:
        for row_ordinal, payload, row_hash in fragment.rows:
            expected_fields = set(PROVIDER_FIELDS)
            if set(payload) != expected_fields:
                raise CalendarValidationError(
                    "PROVIDER_FIELDS",
                    f"fields must be exact: missing={sorted(expected_fields - set(payload))} "
                    f"extra={sorted(set(payload) - expected_fields)}",
                )
            exchange = payload["exchange"]
            if exchange != "SSE":
                raise CalendarValidationError(
                    "WRONG_EXCHANGE", f"calendar row exchange={exchange!r}"
                )
            cal_date = _calendar_date(payload["cal_date"], "cal_date")
            assert cal_date is not None
            raw_is_open = payload["is_open"]
            if isinstance(raw_is_open, bool) or raw_is_open not in (0, 1, "0", "1"):
                raise CalendarValidationError(
                    "INVALID_IS_OPEN", f"is_open={raw_is_open!r}"
                )
            is_open = int(raw_is_open)
            pretrade_date = _calendar_date(
                payload["pretrade_date"], "pretrade_date", nullable=True
            )
            grain = (exchange, cal_date)
            if grain in grains:
                raise CalendarValidationError(
                    "DUPLICATE_GRAIN", f"duplicate calendar grain={grain!r}"
                )
            grains.add(grain)
            canonical.append(
                CalendarCanonicalRow(
                    exchange=exchange,
                    cal_date=cal_date,
                    is_open=is_open,
                    pretrade_date=pretrade_date,
                    source_fragment_ordinal=fragment.ordinal,
                    source_row_ordinal=row_ordinal,
                    source_row_hash=row_hash,
                )
            )
    return tuple(canonical)


def _validate_generation_coverage(
    canonical: tuple[CalendarCanonicalRow, ...],
    contract: Any,
    observed_at: datetime,
) -> tuple[CalendarCanonicalRow, ...]:
    coverage_start = _calendar_date(contract.coverage_start, "coverage_start")
    required_through = contract.required_through(observed_at)
    assert coverage_start is not None
    if not isinstance(required_through, date) or isinstance(required_through, datetime):
        raise CalendarValidationError(
            "CONTRACT_DATE", "required_through must return a date"
        )
    expected_count = (required_through - coverage_start).days + 1
    if expected_count <= 0:
        raise CalendarValidationError("CONTRACT_DATE", "invalid calendar coverage range")
    ordered = sorted(canonical, key=lambda row: row.cal_date)
    expected_dates = [coverage_start + timedelta(days=index) for index in range(expected_count)]
    actual_dates = [row.cal_date for row in ordered]
    if actual_dates != expected_dates:
        missing = sorted(set(expected_dates) - set(actual_dates))
        extra = sorted(set(actual_dates) - set(expected_dates))
        raise CalendarValidationError(
            "COVERAGE_MISMATCH",
            f"missing={missing[:3]!r} extra={extra[:3]!r} counts=({len(missing)},{len(extra)})",
        )
    if not any(row.is_open == 1 for row in ordered) or not any(
        row.is_open == 0 for row in ordered
    ):
        raise CalendarValidationError(
            "OPEN_CLOSED_COMPLETENESS", "generation must contain open and closed dates"
        )
    previous_open: date | None = None
    for index, row in enumerate(ordered):
        if row.pretrade_date != previous_open:
            raise CalendarValidationError(
                "PRETRADE_CHAIN",
                f"date={row.cal_date} actual={row.pretrade_date} expected={previous_open}",
            )
        if row.is_open:
            previous_open = row.cal_date
        if index == 0 and row.pretrade_date is not None:  # explicit first-row invariant
            raise CalendarValidationError("PRETRADE_CHAIN", "first pretrade_date must be null")
    return tuple(ordered)


def _validate_evidence(
    batch_id: str,
    batch: dict[str, Any],
    fragments: tuple[_LandedFragment, ...],
    contract: Any,
    trusted_now: datetime,
) -> tuple[CalendarCanonicalRow, ...]:
    observed_at = _validate_batch_identity_and_time(
        batch_id, batch, contract, trusted_now
    )
    _validate_fragment_chain(batch_id, batch, fragments, contract, observed_at)
    canonical = _canonicalize_provider_rows(fragments)
    return _validate_generation_coverage(canonical, contract, observed_at)


def _content_hash(
    validated_rows: tuple[CalendarCanonicalRow, ...],
) -> str:
    payload = [
        {
            "exchange": row.exchange,
            "cal_date": row.cal_date.isoformat(),
            "is_open": row.is_open,
            "pretrade_date": row.pretrade_date.isoformat()
            if row.pretrade_date
            else None,
        }
        for row in validated_rows
    ]
    return _sha256(_stable_json(payload))


def validate_landed_calendar_batch(
    conn,
    batch_id: str,
    contract: Any,
    trusted_now: datetime | str,
) -> ValidatedCalendarGeneration:
    """Read-only proof of LANDED or ACCEPTED evidence; performs no DDL or writes."""

    contract = _contract(contract)
    verify_calendar_acceptance_schema(conn)
    batch_id = str(batch_id or "").strip()
    batch = _load_batch(conn, batch_id)
    status = str(batch["status"])
    if status not in {"LANDED", "ACCEPTED"}:
        raise CalendarAcceptanceError(
            f"calendar batch {batch_id!r} cannot be validated in status={status!r}"
        )
    trusted = _aware_datetime(trusted_now, "trusted_now")
    fragments = _loaded_fragments(conn, batch_id)
    canonical_rows = _validate_evidence(batch_id, batch, fragments, contract, trusted)
    observed_at = _aware_datetime(batch["observed_at"], "observed_at")
    content_hash = _content_hash(canonical_rows)
    if status == "LANDED" and (
        batch["canonical_row_count"] is not None
        or batch["canonical_hash"] is not None
        or batch["accepted_at"] is not None
    ):
        raise CalendarValidationError(
            "PREMATURE_PUBLICATION_EVIDENCE", "LANDED batch carries accepted fields"
        )
    if status == "ACCEPTED" and (
        int(batch["canonical_row_count"] or -1) != len(canonical_rows)
        or str(batch["canonical_hash"] or "") != content_hash
        or batch["validated_at"] is None
        or batch["accepted_at"] is None
    ):
        raise CalendarValidationError(
            "ACCEPTED_BATCH_EVIDENCE", "accepted batch count/hash/timestamp drift"
        )
    return ValidatedCalendarGeneration(
        batch_id=batch_id,
        observed_at=observed_at,
        available_at=observed_at,
        canonical_rows=canonical_rows,
        content_hash=content_hash,
    )


def _reject(
    conn,
    batch_id: str,
    error: CalendarValidationError,
    validated_at: datetime,
) -> CalendarAcceptanceOutcome:
    conn.execute(
        f"""
        UPDATE {INGEST_BATCH_TABLE}
           SET status = 'REJECTED', validated_at = ?, rejection_code = ?,
               rejection_detail = ?
         WHERE batch_id = ? AND dataset_id = ? AND status = 'LANDED'
        """,
        [validated_at, error.code, error.detail[:1000], batch_id, DATASET_ID],
    )
    _kill("tx_b_after_rejection")
    current = conn.execute(
        f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?", [batch_id]
    ).fetchone()
    if current is None or current[0] != "REJECTED":
        raise CalendarAcceptanceError("calendar rejection lost its LANDED owner")
    return CalendarAcceptanceOutcome(
        "REJECTED", batch_id, batch_id, rejection_code=error.code
    )


def _prove_published(
    conn,
    validated: ValidatedCalendarGeneration,
    contract: Any,
) -> CalendarAcceptanceOutcome:
    pointer = conn.execute(
        f"""
        SELECT batch_id, contract_version, contract_hash, config_hash, row_count,
               content_hash, observed_at, available_at, accepted_at
          FROM {ACCEPTED_TABLE}
         WHERE dataset_id = ? AND partition_value = ?
        """,
        [DATASET_ID, validated.batch_id],
    ).fetchone()
    if pointer is None:
        raise CalendarAcceptanceError("accepted calendar batch has no accepted pointer")
    expected_prefix = (
        validated.batch_id,
        str(contract.contract_version),
        str(contract.contract_hash),
        str(contract.config_hash),
        validated.row_count,
        validated.content_hash,
    )
    if tuple(pointer)[:6] != expected_prefix:
        raise CalendarAcceptanceError("accepted calendar pointer evidence drift")
    if (
        _aware_datetime(pointer[6], "pointer observed_at") != validated.observed_at
        or _aware_datetime(pointer[7], "pointer available_at") != validated.available_at
        or pointer[8] is None
    ):
        raise CalendarAcceptanceError("accepted calendar pointer time evidence drift")
    batch = _load_batch(conn, validated.batch_id)
    pointer_accepted_at = _aware_datetime(pointer[8], "pointer accepted_at")
    if (
        batch["accepted_at"] is None
        or pointer_accepted_at
        != _aware_datetime(batch["accepted_at"], "batch accepted_at")
        or pointer_accepted_at < validated.available_at
    ):
        raise CalendarAcceptanceError("accepted calendar acceptance-time evidence drift")

    stored = conn.execute(
        f"""
        SELECT exchange, cal_date, is_open, pretrade_date,
               source_fragment_ordinal, source_row_ordinal, source_row_hash,
               available_at, contract_version, config_hash, built_at
          FROM {CANONICAL_TABLE}
         WHERE generation_id = ? ORDER BY cal_date, exchange
        """,
        [validated.batch_id],
    ).fetchall()
    expected = [
        (
            row.exchange,
            row.cal_date,
            row.is_open,
            row.pretrade_date,
            row.source_fragment_ordinal,
            row.source_row_ordinal,
            row.source_row_hash,
            validated.available_at,
            str(contract.contract_version),
            str(contract.config_hash),
            pointer_accepted_at,
        )
        for row in validated.canonical_rows
    ]
    if [tuple(row) for row in stored] != expected:
        raise CalendarAcceptanceError("accepted canonical rows/lineage drift")
    return CalendarAcceptanceOutcome(
        "ACCEPTED",
        validated.batch_id,
        validated.batch_id,
        validated.row_count,
        validated.content_hash,
    )


def accept_calendar_batch(
    conn,
    batch_id: str,
    contract: Any,
) -> CalendarAcceptanceOutcome:
    """Tx-B: validate landing, then atomically append canonical and accepted facts."""

    contract = _contract(contract)
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise CalendarAcceptanceError("batch_id must be non-empty")
    verify_calendar_acceptance_schema(conn)
    effective_now = _aware_datetime(_now_utc(), "trusted_now")
    effective_accepted = effective_now
    conn.execute("BEGIN TRANSACTION")
    try:
        batch = _load_batch(conn, batch_id)
        status = str(batch["status"])
        if status == "REJECTED":
            outcome = CalendarAcceptanceOutcome(
                "REJECTED",
                batch_id,
                batch_id,
                rejection_code=str(batch["rejection_code"] or "UNKNOWN"),
            )
            conn.execute("COMMIT")
            return outcome
        if status == "ACCEPTED":
            validated = validate_landed_calendar_batch(
                conn, batch_id, contract, effective_now
            )
            outcome = _prove_published(conn, validated, contract)
            conn.execute("COMMIT")
            return outcome
        if status != "LANDED":
            raise CalendarAcceptanceError(
                f"unsupported calendar batch status={status!r}"
            )
        try:
            validated = validate_landed_calendar_batch(
                conn, batch_id, contract, effective_now
            )
        except CalendarValidationError as error:
            outcome = _reject(conn, batch_id, error, effective_now)
            conn.execute("COMMIT")
            return outcome
        _kill("tx_b_after_validation")
        if effective_accepted < validated.available_at:
            raise CalendarAcceptanceError("accepted_at cannot precede available_at")

        rows = [
            (
                batch_id,
                row.exchange,
                row.cal_date,
                row.is_open,
                row.pretrade_date,
                row.source_fragment_ordinal,
                row.source_row_ordinal,
                row.source_row_hash,
                validated.available_at,
                str(contract.contract_version),
                str(contract.config_hash),
                effective_accepted,
            )
            for row in validated.canonical_rows
        ]
        conn.executemany(
            f"""
            INSERT INTO {CANONICAL_TABLE} (
                generation_id, exchange, cal_date, is_open, pretrade_date,
                source_fragment_ordinal, source_row_ordinal, source_row_hash,
                available_at, contract_version, config_hash, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        _kill("tx_b_after_canonical")
        conn.execute(
            f"""
            INSERT INTO {ACCEPTED_TABLE} (
                dataset_id, partition_value, batch_id, contract_version,
                contract_hash, config_hash, row_count, content_hash,
                observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                DATASET_ID,
                batch_id,
                batch_id,
                str(contract.contract_version),
                str(contract.contract_hash),
                str(contract.config_hash),
                validated.row_count,
                validated.content_hash,
                validated.observed_at,
                validated.available_at,
                effective_accepted,
            ],
        )
        _kill("tx_b_after_pointer")
        conn.execute(
            f"""
            UPDATE {INGEST_BATCH_TABLE}
               SET status = 'ACCEPTED', canonical_row_count = ?, canonical_hash = ?,
                   validated_at = ?, accepted_at = ?, rejection_code = NULL,
                   rejection_detail = NULL
             WHERE batch_id = ? AND dataset_id = ? AND status = 'LANDED'
            """,
            [
                validated.row_count,
                validated.content_hash,
                effective_now,
                effective_accepted,
                batch_id,
                DATASET_ID,
            ],
        )
        _kill("tx_b_after_batch")
        current = conn.execute(
            f"SELECT status FROM {INGEST_BATCH_TABLE} WHERE batch_id = ?", [batch_id]
        ).fetchone()
        if current is None or current[0] != "ACCEPTED":
            raise CalendarAcceptanceError("calendar acceptance lost its LANDED owner")
        conn.execute("COMMIT")
    except Exception as exc:
        _rollback(conn, exc)
        raise
    return CalendarAcceptanceOutcome(
        "ACCEPTED",
        batch_id,
        batch_id,
        validated.row_count,
        validated.content_hash,
    )


__all__ = [
    "CalendarAcceptanceError",
    "CalendarAcceptanceOutcome",
    "CalendarCanonicalRow",
    "CalendarFragmentCapture",
    "CalendarLandingBatch",
    "CalendarValidationError",
    "ValidatedCalendarGeneration",
    "accept_calendar_batch",
    "land_calendar_batch",
    "validate_landed_calendar_batch",
]
