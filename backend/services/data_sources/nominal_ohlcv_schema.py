"""Fixed schema contract for accepted nominal daily OHLCV partitions."""
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

DATASET_ID = "tier0.market_data.nominal_ohlcv_daily"
LANDING_TABLE = "landing_tushare_daily"
CANONICAL_TABLE = "canonical_nominal_ohlcv_daily"
SCHEMA_ID = "tier0.market_data.nominal_ohlcv_daily.canonical"
SCHEMA_VERSION = "1"
WRITER_ID = "services.data_sources.nominal_ohlcv_acceptance"
CONTRACT_VERSION = "1"
PROVIDER_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
NUMERIC_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)

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
            "name": "open",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "high",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "low",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "close",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "pre_close",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "change",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "pct_chg",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "percent",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "vol",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "lot",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        {
            "name": "amount",
            "duckdb_type": "DOUBLE",
            "nullable": False,
            "unit": "CNY_thousand",
            "null_semantics": "forbidden",
            "origin": "provider",
        },
        *lineage_fields(),
    ],
}
SCHEMA_CONTRACT: Mapping[str, Any] = _freeze(_SCHEMA_PAYLOAD)
SCHEMA_HASH = schema_contract_hash(SCHEMA_CONTRACT)

DOMAIN = SecurityDayDomain(
    domain="daily",
    dataset_id=DATASET_ID,
    schema_id=SCHEMA_ID,
    schema_version=SCHEMA_VERSION,
    writer_id=WRITER_ID,
    landing_table=LANDING_TABLE,
    canonical_table=CANONICAL_TABLE,
    provider_fields=PROVIDER_FIELDS,
    numeric_fields=NUMERIC_FIELDS,
    non_null_numeric_fields=NUMERIC_FIELDS,
    text_fields=(),
    grain=("ts_code", "trade_date"),
    partition_field="trade_date",
    # 2026-09-01 授权换源 tushare -> tdxhub (通达信); tushare 授权 2026-09-10 到期不续期。
    # 实证零差异: 全市场 5208 只 x 9 字段 46872/46872 全对, 代码集双向零缺失。
    # 注 (2026-09-02 更正: 下面两句原文**都是错的**, 且同日实测各自造成过一次误判):
    #   原写「source 参与 config_hash/contract_hash 计算」—— 已不成立。同日
    #   nominal_ohlcv_contract.py:125-133 明确把 source/api 移出 config_payload
    #   (「传输轴非语义轴」), 换源不再改指纹。语义变更仍被 schema_hash/grain/partition_by/
    #   population_scope/availability/表名/coverage_start 完整覆盖; registry 与 DOMAIN 的
    #   source 一致性由 _expected_transport 独立守卫, 不靠 hash。
    #   原写「读侧无 "hash 必须相等" 的校验」—— **假的**。security_day_reader.py:96 就是
    #   严格相等; 正因如此, 当初若让 source 进指纹, 换源会让既有 accepted 分区全部读不出来
    #   (实测 daily 1,858 个 + stock_st 1,128 个)。这句话本身曾把人带进沟里, 别再复活。
    source="tdxhub",
    api="daily",
    target_db="tushare_raw",
    compatibility_table="raw_tushare_daily",
    contract_version=CONTRACT_VERSION,
    coverage_start="20190102",
    available_after_legacy="18:00",
    availability_axis="trading_day",
    availability_rule="same_day_at",
    availability_at="18:00",
    population_kind="raw_evidence",
    population_label="provider_response",
    population_usage="evidence_only",
    # Fixture/tests use small partitions; live canary still has registry min_rows.
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
