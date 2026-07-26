"""Fixed schema for E0 holders_top10 formal land→accept tracer.

Accepted truth is the landing/canonical pair.  Shadow compare uses
``PROVIDER_FIELDS`` only.  Episode enrichment columns live on canonical as
nullable ``ENRICHMENT_FIELDS`` so formal-only writes no longer depend on a
legacy mirror for research rebuild.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.security_day_partition import _freeze, _plain, schema_contract_hash

DATASET_ID = "tier0.disclosure.top10_float_holders_period"
LANDING_TABLE = "landing_miaoxiang_holders_top10"
CANONICAL_TABLE = "canonical_top10_float_holders_period"
SCHEMA_ID = "tier0.disclosure.top10_float_holders_period.canonical"
SCHEMA_VERSION = "2"
WRITER_ID = "services.data_sources.holders_top10_acceptance"
CONTRACT_VERSION = "3"
SOURCE = "miaoxiang"
API = "RPT_F10_EH_FREEHOLDERS"
# Retired 2026-07-26 — table DROPped; land-from-legacy / mirror refuse.
COMPATIBILITY_RETIRED = True
COMPATIBILITY_TABLE = "fact_top10_holder_period"  # sentinel name only; do not SQL
PARTITION_FIELD = "notice_date"
GRAIN = (
    "stock_code",
    "report_date",
    "holder_set",
    "holder_rank",
    "row_seq",
    "is_exit_row",
)


def assign_unique_holders_row_seq(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Renumber ``row_seq`` so GRAIN is unique within a partition load.

    Miaoxiang can emit multiple holder_name rows at the same HOLDER_RANK;
    legacy ``_clean`` historically hard-coded ``row_seq=1``, which then fails
    accept with DUPLICATE_GRAIN. ``row_seq`` exists in GRAIN for this case —
    assign stable 1..n ordered by holder_name within
    (stock_code, report_date, holder_set, holder_rank, is_exit_row).
    """

    from collections import defaultdict

    prepared = [dict(row) for row in rows]
    prepared.sort(
        key=lambda r: (
            str(r.get("stock_code") or ""),
            str(r.get("report_date") or ""),
            str(r.get("holder_set") or ""),
            int(r.get("holder_rank") or 0),
            bool(r.get("is_exit_row")),
            str(r.get("holder_name") or ""),
            str(r.get("notice_date") or ""),
        )
    )
    counters: dict[tuple[Any, ...], int] = defaultdict(int)
    out: list[dict[str, Any]] = []
    for row in prepared:
        key = (
            str(row.get("stock_code") or ""),
            str(row.get("report_date") or ""),
            str(row.get("holder_set") or ""),
            int(row.get("holder_rank") or 0),
            bool(row.get("is_exit_row")),
        )
        counters[key] += 1
        row["row_seq"] = counters[key]
        out.append(row)
    return out


# Shadow / provider identity projection (stable compare surface).
PROVIDER_FIELDS = (
    "stock_code",
    "report_date",
    "holder_set",
    "holder_rank",
    "row_seq",
    "holder_name",
    "hold_ratio_float",
    "notice_date",
    "is_exit_row",
)
# Episode rebuild columns carried on canonical (nullable for historical canary).
ENRICHMENT_FIELDS = (
    "holder_name_norm",
    "share_class",
    "shares_approx",
    "change_status",
    "hold_change_num",
    "holder_type",
)
CANONICAL_ROW_FIELDS = PROVIDER_FIELDS + ENRICHMENT_FIELDS

_SCHEMA_PAYLOAD: dict[str, Any] = {
    "schema_id": SCHEMA_ID,
    "schema_version": SCHEMA_VERSION,
    "dataset_id": DATASET_ID,
    "canonical_table": CANONICAL_TABLE,
    "primary_key": list(GRAIN),
    "duplicate_policy": "reject",
    "fields": [
        {
            "name": "stock_code",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "security_code",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "report_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "report_period_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_set",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "holder_set_label",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_rank",
            "duckdb_type": "INTEGER",
            "nullable": False,
            "unit": "rank",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "row_seq",
            "duckdb_type": "INTEGER",
            "nullable": False,
            "unit": "row_sequence",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_name",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "holder_name_label",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "hold_ratio_float",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "percent",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "notice_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "calendar_date_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "provider",
            "role": "availability_event_time",
        },
        {
            "name": "is_exit_row",
            "duckdb_type": "BOOLEAN",
            "nullable": False,
            "unit": "derived_exit_flag",
            "null_semantics": "forbidden",
            "origin": "derived",
        },
        {
            "name": "holder_name_norm",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "holder_name_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "share_class",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "share_class_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "shares_approx",
            "duckdb_type": "BIGINT",
            "nullable": True,
            "unit": "share_count",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "change_status",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "change_status_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "hold_change_num",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "share_count_delta",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "holder_type",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "holder_type_label",
            "null_semantics": "enrichment_optional_historical_null",
            "origin": "enrichment",
        },
        {
            "name": "available_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_instant",
            "null_semantics": "forbidden",
            "origin": "batch",
            "role": "publication_visibility",
        },
        {
            "name": "ingest_batch_id",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "batch_id",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "source_row_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "contract_version",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "version_token",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "config_hash",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "sha256_hex",
            "null_semantics": "forbidden",
            "origin": "system",
        },
        {
            "name": "built_at",
            "duckdb_type": "TIMESTAMP WITH TIME ZONE",
            "nullable": False,
            "unit": "utc_instant",
            "null_semantics": "forbidden",
            "origin": "system",
        },
    ],
}
SCHEMA_CONTRACT: Mapping[str, Any] = _freeze(_SCHEMA_PAYLOAD)
SCHEMA_HASH = schema_contract_hash(SCHEMA_CONTRACT)


def schema_contract_payload() -> dict[str, Any]:
    return _plain(SCHEMA_CONTRACT)


__all__ = [
    "ACCEPTED_TABLE",
    "API",
    "CANONICAL_ROW_FIELDS",
    "CANONICAL_TABLE",
    "COMPATIBILITY_RETIRED",
    "COMPATIBILITY_TABLE",
    "CONTRACT_VERSION",
    "DATASET_ID",
    "ENRICHMENT_FIELDS",
    "GRAIN",
    "INGEST_BATCH_TABLE",
    "LANDING_TABLE",
    "PARTITION_FIELD",
    "PROVIDER_FIELDS",
    "SCHEMA_CONTRACT",
    "SCHEMA_HASH",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SOURCE",
    "WRITER_ID",
    "assign_unique_holders_row_seq",
    "schema_contract_payload",
]
