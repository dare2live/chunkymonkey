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
    # tdx_industry_client + block_client (通达信行业/板块) 已退役物删 2026-06-23 (§4.3 行业/概念全切东财 dim_stock_dc_industry/dc_concept)
    # tdx_affair_client (通达信季报 gpcw) 退役物删 2026-06-27 (通达信全删: 财务派生迁 tushare 周期模型;
    #   gpcw raw_detail/wide + dim_field 簇物删; client 0 import dead)。
    # tdx_f10_extra_client 退役 2026-06-24 (随旧 updater 删, 唯一 caller updater_sync.sync_raw 已删):
    #   户数→tushare stk_holdernumber / 增减持→tushare stk_holdertrade / 同大股东→aif10 holder derive (机构档案);
    #   十大流通股东已迁 aif10 (services/holders_aif10). 详 analysis/miaoxiang_aif10_source_decision_20260624.md。
    # xdxr_client ClientSpec 已删 2026-06-28 (残留清理批1: services.xdxr_client 模块已删, 复权切 tushare adj_factor;
    #   price_xdxr 表批3 物删)。check_dead_references D 扫坐实死登记。

    # ── tier 2: aif10 妙想 (主源) ─────────────────
    # lhb_client ClientSpec 已删 2026-06-29 (批2b 数据源切 tushare): LHB 走 tushare top_list/top_inst
    #   (raw_tushare_top_list 142k / top_inst 178万 已拉全, SERVE entity top_list/top_inst 在); raw_lhb_daily(aif10+akshare) 退役物删。
    ClientSpec(
        client_id="qfii_client",
        module="services.qfii_client",
        description="QFII 持仓季频 (aif10 RPT_DMSK_HOLDERS, akshare 兜底)",
        upstream_source="aif10:RPT_DMSK_HOLDERS",
        source_tier=2,
        fallback_chain=["aif10", "akshare"],
        writes=[
            TableWriteSpec("raw_qfii_holding_quarterly", "QFII 季频持仓", "quarterly", 24*95),
        ],
        sync_step_id="sync_qfii",
    ),
    # institution_survey_client ClientSpec 已删 2026-06-28 (批2 数据源切 tushare): aif10+akshare 源退役,
    #   调研走 tushare stk_surv (raw_tushare_stk_surv, sync_registry stk_surv 域); raw_institution_surveys
    #   + mart_stock_survey_activity 物删。机构调研 PIT 锚降级 surv_date+t+1 (tushare stk_surv 无 notice_date)。
    ClientSpec(
        client_id="aif10_capability_client",
        module="services.aif10_capability_client",
        description="妙想 F10 能力面板 (4 张 raw + 财务历史)",
        upstream_source="aif10:F10",
        source_tier=2,
        fallback_chain=["aif10"],
        writes=[
            TableWriteSpec("raw_aif10_valuation_quantile",  "估值分位",      "t+1",       48),
            TableWriteSpec("raw_aif10_peer_valuation",      "同行排名",      "quarterly", 24*95),
            # raw_aif10_forecast_consensus 已删 2026-06-28 (G5 退役: 0 消费方)
        ],
        sync_step_id="sync_aif10_*",
    ),

    # ── tier 3: akshare (兜底) ───────────────────────────────────────
    # margin_client ClientSpec removed Phase ψ.5 — dead data (see audit)
    # capital_client (akshare 资本运作 分红/回购/解禁/配股) 已退役 2026-06-27 (通达信全删 M4):
    #   用户决"cut"不迁移 — 7表(raw_capital_* 5 + dim_capital_behavior_latest + capital_detail_sync_state)+writer物删,
    #   消费侧 scoring/signals_v2 已切; 档B 若需从 tushare dividend/repurchase/share_float 重接。
    # financial_client ClientSpec 已删 2026-06-28 (残留清理批1: services.financial_client 模块已删[U4],
    #   dim_financial_latest/fact_financial_derived 表已物删; 财务走 tushare sync:fina_indicator/balancesheet/income 域)。
    # financial_indicator_client (akshare 财务指标) 已退役 2026-06-19。
    # akshare_client ClientSpec 已删 2026-06-28 (残留清理批1: services.akshare_client 模块已删, K线 tushare 唯一;
    #   price_kline akshare HS300 表批3 物删, canonical=price_kline_qfq_tushare)。
    # akshare_panel_client (build_akshare_panel: jgdy/dzjy/hot_rank/research_report/profit_forecast) +
    # executive_trade_client (build_executive_trade_events: raw_executive_trade/fact_executive_trade_event)
    # 已退役 2026-06-27 (通达信全删 M4: akshare event 表退役, 用户决cut不迁移):
    #   builder 非 live (不在 pipeline DAG); 喂 panel 的 fact_feature_panel 非 live built; 下游 fact_capital_flow_pit_daily 已 reset 删=死链。
    #   feature_registry event_activity 的 akshare 源特征 (exec_buy_*/dzjy/jgdy) 标 research_only_source_gap, 档B 从 tushare(block_trade/stk_holdertrade)重接。
]


# ─────────────────────────────────────────────────────────────────────
# 派生层 — 不是直接对接外部源, 但仍是 dim_data_asset 的一员
# 这里仅登记 *跨多个 raw 输入* 的派生写入器, 单文件派生无需登记 (会被 grep 自动捕到)
# ─────────────────────────────────────────────────────────────────────

DERIVED_WRITERS: list[ClientSpec] = [
    # rebuild_holder_events + build_lhb_events ClientSpec 已删 2026-06-28 (Phase 0 机构+事件 serving 退役):
    #   fact_holder_event/fact_lhb_event 是死 event 派生 — DB 已无表, 消费者 run_portfolio_mvp simulator 已退役;
    #   rebuild_holder_events 脚本本已不存在(死注册)。raw_lhb_daily/fact_top10_holder_period 基础表保留,
    #   Phase 3 机构档案以 as-of 跟随口径(披露日 T+1 进出)重建进出事件, 不复活旧 event 派生。
    # build_feature_panel_duck / run_daily_topk / train_multidim_model / run_multidim_walkforward ClientSpec
    #   已删 2026-06-28 (残留清理批1: module=scripts.<已删> + writes 表全物删[策略/特征/模型层退役], D 扫坐实)。
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
