"""Fixed schema contract for accepted same-day ST membership partitions."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.data_sources.security_day_partition import (
    SecurityDayDomain,
    schema_contract_hash,
    _freeze,
    _plain,
)
from services.data_sources.security_day_reader import lineage_fields

DATASET_ID = "tier0.security_identity.stock_st_daily"
LANDING_TABLE = "landing_tushare_stock_st"
CANONICAL_TABLE = "canonical_stock_st_daily"
SCHEMA_ID = "tier0.security_identity.stock_st_daily.canonical"
SCHEMA_VERSION = "1"
WRITER_ID = "services.data_sources.stock_st_acceptance"
CONTRACT_VERSION = "1"
PROVIDER_FIELDS = ("ts_code", "trade_date", "name", "type", "type_name")
TEXT_FIELDS = ("name", "type", "type_name")

_SCHEMA_PAYLOAD: dict[str, Any] = {
    "schema_id": SCHEMA_ID,
    "schema_version": SCHEMA_VERSION,
    "dataset_id": DATASET_ID,
    "canonical_table": CANONICAL_TABLE,
    "primary_key": ["trade_date", "ts_code"],
    "duplicate_policy": "reject",
    "fields": [
        {
            "name": "trade_date",
            "duckdb_type": "DATE",
            "nullable": False,
            "unit": "calendar_date",
            "null_semantics": "forbidden",
            "origin": "provider",
            "role": "event_and_effective_time",
        },
        {
            "name": "ts_code",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "security_identifier",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "name",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "security_name_label",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "type",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "st_type_code",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "type_name",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "st_type_label",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        *lineage_fields(),
    ],
}
SCHEMA_CONTRACT: Mapping[str, Any] = _freeze(_SCHEMA_PAYLOAD)
SCHEMA_HASH = schema_contract_hash(SCHEMA_CONTRACT)

DOMAIN = SecurityDayDomain(
    domain="stock_st",
    dataset_id=DATASET_ID,
    schema_id=SCHEMA_ID,
    schema_version=SCHEMA_VERSION,
    writer_id=WRITER_ID,
    landing_table=LANDING_TABLE,
    canonical_table=CANONICAL_TABLE,
    provider_fields=PROVIDER_FIELDS,
    numeric_fields=(),
    non_null_numeric_fields=(),
    text_fields=TEXT_FIELDS,
    grain=("ts_code", "trade_date"),
    partition_field="trade_date",
    # 2026-09-01 授权换源 -> 本地派生 (见 sync_registry stock_st 域注释)。source 参与
    # config_hash 计算, 换源后新写入行带新 hash、旧行保留旧值 = 预期溯源语义。
    source="stock_st_derive",
    api="stock_st",
    target_db="tushare_raw",
    compatibility_table="raw_tushare_stock_st",
    contract_version=CONTRACT_VERSION,
    coverage_start="20220104",
    available_after_legacy="09:20",
    availability_axis="trading_day",
    availability_rule="same_day_at",
    availability_at="09:20",
    population_kind="raw_evidence",
    population_label="provider_response",
    population_usage="evidence_only",
    min_rows=1,
    schema_payload=SCHEMA_CONTRACT,
    schema_hash=SCHEMA_HASH,
)


def schema_contract_payload() -> dict[str, Any]:
    return _plain(SCHEMA_CONTRACT)


__all__ = [
    "CANONICAL_TABLE",
    "CONTRACT_VERSION",
    "DATASET_ID",
    "DOMAIN",
    "LANDING_TABLE",
    "PROVIDER_FIELDS",
    "SCHEMA_CONTRACT",
    "SCHEMA_HASH",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "WRITER_ID",
    "schema_contract_payload",
]
