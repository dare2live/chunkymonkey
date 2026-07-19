"""Fixed schema for E0 org_holding formal land→accept tracer.

Compatibility research table remains ``raw_org_holding_aif10`` (NONCONFORMING
direct-write strangler) until DatasetSnapshot cutover.  Accepted truth for this
tracer is the landing/canonical pair below.  Partition axis = available_date
(disclosure deadline upper bound).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.security_day_partition import _freeze, _plain, schema_contract_hash

DATASET_ID = "tier0.disclosure.org_holding_detail_period"
LANDING_TABLE = "landing_miaoxiang_org_holding"
CANONICAL_TABLE = "canonical_org_holding_detail_period"
SCHEMA_ID = "tier0.disclosure.org_holding_detail_period.canonical"
SCHEMA_VERSION = "1"
WRITER_ID = "services.data_sources.org_holding_acceptance"
CONTRACT_VERSION = "1"
SOURCE = "miaoxiang"
API = "RPT_MAIN_ORGHOLDDETAIL"
COMPATIBILITY_TABLE = "raw_org_holding_aif10"
PARTITION_FIELD = "available_date"
GRAIN = (
    "report_date",
    "stock_code",
    "holder_code",
    "fund_derivecode",
)
PROVIDER_FIELDS = (
    "report_date",
    "available_date",
    "stock_code",
    "holder_code",
    "fund_derivecode",
    "holder_name",
    "org_type_name",
    "total_shares",
    "free_shares_ratio",
)


def disclosure_deadline_yyyymmdd(report_date: str) -> str | None:
    """Report period → statutory disclosure deadline (PIT conservative upper bound)."""

    compact = str(report_date or "").replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        return None
    y, md = compact[:4], compact[4:]
    deadline = {
        "0331": f"{y}0430",
        "0630": f"{y}0831",
        "0930": f"{y}1031",
        "1231": f"{int(y) + 1}0430",
    }.get(md)
    return deadline


_SCHEMA_PAYLOAD: dict[str, Any] = {
    "schema_id": SCHEMA_ID,
    "schema_version": SCHEMA_VERSION,
    "dataset_id": DATASET_ID,
    "canonical_table": CANONICAL_TABLE,
    "primary_key": list(GRAIN),
    "duplicate_policy": "reject",
    "fields": [
        {
            "name": "report_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "report_period_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "available_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "calendar_date_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "derived",
            "role": "availability_event_time",
        },
        {
            "name": "stock_code",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "security_code",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_code",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "holder_code",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "fund_derivecode",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "fund_derive_code",
            "null_semantics": "empty_string_allowed",
            "origin": "provider",
        },
        {
            "name": "holder_name",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "holder_name_label",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "org_type_name",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "org_type_label",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "total_shares",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "shares",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "free_shares_ratio",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "percent",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
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
    "CANONICAL_TABLE",
    "COMPATIBILITY_TABLE",
    "CONTRACT_VERSION",
    "DATASET_ID",
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
    "disclosure_deadline_yyyymmdd",
    "schema_contract_payload",
]
