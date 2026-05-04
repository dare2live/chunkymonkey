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
    ClientSpec(
        client_id="tdx_industry_client",
        module="services.tdx_industry_client",
        description="通达信行业三级分类 (申万对照)",
        upstream_source="tdxhub.block (tdxhy.cfg)",
        source_tier=1,
        fallback_chain=["tdxhub"],
        writes=[
            TableWriteSpec("dim_stock_tdx_industry", "股票×行业映射", "weekly", 24*7),
            TableWriteSpec("dim_stock_tdx_industry_history", "行业归属历史", "weekly", 24*7),
        ],
        sync_step_id="sync_tdx_industry",
    ),
    ClientSpec(
        client_id="block_client",
        module="services.block_client",
        description="通达信板块归属",
        upstream_source="tdxhub.block",
        source_tier=1,
        fallback_chain=["tdxhub"],
        writes=[
            TableWriteSpec("dim_stock_tdx_block", "股票×板块映射", "weekly", 24*7),
            TableWriteSpec("dim_tdx_block_catalog", "板块目录", "weekly", 24*7),
        ],
        sync_step_id="sync_tdx_block",
    ),
    ClientSpec(
        client_id="tdx_affair_client",
        module="services.tdx_affair_client",
        description="通达信季报 (gpcw)",
        upstream_source="tdxhub.affair (gpcw)",
        source_tier=1,
        fallback_chain=["tdxhub"],
        writes=[
            TableWriteSpec("raw_gpcw_detail", "财务报表原始 (季频)", "quarterly", 24*95),
            TableWriteSpec("raw_tdx_gpcw_wide", "TDX gpcw 宽字段保留", "quarterly", 24*95),
            TableWriteSpec("dim_tdx_gpcw_field", "TDX gpcw 字段字典", "static", 24*365),
        ],
        sync_step_id="sync_gpcw_data",
    ),
    ClientSpec(
        client_id="tdx_f10_extra_client",
        module="services.tdx_f10_extra_client",
        description="通达信 F10 Format B 补充解析",
        upstream_source="tdxhub.holders raw_tdx_f10_holder_research",
        source_tier=1,
        fallback_chain=["tdxhub"],
        writes=[
            TableWriteSpec("raw_tdx_f10_holder_count_history", "F10 股东人数变化 raw", "quarterly", 24*95),
            TableWriteSpec("fact_holder_count_period", "F10 股东人数变化 canonical", "quarterly", 24*95),
            TableWriteSpec("fact_shareholder_trade_tdx_b", "F10 B 重要股东变动", "event", 24*95),
            TableWriteSpec("fact_common_major_holder_stock", "F10 同大股东个股 schema", "static", 24*365),
            TableWriteSpec("fact_fund_holding_tdx_f10", "F10 基金持股 schema", "static", 24*365),
        ],
        sync_step_id="sync_raw",
    ),
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
            TableWriteSpec("raw_aif10_holder_count",        "股东人数",      "quarterly", 24*95),
            TableWriteSpec("raw_aif10_valuation_quantile",  "估值分位",      "t+1",       48),
            TableWriteSpec("raw_aif10_peer_valuation",      "同行排名",      "t+1",       48),
            TableWriteSpec("raw_aif10_forecast_consensus",  "一致预期",      "t+1",       48),
            TableWriteSpec("raw_aif10_financial_history",   "财务长历史 200Q", "quarterly", 24*95),
        ],
        sync_step_id="sync_aif10_*",
    ),

    # ── tier 3: akshare (兜底) ───────────────────────────────────────
    ClientSpec(
        client_id="margin_client",
        module="services.margin_client",
        description="融资融券明细",
        upstream_source="akshare:stock_margin_detail_sse/szse",
        source_tier=3,
        fallback_chain=["akshare"],
        writes=[
            TableWriteSpec("raw_margin_daily", "融资融券原始", "t+1", 48),
        ],
        sync_step_id="sync_margin",
    ),
    ClientSpec(
        client_id="capital_client",
        module="services.capital_client",
        description="资本运作 (分红/回购/解禁/配股)",
        upstream_source="akshare:stock_fhps_em/repurchase/lockup",
        source_tier=3,
        fallback_chain=["akshare"],
        writes=[
            TableWriteSpec("raw_capital_dividend_summary", "分红汇总",   "t+1", 48),
            TableWriteSpec("raw_capital_dividend_detail",  "分红明细",   "t+1", 48),
            TableWriteSpec("raw_capital_repurchase",       "回购",      "t+1", 48),
            TableWriteSpec("raw_capital_unlock",           "解禁",      "t+1", 48),
            TableWriteSpec("raw_capital_allotment_detail", "配股明细",   "t+1", 48),
            TableWriteSpec("dim_capital_behavior_latest",  "资本运作汇总", "t+1", 48),
        ],
        sync_step_id="sync_capital",
    ),
    ClientSpec(
        client_id="financial_client",
        module="services.financial_client",
        description="财务报表 (gpcw 兜底 / 派生指标)",
        upstream_source="tdxhub.affair (主), akshare 兜底",
        source_tier=1,
        fallback_chain=["tdxhub", "akshare"],
        writes=[
            TableWriteSpec("raw_gpcw_financial",       "财务报表 (兜底写入)",   "quarterly", 24*95),
            TableWriteSpec("dim_financial_latest",     "最新季报快照", "quarterly", 24*95),
            TableWriteSpec("fact_financial_derived",   "派生财务指标", "quarterly", 24*95),
        ],
        sync_step_id="sync_financial",
    ),
    ClientSpec(
        client_id="financial_indicator_client",
        module="services.financial_indicator_client",
        description="akshare 财务指标 (ROE / 毛利率等)",
        upstream_source="akshare:stock_financial_abstract_em",
        source_tier=3,
        fallback_chain=["akshare"],
        writes=[
            TableWriteSpec("raw_financial_indicator_ak",   "原始指标",   "quarterly", 24*95),
            TableWriteSpec("dim_financial_indicator_latest","最新指标快照","quarterly", 24*95),
            TableWriteSpec("fact_financial_indicator_ak",  "指标事实表", "quarterly", 24*95),
        ],
        sync_step_id="sync_financial_indicator",
    ),
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
]


