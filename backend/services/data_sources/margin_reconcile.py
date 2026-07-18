"""Read-only accepted-partition shadow reconciliation for TuShare margin.

This module is deliberately margin-specific.  It follows the accepted pointer
to its canonical rows, proves the pointer/batch evidence is coherent, and then
compares the published business fields with the legacy projection at the
declared grain.  It never creates, repairs, or publishes data.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable

from services.data_sources.contracts import load_dataset_contract
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
    BATCH_DATASET_MISMATCH = "BATCH_DATASET_MISMATCH"
    BATCH_PARTITION_MISMATCH = "BATCH_PARTITION_MISMATCH"
    BATCH_NOT_ACCEPTED = "BATCH_NOT_ACCEPTED"
    CURRENT_CONTRACT_MISMATCH = "CURRENT_CONTRACT_MISMATCH"
    ACCEPTANCE_EVIDENCE_MISMATCH = "ACCEPTANCE_EVIDENCE_MISMATCH"
    ACCEPTED_CONTENT_HASH_MISMATCH = "ACCEPTED_CONTENT_HASH_MISMATCH"
    CANONICAL_COUNT_MISMATCH = "CANONICAL_COUNT_MISMATCH"
    CANONICAL_CONTENT_MISMATCH = "CANONICAL_CONTENT_MISMATCH"
    CANONICAL_BATCH_MISMATCH = "CANONICAL_BATCH_MISMATCH"
    CANONICAL_EVIDENCE_MISMATCH = "CANONICAL_EVIDENCE_MISMATCH"
    CANONICAL_DUPLICATE_GRAIN = "CANONICAL_DUPLICATE_GRAIN"
    LEGACY_DUPLICATE_GRAIN = "LEGACY_DUPLICATE_GRAIN"
    LEGACY_ROW_MISSING = "LEGACY_ROW_MISSING"
    LEGACY_ROW_EXTRA = "LEGACY_ROW_EXTRA"
    NULL_MISMATCH = "NULL_MISMATCH"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    QUERY_ERROR = "QUERY_ERROR"


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

    @property
    def ok(self) -> bool:
        return self.status is MarginReconcileStatus.PARITY


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
        "dataset_id": "string",
        "partition_value": "string",
        "batch_id": "string",
        "contract_version": "string",
        "contract_hash": "string",
        "config_hash": "string",
        "row_count": "integer",
        "content_hash": "string",
    },
    INGEST_BATCH_TABLE: {
        "batch_id": "string",
        "dataset_id": "string",
        "partition_value": "string",
        "status": "string",
        "contract_version": "string",
        "contract_hash": "string",
        "config_hash": "string",
        "canonical_row_count": "integer",
        "canonical_hash": "string",
    },
    LANDING_TABLE: {
        "batch_id": "string",
        "fragment_exchange_id": "string",
        "fragment_ordinal": "integer",
        "row_ordinal": "integer",
        "payload_json": "string",
    },
    CANONICAL_TABLE: {
        "trade_date": "date",
        "exchange_id": "string",
        **{field: "decimal38_6" for field in NUMERIC_FIELDS},
        "ingest_batch_id": "string",
        "contract_version": "string",
        "config_hash": "string",
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


def _schema_issues(conn, legacy_table: str) -> list[MarginReconcileIssue]:
    issues: list[MarginReconcileIssue] = []
    required_tables = {
        **_FORMAL_REQUIRED_COLUMNS,
        legacy_table: _LEGACY_REQUIRED_COLUMNS,
    }
    for table, required in required_tables.items():
        try:
            columns = {
                str(row[0]): str(row[1]).upper()
                for row in conn.execute(f"DESCRIBE {table}").fetchall()
            }
        except Exception as exc:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.SCHEMA_MISMATCH,
                    f"{table} is unavailable: {str(exc)[:300]}",
                )
            )
            continue
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


def reconcile_margin_partition(
    conn, partition_value: Any, *, contract=None
) -> MarginReconcileReport:
    """Compare one accepted canonical partition with its legacy projection.

    Only ``DESCRIBE`` and ``SELECT`` are issued.  Invalid or contradictory
    state is returned as a typed failed report; this function never repairs it.
    """
    partition = _partition(partition_value)
    if partition is None:
        return _report(
            str(partition_value or ""),
            (
                MarginReconcileIssue(
                    MarginReconcileCode.INVALID_PARTITION,
                    f"expected a real YYYYMMDD partition, got {partition_value!r}",
                ),
            ),
        )

    contract = contract or load_dataset_contract("margin")
    legacy_table = contract.compatibility_table
    schema_issues = _schema_issues(conn, legacy_table)
    if schema_issues:
        return _report(partition, schema_issues)

    issues: list[MarginReconcileIssue] = []
    accepted_batch_id: str | None = None
    accepted_row_count: int | None = None
    canonical_row_count: int | None = None
    legacy_row_count: int | None = None

    try:
        accepted_rows = conn.execute(
            f"""
            SELECT dataset_id, partition_value, batch_id, contract_version,
                   contract_hash, config_hash, row_count, content_hash
              FROM {ACCEPTED_TABLE}
             WHERE dataset_id = ? AND partition_value = ?
            """,
            [DATASET_ID, partition],
        ).fetchall()
        if not accepted_rows:
            return _report(
                partition,
                (
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTED_PARTITION_MISSING,
                        "no accepted pointer exists for the requested margin partition",
                    ),
                ),
            )
        if len(accepted_rows) != 1:
            return _report(
                partition,
                (
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTED_PARTITION_DUPLICATE,
                        f"expected one accepted pointer, found {len(accepted_rows)}",
                    ),
                ),
            )

        accepted = accepted_rows[0]
        accepted_batch_id = str(accepted[2])
        accepted_row_count = int(accepted[6])
        accepted_contract_evidence = (
            str(accepted[3]),
            str(accepted[4]),
            str(accepted[5]),
        )
        current_contract_evidence = (
            contract.contract_version,
            contract.contract_hash,
            contract.config_hash,
        )
        if accepted_contract_evidence != current_contract_evidence:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CURRENT_CONTRACT_MISMATCH,
                    "accepted pointer was published under a non-current contract/config",
                    accepted_value=accepted_contract_evidence,
                    legacy_value=current_contract_evidence,
                )
            )
        batch_rows = conn.execute(
            f"""
            SELECT batch_id, dataset_id, partition_value, status, contract_version,
                   contract_hash, config_hash, canonical_row_count, canonical_hash
              FROM {INGEST_BATCH_TABLE}
             WHERE batch_id = ?
            """,
            [accepted_batch_id],
        ).fetchall()
        batch = batch_rows[0] if len(batch_rows) == 1 else None
        if not batch_rows:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.INGEST_BATCH_MISSING,
                    f"accepted pointer batch_id={accepted_batch_id!r} does not exist",
                )
            )
        elif len(batch_rows) != 1:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.INGEST_BATCH_DUPLICATE,
                    f"batch_id={accepted_batch_id!r} has {len(batch_rows)} rows",
                )
            )
        else:
            if str(batch[1]) != DATASET_ID:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.BATCH_DATASET_MISMATCH,
                        f"batch dataset_id={batch[1]!r} expected={DATASET_ID!r}",
                    )
                )
            if str(batch[2]) != partition:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.BATCH_PARTITION_MISMATCH,
                        f"batch partition={batch[2]!r} expected={partition!r}",
                    )
                )
            if str(batch[3]) != "ACCEPTED":
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.BATCH_NOT_ACCEPTED,
                        f"accepted pointer targets batch status={batch[3]!r}",
                    )
                )
            accepted_evidence = (
                str(accepted[3]),
                str(accepted[4]),
                str(accepted[5]),
                accepted_row_count,
                str(accepted[7]),
            )
            batch_evidence = (
                str(batch[4]),
                str(batch[5]),
                str(batch[6]),
                None if batch[7] is None else int(batch[7]),
                None if batch[8] is None else str(batch[8]),
            )
            if accepted_evidence != batch_evidence:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTANCE_EVIDENCE_MISMATCH,
                        "accepted pointer contract/config/count/hash contradict ingest_batch",
                        accepted_value=accepted_evidence,
                        legacy_value=batch_evidence,
                    )
                )

        landing_payload_rows = conn.execute(
            f"""
            SELECT fragment_exchange_id, payload_json
              FROM {LANDING_TABLE}
             WHERE batch_id = ?
             ORDER BY fragment_ordinal, row_ordinal
            """,
            [accepted_batch_id],
        ).fetchall()
        partition_iso = f"{partition[:4]}-{partition[4:6]}-{partition[6:]}"
        canonical_fields = (
            *MARGIN_FIELDS,
            "ingest_batch_id",
            "contract_version",
            "config_hash",
        )
        canonical_rows = _row_dicts(
            conn.execute(
                f"""
                SELECT {', '.join(canonical_fields)}
                  FROM {CANONICAL_TABLE}
                 WHERE trade_date = CAST(? AS DATE)
                 ORDER BY trade_date, exchange_id
                """,
                [partition_iso],
            ).fetchall(),
            canonical_fields,
        )
        legacy_rows = _row_dicts(
            conn.execute(
                f"""
                SELECT {', '.join(MARGIN_FIELDS)}
                  FROM {legacy_table}
                 WHERE REPLACE(CAST(trade_date AS VARCHAR), '-', '') = ?
                 ORDER BY trade_date, exchange_id
                """,
                [partition],
            ).fetchall(),
            MARGIN_FIELDS,
        )
        canonical_row_count = len(canonical_rows)
        legacy_row_count = len(legacy_rows)
    except Exception as exc:
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.QUERY_ERROR,
                f"read-only margin reconciliation query failed: {str(exc)[:500]}",
            )
        )
        return _report(
            partition,
            issues,
            accepted_batch_id=accepted_batch_id,
            accepted_row_count=accepted_row_count,
            canonical_row_count=canonical_row_count,
            legacy_row_count=legacy_row_count,
        )

    if canonical_row_count != accepted_row_count:
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.CANONICAL_COUNT_MISMATCH,
                f"accepted row_count={accepted_row_count} canonical rows={canonical_row_count}",
                accepted_value=accepted_row_count,
                legacy_value=canonical_row_count,
            )
        )

    if not landing_payload_rows:
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.ACCEPTED_CONTENT_HASH_MISMATCH,
                "accepted batch has no retained landing payload to prove content",
            )
        )
    else:
        try:
            source_rows = _landing_business_rows(landing_payload_rows, partition)
            source_hash = canonical_content_hash(source_rows)
        except (InvalidOperation, ValueError, TypeError) as exc:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.ACCEPTED_CONTENT_HASH_MISMATCH,
                    f"accepted landing content is invalid: {str(exc)[:300]}",
                )
            )
        else:
            evidence_hashes = [str(accepted[7])]
            if batch is not None:
                evidence_hashes.append(str(batch[8]))
            if any(value != source_hash for value in evidence_hashes):
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.ACCEPTED_CONTENT_HASH_MISMATCH,
                        "accepted landing hash contradicts pointer or ingest evidence",
                        accepted_value=tuple(evidence_hashes),
                        legacy_value=source_hash,
                    )
                )
            source_issue = _canonical_source_issue(source_rows, canonical_rows)
            if source_issue is not None:
                issues.append(source_issue)

    for row in canonical_rows:
        if str(row["ingest_batch_id"]) != accepted_batch_id:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_BATCH_MISMATCH,
                    "canonical row does not belong to the accepted batch",
                    grain=_grain(row),
                    accepted_value=accepted_batch_id,
                    legacy_value=str(row["ingest_batch_id"]),
                )
            )
        expected_evidence = (str(accepted[3]), str(accepted[5]))
        actual_evidence = (str(row["contract_version"]), str(row["config_hash"]))
        if actual_evidence != expected_evidence:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_EVIDENCE_MISMATCH,
                    "canonical row contract/config differs from accepted pointer",
                    grain=_grain(row),
                    accepted_value=expected_evidence,
                    legacy_value=actual_evidence,
                )
            )

    canonical_by_grain = _group_rows(canonical_rows)
    legacy_by_grain = _group_rows(legacy_rows)
    for grain, rows in sorted(canonical_by_grain.items()):
        if len(rows) > 1:
            issues.append(
                MarginReconcileIssue(
                    MarginReconcileCode.CANONICAL_DUPLICATE_GRAIN,
                    f"canonical grain has {len(rows)} rows",
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

    canonical_grains = set(canonical_by_grain)
    legacy_grains = set(legacy_by_grain)
    for grain in sorted(canonical_grains - legacy_grains):
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.LEGACY_ROW_MISSING,
                "accepted canonical grain is absent from the legacy projection",
                grain=grain,
            )
        )
    for grain in sorted(legacy_grains - canonical_grains):
        issues.append(
            MarginReconcileIssue(
                MarginReconcileCode.LEGACY_ROW_EXTRA,
                "legacy projection contains a grain absent from accepted canonical",
                grain=grain,
            )
        )

    for grain in sorted(canonical_grains & legacy_grains):
        if len(canonical_by_grain[grain]) != 1 or len(legacy_by_grain[grain]) != 1:
            continue
        canonical = canonical_by_grain[grain][0]
        legacy = legacy_by_grain[grain][0]
        for field in NUMERIC_FIELDS:
            accepted_value = canonical[field]
            legacy_value = legacy[field]
            if (accepted_value is None) != (legacy_value is None):
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.NULL_MISMATCH,
                        "accepted canonical and legacy nullability differ",
                        grain=grain,
                        field=field,
                        accepted_value=accepted_value,
                        legacy_value=legacy_value,
                    )
                )
                continue
            if accepted_value is None:
                continue
            try:
                equal = _decimal(accepted_value) == _decimal(legacy_value)
            except (InvalidOperation, ValueError, TypeError):
                equal = False
            if not equal:
                issues.append(
                    MarginReconcileIssue(
                        MarginReconcileCode.VALUE_MISMATCH,
                        "accepted canonical and legacy numeric values differ",
                        grain=grain,
                        field=field,
                        accepted_value=accepted_value,
                        legacy_value=legacy_value,
                    )
                )

    return _report(
        partition,
        issues,
        accepted_batch_id=accepted_batch_id,
        accepted_row_count=accepted_row_count,
        canonical_row_count=canonical_row_count,
        legacy_row_count=legacy_row_count,
    )


__all__ = [
    "MarginReconcileCode",
    "MarginReconcileIssue",
    "MarginReconcileReport",
    "MarginReconcileStatus",
    "reconcile_margin_partition",
]
