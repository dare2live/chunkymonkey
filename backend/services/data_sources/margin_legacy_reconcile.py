"""Pure typed comparison of one accepted margin partition with legacy rows.

The module consumes a preloaded snapshot and never creates, repairs, publishes,
or queries data.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable

from services.data_sources.margin_evidence import (
    ACCEPTED_EVIDENCE_FIELDS,
    BATCH_EVIDENCE_FIELDS,
    CANONICAL_EVIDENCE_FIELDS,
    LANDING_EVIDENCE_FIELDS,
    MarginEvidenceSnapshot,
)
from services.data_sources.margin_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    MARGIN_FIELDS,
    NON_NULL_NUMERIC_FIELDS,
    NUMERIC_FIELDS,
)
from services.data_sources.margin_validation import canonical_content_hash


class MarginReconcileStatus(str, Enum):
    """Overall cutover-gate outcome."""

    PARITY = "PARITY"
    FAILED = "FAILED"


class MarginReconcileCode(str, Enum):
    """Stable, machine-consumable reasons a partition is not reconciled."""

    INVALID_PARTITION = "INVALID_PARTITION"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    ACCEPTED_PARTITION_MISSING = "ACCEPTED_PARTITION_MISSING"
    ACCEPTED_PARTITION_DUPLICATE = "ACCEPTED_PARTITION_DUPLICATE"
    INGEST_BATCH_MISSING = "INGEST_BATCH_MISSING"
    INGEST_BATCH_DUPLICATE = "INGEST_BATCH_DUPLICATE"
    UNRESOLVED_LANDING = "UNRESOLVED_LANDING"
    BATCH_DATASET_MISMATCH = "BATCH_DATASET_MISMATCH"
    BATCH_PARTITION_MISMATCH = "BATCH_PARTITION_MISMATCH"
    BATCH_NOT_ACCEPTED = "BATCH_NOT_ACCEPTED"
    CURRENT_CONTRACT_MISMATCH = "CURRENT_CONTRACT_MISMATCH"
    ACCEPTANCE_EVIDENCE_MISMATCH = "ACCEPTANCE_EVIDENCE_MISMATCH"
    ACCEPTED_CONTENT_HASH_MISMATCH = "ACCEPTED_CONTENT_HASH_MISMATCH"
    CANONICAL_COUNT_MISMATCH = "CANONICAL_COUNT_MISMATCH"
    CANONICAL_CONTENT_MISMATCH = "CANONICAL_CONTENT_MISMATCH"
    FORMAL_EVIDENCE_INVALID = "FORMAL_EVIDENCE_INVALID"
    CANONICAL_BATCH_MISMATCH = "CANONICAL_BATCH_MISMATCH"
    CANONICAL_EVIDENCE_MISMATCH = "CANONICAL_EVIDENCE_MISMATCH"
    CANONICAL_DUPLICATE_GRAIN = "CANONICAL_DUPLICATE_GRAIN"
    LEGACY_DUPLICATE_GRAIN = "LEGACY_DUPLICATE_GRAIN"
    LEGACY_ROW_MISSING = "LEGACY_ROW_MISSING"
    LEGACY_ROW_EXTRA = "LEGACY_ROW_EXTRA"
    NULL_MISMATCH = "NULL_MISMATCH"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    QUERY_ERROR = "QUERY_ERROR"


# Historical repair may replace only formal publication evidence after the
# already-accepted chain has been proved and the discrepancy is confined to
# the legacy comparison surface.  New reconcile codes default to BLOCKED until
# this owner explicitly classifies them.
HISTORY_REPAIRABLE_CODES = frozenset(
    {
        MarginReconcileCode.LEGACY_ROW_MISSING,
        MarginReconcileCode.LEGACY_ROW_EXTRA,
        MarginReconcileCode.LEGACY_DUPLICATE_GRAIN,
        MarginReconcileCode.NULL_MISMATCH,
        MarginReconcileCode.VALUE_MISMATCH,
    }
)


@dataclass(frozen=True)
class MarginReconcileIssue:
    """One independently actionable reconciliation failure."""

    code: MarginReconcileCode
    detail: str
    grain: tuple[str, str] | None = None
    field: str | None = None
    accepted_value: Any = None
    legacy_value: Any = None


@dataclass(frozen=True)
class MarginReconcileReport:
    """Typed evidence returned for one requested partition."""

    dataset_id: str
    partition_value: str
    status: MarginReconcileStatus
    accepted_batch_id: str | None
    accepted_row_count: int | None
    canonical_row_count: int | None
    legacy_row_count: int | None
    issues: tuple[MarginReconcileIssue, ...]
    accepted_content_hash: str | None = None
    recoverable_landing_batch_id: str | None = None
    recoverable_landing_payload_hash: str | None = None
    unresolved_landing_batch_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is MarginReconcileStatus.PARITY


@dataclass(frozen=True)
class MarginHistoryComparison:
    """Pure pre-publication comparison of one candidate with legacy truth."""

    partition_value: str
    candidate_row_count: int
    legacy_row_count: int
    candidate_hash: str | None
    legacy_hash: str | None
    issues: tuple[MarginReconcileIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def issue_codes(self) -> tuple[MarginReconcileCode, ...]:
        return tuple(sorted({issue.code for issue in self.issues}, key=lambda code: code.value))


_INTEGER_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
}

_STRING_TYPES = {"VARCHAR", "TEXT", "STRING"}

_FORMAL_REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    ACCEPTED_TABLE: {
        **{field: "string" for field in ACCEPTED_EVIDENCE_FIELDS},
        "row_count": "integer",
        "accepted_at": "timestamp",
        "observed_at": "timestamp",
        "available_at": "timestamp",
    },
    INGEST_BATCH_TABLE: {
        **{field: "string" for field in BATCH_EVIDENCE_FIELDS[1:]},
        "canonical_row_count": "integer",
        "expected_fragment_count": "integer",
        "completed_fragment_count": "integer",
        "failed_fragment_count": "integer",
        "landing_row_count": "integer",
        "observed_at": "timestamp",
        "available_at": "timestamp",
    },
    LANDING_TABLE: {
        **{field: "string" for field in LANDING_EVIDENCE_FIELDS[1:]},
        "fragment_ordinal": "integer",
        "row_ordinal": "integer",
    },
    CANONICAL_TABLE: {
        **{field: "string" for field in CANONICAL_EVIDENCE_FIELDS[1:]},
        "trade_date": "date",
        **{field: "decimal38_6" for field in NUMERIC_FIELDS},
        "available_at": "timestamp",
    },
}
_LEGACY_REQUIRED_COLUMNS = {
    "trade_date": "date_or_string",
    "exchange_id": "string",
    **{field: "numeric" for field in NUMERIC_FIELDS},
}


def _report(
    partition: str,
    issues: Iterable[MarginReconcileIssue],
    *,
    accepted_batch_id: str | None = None,
    accepted_row_count: int | None = None,
    canonical_row_count: int | None = None,
    legacy_row_count: int | None = None,
    accepted_content_hash: str | None = None,
    recoverable_landing_batch_id: str | None = None,
    recoverable_landing_payload_hash: str | None = None,
    unresolved_landing_batch_ids: tuple[str, ...] = (),
) -> MarginReconcileReport:
    frozen_issues = tuple(issues)
    return MarginReconcileReport(
        dataset_id=DATASET_ID,
        partition_value=partition,
        status=(
            MarginReconcileStatus.PARITY
            if not frozen_issues
            else MarginReconcileStatus.FAILED
        ),
        accepted_batch_id=accepted_batch_id,
        accepted_row_count=accepted_row_count,
        canonical_row_count=canonical_row_count,
        legacy_row_count=legacy_row_count,
        issues=frozen_issues,
        accepted_content_hash=accepted_content_hash,
        recoverable_landing_batch_id=recoverable_landing_batch_id,
        recoverable_landing_payload_hash=recoverable_landing_payload_hash,
        unresolved_landing_batch_ids=unresolved_landing_batch_ids,
    )


def _with_issue(
    report: MarginReconcileReport, issue: MarginReconcileIssue
) -> MarginReconcileReport:
    return _report(
        report.partition_value,
        (*report.issues, issue),
        accepted_batch_id=report.accepted_batch_id,
        accepted_row_count=report.accepted_row_count,
        canonical_row_count=report.canonical_row_count,
        legacy_row_count=report.legacy_row_count,
        accepted_content_hash=report.accepted_content_hash,
        recoverable_landing_batch_id=report.recoverable_landing_batch_id,
        recoverable_landing_payload_hash=report.recoverable_landing_payload_hash,
        unresolved_landing_batch_ids=report.unresolved_landing_batch_ids,
    )


def _partition(value: Any) -> str | None:
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        compact = raw
    elif (
        len(raw) == 10
        and raw[4] == raw[7] == "-"
        and raw[:4].isdigit()
        and raw[5:7].isdigit()
        and raw[8:].isdigit()
    ):
        compact = raw.replace("-", "")
    else:
        return None
    try:
        parsed = datetime.strptime(compact, "%Y%m%d")
    except ValueError:
        return None
    return parsed.strftime("%Y%m%d")


def _type_matches(actual: str, expected: str) -> bool:
    normalized = actual.upper().replace(" ", "")
    if expected == "string":
        return actual.upper() in _STRING_TYPES
    if expected == "integer":
        return actual.upper() in _INTEGER_TYPES
    if expected == "date":
        return actual.upper() == "DATE"
    if expected == "date_or_string":
        return actual.upper() == "DATE" or actual.upper() in _STRING_TYPES
    if expected == "decimal38_6":
        return normalized == "DECIMAL(38,6)"
    if expected == "numeric":
        return normalized.startswith("DECIMAL(") or actual.upper() in _INTEGER_TYPES
    if expected == "timestamp":
        return actual.upper() in {
            "TIMESTAMP",
            "TIMESTAMP WITH TIME ZONE",
            "TIMESTAMPTZ",
        }
    return False


def _snapshot_schema_issues(
    snapshot: MarginEvidenceSnapshot, legacy_table: str
) -> list[MarginReconcileIssue]:
    """Apply the same schema gate to one already-read evidence generation."""

    issues: list[MarginReconcileIssue] = []
    required_tables = {
        **_FORMAL_REQUIRED_COLUMNS,
        legacy_table: _LEGACY_REQUIRED_COLUMNS,
    }
    for table, required in required_tables.items():
        schema = snapshot.schema_for(table)
        if schema is None or not schema.available:
            detail = schema.error if schema is not None else "schema was not loaded"
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.SCHEMA_MISMATCH,
                    f"{table} is unavailable: {str(detail)[:300]}",
                )
            )
            continue
        columns = dict(schema.columns)
        missing = sorted(set(required) - set(columns))
        mismatched = {
            column: (columns[column], expected)
            for column, expected in required.items()
            if column in columns and not _type_matches(columns[column], expected)
        }
        if missing or mismatched:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.SCHEMA_MISMATCH,
                    f"{table} missing={missing} incompatible_types={mismatched}",
                )
            )
    return issues


def _compact_trade_date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    return _partition(value) or f"<invalid:{value}>"


def _grain(row: dict[str, Any]) -> tuple[str, str]:
    return (_compact_trade_date(row["trade_date"]), str(row["exchange_id"]))


def _group_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_grain(row)].append(row)
    return dict(grouped)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidOperation("bool is not a margin numeric")
    number = Decimal(str(value))
    if not number.is_finite():
        raise InvalidOperation("non-finite margin numeric")
    return number


def _row_dicts(rows: list[tuple[Any, ...]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(zip(fields, row, strict=True)) for row in rows]


def _landing_business_rows(
    payload_rows: list[tuple[Any, ...]], partition: str
) -> list[dict[str, Any]]:
    """Rebuild the writer's hash input without guessing DECIMAL storage scale."""
    rows: list[dict[str, Any]] = []
    for fragment_exchange_id, payload_json in payload_rows:
        value = json.loads(str(payload_json))
        if not isinstance(value, dict) or set(value) != set(MARGIN_FIELDS):
            raise ValueError("accepted landing payload does not match MARGIN_FIELDS")
        trade_date = _partition(value["trade_date"])
        exchange_id = str(value["exchange_id"] or "").upper()
        if trade_date != partition or exchange_id != str(fragment_exchange_id).upper():
            raise ValueError("accepted landing payload identity contradicts its fragment")
        normalized: dict[str, Any] = {
            "trade_date": trade_date,
            "exchange_id": exchange_id,
        }
        for field in NUMERIC_FIELDS:
            raw = value[field]
            if raw is None:
                if field in NON_NULL_NUMERIC_FIELDS:
                    raise ValueError(f"accepted landing payload has null {field}")
                normalized[field] = None
            else:
                normalized[field] = _decimal(raw)
        rows.append(normalized)
    return rows


