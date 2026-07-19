"""Fixed schema for E0 stk_holdertrade formal land→accept tracer.

Compatibility research table remains ``raw_tushare_stk_holdertrade``
(NONCONFORMING direct-write strangler) until DatasetSnapshot cutover.
Partition axis = ann_date.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE, INGEST_BATCH_TABLE
from services.data_sources.security_day_partition import _freeze, _plain, schema_contract_hash

DATASET_ID = "tier0.disclosure.stock_holder_trade_announcement"
LANDING_TABLE = "landing_tushare_stk_holdertrade"
CANONICAL_TABLE = "canonical_stk_holdertrade_announcement"
SCHEMA_ID = "tier0.disclosure.stock_holder_trade_announcement.canonical"
SCHEMA_VERSION = "1"
WRITER_ID = "services.data_sources.stk_holdertrade_acceptance"
CONTRACT_VERSION = "1"
SOURCE = "tushare"
API = "stk_holdertrade"
COMPATIBILITY_TABLE = "raw_tushare_stk_holdertrade"
PARTITION_FIELD = "ann_date"
GRAIN = (
    "ts_code",
    "ann_date",
    "holder_name",
    "in_de",
)
PROVIDER_FIELDS = (
    "ts_code",
    "ann_date",
    "holder_name",
    "in_de",
    "holder_type",
    "change_vol",
    "change_ratio",
    "after_share",
    "after_ratio",
    "avg_price",
    "total_share",
)

_SCHEMA_PAYLOAD: dict[str, Any] = {
    "schema_id": SCHEMA_ID,
    "schema_version": SCHEMA_VERSION,
    "dataset_id": DATASET_ID,
    "canonical_table": CANONICAL_TABLE,
    "primary_key": list(GRAIN),
    "duplicate_policy": "reject",
    "fields": [
        {
            "name": "ts_code",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "ts_code",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "ann_date",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "calendar_date_yyyymmdd",
            "null_semantics": "forbidden",
            "origin": "provider",
            "role": "availability_event_time",
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
            "name": "in_de",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "in_de_direction",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "holder_type",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "holder_type_code",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "change_vol",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "shares",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "change_ratio",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "percent",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "after_share",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "shares",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "after_ratio",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "percent",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "avg_price",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "price",
            "null_semantics": "provider_null_allowed",
            "origin": "provider",
        },
        {
            "name": "total_share",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "shares",
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
    "schema_contract_payload",
]
