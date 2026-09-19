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
from services.data_sources.stock_st_acquire_rules import (
    build_st_origin_validator,
    load_stock_st_acquire_rules,
)

DATASET_ID = "tier0.security_identity.stock_st_daily"
LANDING_TABLE = "landing_tushare_stock_st"
CANONICAL_TABLE = "canonical_stock_st_daily"
SCHEMA_ID = "tier0.security_identity.stock_st_daily.canonical"
# 2026-09-18 v2: name 改可空 (baostock 水库路径不报告简称) + 新增 st_origin
# (逐行来源标签, 见 stock_st_acquire.yaml)。改 canonical 形状必须同时抬 schema 与
# contract 版本, 否则下游分不清两份数据 (同 daily v2 的先例)。
SCHEMA_VERSION = "2"
WRITER_ID = "services.data_sources.stock_st_acceptance"
CONTRACT_VERSION = "2"
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
        # 2026-09-18 v2 改可空。理由不是"名字不重要"——是 baostock 水库路径 (isST)
        # 结构上不给简称 (F4: 同接口同行没有名称字段), 而名称快照路径与历史 tushare
        # 行仍然报告 name。v1 声明 forbidden 的代价是 baostock 路径的每一行都要在
        # name 上编造一个假值才能落地——那是伪造, 不是"缺失传播为缺失"。
        # null_semantics 显式区分"供应商结构性不报告"与"其它域常见的暂不可知",
        # 防止读侧把它当成可以随便 fallback 的暂态缺失。
        # origin 保持 provider 不动: 可空性与"值由谁产生"是正交的两个轴 (同
        # nominal_ohlcv_schema.py pre_close 的论证)。逐行的真实来源由 st_origin 说,
        # 不挤进列级声明。
        {
            "name": "name",
            "duckdb_type": "VARCHAR",
            "nullable": True,
            "unit": "security_name_label",
            "null_semantics": "provider_does_not_report_name; never_fill_from_name_snapshot",
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
        # 2026-09-18 v2 新增。**域级**增补列, 不进 lineage_fields() (那六列是所有
        # SecurityDay 域共有的血缘列) 也**不进 PROVIDER_FIELDS** (canonical_content_hash
        # 只按 provider_fields 算, 加它不动既有 173,413 行历史指纹, 同 daily
        # pre_close_origin 的先例已实测这一性质)。
        # 为什么需要它: name 可空之后, "这一行是不是 ST 成员这个判断从哪来" 成了必须
        # 逐行回答的问题——三条路径 (tushare 供应商原行 / 名称快照派生 / baostock 水库
        # 派生) 的可信度与覆盖范围天差地别, 列级 origin 说不了逐行的事。取值集/
        # kind/reports_name/coverage 定义在 stock_st_acquire.yaml, 不进
        # _SCHEMA_PAYLOAD (进 payload 会改 SCHEMA_HASH)。
        {
            "name": "st_origin",
            "duckdb_type": "VARCHAR",
            "nullable": False,
            "unit": "provenance_label",
            "null_semantics": "forbidden",
            "origin": "system",
            "role": "value_provenance",
        },
    ],
}
SCHEMA_CONTRACT: Mapping[str, Any] = _freeze(_SCHEMA_PAYLOAD)
SCHEMA_HASH = schema_contract_hash(SCHEMA_CONTRACT)

ENRICHMENT_FIELDS = ("st_origin",)

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
    # 2026-09-01 授权换源 -> 本地派生 (见 sync_registry stock_st 域注释)。
    # 2026-09-18 更正 (F6, 同 nominal_ohlcv_schema.py:227-235 已更正的同型注释):
    # source/api **不参与** config_hash 计算 —— stock_st_contract.py:124-133 明确把
    # 它们移出 config_payload ("传输轴非语义轴")。换源不改指纹; 语义变更仍被
    # schema_hash/grain/partition_by/population_scope/availability/表名/coverage_start
    # 完整覆盖。这条注释曾经说反过来 (source 参与 config_hash), 那是错的 —— 若真让
    # source 进指纹, 换源会让既有 accepted 分区 (1,128 个) 全部读不出来
    # (security_day_reader 对指针戳严格相等)。
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
    enrichment_fields=ENRICHMENT_FIELDS,
    # 2026-09-18: 域级逐行校验钩子 —— st_origin 取值集/reports_name⇔name非NULL 校验,
    # 规则本身定义在 stock_st_acquire_rules.py, 这里只接一个通用调用点 (同 daily
    # pre_close_origin 校验钩子的先例)。
    enrichment_validator=build_st_origin_validator(load_stock_st_acquire_rules()),
)


def schema_contract_payload() -> dict[str, Any]:
    return _plain(SCHEMA_CONTRACT)


__all__ = [
    "CANONICAL_TABLE",
    "CONTRACT_VERSION",
    "DATASET_ID",
    "DOMAIN",
    "ENRICHMENT_FIELDS",
    "LANDING_TABLE",
    "PROVIDER_FIELDS",
    "SCHEMA_CONTRACT",
    "SCHEMA_HASH",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "WRITER_ID",
    "schema_contract_payload",
]
