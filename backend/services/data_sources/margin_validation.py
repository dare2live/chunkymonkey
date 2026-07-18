"""Immutable evidence hashing and semantic validation for TuShare margin."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from services.data_sources.availability import (
    SyncWindowError,
    TradingSessionIndex,
    prepare_trading_session_index,
    publication_cutoff,
)
from services.data_sources.margin_schema import (
    LANDING_TABLE,
    MARGIN_FIELDS,
    NON_NULL_NUMERIC_FIELDS,
    NUMERIC_FIELDS,
)


class MarginValidationError(ValueError):
    """A landed batch cannot satisfy the current margin contract."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def load_margin_publication_sessions(
    partition: str, *, limit: int | None = 2
) -> tuple[str, ...]:
    """Read publication sessions from the Tier0 calendar truth."""

    from services.data_access.resolver import connect_ro

    calendar = connect_ro("reference")
    try:
        limit_sql = "" if limit is None else f" LIMIT {int(limit)}"
        rows = calendar.execute(
            """
            SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS trade_date
              FROM dim_trading_calendar
             WHERE is_trading = 1
               AND replace(CAST(trade_date AS VARCHAR), '-', '') >= ?
             ORDER BY 1
            """
            + limit_sql,
            [partition],
        ).fetchall()
    finally:
        calendar.close()
    return tuple(str(row[0]) for row in rows)