def _canonical_source_issue(
    source_rows: list[dict[str, Any]], canonical_rows: list[dict[str, Any]]
) -> MarginReconcileIssue | None:
    """Return the first exact business-content break in accepted lineage."""
    source = _group_rows(source_rows)
    canonical = _group_rows(canonical_rows)
    duplicate = next(
        (
            (grain, len(rows), "landing")
            for grain, rows in sorted(source.items())
            if len(rows) != 1
        ),
        None,
    ) or next(
        (
            (grain, len(rows), "canonical")
            for grain, rows in sorted(canonical.items())
            if len(rows) != 1
        ),
        None,
    )
    if duplicate is not None:
        grain, count, surface = duplicate
        return MarginReconcileIssue(
            MarginReconcileCode.CANONICAL_CONTENT_MISMATCH,
            f"{surface} accepted-content grain has {count} rows",
            grain=grain,
        )
    if set(source) != set(canonical):
        missing = sorted(set(source) - set(canonical))
        extra = sorted(set(canonical) - set(source))
        return MarginReconcileIssue(
            MarginReconcileCode.CANONICAL_CONTENT_MISMATCH,
            f"canonical grains differ from accepted landing: missing={missing} extra={extra}",
        )
    for grain in sorted(source):
        expected = source[grain][0]
        actual = canonical[grain][0]
        for field in NUMERIC_FIELDS:
            expected_value = expected[field]
            actual_value = actual[field]
            if (expected_value is None) != (actual_value is None):
                return MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_CONTENT_MISMATCH,
                    "canonical nullability differs from accepted landing",
                    grain=grain,
                    field=field,
                    accepted_value=expected_value,
                    legacy_value=actual_value,
                )
            if expected_value is not None and _decimal(expected_value) != _decimal(actual_value):
                return MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_CONTENT_MISMATCH,
                    "canonical value differs from accepted landing",
                    grain=grain,
                    field=field,
                    accepted_value=expected_value,
                    legacy_value=actual_value,
                )
    return None