# ─────────────────────────────────────────────────────────────────────
# 派生层 — 不是直接对接外部源, 但仍是 dim_data_asset 的一员
# 这里仅登记 *跨多个 raw 输入* 的派生写入器, 单文件派生无需登记 (会被 grep 自动捕到)
# ─────────────────────────────────────────────────────────────────────

DERIVED_WRITERS: list[ClientSpec] = [
    ClientSpec(
        client_id="rebuild_holder_events",
        module="scripts.rebuild_holder_events",
        description="从 fact_top10_holder_period 派生 fact_holder_event",
        upstream_source="derived: fact_top10_holder_period",
        source_tier=99,
        writes=[
            TableWriteSpec("fact_holder_event", "持仓事件 (lag 派生)", "derived", 48),
        ],
    ),
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
        client_id="build_candidate_feature_panel",
        module="scripts.build_candidate_feature_panel",
        description="候选特征面板 (不替换 champion)",
        upstream_source="derived: fact_feature_panel + fact_common_major_holder_stock + fact_fund_holding_tdx_f10 + raw_gpcw_detail",
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
    ClientSpec(
        client_id="train_tdx_challenger_model",
        module="scripts.train_tdx_challenger_model",
        description="TDX 保留特征 challenger 报告 (不替换 champion)",
        upstream_source="derived: fact_feature_panel_candidate + mart_feature_retention_decision",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_tdx_challenger_report", "TDX challenger 模型报告", "on-demand", 24*30),
        ],
    ),
    ClientSpec(
        client_id="mark_deprecated_data_assets",
        module="scripts.mark_deprecated_data_assets",
        description="数据资产退役标记记录 (不删表)",
        upstream_source="derived: dim_data_asset + stale reference audit",
        source_tier=99,
        writes=[
            TableWriteSpec("mart_data_deprecation_record", "数据资产退役记录", "on-demand", 24*30),
        ],
    ),
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


def upstream_for_table(table: str) -> tuple[Optional[str], Optional[int]]:
    """seed_dim_data_asset.py 兼容接口: (upstream_source, source_tier)."""
    found = get_table_metadata(table)
    if found is None:
        return None, None
    client, _ = found
    return client.upstream_source, client.source_tier


def freshness_for_table(table: str) -> Optional[tuple[str, int]]:
    """seed_dim_data_asset.py 兼容接口: (freshness, sla_hours)."""
    found = get_table_metadata(table)
    if found is None:
        return None
    _, w = found
    return w.freshness, w.sla_hours


def to_dicts() -> list[dict]:
    """JSON 序列化, 给路由/UI 用."""
    out = []
    for c in all_clients():
        d = asdict(c)
        # writes 里的 dataclass 已经被 asdict 递归展开
        out.append(d)
    return out
