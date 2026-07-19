"""E0 read-side shadow: legacy research tables vs accepted canonical projection.

Compares provider-field projections that research ultimately depends on.
``cutover_allowed`` is true only when all three inventory domains MATCH on the
partitions serving the response (honest serve-side gate).  Sidecar remains
observational; research read policy decides canonical vs legacy fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from services.data_sources.disclosure_boundaries import disclosure_domains
from services.data_sources.holders_top10_schema import (
    CANONICAL_TABLE as HOLDERS_CANONICAL,
    COMPATIBILITY_TABLE as HOLDERS_LEGACY,
    GRAIN as HOLDERS_GRAIN,
    PARTITION_FIELD as HOLDERS_PARTITION,
    PROVIDER_FIELDS as HOLDERS_FIELDS,
)
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE as ORG_CANONICAL,
    COMPATIBILITY_TABLE as ORG_LEGACY,
    GRAIN as ORG_GRAIN,
    PARTITION_FIELD as ORG_PARTITION,
    PROVIDER_FIELDS as ORG_FIELDS,
)
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE as STK_CANONICAL,
    COMPATIBILITY_TABLE as STK_LEGACY,
    GRAIN as STK_GRAIN,
    PARTITION_FIELD as STK_PARTITION,
    PROVIDER_FIELDS as STK_FIELDS,
)

ShadowStatus = Literal["MATCH", "MISMATCH", "UNAVAILABLE", "SKIPPED"]
OverallStatus = Literal["MATCH", "MISMATCH", "PARTIAL", "UNAVAILABLE", "NOT_EVALUATED"]

_DATE_FIELDS = frozenset(
    {
        "notice_date",
        "report_date",
        "available_date",
        "ann_date",
        "page_update_date",
        "effective_date",
    }
)


@dataclass(frozen=True)
class _DomainSpec:
    domain: str
    canonical_table: str
    legacy_table: str
    partition_field: str
    provider_fields: tuple[str, ...]
    grain: tuple[str, ...]
    legacy_extra_where: str | None = None


_SPECS: dict[str, _DomainSpec] = {
    "holders_top10": _DomainSpec(
        domain="holders_top10",
        canonical_table=HOLDERS_CANONICAL,
        legacy_table=HOLDERS_LEGACY,
        partition_field=HOLDERS_PARTITION,
        provider_fields=HOLDERS_FIELDS,
        grain=HOLDERS_GRAIN,
        legacy_extra_where="source = 'miaoxiang'",
    ),
    "org_holding": _DomainSpec(
        domain="org_holding",
        canonical_table=ORG_CANONICAL,
        legacy_table=ORG_LEGACY,
        partition_field=ORG_PARTITION,
        provider_fields=ORG_FIELDS,
        grain=ORG_GRAIN,
    ),
    "stk_holdertrade": _DomainSpec(
        domain="stk_holdertrade",
        canonical_table=STK_CANONICAL,
        legacy_table=STK_LEGACY,
        partition_field=STK_PARTITION,
        provider_fields=STK_FIELDS,
        grain=STK_GRAIN,
    ),
}


@dataclass(frozen=True)
class DisclosureDomainShadowReport:
    domain: str
    partition: str | None
    status: ShadowStatus
    legacy_row_count: int
    canonical_row_count: int
    compared_fields: tuple[str, ...]
    rows_match: bool
    mismatch_count: int
    sample_mismatches: tuple[dict[str, Any], ...]
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "partition": self.partition,
            "status": self.status,
            "legacy_row_count": self.legacy_row_count,
            "canonical_row_count": self.canonical_row_count,
            "compared_fields": list(self.compared_fields),
            "rows_match": self.rows_match,
            "mismatch_count": self.mismatch_count,
            "sample_mismatches": list(self.sample_mismatches),
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class DisclosureShadowCompareReport:
    """Read-only legacy vs canonical disclosure delta for cutover evidence."""

    overall_status: OverallStatus
    cutover_allowed: bool
    domains: tuple[DisclosureDomainShadowReport, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "cutover_allowed": self.cutover_allowed,
            "domains": [item.as_dict() for item in self.domains],
            "notes": list(self.notes),
        }


def _compact_yyyymmdd(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def _normalize_cell(field: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value)
    if field in _DATE_FIELDS or (
        len("".join(ch for ch in text if ch.isdigit())) >= 8
        and any(ch in text for ch in "-/")
        and field.endswith("date")
    ):
        compact = _compact_yyyymmdd(text)
        if compact is not None:
            return compact
    if isinstance(value, float) or (
        isinstance(value, str) and text.replace(".", "", 1).replace("-", "", 1).isdigit()
    ):
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            pass
    return text


def _normalize_row(fields: tuple[str, ...], row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(_normalize_cell(name, row.get(name)) for name in fields)


def _table_exists(conn, table: str) -> bool:
    try:
        rows = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = ?
             LIMIT 1
            """,
            [table],
        ).fetchall()
        return bool(rows)
    except Exception:  # noqa: BLE001 — catalog probe must fail closed
        try:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 0")
            return True
        except Exception:  # noqa: BLE001
            return False