def _business_row_issues(
    candidate_rows: list[dict[str, Any]], legacy_rows: list[dict[str, Any]]
) -> list[MarginReconcileIssue]:
    """Apply the canonical/legacy value rules without requiring publication."""

    issues: list[MarginReconcileIssue] = []
    candidate_by_grain = _group_rows(candidate_rows)
    legacy_by_grain = _group_rows(legacy_rows)
    for grain, rows in sorted(candidate_by_grain.items()):
        if len(rows) > 1:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_DUPLICATE_GRAIN,
                    f"candidate canonical grain has {len(rows)} rows",
                    grain=grain,
                )
            )
    for grain, rows in sorted(legacy_by_grain.items()):
        if len(rows) > 1:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.LEGACY_DUPLICATE_GRAIN,
                    f"legacy grain has {len(rows)} rows",
                    grain=grain,
                )
            )

    candidate_grains = set(candidate_by_grain)
    legacy_grains = set(legacy_by_grain)
    for grain in sorted(candidate_grains - legacy_grains):
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.LEGACY_ROW_MISSING,
                "candidate canonical grain is absent from the legacy projection",
                grain=grain,
            )
        )
    for grain in sorted(legacy_grains - candidate_grains):
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.LEGACY_ROW_EXTRA,
                "legacy projection contains a grain absent from candidate canonical",
                grain=grain,
            )
        )

    for grain in sorted(candidate_grains & legacy_grains):
        if len(candidate_by_grain[grain]) != 1 or len(legacy_by_grain[grain]) != 1:
            continue
        candidate = candidate_by_grain[grain][0]
        legacy = legacy_by_grain[grain][0]
        for field in NUMERIC_FIELDS:
            candidate_value = candidate[field]
            legacy_value = legacy[field]
            if (candidate_value is None) != (legacy_value is None):
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.NULL_MISMATCH,
                        "candidate canonical and legacy nullability differ",
                        grain=grain,
                        field=field,
                        accepted_value=candidate_value,
                        legacy_value=legacy_value,
                    )
                )
                continue
            if candidate_value is None:
                continue
            try:
                equal = _decimal(candidate_value) == _decimal(legacy_value)
            except (InvalidOperation, ValueError, TypeError):
                equal = False
            if not equal:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.VALUE_MISMATCH,
                        "candidate canonical and legacy numeric values differ",
                        grain=grain,
                        field=field,
                        accepted_value=candidate_value,
                        legacy_value=legacy_value,
                    )
                )
    return issues


