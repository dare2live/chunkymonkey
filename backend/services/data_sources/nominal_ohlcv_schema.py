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
# 2026-09-13 v2: pre_close/change/pct_chg 改可空 + 新增 pre_close_origin。
# 改 canonical 形状必须同时抬 schema 与 contract 版本, 否则下游分不清两份数据
# (同 holders_top10 的 test_identity_promotion_bumped_both_versions)。
SCHEMA_VERSION = "2"
WRITER_ID = "services.data_sources.nominal_ohlcv_acceptance"
CONTRACT_VERSION = "2"
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
        # 2026-09-13 v2 改可空。理由不是"值不重要", 恰恰相反: 它是 adjust_factor 全链的
        # 唯一输入 (ratio[t] = close[t-1] / pre_close[t]), 错一个点会让那只股此后全部
        # hfq_factor 永久作废。v1 声明 forbidden 的代价是**整个交易日的分区被 REJECTED**
        # (NULL_NUMERIC), 即一行取不到就丢一天 —— 那不是"缺失传播为缺失", 是缺失放大。
        # 可空之后, 取不到的那一行落 NULL、其余行照常, 由消费方按 null_semantics 处置。
        # origin 保持 provider 不动: 可空性与"值由谁产生"是正交的两个轴 (margin 同表内
        # rqye/rqmcl/rqyl 也是 nullable=True + 同一 null 语义 + origin=provider);
        # 逐行的真实来源由 pre_close_origin 说, 不挤进列级声明。
        {
            "name": "pre_close",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "CNY",
            "null_semantics": "provider_unknown_or_not_reported; never_zero_fill",
            "origin": "provider",
        },
        # change 与 pct_chg 都是从 pre_close 派生的 (change = close - pre_close), 所以
        # pre_close 不可知时它们必然也不可知 —— 让它们继续 forbidden 等于强迫适配器
        # 用一个编造的 pre_close 去算出两个编造的值。
        {
            "name": "change",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "CNY",
            "null_semantics": "provider_unknown_or_not_reported; never_zero_fill",
            "origin": "provider",
        },
        {
            "name": "pct_chg",
            "duckdb_type": "DOUBLE",
            "nullable": True,
            "unit": "percent",
            "null_semantics": "provider_unknown_or_not_reported; never_zero_fill",
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
        # 2026-09-13 v2 新增。**域级**增补列, 不进 lineage_fields() —— 那六列是所有
        # SecurityDay 域共有的血缘列, 而 stock_st 没有 pre_close, 不该长出这一列。
        # 也**不进 PROVIDER_FIELDS**: canonical_content_hash 只按 provider_fields 算,
        # 实测同一批行带不带本列算出的 content_hash 逐位相同 —— 所以加它不动既有
        # 859 万行指纹, 也不动两份冻结快照。
        # 为什么需要它: 三列可空之后, "这一行的 pre_close 到底是谁给的" 成了必须回答
        # 的问题 —— 是供应商原样给的、是按交易所公式推的、还是根本不知道, 三者的可信
        # 度天差地别, 而列级 origin 说不了逐行的事。
        # 取值不用 allowed_values 做 CHECK: 实测 DuckDB 1.5.2 不支持给既有表 ALTER 加
        # CHECK, 那会造成"测试新建表有约束、859 万行的生产表永远没有"的分裂; 改由
        # _candidate_rows 逐行校验 (对新表旧表一视同仁)。
        {
            "name": "pre_close_origin",
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

ENRICHMENT_FIELDS = ("pre_close_origin",)
# 从 payload 的 nullable 派生, 不手写第二份清单 (照 margin_schema.NON_NULL_NUMERIC_FIELDS
# 的形态)。手写会让"schema 说可空"与"non_null 清单"两处各抄一份, 改一处漏一处就永久
# 不等且没有任何东西会红 —— holders_top10 的 _HASH_FIELDS 注释记的正是这个教训。
_FIELD_BY_NAME = {str(f["name"]): f for f in _SCHEMA_PAYLOAD["fields"]}
NON_NULL_NUMERIC_FIELDS = tuple(
    name for name in NUMERIC_FIELDS if not bool(_FIELD_BY_NAME[name]["nullable"])
)

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
    non_null_numeric_fields=NON_NULL_NUMERIC_FIELDS,
    enrichment_fields=ENRICHMENT_FIELDS,
    text_fields=(),
    grain=("ts_code", "trade_date"),
    partition_field="trade_date",
    # 2026-09-08: vol > 0 才算这一行真的成交了。通达信自 2026-08-31 起把停牌日也落成一行
    # (四价=前收, vol=0), tushare 七年是整行不落 —— 同一只股 000635.SZ 08-28 无行、08-31 有行,
    # 同样是停牌, 差别只在供货商。universe 判据「观察日有名义日 K 线」原本靠"停牌日没有行"
    # 才等价于"在交易", 换源后这个等价消失, 故把判据显式化到本字段。
    # 对 2026-08-28 及以前完全 no-op: 全表 8,595,304 行里 vol=0 与 vol IS NULL 各 0 行。
    activity_field="vol",
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
    "ENRICHMENT_FIELDS",
    "LANDING_TABLE",
    "NON_NULL_NUMERIC_FIELDS",
    "PROVIDER_FIELDS",
    "SCHEMA_CONTRACT",
    "SCHEMA_HASH",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "WRITER_ID",
    "schema_contract_payload",
]