def _select_sql(
    table: str,
    fields: tuple[str, ...],
    *,
    partition_field: str,
    partition: str | None,
    extra_where: str | None,
    limit: int,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if partition is not None:
        # Normalize both sides so ISO legacy dates match compact partitions.
        clauses.append(
            f"replace(CAST({partition_field} AS VARCHAR), '-', '') = ?"
        )
        params.append(partition)
    if extra_where:
        clauses.append(f"({extra_where})")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT {', '.join(fields)} FROM {table} {where} "
        f"ORDER BY {', '.join(fields)} LIMIT ?"
    )
    params.append(int(limit))
    return sql, params


def _fetch_rows(
    conn,
    table: str,
    fields: tuple[str, ...],
    *,
    partition_field: str,
    partition: str | None,
    extra_where: str | None,
    limit: int,
) -> list[dict[str, Any]] | None:
    if not _table_exists(conn, table):
        return None
    sql, params = _select_sql(
        table,
        fields,
        partition_field=partition_field,
        partition=partition,
        extra_where=extra_where,
        limit=limit,
    )
    try:
        raw = conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 — missing/locked table → unavailable
        return None
    return [dict(zip(fields, row, strict=True)) for row in raw]


def _latest_partition(conn, table: str, partition_field: str) -> str | None:
    if not _table_exists(conn, table):
        return None
    try:
        row = conn.execute(
            f"""
            SELECT replace(CAST({partition_field} AS VARCHAR), '-', '')
              FROM {table}
             WHERE {partition_field} IS NOT NULL
             ORDER BY 1 DESC
             LIMIT 1
            """
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    return _compact_yyyymmdd(row[0])


def _grain_key(
    fields: tuple[str, ...], grain: tuple[str, ...], row: Mapping[str, Any]
) -> tuple[Any, ...]:
    normalized = dict(zip(fields, _normalize_row(fields, row), strict=True))
    return tuple(normalized.get(name) for name in grain)


def compare_disclosure_domain_shadow(
    conn,
    domain: str,
    *,
    partition: str | None = None,
    max_rows: int = 200,
    sample_limit: int = 5,
) -> DisclosureDomainShadowReport:
    """Bounded legacy↔canonical provider-field compare for one disclosure domain."""

    spec = _SPECS.get(str(domain or "").strip())
    if spec is None:
        return DisclosureDomainShadowReport(
            domain=str(domain or ""),
            partition=None,
            status="UNAVAILABLE",
            legacy_row_count=0,
            canonical_row_count=0,
            compared_fields=(),
            rows_match=False,
            mismatch_count=0,
            sample_mismatches=(),
            issues=("unknown_disclosure_domain",),
        )

    issues: list[str] = ["disclosure_shadow_compare_only"]
    part = _compact_yyyymmdd(partition) if partition else None
    if part is None:
        part = _latest_partition(conn, spec.canonical_table, spec.partition_field)
        if part is None:
            part = _latest_partition(conn, spec.legacy_table, spec.partition_field)

    limit = max(1, min(int(max_rows), 2000))
    legacy_rows = _fetch_rows(
        conn,
        spec.legacy_table,
        spec.provider_fields,
        partition_field=spec.partition_field,
        partition=part,
        extra_where=spec.legacy_extra_where,
        limit=limit,
    )
    canonical_rows = _fetch_rows(
        conn,
        spec.canonical_table,
        spec.provider_fields,
        partition_field=spec.partition_field,
        partition=part,
        extra_where=None,
        limit=limit,
    )

    if legacy_rows is None and canonical_rows is None:
        issues.append("both_tables_unavailable")
        return DisclosureDomainShadowReport(
            domain=spec.domain,
            partition=part,
            status="UNAVAILABLE",
            legacy_row_count=0,
            canonical_row_count=0,
            compared_fields=spec.provider_fields,
            rows_match=False,
            mismatch_count=0,
            sample_mismatches=(),
            issues=tuple(issues),
        )
    if canonical_rows is None:
        issues.append("canonical_table_unavailable")
        return DisclosureDomainShadowReport(
            domain=spec.domain,
            partition=part,
            status="UNAVAILABLE",
            legacy_row_count=len(legacy_rows or ()),
            canonical_row_count=0,
            compared_fields=spec.provider_fields,
            rows_match=False,
            mismatch_count=0,
            sample_mismatches=(),
            issues=tuple(issues),
        )
    if legacy_rows is None:
        issues.append("legacy_table_unavailable")
        return DisclosureDomainShadowReport(
            domain=spec.domain,
            partition=part,
            status="UNAVAILABLE",
            legacy_row_count=0,
            canonical_row_count=len(canonical_rows),
            compared_fields=spec.provider_fields,
            rows_match=False,
            mismatch_count=0,
            sample_mismatches=(),
            issues=tuple(issues),
        )

    if not legacy_rows and not canonical_rows:
        issues.append("empty_partition_sample")
        return DisclosureDomainShadowReport(
            domain=spec.domain,
            partition=part,
            status="SKIPPED",
            legacy_row_count=0,
            canonical_row_count=0,
            compared_fields=spec.provider_fields,
            rows_match=False,
            mismatch_count=0,
            sample_mismatches=(),
            issues=tuple(issues),
        )

    legacy_map = {
        _grain_key(spec.provider_fields, spec.grain, row): _normalize_row(
            spec.provider_fields, row
        )
        for row in legacy_rows
    }
    canonical_map = {
        _grain_key(spec.provider_fields, spec.grain, row): _normalize_row(
            spec.provider_fields, row
        )
        for row in canonical_rows
    }

    mismatches: list[dict[str, Any]] = []
    keys = sorted(set(legacy_map) | set(canonical_map), key=lambda item: str(item))
    for key in keys:
        left = legacy_map.get(key)
        right = canonical_map.get(key)
        if left == right:
            continue
        diff_fields: list[str] = []
        if left is None or right is None:
            diff_fields.append("row_presence")
        else:
            for idx, field in enumerate(spec.provider_fields):
                if left[idx] != right[idx]:
                    diff_fields.append(field)
        if len(mismatches) < max(1, int(sample_limit)):
            mismatches.append(
                {
                    "grain": dict(zip(spec.grain, key, strict=True)),
                    "diff_fields": diff_fields,
                    "legacy": (
                        None
                        if left is None
                        else dict(zip(spec.provider_fields, left, strict=True))
                    ),
                    "canonical": (
                        None
                        if right is None
                        else dict(zip(spec.provider_fields, right, strict=True))
                    ),
                }
            )

    mismatch_count = 0
    for key in keys:
        if legacy_map.get(key) != canonical_map.get(key):
            mismatch_count += 1

    if mismatch_count == 0 and len(legacy_map) == len(canonical_map):
        status: ShadowStatus = "MATCH"
        rows_match = True
    else:
        status = "MISMATCH"
        rows_match = False
        if len(legacy_map) != len(canonical_map):
            issues.append("row_count_diverges")
        if mismatch_count:
            issues.append("provider_field_projection_diverges")

    return DisclosureDomainShadowReport(
        domain=spec.domain,
        partition=part,
        status=status,
        legacy_row_count=len(legacy_rows),
        canonical_row_count=len(canonical_rows),
        compared_fields=spec.provider_fields,
        rows_match=rows_match,
        mismatch_count=mismatch_count,
        sample_mismatches=tuple(mismatches),
        issues=tuple(issues),
    )


def compare_disclosure_research_shadow(
    conn,
    *,
    partitions: Mapping[str, str] | None = None,
    max_rows_per_domain: int = 200,
    domains: tuple[str, ...] | None = None,
    domain_conns: Mapping[str, Any] | None = None,
) -> DisclosureShadowCompareReport:
    """Shadow-compare all E0 disclosure domains for research cutover evidence.

    ``cutover_allowed`` is true only when the full three-domain inventory is
    selected and every domain MATCH on the partitions serving the response.

    ``domain_conns`` routes a domain to a different DuckDB connection when
    legacy/canonical live outside the default DB (stk_holdertrade → tushare_raw).
    """

    selected = domains or disclosure_domains()
    part_map = {str(k): str(v) for k, v in (partitions or {}).items()}
    conn_map = {str(k): v for k, v in (domain_conns or {}).items()}
    domain_reports = tuple(
        compare_disclosure_domain_shadow(
            conn_map.get(name, conn),
            name,
            partition=part_map.get(name),
            max_rows=max_rows_per_domain,
        )
        for name in selected
    )

    statuses = {item.status for item in domain_reports}
    if statuses == {"MATCH"}:
        overall: OverallStatus = "MATCH"
    elif statuses == {"UNAVAILABLE"}:
        overall = "UNAVAILABLE"
    elif statuses == {"SKIPPED"}:
        overall = "NOT_EVALUATED"
    elif "MISMATCH" in statuses:
        overall = "MISMATCH"
    elif "MATCH" in statuses and statuses - {"MATCH", "SKIPPED"}:
        overall = "PARTIAL"
    elif "MATCH" in statuses:
        overall = "PARTIAL"
    else:
        overall = "UNAVAILABLE"

    from services.data_sources.disclosure_research_read import (
        cutover_allowed_from_shadow,
    )

    provisional = DisclosureShadowCompareReport(
        overall_status=overall,
        cutover_allowed=False,
        domains=domain_reports,
        notes=(),
    )
    allowed = cutover_allowed_from_shadow(provisional)

    notes = [
        "disclosure_shadow_compare_sidecar",
        "cutover_allowed_requires_three_domain_match_on_serving_partitions",
    ]
    if allowed:
        notes.append("cutover_allowed_true")
        notes.append("research_read_prefers_canonical_for_matched_domains")
    else:
        notes.append("cutover_allowed_false")
    if overall == "MATCH":
        notes.append("fixture_or_sample_provider_fields_match")
    if overall == "MISMATCH":
        notes.append("legacy_canonical_provider_fields_diverge")
    if overall == "UNAVAILABLE":
        notes.append("accepted_canonical_projection_unavailable")

    return DisclosureShadowCompareReport(
        overall_status=overall,
        cutover_allowed=allowed,
        domains=domain_reports,
        notes=tuple(notes),
    )


def empty_disclosure_shadow(*, reason: str) -> DisclosureShadowCompareReport:
    """Fail-closed sidecar when no DB sample can be evaluated."""

    domains = tuple(
        DisclosureDomainShadowReport(
            domain=name,
            partition=None,
            status="UNAVAILABLE",
            legacy_row_count=0,
            canonical_row_count=0,
            compared_fields=_SPECS[name].provider_fields,
            rows_match=False,
            mismatch_count=0,
            sample_mismatches=(),
            issues=("disclosure_shadow_compare_only", reason),
        )
        for name in disclosure_domains()
    )
    return DisclosureShadowCompareReport(
        overall_status="UNAVAILABLE",
        cutover_allowed=False,
        domains=domains,
        notes=(
            "disclosure_shadow_compare_only",
            "cutover_allowed_false",
            reason,
        ),
    )


__all__ = [
    "DisclosureDomainShadowReport",
    "DisclosureShadowCompareReport",
    "compare_disclosure_domain_shadow",
    "compare_disclosure_research_shadow",
    "empty_disclosure_shadow",
]