def _history_content_hash(rows: list[dict[str, Any]]) -> str | None:
    """Hash business values using the canonical writer's normalization."""

    try:
        normalized = []
        for row in rows:
            normalized.append(
                {
                    "trade_date": _compact_trade_date(row["trade_date"]),
                    "exchange_id": str(row["exchange_id"]),
                    **{
                        field: None if row[field] is None else _decimal(row[field])
                        for field in NUMERIC_FIELDS
                    },
                }
            )
        return canonical_content_hash(normalized)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None


def compare_margin_history_rows(
    partition_value: Any,
    candidate_rows: Iterable[dict[str, Any]],
    legacy_rows: Iterable[dict[str, Any]],
) -> MarginHistoryComparison:
    """Compare a validated history candidate before either surface is mutated."""

    partition = _partition(partition_value)
    candidate = [dict(row) for row in candidate_rows]
    legacy = [dict(row) for row in legacy_rows]
    if partition is None:
        issues = (
            MarginReconcileIssue(
                MarginReconcileCode.INVALID_PARTITION,
                f"expected a real YYYYMMDD partition, got {partition_value!r}",
            ),
        )
        return MarginHistoryComparison(
            str(partition_value or ""), len(candidate), len(legacy), None, None, issues
        )
    try:
        issues = tuple(_business_row_issues(candidate, legacy))
    except (KeyError, TypeError, ValueError) as exc:
        issues = (
            MarginReconcileIssue(
                MarginReconcileCode.QUERY_ERROR,
                f"history comparison rows are malformed: {str(exc)[:300]}",
            ),
        )
    return MarginHistoryComparison(
        partition_value=partition,
        candidate_row_count=len(candidate),
        legacy_row_count=len(legacy),
        candidate_hash=_history_content_hash(candidate),
        legacy_hash=_history_content_hash(legacy),
        issues=issues,
    )




__all__ = [
    "HISTORY_REPAIRABLE_CODES",
    "MarginHistoryComparison",
    "MarginReconcileCode",
    "MarginReconcileIssue",
    "MarginReconcileReport",
    "MarginReconcileStatus",
    "compare_margin_history_rows",
]
