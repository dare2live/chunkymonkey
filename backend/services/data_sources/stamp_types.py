"""check_contract_stamp_consistency 的共享类型 (2026-09-02 拆分).

从 check_contract_stamp_consistency.py 拆出 (原 1355 行触发
minimal-module-no-new-godfile god-file 断言)。本模块只放三段互不相干职责里
最基础的一段: 域注册表 (DomainSpec/DOMAIN_REGISTRY/DOMAIN_BY_DATASET_ID) +
结果类型 (Finding/DbUnavailable)。不反向 import stamp_discovery / stamp_checks
—— 那两个模块都要 import 本模块的类型, 反向会循环导入。

拆分本身是纯结构重排, 零行为变化; 各 dataclass/常量的说明文字原样搬自原文件
对应 SECTION, 未作任何逻辑改动。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

# ============================================================================
# SECTION 1 — 域注册表 (数据, 不是散落 hardcode; 与 check_grain_uniqueness.py 的
# MART_GRAINS 同一惯例: 集中一处数据表, 改契约新增域时只改这一张表)。
#
# 每条登记: 现算契约怎么 load (module:func[,固定 arg]) / db 在哪个 manifest alias /
# canonical 表叫什么、按哪列关联指针 / payload_hash 用哪套产线公式重算 (margin 是
# fragment 多请求家族; "simple" 是 holders_top10/stock_st/nominal_ohlcv/org_holding/
# stk_holdertrade 共用的单请求家族, 已实测 5 个域公式逐字节相同; calendar 独立一套
# 分页 fragment 家族)。
# ============================================================================


@dataclass(frozen=True)
class DomainSpec:
    name: str
    dataset_id: str
    db_alias: str
    loader_module: str
    loader_func: str
    canonical_table: str
    join_column: str
    payload_family: str  # "margin" | "simple" | "calendar"
    loader_arg: str | None = None
    landing_table: str | None = None  # payload_family == "simple"
    calendar_landing_table: str | None = None
    calendar_fragment_table: str | None = None

    def load_contract(self) -> Any:
        mod = importlib.import_module(self.loader_module)
        fn = getattr(mod, self.loader_func)
        return fn(self.loader_arg) if self.loader_arg is not None else fn()


DOMAIN_REGISTRY: tuple[DomainSpec, ...] = (
    DomainSpec(
        name="margin",
        dataset_id="tier0.market_data.margin_exchange_daily",
        db_alias="tushare_raw",
        loader_module="services.data_sources.contracts",
        loader_func="load_dataset_contract",
        loader_arg="margin",
        canonical_table="canonical_margin_exchange_daily",
        join_column="ingest_batch_id",
        payload_family="margin",
    ),
    DomainSpec(
        name="stock_st",
        dataset_id="tier0.security_identity.stock_st_daily",
        db_alias="tushare_raw",
        loader_module="services.data_sources.stock_st_contract",
        loader_func="load_stock_st_contract",
        canonical_table="canonical_stock_st_daily",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_tushare_stock_st",
    ),
    DomainSpec(
        name="nominal_ohlcv",
        dataset_id="tier0.market_data.nominal_ohlcv_daily",
        db_alias="tushare_raw",
        loader_module="services.data_sources.nominal_ohlcv_contract",
        loader_func="load_nominal_ohlcv_contract",
        canonical_table="canonical_nominal_ohlcv_daily",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_tushare_daily",
    ),
    DomainSpec(
        name="org_holding",
        dataset_id="tier0.disclosure.org_holding_detail_period",
        db_alias="org_holding",
        loader_module="services.data_sources.org_holding_contract",
        loader_func="load_org_holding_contract",
        canonical_table="canonical_org_holding_detail_period",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_miaoxiang_org_holding",
    ),
    DomainSpec(
        name="stk_holdertrade",
        dataset_id="tier0.disclosure.stock_holder_trade_announcement",
        db_alias="tushare_raw",
        loader_module="services.data_sources.stk_holdertrade_contract",
        loader_func="load_stk_holdertrade_contract",
        canonical_table="canonical_stk_holdertrade_announcement",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_tushare_stk_holdertrade",
    ),
    DomainSpec(
        name="holders_top10",
        dataset_id="tier0.disclosure.top10_float_holders_period",
        db_alias="smartmoney",
        loader_module="services.data_sources.holders_top10_contract",
        loader_func="load_holders_top10_contract",
        canonical_table="canonical_top10_float_holders_period",
        join_column="ingest_batch_id",
        payload_family="simple",
        landing_table="landing_miaoxiang_holders_top10",
    ),
    DomainSpec(
        name="calendar",
        dataset_id="tier0.reference.sse_trading_calendar_generation",
        db_alias="tushare_raw",
        loader_module="services.data_sources.calendar_reader",
        loader_func="_contract_from_live_registry",
        canonical_table="canonical_sse_trading_calendar_generation",
        join_column="generation_id",
        payload_family="calendar",
        calendar_landing_table="landing_tushare_trade_cal",
        calendar_fragment_table="landing_tushare_trade_cal_fragment",
    ),
)
DOMAIN_BY_DATASET_ID: dict[str, DomainSpec] = {d.dataset_id: d for d in DOMAIN_REGISTRY}


# ============================================================================
# SECTION 5 — Finding 数据结构
# ============================================================================


@dataclass
class Finding:
    check: str  # "1_pointer" | "2_canonical" | "3_lineage" | "4_ingest_batch" | "discovery"
    severity: str  # "FAIL" | "WARN" | "INFO"
    domain: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "domain": self.domain,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DbUnavailable:
    """discover_databases() 的"非连接"结果, 区分两种完全不同的情况 (2026-09-02 修:
    此前两种情况被合并成同一个"不可达"字符串, 而它们的正确应对天差地别):
      - "unreachable": 库真的连不上 (文件不存在/损坏/写锁占用等) —— 可能是瞬时运维状态,
        默认降级为 WARN (与 check_grain_uniqueness.py 的 db_unreachable 同一惯例)。
      - "missing_tables": 库能正常打开, 只是没有 accepted_partition/ingest_batch 这两张表
        —— 这不是"不可达", 是结构性问题 (表被改名/退役, 或本门的 DomainSpec.db_alias
        本身配错了)。默认按 FAIL 处理 (fail-closed): 这正是本门要根治的"该查的没查,
        输出却和查了没问题一模一样"的恒绿形态, 不能用"不可达"这种听起来无害的话带过。
    """

    reason: str  # "unreachable" | "missing_tables"
    detail: str

