"""Registry-driven completeness truth for dated raw batches.

One definition is shared by drain, continuity, and watermark reconciliation so a
date cannot be complete in one control-plane surface and partial in another.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BatchCompletenessError(ValueError):
    """A provider batch cannot satisfy its registry completeness contract."""


@dataclass(frozen=True)
class VerifiedBatchFrontier:
    last_date: str
    row_count: int
    last_success_at: Any | None


def _identifier(value: Any) -> str:
    raw = str(value or "")
    if not _IDENTIFIER.fullmatch(raw):
        raise ValueError(f"invalid registry identifier: {raw!r}")
    return f'"{raw}"'


def _table_name(spec: dict[str, Any]) -> str:
    return str(spec.get("target_table") or spec.get("table") or "")


def _date_column(spec: dict[str, Any]) -> str:
    return str(
        spec.get("freshness_date_column")
        or spec.get("date_param")
        or "trade_date"
    )


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [table],
        ).fetchone()
    )


def _columns(conn, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()}


def _normalised_rows_cte(
    conn,
    spec: dict[str, Any],
) -> tuple[str, list[Any], str, str, set[str]]:
    """Build the write-path-equivalent row set: universe-filtered and grain-deduped."""
    table = _table_name(spec)
    columns = _columns(conn, table)
    date_col = _date_column(spec)
    grain = [str(column) for column in spec.get("grain") or []]
    missing_grain = [column for column in grain if column not in columns]
    if not grain or missing_grain:
        raise ValueError(f"{table}: completeness grain missing: {missing_grain or grain!r}")
    if date_col not in columns or date_col not in grain:
        raise ValueError(
            f"{table}: completeness date column must exist in registry grain: {date_col!r}"
        )

    contract = spec.get("batch_completeness") or {}
    group_from = contract.get("group_from") or {}
    group_col = str(group_from.get("column") or "")
    if (contract.get("required_groups") or contract.get("required_groups_since")):
        if group_col not in columns or group_col not in grain:
            raise ValueError(
                f"{table}: completeness group column must exist in registry grain: {group_col!r}"
            )

    date_sql = _identifier(date_col)
    compact_date = f"REPLACE(SUBSTR(CAST({date_sql} AS VARCHAR), 1, 10), '-', '')"
    invalid_count = conn.execute(
        f"SELECT COUNT(*) FROM {_identifier(table)} WHERE {date_sql} IS NOT NULL "
        f"AND NOT REGEXP_FULL_MATCH({compact_date}, '^[0-9]{{8}}$')"
    ).fetchone()[0]
    if invalid_count:
        raise ValueError(f"{table}: {invalid_count} invalid completeness date values")

    # Landing completeness counts the full provider population.  Project-universe
    # filtering is a serve-time concern (universe_serve_filter), not a raw gap gate.
    filters = [f"{date_sql} IS NOT NULL"]
    params: list[Any] = []

    grain_sql = ", ".join(_identifier(column) for column in grain)
    built_at_sql = (
        f"MAX({_identifier('built_at')}) AS built_at"
        if "built_at" in columns
        else "NULL AS built_at"
    )
    cte = (
        "WITH normalised_rows AS ("
        f"SELECT {grain_sql}, {built_at_sql} FROM {_identifier(table)} "
        f"WHERE {' AND '.join(filters)} GROUP BY {grain_sql}"
        ")"
    )
    return cte, params, compact_date, group_col, columns


def complete_batch_dates(conn, spec: dict[str, Any]) -> set[str]:
    """Return compact dates whose row count and required groups are complete."""
    table = _table_name(spec)
    if not table or not _table_exists(conn, table):
        return set()

    cte, base_params, compact_date, group_col, _ = _normalised_rows_cte(conn, spec)
    min_rows = int(spec.get("min_rows_per_batch", 0))
    min_rows_since = str(spec.get("min_rows_since") or "").replace("-", "")
    min_rows_before = int(spec.get("min_rows_before", 1))
    having: list[str] = []
    params: list[Any] = list(base_params)
    if min_rows_since:
        having.append(
            f"COUNT(*) >= CASE WHEN {compact_date} >= ? THEN ? ELSE ? END"
        )
        params.extend([min_rows_since, min_rows, min_rows_before])
    else:
        having.append("COUNT(*) >= ?")
        params.append(min_rows)

    contract = spec.get("batch_completeness") or {}
    group_from = contract.get("group_from") or {}
    required = [str(group).upper() for group in contract.get("required_groups") or []]
    conditional = [
        (str(group).upper(), str(since).replace("-", ""))
        for group, since in (contract.get("required_groups_since") or {}).items()
    ]
    if required or conditional:
        group_sql = _identifier(group_col)
        transform = str(group_from.get("transform") or "")
        if transform == "exchange_suffix":
            group_value = f"UPPER(SPLIT_PART(CAST({group_sql} AS VARCHAR), '.', 2))"
        elif transform == "identity":
            group_value = f"UPPER(TRIM(CAST({group_sql} AS VARCHAR)))"
        else:
            raise ValueError(f"unsupported batch_completeness transform={transform!r}")
        for group in required:
            having.append(
                f"SUM(CASE WHEN {group_value} = ? THEN 1 ELSE 0 END) > 0"
            )
            params.append(group)
        for group, since in conditional:
            having.append(
                f"({compact_date} < ? OR "
                f"SUM(CASE WHEN {group_value} = ? THEN 1 ELSE 0 END) > 0)"
            )
            params.extend([since, group])

    rows = conn.execute(
        f"{cte} SELECT {compact_date} AS batch_date FROM normalised_rows "
        f"GROUP BY 1 HAVING {' AND '.join(having)}",
        params,
    ).fetchall()
    return {str(row[0]).replace("-", "") for row in rows if row[0]}


def latest_complete_batch(conn, spec: dict[str, Any]) -> VerifiedBatchFrontier | None:
    """Return the newest verified date plus the metadata of that exact partition."""
    dates = complete_batch_dates(conn, spec)
    if not dates:
        return None
    latest = max(dates)
    cte, params, compact_date, _, _ = _normalised_rows_cte(conn, spec)
    row = conn.execute(
        f"{cte} SELECT COUNT(*), MAX(built_at) FROM normalised_rows "
        f"WHERE {compact_date} = ?",
        [*params, latest],
    ).fetchone()
    return VerifiedBatchFrontier(
        last_date=latest,
        row_count=int(row[0]) if row else 0,
        last_success_at=row[1] if row else None,
    )
