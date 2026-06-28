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
    ClientSpec(
        client_id="xdxr_client",
        module="services.xdxr_client",
        description="除权除息事件 (写市场库 market.duckdb)",
        upstream_source="tdxhub.quotes",
        source_tier=1,
        fallback_chain=["tdxhub"],
        writes=[
            TableWriteSpec("price_xdxr", "除权除息事件 (market.duckdb)", "t+1", 48),
        ],
        sync_step_id="sync_xdxr",
    ),

    # ── tier 2: aif10 妙想 (tdxhub 不覆盖时的次级源) ─────────────────
    ClientSpec(
        client_id="lhb_client",
        module="services.lhb_client",
        description="龙虎榜 (aif10 RPT_DAILYBILLBOARD_DETAILSNEW, akshare 兜底)",
        upstream_source="aif10:RPT_DAILYBILLBOARD_DETAILSNEW",
        source_tier=2,
        fallback_chain=["aif10", "akshare"],
        writes=[
            TableWriteSpec("raw_lhb_daily", "龙虎榜原始", "t+1", 48),
        ],
        sync_step_id="sync_lhb",
    ),
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
    ClientSpec(
        client_id="institution_survey_client",
        module="services.institution_survey_client",
        description="机构调研 (aif10 RPT_ORG_SURVEYNEW, akshare 兜底)",
        upstream_source="aif10:RPT_ORG_SURVEYNEW",
        source_tier=2,
        fallback_chain=["aif10", "akshare"],
        writes=[
            TableWriteSpec("raw_institution_surveys", "机构调研原始", "t+1", 48),
            TableWriteSpec("mart_stock_survey_activity", "调研活动派生", "t+1", 48),
        ],
        sync_step_id="sync_surveys",
    ),
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
    ClientSpec(
        client_id="financial_client",
        module="services.financial_client",
        description="财务派生指标 (tushare 周期模型)",  # 2026-06-27 通达信全删: gpcw兜底退役, calc_financial_derived 读 tushare
        upstream_source="tushare fina_indicator/balancesheet/income (registry sync)",
        source_tier=1,
        fallback_chain=["tushare"],
        writes=[
            # raw_gpcw_financial 已物删 (2026-06-27): 源迁 tushare 周期模型
            TableWriteSpec("dim_financial_latest",     "最新季报快照", "quarterly", 24*95),
            TableWriteSpec("fact_financial_derived",   "派生财务指标", "quarterly", 24*95),
        ],
        sync_step_id="sync_financial",
    ),
    # financial_indicator_client (akshare 财务指标) 已退役 2026-06-19: 表(fact/dim/raw/sync_state)+writer物删,
    #   0 live alpha 消费者; 财务指标走 tushare fina_indicator。
    ClientSpec(
        client_id="akshare_client",
        module="services.akshare_client",
        description="K线 (写市场库 market.duckdb.price_kline)",
        upstream_source="akshare:stock_zh_a_hist (东财→新浪→腾讯)",
        source_tier=3,
        fallback_chain=["akshare"],
        writes=[
            TableWriteSpec("price_kline", "K 线 (market.duckdb)", "t+0", 24),
        ],
        sync_step_id="sync_kline",
    ),
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
    ClientSpec(
        client_id="build_feature_panel_duck",
        module="scripts.build_feature_panel_duck",
        description="特征面板 (Alpha158)",
        upstream_source="derived: kline + 财务",
        source_tier=99,
        writes=[
            TableWriteSpec("fact_feature_panel", "特征面板", "t+1", 36),
        ],
    ),
    ClientSpec(
        client_id="run_daily_topk",
        module="scripts.run_daily_topk",
        description="LightGBM 每日推荐",
        upstream_source="derived: fact_feature_panel",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_daily_recommendation",       "topK 推荐", "t+0", 25),
            TableWriteSpec("mart_daily_recommendation_risk",  "topK 风险", "t+0", 25),
        ],
    ),
    ClientSpec(
        client_id="train_multidim_model",
        module="scripts.train_multidim_model",
        description="多维模型训练 + 预测",
        upstream_source="derived: fact_feature_panel",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_multidim_model",        "模型注册",  "on-demand", 24*30),
            TableWriteSpec("mart_multidim_prediction",   "holdout 预测", "on-demand", 24*30),
        ],
    ),
    ClientSpec(
        client_id="run_multidim_walkforward",
        module="scripts.run_multidim_walkforward",
        description="walkforward 切分 + 滚动训练",
        upstream_source="derived: fact_feature_panel",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_model_walkforward_fold",       "fold 注册",  "on-demand", 24*30),
            TableWriteSpec("mart_model_walkforward_prediction", "fold 预测",  "on-demand", 24*30),
        ],
    ),
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
    ClientSpec(
        client_id="build_fund_flow_rank_snapshot_daily",
        module="scripts.build_fund_flow_rank_snapshot_daily",
        description="个股资金流排行快照 (研究侧)",
        upstream_source="derived: akshare:stock_fund_flow_individual",
        source_tier=99,
        writes=[
            TableWriteSpec(
                "mart_stock_fund_flow_rank_snapshot_daily",
                "个股资金流排行快照 (研究侧)",
                "t+0",
                24,
            ),
        ],
        sync_step_id="build_fund_flow_rank_snapshot_daily",
    ),
    ClientSpec(
        client_id="build_candidate_feature_panel",
        module="scripts.build_candidate_feature_panel",
        description="候选特征面板 (不替换 champion)",
        upstream_source="derived: fact_feature_panel + fact_common_major_holder_stock",  # raw_gpcw_detail 已物删 2026-06-27 (通达信全删)
        source_tier=99,
        writes=[
            TableWriteSpec("fact_feature_panel_candidate", "候选特征面板", "on-demand", 24*30),
        ],
    ),
    ClientSpec(
        client_id="feature_selection_experiments",
        module="scripts.run_feature_group_ablation / scripts.run_optuna_feature_elimination",
        description="候选特征消融与 Optuna 减法记录",
        upstream_source="derived: fact_feature_panel_candidate",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_feature_candidate_score", "候选特征评分", "on-demand", 24*30),
            TableWriteSpec("mart_feature_group_ablation", "特征组消融", "on-demand", 24*30),
            TableWriteSpec("mart_model_selection_run", "模型选择实验记录", "on-demand", 24*30),
        ],
    ),
    ClientSpec(
        client_id="validate_tdx_feature_pit",
        module="scripts.validate_tdx_feature_pit",
        description="候选 TDX 特征 PIT 审计",
        upstream_source="derived: fact_feature_panel_candidate + TDX source facts",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_feature_pit_audit", "候选特征 PIT 审计", "on-demand", 24*30),
        ],
    ),
    ClientSpec(
        client_id="run_candidate_walkforward_eval",
        module="scripts.run_walkforward_feature_eval",
        description="候选特征 walk-forward 评估",
        upstream_source="derived: fact_feature_panel_candidate",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_candidate_walkforward_eval", "候选特征 walk-forward 评估", "on-demand", 24*30),
        ],
    ),
    ClientSpec(
        client_id="build_feature_retention_decisions",
        module="scripts.build_feature_retention_decisions",
        description="候选特征保留 / 观察 / 剔除决策",
        upstream_source="derived: candidate feature eval marts",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_feature_retention_decision", "候选特征保留决策", "on-demand", 24*30),
        ],
    ),
    # train_tdx_challenger_model ClientSpec removed Phase ψ.5 — script 已删 (0 imports)
    ClientSpec(
        client_id="tdx_keep_challenger_productionization",
        module="scripts.build_tdx_keep_challenger_panel / scripts.train_tdx_keep_challenger_model / scripts.evaluate_tdx_keep_promotion_gate",
        description="TDX keep productionized challenger: 面板、训练、shadow、promotion gate",
        upstream_source="derived: fact_feature_panel + fact_feature_panel_candidate + lifecycle",
        source_tier=99,
        writes=[
            TableWriteSpec("fact_feature_panel_tdx_keep_challenger", "TDX keep challenger 面板", "on-demand", 24*30),
            TableWriteSpec("mart_multidim_model", "TDX keep challenger 模型注册", "on-demand", 24*30),
            TableWriteSpec("mart_multidim_prediction", "TDX keep challenger 预测", "on-demand", 24*30),
            TableWriteSpec("mart_tdx_keep_promotion_gate", "TDX keep promotion gate", "on-demand", 24*30),
        ],
    ),
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
