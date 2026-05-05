"""派生 SQL / 派生脚本登记表 — 单一真相源.

每个 LineageSpec = 一个派生过程的声明.

当前 registry 默认是 metadata-only: 登记输入/输出/owner/说明, 供 UI、健康检查和
审计使用, 不作为 cron 可执行编排。若后续某条 lineage 要由 run_lineage() 调度,
必须显式设置 metadata_only=False, 并提供可直接调用的 sql_text 或 entry_point。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import hashlib
from typing import Optional


def _sql_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class LineageSpec:
    lineage_id: str                    # e.g. 'mart_daily_recommendation/topk_v1'
    output_table: str
    input_tables: list[str] = field(default_factory=list)
    description: str = ""
    owner: str = ""
    version: str = "v1"
    sql_text: Optional[str] = None         # 纯 SQL 派生, 直接 execute
    entry_point: Optional[str] = None      # 脚本派生, 反射 module:function
    schedule: str = "on-demand"            # 't+0' / 'event' / 'on-demand' / 'cron'
    metadata_only: bool = True             # Phase 0: registry 不是可执行编排

    def sql_hash(self) -> str:
        return _sql_hash((self.sql_text or "") + (self.entry_point or ""))


# ─────────────────────────────────────────────────────────────────────
# 派生注册表 — 渐进式补充
# 当前仅登记 *关键* mart_* 派生路径. 长 tail 派生可后续补.
# ─────────────────────────────────────────────────────────────────────

LINEAGES: list[LineageSpec] = [
    # ── 持仓事件 (W0 已存在的派生) ───────────────────────────────────
    LineageSpec(
        lineage_id="fact_holder_event/lag_window_v1",
        output_table="fact_holder_event",
        input_tables=["fact_top10_holder_period"],
        description="按 (stock_code, holder_name) 分窗 lag, 比较两期持仓变化生成进入/退出/增持/减持事件",
        owner="scripts.rebuild_holder_events",
        entry_point="scripts.rebuild_holder_events:rebuild",
        schedule="event",
    ),

    # ── 财务派生 ─────────────────────────────────────────────────────
    LineageSpec(
        lineage_id="fact_financial_derived/calc_v1",
        output_table="fact_financial_derived",
        input_tables=["raw_gpcw_detail", "dim_active_a_stock"],
        description="季报派生 (ROE/毛利率/资产负债率/同比/环比); 跑在 sync_financial 步骤里",
        owner="services.financial_client",
        entry_point="services.financial_client:calc_financial_derived",
        schedule="quarterly",
    ),
    LineageSpec(
        lineage_id="fact_fundamental_quarterly/build_v1",
        output_table="fact_fundamental_quarterly",
        input_tables=["raw_gpcw_detail"],
        description="季报宽表 (按 stock_code, report_date), 从 raw_gpcw_detail 抽取核心指标",
        owner="scripts.build_fundamental_quarterly",
        entry_point="scripts.build_fundamental_quarterly:build",
        schedule="quarterly",
    ),

    # ── 特征面板 (Alpha158) ──────────────────────────────────────────
    LineageSpec(
        lineage_id="fact_feature_panel/alpha158_v1",
        output_table="fact_feature_panel",
        input_tables=[
            "price_kline",          # market.duckdb 跨库
            "fact_financial_derived",
            "dim_active_a_stock",
            "fact_top10_holder_period",
            "fact_lhb_event",
        ],
        description="Alpha158 特征面板; 含 K线衍生 + 财务比率 + 机构持仓 lag + 龙虎榜事件标志",
        owner="scripts.build_feature_panel_duck",
        entry_point="scripts.build_feature_panel_duck:build",
        schedule="t+1",
    ),
    LineageSpec(
        lineage_id="fact_feature_panel_candidate/tdx_f10_gpcw_v1",
        output_table="fact_feature_panel_candidate",
        input_tables=[
            "fact_feature_panel",
            "fact_holder_count_period",
            "fact_top10_holder_period",
            "fact_common_major_holder_stock",
            "fact_fund_holding_tdx_f10",
            "raw_gpcw_detail",
        ],
        description="候选 TDX F10/gpcw 特征面板; 不替换生产 champion",
        owner="scripts.build_candidate_feature_panel",
        entry_point="scripts.build_candidate_feature_panel:build_candidate_feature_panel",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_feature_group_ablation/candidate_v1",
        output_table="mart_feature_group_ablation",
        input_tables=["fact_feature_panel_candidate"],
        description="候选特征分组消融记录",
        owner="scripts.run_feature_group_ablation",
        entry_point="scripts.run_feature_group_ablation:run_group_ablation",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_model_selection_run/candidate_reduction_v1",
        output_table="mart_model_selection_run",
        input_tables=["fact_feature_panel_candidate"],
        description="候选特征 Optuna/确定性减法记录; 不自动提升 champion",
        owner="scripts.run_optuna_feature_elimination",
        entry_point="scripts.run_optuna_feature_elimination:run_optuna_feature_elimination",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_feature_pit_audit/tdx_candidate_v1",
        output_table="mart_feature_pit_audit",
        input_tables=[
            "fact_feature_panel_candidate",
            "fact_holder_count_period",
            "fact_top10_holder_period",
            "fact_common_major_holder_stock",
            "fact_fund_holding_tdx_f10",
            "raw_gpcw_detail",
        ],
        description="候选 TDX F10/gpcw 特征 PIT 审计; 检查信号日不早于可用源日期",
        owner="scripts.validate_tdx_feature_pit",
        entry_point="scripts.validate_tdx_feature_pit:validate_tdx_feature_pit",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_candidate_walkforward_eval/tdx_candidate_v1",
        output_table="mart_candidate_walkforward_eval",
        input_tables=["fact_feature_panel_candidate"],
        description="候选特征 walk-forward 单特征评估; 不替换生产 champion",
        owner="scripts.run_walkforward_feature_eval",
        entry_point="scripts.run_walkforward_feature_eval:run_walkforward_feature_eval",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_feature_retention_decision/tdx_candidate_v1",
        output_table="mart_feature_retention_decision",
        input_tables=[
            "fact_feature_panel_candidate",
            "mart_feature_pit_audit",
            "mart_candidate_walkforward_eval",
            "mart_feature_group_ablation",
            "mart_model_selection_run",
        ],
        description="整合 PIT、walk-forward、消融、减法实验的候选特征保留决策",
        owner="scripts.build_feature_retention_decisions",
        entry_point="scripts.build_feature_retention_decisions:build_feature_retention_decisions",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_tdx_challenger_report/retained_rank_ensemble_v1",
        output_table="mart_tdx_challenger_report",
        input_tables=["fact_feature_panel_candidate", "mart_feature_retention_decision"],
        description="基于保留 TDX 特征生成 challenger-only rank ensemble 报告; 不提升 champion",
        owner="scripts.train_tdx_challenger_model",
        entry_point="scripts.train_tdx_challenger_model:train_tdx_challenger_model",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="fact_feature_panel_tdx_keep_challenger/tdx_keep_v1",
        output_table="fact_feature_panel_tdx_keep_challenger",
        input_tables=["fact_feature_panel", "fact_feature_panel_candidate", "mart_feature_retention_decision"],
        description="TDX keep production challenger 面板; baseline 特征 + 5 个 keep overlay, 不替换 champion panel",
        owner="scripts.build_tdx_keep_challenger_panel",
        entry_point="scripts.build_tdx_keep_challenger_panel:build_panel",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_tdx_keep_promotion_gate/gated_shadow_v1",
        output_table="mart_tdx_keep_promotion_gate",
        input_tables=[
            "mart_multidim_model",
            "mart_model_lifecycle",
            "mart_model_portfolio_summary",
            "mart_feature_drift",
            "mart_daily_recommendation",
            "mart_feature_pit_audit",
        ],
        description="TDX keep challenger promotion gate; 只输出 promote_ready/keep_shadow/reject, 不自动替换 champion",
        owner="scripts.evaluate_tdx_keep_promotion_gate",
        entry_point="scripts.evaluate_tdx_keep_promotion_gate:evaluate_gate",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_model_feature_lineage/static_map_v1",
        output_table="mart_model_feature_lineage",
        input_tables=["mart_multidim_model"],
        description="按模型 feature_cols_json 固化每个入模特征的源表/source_tier/可用日期/PIT 要求",
        owner="scripts.build_model_feature_lineage",
        entry_point="scripts.build_model_feature_lineage:main",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_model_holding_topk_eval/holding_grid_v1",
        output_table="mart_model_holding_topk_eval",
        input_tables=["mart_multidim_prediction", "fact_feature_panel_candidate", "dim_stock_tdx_industry"],
        description="按持股周期和 topK 规模评估 RankIC、TopK 超额、换手、行业集中度、回撤和成本后收益",
        owner="scripts.evaluate_holding_topk",
        entry_point="scripts.evaluate_holding_topk:main",
        schedule="on-demand",
    ),

    # ── 推荐 / 模型 ──────────────────────────────────────────────────
    LineageSpec(
        lineage_id="mart_daily_recommendation/topk_lgbm_v1",
        output_table="mart_daily_recommendation",
        input_tables=["fact_feature_panel", "mart_multidim_model"],
        description="LightGBM 9 维超参 (Optuna 50 trials) topK 推荐 (含 sigma); run_daily_topk.py",
        owner="scripts.run_daily_topk",
        entry_point="scripts.run_daily_topk:run",
        schedule="t+0",
    ),
    LineageSpec(
        lineage_id="mart_multidim_model/train_v1",
        output_table="mart_multidim_model",
        input_tables=["fact_feature_panel"],
        description="多维模型注册 (Optuna RankIC objective; 9 维超参; 50 trials)",
        owner="scripts.train_multidim_model",
        entry_point="scripts.train_multidim_model:train",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_multidim_prediction/holdout_v1",
        output_table="mart_multidim_prediction",
        input_tables=["mart_multidim_model", "fact_feature_panel"],
        description="模型 holdout 段预测 (用于评估)",
        owner="scripts.train_multidim_model",
        entry_point="scripts.train_multidim_model:train",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_model_walkforward_fold/cuts_v1",
        output_table="mart_model_walkforward_fold",
        input_tables=["fact_feature_panel"],
        description="walkforward 切分 (滚动 train/test 窗口) — 时间稳定性评估",
        owner="scripts.run_multidim_walkforward",
        entry_point="scripts.run_multidim_walkforward:run",
        schedule="on-demand",
    ),
    LineageSpec(
        lineage_id="mart_model_walkforward_prediction/per_fold_v1",
        output_table="mart_model_walkforward_prediction",
        input_tables=["mart_model_walkforward_fold", "fact_feature_panel"],
        description="walkforward 每个 fold 的 holdout 预测",
        owner="scripts.run_multidim_walkforward",
        entry_point="scripts.run_multidim_walkforward:run",
        schedule="on-demand",
    ),

    # ── 关系派生 ─────────────────────────────────────────────────────
    LineageSpec(
        lineage_id="mart_current_relationship/build_v1",
        output_table="mart_current_relationship",
        input_tables=["fact_top10_holder_period", "inst_institutions", "dim_holder_alias"],
        description="当前机构-股票关系 (单点真相, 替代多处独立重算 — 见 CLAUDE.md 数据原则 #8)",
        owner="services.relationship_engine",
        entry_point="services.relationship_engine:build_current",
        schedule="event",
    ),
    LineageSpec(
        lineage_id="mart_stock_trend/build_v1",
        output_table="mart_stock_trend",
        input_tables=["fact_holder_event", "mart_current_relationship", "price_kline"],
        description="股票趋势卡片 (前端 stock-detail) 数据源",
        owner="services.trend_engine",
        entry_point="services.trend_engine:build",
        schedule="t+1",
    ),

    # ── 选股 ─────────────────────────────────────────────────────────
    LineageSpec(
        lineage_id="mart_stock_screening/composite_v1",
        output_table="mart_stock_screening",
        input_tables=[
            "fact_feature_panel", "fact_top10_holder_period",
            "fact_financial_derived", "mart_daily_recommendation",
        ],
        description="综合选股 (打分 + 筛选 + 行业去重); calc_screening 步骤",
        owner="services.screening_engine",
        entry_point="services.screening_engine:run_screening",
        schedule="t+0",
    ),

    # ── 数据健康 ─────────────────────────────────────────────────────
    LineageSpec(
        lineage_id="mart_data_health/snapshot_v1",
        output_table="mart_data_health",
        input_tables=["dim_data_asset"],
        description="每张表的健康快照 (row_count / last_data_date / freshness / severity)",
        owner="scripts.data_health_snapshot",
        entry_point="scripts.data_health_snapshot:main",
        schedule="t+0",
    ),
]


# ─────────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────────

def all_lineages() -> list[LineageSpec]:
    return list(LINEAGES)


def get_lineage(lineage_id: str) -> Optional[LineageSpec]:
    for l in LINEAGES:
        if l.lineage_id == lineage_id:
            return l
    return None


def lineages_for_output(output_table: str) -> list[LineageSpec]:
    return [l for l in LINEAGES if l.output_table == output_table]


def lineages_using(input_table: str) -> list[LineageSpec]:
    """反向: 谁使用了这张表作为输入."""
    return [l for l in LINEAGES if input_table in l.input_tables]


def to_dicts() -> list[dict]:
    out = []
    for l in LINEAGES:
        d = asdict(l)
        d["sql_hash"] = l.sql_hash()
        out.append(d)
    return out
