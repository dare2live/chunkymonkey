"""客户端写入登记表 — 单一真相源.

为什么需要这个:
- seed_dim_data_asset.py 之前把 KNOWN_UPSTREAM_BY_TABLE 硬编码在脚本里, 新增数据源时需要改两处
  (客户端代码 + 脚本元数据). 现在统一在这里声明, 脚本/路由/UI 都从此处读.
- 让 "新增一个数据源" 的流程清晰: 写客户端 → 在此处登记 → 自动出现在 dim_data_asset/UI

每个 ClientSpec 描述一个 *写入器* (而非一个数据源 — 后者是 data_sources/sources/).
一个客户端可能写多张表, 每张表关注不同的刷新粒度和 SLA.

freshness 枚举:
  t+0     — 当天数据 (盘后 / 隔夜可见)
  t+1     — 滞后一天可用
  weekly  — 每周一次
  monthly — 每月一次
  quarterly — 每季度
  static  — 长期不动 (字典/配置)
  on-demand — 手动触发
  derived — 由派生层计算, 不直接对外刷新
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TableWriteSpec:
    table: str
    purpose: str
    freshness: str = "t+1"
    sla_hours: int = 48
    asset_grain: str | None = None
    asset_cadence: str | None = None
    coverage_policy: str | None = None
    null_policy: str | None = None
    pit_policy: str | None = None
    intended_use: str | None = None
    model_eligibility: str | None = None
    strategy_eligibility: str | None = None
    frontend_visibility: str | None = None
    quality_gate_level: str | None = None


@dataclass
class ClientSpec:
    """一个数据写入客户端的元数据."""
    client_id: str                # e.g. "lhb_client"
    module: str                   # e.g. "services.lhb_client"
    description: str
    upstream_source: str          # e.g. "akshare:stock_lhb_detail_em"
    source_tier: int              # 1=primary, 2=secondary, 3=akshare-fallback, 99=derived
    fallback_chain: list[str] = field(default_factory=list)
    writes: list[TableWriteSpec] = field(default_factory=list)
    sync_step_id: Optional[str] = None   # updater.py 中的 step id (如有)


# ─────────────────────────────────────────────────────────────────────
# 客户端清单 (按 source_tier + alphabet 排)
# ─────────────────────────────────────────────────────────────────────

CLIENTS: list[ClientSpec] = [
    # ── tier 1: tdxhub (主源) ─────────────────────────────────────────
    # 退役 ClientSpec (tdx簇/xdxr/lhb 等 2026-06-23~29 数据纯化删) 详 ledger + deletion_record + git史。

    # ── tier 2: aif10 妙想 (主源) ─────────────────
    ClientSpec(
        client_id="qfii_client",
        module="services.qfii_client",
        description="QFII 持仓季频 (aif10 RPT_DMSK_HOLDERS 单源; akshare 兜底已批2c 退役)",
        upstream_source="aif10:RPT_DMSK_HOLDERS",
        source_tier=2,
        fallback_chain=["aif10"],
        writes=[
            TableWriteSpec("raw_qfii_holding_quarterly", "QFII 季频持仓", "quarterly", 24*95),
        ],
        sync_step_id="sync_qfii",
    ),
    # institution_survey_client ClientSpec 已删 2026-06-28 (批2 数据源切 tushare): aif10+akshare 源退役,
    #   调研走 tushare stk_surv (raw_tushare_stk_surv, sync_registry stk_surv 域); raw_institution_surveys
    #   + mart_stock_survey_activity 物删。机构调研 PIT 锚降级 surv_date+t+1 (tushare stk_surv 无 notice_date)。
    # aif10_capability_client ClientSpec 已删 2026-07-07: 唯二 capability (valuation_quantile/
    #   peer_valuation) 唯一消费方 v3_picture 已随 2026-06-28 重建退役且早在其存活期就因
    #   latest-snapshot leakage 从未被特征管线接入(PIT-safe替代 pe_ttm_z_1y/pb_z_1y 已存在),
    #   两表本批一并物删, services.aif10_capability_client 模块整体退役 git rm。

    # ── tier 3: akshare (兜底) ───────────────────────────────────────
    # margin_client ClientSpec removed Phase ψ.5 — dead data (see audit)
    # 退役 ClientSpec (capital/financial/akshare 簇 2026-06-19~28 删, 档B 需要时从 tushare 重接) 详 ledger + git史。
]


# ─────────────────────────────────────────────────────────────────────
# 派生层 — 不是直接对接外部源, 但仍是 dim_data_asset 的一员
# 这里仅登记 *跨多个 raw 输入* 的派生写入器, 单文件派生无需登记 (会被 grep 自动捕到)
# ─────────────────────────────────────────────────────────────────────

DERIVED_WRITERS: list[ClientSpec] = [
    # 退役派生 ClientSpec (event/panel/model runner 簇 2026-06-28 删; 机构档案 edge 重建时以披露日 T+1 口径重建) 详 ledger。
    ClientSpec(
        client_id="data_health_snapshot",
        module="scripts.data_health_snapshot",
        description="数据健康快照",
        upstream_source="derived: dim_data_asset + 各表 stats",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_data_health", "数据健康快照", "t+0", 25),
        ],
    ),
    # build_fund_flow_rank_snapshot_daily / build_candidate_feature_panel / feature_selection_experiments /
    #   validate_tdx_feature_pit / run_candidate_walkforward_eval / build_feature_retention_decisions /
    #   tdx_keep_challenger_productionization ClientSpec 已删 2026-06-28 (残留清理批1: module=scripts.<已删> +
    #   writes mart 全物删[策略/特征/模型/Optuna/fund_flow/tdx_keep 层退役], check_dead_references D 扫坐实死登记)。
    # data_deprecation_records client 已删 2026-06-28 (F4: services.data_deprecation 模块退役, 0 writer;
    #   mart_data_deprecation_record 现为只读历史 mart [schema_marts 建 + data_health 读], 无 active producer)
]


# ─────────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────────

def all_clients() -> list[ClientSpec]:
    """所有客户端 (源接入 + 派生)."""
    return list(CLIENTS) + list(DERIVED_WRITERS)


def get_table_metadata(table: str) -> Optional[tuple[ClientSpec, TableWriteSpec]]:
    """根据表名找到 (client, write_spec). 找不到返回 None."""
    for c in all_clients():
        for w in c.writes:
            if w.table == table:
                return c, w
    return None


# upstream_for_table / freshness_for_table 已删 2026-06-28 (F4: 仅 seed_dim_data_asset.py 兼容接口用,
#   seed 随 dim_data_asset 退役, 0 live 用户。表 metadata 仍由 get_table_metadata 提供给 data_health owner_hint)


def to_dicts() -> list[dict]:
    """JSON 序列化, 给路由/UI 用."""
    out = []
    for c in all_clients():
        d = asdict(c)
        # writes 里的 dataclass 已经被 asdict 递归展开
        out.append(d)
    return out