def _validation_datetime(value: datetime | str, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarginValidationError(
                "INVALID_PUBLICATION_TIME", f"invalid {field}={value!r}"
            ) from exc
    else:
        raise MarginValidationError(
            "INVALID_PUBLICATION_TIME",
            f"invalid {field} type={type(value).__name__}",
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarginValidationError(
            "INVALID_PUBLICATION_TIME", f"{field} must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def validate_margin_publication_time(
    contract,
    partition: str,
    available_at: datetime | str,
    *,
    trading_day_values: tuple[str, ...] | TradingSessionIndex | None = None,
) -> datetime:
    """Prove one margin observation was not published before its typed cutoff."""

    observed = _validation_datetime(available_at, "available_at")
    try:
        cutoff = publication_cutoff(
            contract.availability_policy,
            partition_value=partition,
            trading_day_values=(
                trading_day_values
                if trading_day_values is not None
                else load_margin_publication_sessions(partition)
            ),
        ).astimezone(timezone.utc)
    except SyncWindowError as exc:
        raise MarginValidationError(
            "PUBLICATION_CALENDAR_UNPROVEN", str(exc)
        ) from exc
    if observed < cutoff:
        raise MarginValidationError(
            "PREMATURE_PUBLICATION",
            f"available_at={observed.isoformat()} precedes policy cutoff={cutoff.isoformat()}",
        )
    return observed


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _batch_payload_hash(
    *,
    partition: str,
    source: str,
    contract_version: str,
    contract_hash: str,
    config_hash: str,
    observed_at: datetime,
    available_at: datetime,
    requests: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    row_signatures: list[str],
) -> str:
    return _sha256(_stable_json({
        "partition_value": partition,
        "source": source,
        "contract_version": contract_version,
        "contract_hash": contract_hash,
        "config_hash": config_hash,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "available_at": available_at.astimezone(timezone.utc).isoformat(),
        "requests": requests,
        "fragment_outcomes": outcomes,
        "row_signatures": row_signatures,
    }))


def _decimal(value: Any, field: str, *, nullable: bool) -> Decimal | None:
    if value is None:
        if nullable:
            return None
        raise MarginValidationError("INVALID_NUMERIC", f"{field} is required")
    if isinstance(value, bool):
        raise MarginValidationError("INVALID_NUMERIC", f"{field} bool is not numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarginValidationError("INVALID_NUMERIC", f"{field}={value!r}") from exc
    if not number.is_finite() or number < 0:
        raise MarginValidationError("INVALID_NUMERIC", f"{field}={value!r}")
    return number


def _candidate_rows(
    conn,
    batch_id: str,
    partition: str,
    contract,
    batch: dict[str, Any],
    *,
    landed_rows: Iterable[tuple[Any, ...]] | None = None,
) -> list[dict[str, Any]]:
    try:
        requests = json.loads(batch["request_json"])
        outcomes = json.loads(batch["fragment_outcomes_json"])
    except (TypeError, ValueError) as exc:
        raise MarginValidationError(
            "LANDING_ENVELOPE_DRIFT", "batch request/outcome JSON is not parseable"
        ) from exc
    if not isinstance(requests, list) or not isinstance(outcomes, list):
        raise MarginValidationError(
            "LANDING_ENVELOPE_DRIFT", "batch request/outcome evidence must be lists"
        )
    if len(requests) != len(outcomes):
        raise MarginValidationError(
            "LANDING_ENVELOPE_DRIFT",
            f"request_count={len(requests)} outcome_count={len(outcomes)}",
        )

    expected = set(contract.batch_completeness.required_groups_for(partition))
    normalized_outcomes: list[dict[str, Any]] = []
    observed_groups: list[str] = []
    for ordinal, (request, outcome) in enumerate(
        zip(requests, outcomes, strict=True), start=1
    ):
        if not isinstance(request, dict) or set(request) != {
            "fragment_exchange_id", "request"
        } or not isinstance(request.get("request"), dict):
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", f"invalid request evidence ordinal={ordinal}"
            )
        if not isinstance(outcome, dict) or set(outcome) != {
            "exchange_id", "status", "row_count", "error_type", "error_detail"
        }:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", f"invalid outcome evidence ordinal={ordinal}"
            )
        request_group = str(request["fragment_exchange_id"] or "").upper()
        outcome_group = str(outcome["exchange_id"] or "").upper()
        if request_group != outcome_group:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT",
                f"request/outcome group mismatch ordinal={ordinal}",
            )
        status = str(outcome["status"] or "").lower()
        if status not in {"success", "empty", "error"}:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", f"invalid fragment status={status!r}"
            )
        try:
            row_count = int(outcome["row_count"])
        except (TypeError, ValueError) as exc:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", "fragment row_count is not an integer"
            ) from exc
        if row_count < 0:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", "fragment row_count cannot be negative"
            )
        error_type = str(outcome["error_type"] or "").strip() or None
        error_detail = str(outcome["error_detail"] or "").strip() or None
        if status == "error" and error_type is None:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", "error fragment lost error_type"
            )
        if status != "error" and (error_type is not None or error_detail is not None):
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT", "non-error fragment carries error metadata"
            )
        normalized_outcomes.append({
            "exchange_id": outcome_group,
            "status": status,
            "row_count": row_count,
            "error_type": error_type,
            "error_detail": error_detail,
        })
        observed_groups.append(outcome_group)

    if len(observed_groups) != len(set(observed_groups)):
        raise MarginValidationError(
            "LANDING_ENVELOPE_DRIFT", "duplicate fragment outcome group"
        )
    missing = expected - set(observed_groups)
    if missing:
        raise MarginValidationError(
            "MISSING_REQUIRED_GROUP",
            f"missing={sorted(missing)} observed={sorted(observed_groups)}",
        )
    unexpected = set(observed_groups) - expected
    if unexpected:
        raise MarginValidationError(
            "UNEXPECTED_GROUP",
            f"expected={sorted(expected)} observed={sorted(observed_groups)}",
        )

    landed = (
        list(landed_rows)
        if landed_rows is not None
        else conn.execute(
            f"""
            SELECT fragment_exchange_id, fragment_ordinal, row_ordinal,
                   request_json, payload_json, row_hash
              FROM {LANDING_TABLE}
             WHERE batch_id = ?
             ORDER BY fragment_ordinal, row_ordinal
            """,
            [batch_id],
        ).fetchall()
    )
    rows_by_ordinal: dict[int, list[tuple[Any, ...]]] = {}
    row_signatures: list[str] = []
    for landed_row in landed:
        _, fragment_ordinal, row_ordinal, _, payload_json, row_hash = landed_row
        ordinal = int(fragment_ordinal)
        rows_by_ordinal.setdefault(ordinal, []).append(landed_row)
        if _sha256(str(payload_json)) != str(row_hash):
            raise MarginValidationError(
                "LANDING_HASH_MISMATCH",
                f"row hash mismatch ordinal={fragment_ordinal}/{row_ordinal}",
            )
        row_signatures.append(f"{fragment_ordinal}:{row_ordinal}:{row_hash}")

    for ordinal, (request, outcome) in enumerate(
        zip(requests, normalized_outcomes, strict=True), start=1
    ):
        fragment_rows = rows_by_ordinal.get(ordinal, [])
        if len(fragment_rows) != outcome["row_count"]:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT",
                f"fragment={outcome['exchange_id']} stored_rows={len(fragment_rows)} "
                f"recorded_rows={outcome['row_count']}",
            )
        expected_request_json = _stable_json(request["request"])
        expected_ordinals = list(range(1, len(fragment_rows) + 1))
        actual_ordinals = [int(row[2]) for row in fragment_rows]
        if actual_ordinals != expected_ordinals:
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT",
                f"fragment={outcome['exchange_id']} row ordinals are not contiguous",
            )
        if any(
            str(row[0]).upper() != outcome["exchange_id"]
            or str(row[3]) != expected_request_json
            for row in fragment_rows
        ):
            raise MarginValidationError(
                "LANDING_ENVELOPE_DRIFT",
                f"fragment={outcome['exchange_id']} request/group evidence mismatch",
            )

    completed = sum(outcome["status"] != "error" for outcome in normalized_outcomes)
    failed = sum(outcome["status"] == "error" for outcome in normalized_outcomes)
    if (
        int(batch["expected_fragment_count"]) != len(expected)
        or int(batch["completed_fragment_count"]) != completed
        or int(batch["failed_fragment_count"]) != failed
        or int(batch["landing_row_count"]) != len(landed)
    ):
        raise MarginValidationError(
            "LANDING_ENVELOPE_DRIFT", "batch fragment/row counters contradict landing"
        )
    recomputed_payload_hash = _batch_payload_hash(
        partition=partition,
        source=str(batch["source_name"]),
        contract_version=str(batch["contract_version"]),
        contract_hash=str(batch["contract_hash"]),
        config_hash=str(batch["config_hash"]),
        observed_at=batch["observed_at"],
        available_at=batch["available_at"],
        requests=requests,
        outcomes=normalized_outcomes,
        row_signatures=row_signatures,
    )
    if recomputed_payload_hash != str(batch["payload_hash"]):
        raise MarginValidationError(
            "LANDING_HASH_MISMATCH", "batch payload hash contradicts landed evidence"
        )
    provider_failures = [
        f"{outcome['exchange_id']}:{outcome['error_type']}"
        for outcome in normalized_outcomes
        if outcome["status"] == "error"
        and str(outcome["error_type"]).lower() != "not_attempted"
    ]
    not_attempted = [
        outcome["exchange_id"]
        for outcome in normalized_outcomes
        if outcome["status"] == "error"
        and str(outcome["error_type"]).lower() == "not_attempted"
    ]
    empty = [
        outcome["exchange_id"]
        for outcome in normalized_outcomes
        if outcome["status"] == "empty"
    ]
    if provider_failures:
        detail = f"failures={provider_failures}"
        if not_attempted:
            detail += f" not_attempted={not_attempted}"
        raise MarginValidationError("FRAGMENT_FAILED", detail)
    if empty:
        detail = f"empty_fragments={empty}"
        if not_attempted:
            detail += f" not_attempted={not_attempted}"
        raise MarginValidationError("ZERO_ROWS", detail)
    if not_attempted:
        failures = [
            f"{outcome['exchange_id']}:{outcome['error_type']}"
            for outcome in normalized_outcomes
            if outcome["status"] == "error"
        ]
        raise MarginValidationError("FRAGMENT_FAILED", f"failures={failures}")
    if not landed:
        raise MarginValidationError("EMPTY_LANDING", "landed batch has no response rows")

    candidate: list[dict[str, Any]] = []
    grain: set[tuple[str, str]] = set()
    for fragment_exchange_id, _, _, _, payload_json, row_hash in landed:
        value = json.loads(payload_json)
        if not isinstance(value, dict) or set(value) != set(MARGIN_FIELDS):
            actual = sorted(value) if isinstance(value, dict) else type(value).__name__
            raise MarginValidationError(
                "SCHEMA_DRIFT", f"expected={list(MARGIN_FIELDS)} actual={actual}"
            )
        trade_date = str(value["trade_date"] or "").replace("-", "")
        exchange_id = str(value["exchange_id"] or "").upper()
        if trade_date != partition or exchange_id != str(fragment_exchange_id).upper():
            raise MarginValidationError(
                "WRONG_PARTITION",
                f"requested={partition}/{fragment_exchange_id} row={trade_date}/{exchange_id}",
            )
        key = (trade_date, exchange_id)
        if key in grain:
            raise MarginValidationError("DUPLICATE_GRAIN", f"duplicate={key}")
        grain.add(key)
        normalized = {"trade_date": trade_date, "exchange_id": exchange_id}
        for field in NUMERIC_FIELDS:
            normalized[field] = _decimal(
                value[field], field, nullable=field not in NON_NULL_NUMERIC_FIELDS
            )
        if normalized["rqye"] is not None and (
            normalized["rzrqye"] != normalized["rzye"] + normalized["rqye"]
        ):
            raise MarginValidationError(
                "INCONSISTENT_TOTAL", "rzrqye must equal rzye + rqye when rqye is known"
            )
        normalized["source_row_hash"] = str(row_hash)
        candidate.append(normalized)

    return candidate


