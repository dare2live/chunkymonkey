"""Immutable, set-based evidence reads for formal margin truth.

The acceptance, state, and reconcile layers own different judgments, but they
must not each rediscover the same accepted partitions with per-partition SQL.
This module owns only the read boundary: one accepted-pointer scope is loaded
into immutable row tuples, then business validators consume that exact snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.data_sources.margin_schema import (
    ACCEPTED_TABLE,
    CANONICAL_TABLE,
    DATASET_ID,
    INGEST_BATCH_TABLE,
    LANDING_TABLE,
    MARGIN_FIELDS,
)


ACCEPTED_EVIDENCE_FIELDS = (
    "dataset_id",
    "partition_value",
    "batch_id",
    "contract_version",
    "contract_hash",
    "config_hash",
    "row_count",
    "content_hash",
    "accepted_at",
    "observed_at",
    "available_at",
)

BATCH_EVIDENCE_FIELDS = (
    "evidence_partition_value",
    "batch_id",
    "dataset_id",
    "status",
    "partition_value",
    "canonical_row_count",
    "canonical_hash",
    "contract_version",
    "contract_hash",
    "config_hash",
    "source_name",
    "writer_id",
    "request_json",
    "fragment_outcomes_json",
    "expected_fragment_count",
    "completed_fragment_count",
    "failed_fragment_count",
    "landing_row_count",
    "payload_hash",
    "observed_at",
    "available_at",
)

LANDING_EVIDENCE_FIELDS = (
    "accepted_partition_value",
    "batch_id",
    "fragment_exchange_id",
    "fragment_ordinal",
    "row_ordinal",
    "request_json",
    "payload_json",
    "row_hash",
)

CANONICAL_EVIDENCE_FIELDS = (
    "accepted_partition_value",
    *MARGIN_FIELDS,
    "ingest_batch_id",
    "source_row_hash",
    "contract_version",
    "config_hash",
    "available_at",
)

LEGACY_EVIDENCE_FIELDS = (
    "accepted_partition_value",
    *MARGIN_FIELDS,
)


class MarginEvidenceLoadError(RuntimeError):
    """The formal evidence snapshot could not be read coherently."""


@dataclass(frozen=True)
class TableSchemaEvidence:
    table: str
    columns: tuple[tuple[str, str], ...]
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class MarginEvidenceSnapshot:
    """Immutable rows from one bounded read pass over a formal margin scope.

    The caller's connection owns transaction isolation.  Cross-query
    contradictions are rejected by the state proof; this object does not claim
    to open or commit an MVCC transaction on the caller's behalf.
    """

    contract: Any
    partition_value: str | None
    include_legacy: bool
    schemas: tuple[TableSchemaEvidence, ...]
    accepted_rows: tuple[tuple[Any, ...], ...] = ()
    batch_rows: tuple[tuple[Any, ...], ...] = ()
    landing_rows: tuple[tuple[Any, ...], ...] = ()
    canonical_rows: tuple[tuple[Any, ...], ...] = ()
    legacy_rows: tuple[tuple[Any, ...], ...] = ()
    load_error: str | None = None

    def schema_for(self, table: str) -> TableSchemaEvidence | None:
        return next((item for item in self.schemas if item.table == table), None)

    def scope_error(self) -> str | None:
        """Return a contradiction between declared and retained row scope."""

        if self.partition_value is None:
            return None
        surfaces = (
            ("accepted", self.accepted_rows, 1),
            ("batch", self.batch_rows, 0),
            ("landing", self.landing_rows, 0),
            ("canonical", self.canonical_rows, 0),
            ("legacy", self.legacy_rows, 0),
        )
        for surface, rows, partition_index in surfaces:
            try:
                outside = sorted({
                    str(row[partition_index])
                    for row in rows
                    if str(row[partition_index]) != self.partition_value
                })
            except (IndexError, TypeError) as exc:
                return f"margin evidence snapshot has malformed {surface} scope: {exc}"
            if outside:
                return (
                    "margin evidence snapshot contains rows outside declared scope "
                    f"scope={self.partition_value} surface={surface} outside={outside}"
                )
        return None


def _rows(cursor) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row) for row in cursor.fetchall())


def _load_schemas(conn, tables: tuple[str, ...]) -> tuple[TableSchemaEvidence, ...]:
    placeholders = ", ".join("?" for _table in tables)
    rows = conn.execute(
        f"""
        SELECT table_name, column_name, data_type
          FROM information_schema.columns
         WHERE table_schema = 'main'
           AND table_name IN ({placeholders})
         ORDER BY table_name, ordinal_position
        """,
        list(tables),
    ).fetchall()
    columns_by_table: dict[str, list[tuple[str, str]]] = {
        table: [] for table in tables
    }
    for table, column, kind in rows:
        if str(table) in columns_by_table:
            columns_by_table[str(table)].append((str(column), str(kind).upper()))
    return tuple(
        TableSchemaEvidence(
            table=table,
            columns=tuple(columns_by_table[table]),
            error=None if columns_by_table[table] else "table does not exist",
        )
        for table in tables
    )


def _missing_query_columns(
    schemas: tuple[TableSchemaEvidence, ...],
    *,
    legacy_table: str,
    include_legacy: bool,
) -> dict[str, list[str]]:
    required = {
        ACCEPTED_TABLE: set(ACCEPTED_EVIDENCE_FIELDS),
        INGEST_BATCH_TABLE: set(BATCH_EVIDENCE_FIELDS[1:]),
        LANDING_TABLE: set(LANDING_EVIDENCE_FIELDS[1:]),
        CANONICAL_TABLE: set(CANONICAL_EVIDENCE_FIELDS[1:]),
    }
    if include_legacy:
        required[legacy_table] = set(LEGACY_EVIDENCE_FIELDS[1:])
    by_table = {item.table: item for item in schemas}
    missing: dict[str, list[str]] = {}
    for table, columns in required.items():
        schema = by_table.get(table)
        if schema is None or not schema.available:
            continue
        actual = {name for name, _kind in schema.columns}
        absent = sorted(columns - actual)
        if absent:
            missing[table] = absent
    return missing


def load_margin_evidence_snapshot(
    conn,
    *,
    contract,
    partition_value: str | None = None,
    include_legacy: bool = False,
) -> MarginEvidenceSnapshot:
    """Load an accepted-pointer scope with a query count independent of N.

    ``partition_value=None`` follows every current accepted pointer through
    set-based joins.  A concrete partition preserves the exact recovery scope
    used by the single-partition public APIs.  No growing ``IN`` list is built.
    """

    legacy_table = contract.compatibility_table
    formal_tables = (
        ACCEPTED_TABLE,
        INGEST_BATCH_TABLE,
        LANDING_TABLE,
        CANONICAL_TABLE,
    )
    schema_tables = (*formal_tables, legacy_table) if include_legacy else formal_tables
    try:
        schemas = _load_schemas(conn, schema_tables)
    except Exception as exc:
        raise MarginEvidenceLoadError(
            f"margin schema evidence query failed: {str(exc)[:500]}"
        ) from exc
    formal_available = [
        bool((schema := next(item for item in schemas if item.table == table)).available)
        for table in formal_tables
    ]
    base = {
        "contract": contract,
        "partition_value": partition_value,
        "include_legacy": include_legacy,
        "schemas": schemas,
    }
    if not any(formal_available):
        return MarginEvidenceSnapshot(**base)
    if not all(formal_available):
        missing = [
            table for table, available in zip(formal_tables, formal_available, strict=True)
            if not available
        ]
        return MarginEvidenceSnapshot(
            **base,
            load_error=f"partial formal margin schema missing={missing}",
        )

    missing_columns = _missing_query_columns(
        schemas,
        legacy_table=legacy_table,
        include_legacy=False,
    )
    if missing_columns:
        return MarginEvidenceSnapshot(
            **base,
            load_error=f"formal margin query columns missing={missing_columns}",
        )

    scope_sql = "a.dataset_id = ?"
    scope_params: list[Any] = [DATASET_ID]
    if partition_value is not None:
        scope_sql += " AND a.partition_value = ?"
        scope_params.append(partition_value)

    try:
        accepted_rows = _rows(
            conn.execute(
                f"""
                SELECT {', '.join(f'a.{field}' for field in ACCEPTED_EVIDENCE_FIELDS)}
                  FROM {ACCEPTED_TABLE} a
                 WHERE {scope_sql}
                 ORDER BY a.partition_value
                """,
                scope_params,
            )
        )
        batch_scope = ""
        batch_scope_params: list[Any] = [DATASET_ID, DATASET_ID]
        if partition_value is not None:
            batch_scope = (
                "AND ((a.batch_id IS NOT NULL AND a.partition_value = ?) "
                "OR (b.status = 'LANDED' AND b.partition_value = ?))"
            )
            batch_scope_params.extend((partition_value, partition_value))
        batch_rows = _rows(
            conn.execute(
                f"""
                SELECT COALESCE(a.partition_value, b.partition_value),
                       {', '.join(f'b.{field}' for field in BATCH_EVIDENCE_FIELDS[1:])}
                  FROM {INGEST_BATCH_TABLE} b
                  LEFT JOIN {ACCEPTED_TABLE} a
                    ON a.batch_id = b.batch_id AND a.dataset_id = ?
                 WHERE b.dataset_id = ?
                   AND (a.batch_id IS NOT NULL OR b.status = 'LANDED')
                   {batch_scope}
                 ORDER BY COALESCE(a.partition_value, b.partition_value), b.batch_id
                """,
                batch_scope_params,
            )
        )
        landing_rows = _rows(
            conn.execute(
                f"""
                SELECT a.partition_value,
                       {', '.join(f'l.{field}' for field in LANDING_EVIDENCE_FIELDS[1:])}
                  FROM {ACCEPTED_TABLE} a
                  JOIN {LANDING_TABLE} l ON l.batch_id = a.batch_id
                 WHERE {scope_sql}
                 ORDER BY a.partition_value, l.fragment_ordinal, l.row_ordinal
                """,
                scope_params,
            )
        )
        canonical_rows = _rows(
            conn.execute(
                f"""
                SELECT a.partition_value,
                       {', '.join(f'c.{field}' for field in CANONICAL_EVIDENCE_FIELDS[1:])}
                  FROM {ACCEPTED_TABLE} a
                  JOIN {CANONICAL_TABLE} c
                    ON c.trade_date = CAST(TRY_STRPTIME(a.partition_value, '%Y%m%d') AS DATE)
                 WHERE {scope_sql}
                 ORDER BY a.partition_value, c.trade_date, c.exchange_id
                """,
                scope_params,
            )
        )
        legacy_rows: tuple[tuple[Any, ...], ...] = ()
        legacy_schema = next(
            (item for item in schemas if item.table == legacy_table), None
        )
        legacy_missing_columns = _missing_query_columns(
            schemas,
            legacy_table=legacy_table,
            include_legacy=True,
        ).get(legacy_table)
        if (
            include_legacy
            and legacy_schema is not None
            and legacy_schema.available
            and not legacy_missing_columns
        ):
            legacy_rows = _rows(
                conn.execute(
                    f"""
                    SELECT a.partition_value,
                           {', '.join(f'l.{field}' for field in LEGACY_EVIDENCE_FIELDS[1:])}
                      FROM {ACCEPTED_TABLE} a
                      JOIN {legacy_table} l
                        ON REPLACE(CAST(l.trade_date AS VARCHAR), '-', '') = a.partition_value
                     WHERE {scope_sql}
                     ORDER BY a.partition_value, l.trade_date, l.exchange_id
                    """,
                    scope_params,
                )
            )
    except Exception as exc:
        raise MarginEvidenceLoadError(
            f"read-only margin evidence query failed: {str(exc)[:500]}"
        ) from exc

    return MarginEvidenceSnapshot(
        **base,
        accepted_rows=accepted_rows,
        batch_rows=batch_rows,
        landing_rows=landing_rows,
        canonical_rows=canonical_rows,
        legacy_rows=legacy_rows,
    )


__all__ = [
    "ACCEPTED_EVIDENCE_FIELDS",
    "BATCH_EVIDENCE_FIELDS",
    "CANONICAL_EVIDENCE_FIELDS",
    "LANDING_EVIDENCE_FIELDS",
    "LEGACY_EVIDENCE_FIELDS",
    "MarginEvidenceLoadError",
    "MarginEvidenceSnapshot",
    "TableSchemaEvidence",
    "load_margin_evidence_snapshot",
]