def canonical_content_hash(rows: list[dict[str, Any]]) -> str:
    """Return the writer-owned content fingerprint for canonical margin rows."""
    encoded = []
    for row in sorted(rows, key=lambda item: (item["trade_date"], item["exchange_id"])):
        encoded.append({
            field: (
                None if row[field] is None
                else format(Decimal(str(row[field])).normalize(), "f")
                if field in NUMERIC_FIELDS
                else row[field]
            )
            for field in MARGIN_FIELDS
        })
    return _sha256(_stable_json(encoded))


def _provider_rows(conn, batch_id: str) -> tuple[dict[str, Any], ...]:
    """Rehydrate the exact verified provider payload shape for shadow writing."""
    payloads = conn.execute(
        f"""
        SELECT payload_json
          FROM {LANDING_TABLE}
         WHERE batch_id = ?
         ORDER BY fragment_ordinal, row_ordinal
        """,
        [batch_id],
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for (payload_json,) in payloads:
        value = json.loads(payload_json)
        if not isinstance(value, dict):
            raise MarginValidationError(
                "SCHEMA_DRIFT", "verified landing payload is no longer an object"
            )
        rows.append(value)
    return tuple(rows)


__all__ = [
    "MarginValidationError",
    "canonical_content_hash",
    "load_margin_publication_sessions",
    "validate_margin_publication_time",
]
