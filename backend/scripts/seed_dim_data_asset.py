"""自动发现并 seed dim_data_asset.

工作流:
  1. 从 information_schema 拉所有表名 + 行数
  2. 按前缀 (raw_/dim_/fact_/mart_/sys_) 分层
  3. grep backend/ 找 writer (INSERT INTO X / CREATE TABLE X / UPDATE X)
  4. grep backend/ 找 reader (FROM X / JOIN X), 排除自引用
  5. UPSERT 到 dim_data_asset, auto_discovered=TRUE

人工补字段 (用 services/data_asset_curator.py 单独维护或直接 SQL UPDATE):
  - purpose (一句话用途)
  - upstream_source / source_tier / fallback_chain
  - expected_freshness / sla_hours
  - consumed_by_views (前端 view-* 引用)

可重复运行: 对已存在的行只更新 auto-discovered 字段, 不覆盖人工补的部分.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed-dim-data-asset")

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(REPO / "backend"))
from services.db import get_conn  # noqa: E402


# 表前缀 → layer
PREFIX_LAYER = {
    "raw": "raw",
    "dim": "dim",
    "fact": "fact",
    "mart": "mart",
    "sys": "sys",
    "_cache": "cache",
    "research": "research",
    "step": "sys",
    "app": "sys",
    "exclude": "sys",
    "exclusion": "sys",
    "scan": "sys",
    "v": "view",
}


def detect_layer(table_name: str) -> str:
    if table_name.startswith("_cache"):
        return "cache"
    prefix = table_name.split("_", 1)[0]
    return PREFIX_LAYER.get(prefix, "other")


# 客户端写入元数据从 services/data_sources/clients_registry.py 单一真相源读取.
# 这里仅保留 *registry 没覆盖* 的额外补丁 (派生层散落表 + 极少数特殊源).
from services.data_sources.clients_registry import (
    get_table_metadata as _registry_table_metadata,
    upstream_for_table as _registry_upstream,
    freshness_for_table as _registry_freshness,
)

EXTRA_WRITER_BY_TABLE = {
    # Dynamic f-string writers are intentionally kept configurable in code, but
    # static grep cannot infer the concrete target table from imported constants.
    "mart_synergy_policy_mtm_rerank": "backend/scripts/rerank_optuna_synergy_mtm.py",
    "mart_synergy_policy_mtm_rerank_summary": "backend/scripts/rerank_optuna_synergy_mtm.py",
    "mart_synergy_policy_mtm_strategy_sweep": "backend/scripts/sweep_synergy_mtm_strategy.py",
    "mart_synergy_policy_mtm_strategy_sweep_summary": "backend/scripts/sweep_synergy_mtm_strategy.py",
    # mart_macd_state_history build 登记已删 (2026-06-28 U3 退役: 脚本+表 GONE)
}

EXTRA_UPSTREAM_BY_TABLE = {
    # 多个 client 链回写, 在 registry 内不太适合归到单一 client
    "fact_top10_holder_period":     ("东财妙想 aif10 (services/holders_aif10, source=miaoxiang)", 1),
    "fact_controlling_shareholder": ("tdxhub.holders", 1),
    "fact_shareholder_plan":        ("tdxhub.holders", 1),
    "fact_shareholder_trade":       ("tdxhub.holders", 1),
    # raw_executive_trade (akshare M4) + raw_gpcw_dividend (gpcw) 源映射已删 2026-06-27 (通达信全删: 表 GONE)
    "dim_active_a_stock":           ("tushare stock_basic (list_status=L, 2026-06-19 切, 排北交所)", 1),  # rule-compliance: ok evidence=metadata-registry
    "dim_trading_calendar":         ("akshare tool_trade_date_hist_sina", 3),
    "dim_holder_alias":             ("manual seed", None),
    # derived (多源派生, 不是单 client 写)
    "fact_holder_event":            ("derived from fact_top10_holder_period", None),
    "fact_lhb_event":               ("derived from raw_lhb_daily", None),
    "fact_technical_trigger":       ("derived from build_formula_signals_history + build_signal_context", None),
    "fact_institution_event":       ("derived (gen_events + return_engine)", None),
    # fact_fundamental_quarterly seed 已删 2026-06-27 (通达信全删: gpcw派生表退役物删)
    # mart_tdx_gpcw_field_profile seed 已删 2026-06-27 (通达信全删; 表不存在=从未物化)
    "mart_current_relationship":    ("derived (build_current_relationship)", None),
    "mart_stock_trend":             ("derived (build_trends step)", None),
    "mart_stock_screening":         ("derived (calc_screening manual step)", None),
    # mart_macd_state_history upstream 登记已删 (2026-06-28 U3 退役: 脚本+表 GONE)
    "mart_synergy_policy_mtm_rerank": (
        "derived from mart_optuna_synergy_trial + validate_synergy_policy_mark_to_market",
        None,
    ),
    "mart_synergy_policy_mtm_rerank_summary": (
        "derived from mart_synergy_policy_mtm_rerank",
        None,
    ),
    "mart_synergy_policy_mtm_strategy_sweep": (
        "derived from mart_synergy_policy_candidate + validate_synergy_policy_mark_to_market",
        None,
    ),
    "mart_synergy_policy_mtm_strategy_sweep_summary": (
        "derived from mart_synergy_policy_mtm_strategy_sweep",
        None,
    ),
}

EXTRA_FRESHNESS_BY_TABLE = {
    "fact_top10_holder_period": ("t+1", 48),
    "fact_shareholder_plan": ("event", 48),
    "fact_shareholder_trade": ("event", 48),
    "mart_current_relationship": ("event", 48),
    "mart_dual_confirm": ("event", 48),
    # Backtests, audits, model diagnostics, and fingerprints are generated on
    # demand. Their rows remain useful historical artifacts, but they are not
    # daily freshness obligations.
    "fact_institution_follow_backtest": ("on-demand", 24 * 30),
    "fact_policy_equity_curve": ("on-demand", 24 * 30),
    "fact_policy_eval": ("on-demand", 24 * 30),
    "fact_policy_trade": ("on-demand", 24 * 30),
    "mart_audit_snapshot_state": ("on-demand", 24 * 30),
    # mart_data_audit_report retention 登记已删 2026-06-28 (残留清扫 E: 孤儿表物删, audit 改写 JSON)
    "mart_etf_snapshot_latest": ("on-demand", 24 * 30),
    "mart_etf_snapshot_state": ("on-demand", 24 * 30),
    "mart_challenger_evidence_bundle": ("on-demand", 24 * 30),
    "mart_champion_candidate_evaluation": ("on-demand", 24 * 30),
    "mart_model_ablation_run": ("on-demand", 24 * 30),
    "mart_model_feature_lineage": ("on-demand", 24 * 30),
    "mart_model_holding_topk_eval": ("on-demand", 24 * 30),
    "mart_model_portfolio_curve": ("on-demand", 24 * 30),
    "mart_model_walkforward_portfolio_summary": ("on-demand", 24 * 30),
    "mart_prediction_outcome": ("on-demand", 24 * 30),
    "mart_step_fingerprint": ("on-demand", 24 * 30),
    # Optional governance/research outputs. Empty means the corresponding
    # workflow has not been activated for the current policy, not source loss.
    "mart_candidate_feature_set_contract": ("on-demand", 24 * 30),
    "mart_data_processing_tool_issue": ("on-demand", 24 * 30),
    "mart_data_processing_tool_run": ("on-demand", 24 * 30),
    "mart_feature_candidate_coverage": ("on-demand", 24 * 30),
    "mart_feature_drift_mitigation_panel_build": ("on-demand", 24 * 30),
    "mart_hybrid_feature_panel_build": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_position": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_daily_path": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_gate": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_evidence_bundle": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_rerank": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_rerank_summary": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_strategy_sweep": ("on-demand", 24 * 30),
    "mart_synergy_policy_mtm_strategy_sweep_summary": ("on-demand", 24 * 30),
    # mart_macd_state_history retention 已删 2026-06-28 (U3 退役: 脚本+表 GONE)
    # mart_tdx_gpcw_auto_* (5) retention 已删 2026-06-27 (通达信全删: gpcw auto-feature 流水线退役, 表 GONE)
    "mart_temporal_research_panel": ("on-demand", 24 * 30),
    # mart_tdx_f10_source_date_section_audit retention 已删 2026-06-27 (通达信全删: tdx F10 source-date audit 退役, 表 GONE)
    "mart_architecture_cleanup_plan": ("on-demand", 24 * 30),
    # mart_tdx_server_health seed 已删 2026-06-27 (通达信全删 单元7 物删)
    "mart_temporal_research_panel_quality": ("on-demand", 24 * 30),
    # mart_tdx_gpcw_file_manifest seed 已删 2026-06-27 (通达信全删 gpcw物删)
    "fact_technical_trigger": ("event", 48),
    # Drift is a current champion monitor, not a raw source. Empty rows should
    # be fixed by running compute_feature_drift, not source backfill.
    "mart_feature_drift": ("t+0", 25),
    "mart_feature_drift_histogram": ("t+0", 25),
}


EXTRA_ASSET_CONTRACT_BY_TABLE = {
    "mart_challenger_evidence_bundle": {
        "asset_grain": "candidate_model_id+evidence_run_id",
        "asset_cadence": "on_demand",
        "coverage_policy": "only_when_active_challenger_exists",
        "null_policy": "empty_allowed_without_active_challenger",
        "pit_policy": "inherits_candidate_feature_policy",
        "intended_use": "promotion_evidence_for_candidate_only",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "promotion_gate_context",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    "mart_architecture_cleanup_plan": {
        "asset_grain": "run_id+asset_id",
        "asset_cadence": "on_demand",
        "coverage_policy": "workflow_dependent",
        "null_policy": "empty_allowed_without_active_plan",
        "pit_policy": "inherits_inventory_run",
        "intended_use": "governance_context",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "attention_filter_context",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    "mart_champion_candidate_evaluation": {
        "asset_grain": "candidate_model_id+evaluation_run_id",
        "asset_cadence": "on_demand",
        "coverage_policy": "only_when_active_challenger_exists",
        "null_policy": "empty_allowed_without_active_challenger",
        "pit_policy": "inherits_candidate_feature_policy",
        "intended_use": "promotion_decision_for_candidate_only",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "promotion_gate_context",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    "mart_feature_drift": {
        "asset_grain": "model_id+feature+snapshot_at",
        "asset_cadence": "trading_day_or_model_refresh",
        "coverage_policy": "current_champion_model_features",
        "null_policy": "no_unclassified_nulls",
        "pit_policy": "inherits_model_training_policy",
        "intended_use": "champion_model_monitoring",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "promotion_and_retrain_gate",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "blocking",
    },
    "mart_feature_drift_histogram": {
        "asset_grain": "model_id+feature+bucket",
        "asset_cadence": "trading_day_or_model_refresh",
        "coverage_policy": "current_champion_model_features",
        "null_policy": "no_unclassified_nulls",
        "pit_policy": "inherits_model_training_policy",
        "intended_use": "champion_model_monitoring_cache",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "promotion_and_retrain_gate",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "blocking",
    },
    "mart_synergy_policy_mtm_rerank": {
        "asset_grain": "run_id+optuna_run_id+trial_number",
        "asset_cadence": "on_demand_research",
        "coverage_policy": "top_optuna_trials_selected_for_mtm_rerank",
        "null_policy": "no_unclassified_nulls_metrics_nullable_only_when_trial_fails_before_mtm",
        "pit_policy": "inherits_synergy_policy_candidate_and_tdxhub_mtm_policy",
        "intended_use": "post_optuna_mtm_drawdown_exposure_reranking",
        "model_eligibility": "not_model_input_research_evidence_only",
        "strategy_eligibility": "research_rerank_gate_before_candidate_promotion",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    "mart_synergy_policy_mtm_rerank_summary": {
        "asset_grain": "run_id",
        "asset_cadence": "on_demand_research",
        "coverage_policy": "summary_for_mtm_rerank_run",
        "null_policy": "best_trial_nullable_only_when_no_trials_evaluated",
        "pit_policy": "inherits_mart_synergy_policy_mtm_rerank",
        "intended_use": "post_optuna_mtm_rerank_summary_and_governance",
        "model_eligibility": "not_model_input_research_evidence_only",
        "strategy_eligibility": "research_rerank_gate_before_candidate_promotion",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    "mart_synergy_policy_mtm_strategy_sweep": {
        "asset_grain": "run_id+variant_id",
        "asset_cadence": "on_demand_research",
        "coverage_policy": "strategy_parameter_grid_selected_for_mtm_validation",
        "null_policy": "no_unclassified_nulls_metrics_nullable_only_when_variant_fails_before_mtm",
        "pit_policy": "inherits_synergy_policy_candidate_tdxhub_mtm_and_market_state_filter_policy",
        "intended_use": "mtm_strategy_parameter_search_before_candidate_promotion",
        "model_eligibility": "not_model_input_research_evidence_only",
        "strategy_eligibility": "strategy_parameter_gate_before_candidate_promotion",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    "mart_synergy_policy_mtm_strategy_sweep_summary": {
        "asset_grain": "run_id",
        "asset_cadence": "on_demand_research",
        "coverage_policy": "summary_for_mtm_strategy_parameter_sweep",
        "null_policy": "best_variant_nullable_only_when_no_variants_evaluated",
        "pit_policy": "inherits_mart_synergy_policy_mtm_strategy_sweep",
        "intended_use": "mtm_strategy_parameter_search_summary_and_governance",
        "model_eligibility": "not_model_input_research_evidence_only",
        "strategy_eligibility": "strategy_parameter_gate_before_candidate_promotion",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    },
    # raw_tdx_industry_file_snapshot active 登记已删 (2026-06-23 通达信物删, 移入下方 EXTRA_DEPRECATED ledger)
    # mart_macd_state_history asset metadata 登记已删 (2026-06-28 U3 退役: 脚本+表 GONE)
}

EXTRA_DEPRECATED_ASSET_BY_TABLE = {
    # tdx F10 产品退役 (2026-06-24): 3 张冻结表 (tdx_f10_extra_client 已删, 更新路径死). 0 真实代码消费方.
    "fact_holder_count_period": {"purpose": "股东户数 (tdx F10 已物删 2026-06-26, →tushare)", "deprecation_status": "deleted", "deprecated_reason": "tdx F10退役; tushare stk_holdernumber 2019+ 全史替代 (物删, pre-2019无K线不可用)", "replacement_table": "raw_tushare_stk_holdernumber"},
    "fact_shareholder_plan_tdx_f10": {"purpose": "股东增减持计划/意向 (已物删 2026-06-26)", "deprecation_status": "deleted", "deprecated_reason": "tdx F10退役通达信全删; 用户 2026-06-26 拍板物删覆盖归档保留决议; 增减持意向无 tushare/aif10 等价, 永久丢失已知情接受", "replacement_table": None},
    "fact_common_major_holder_stock": {"purpose": "主要股东跨公司持股网络 (归档冻结, 94% peer≠self)", "deprecation_status": "archived", "deprecated_reason": "tdx F10退役; aif10 MAIN_ORGHOLDDETAIL=机构持仓≠跨公司网络, RELATION=实控人单关系, 无等价→保留唯一数据不物删", "replacement_table": None},
    # 东财全套迁移 Stage④ (2026-06-23): 申万当前快照 dim + 通达信(tdx)行业/板块 6 表物删 (§4.3 行业/概念切东财)。
    # 留 governance ledger row 防 data_health 假红; 行业现 = dim_stock_dc_industry, 深史 PIT = tushare_raw.v_sw_industry_pit。
    "dim_stock_sw_industry": {"purpose": "申万当前行业快照 (物删④-1, 切东财)", "deprecation_status": "deprecated", "deprecated_reason": "东财迁移Stage④-1 行业切 dim_stock_dc_industry; 深史PIT走v_sw_industry_pit", "replacement_table": "dim_stock_dc_industry"},
    "dim_stock_tdx_industry": {"purpose": "通达信行业三级 (物删, 切东财)", "deprecation_status": "deprecated", "deprecated_reason": "东财迁移Stage④ 行业切 dim_stock_dc_industry", "replacement_table": "dim_stock_dc_industry"},
    "dim_stock_tdx_industry_history": {"purpose": "通达信行业归属历史 (物删)", "deprecation_status": "deprecated", "deprecated_reason": "东财迁移Stage④; 深史PIT走v_sw_industry_pit", "replacement_table": "v_sw_industry_pit"},
    "raw_tdx_industry_file_snapshot": {"purpose": "通达信 tdxhy.cfg 原始快照 (物删)", "deprecation_status": "deprecated", "deprecated_reason": "东财迁移Stage④ 通达信源退役", "replacement_table": None},
    "dim_stock_tdx_block": {"purpose": "通达信板块归属 (物删, 切东财概念)", "deprecation_status": "deprecated", "deprecated_reason": "东财迁移Stage④ 概念切 dim_stock_dc_concept", "replacement_table": "dim_stock_dc_concept"},
    "dim_tdx_block_catalog": {"purpose": "通达信板块目录 (物删)", "deprecation_status": "deprecated", "deprecated_reason": "东财迁移Stage④ 概念切东财", "replacement_table": "dim_stock_dc_concept"},
    # Phase ψ.5 removed — dead data. Keep the ledger row for governance, but do
    # not let the missing table stay in yellow health forever.
    "raw_margin_daily": {
        "purpose": "融资融券日度已退役",
        "writer_module": "services/margin_client.py",
        "upstream_source": "akshare:stock_margin_detail_sse/szse",
        "source_tier": 3,
        "expected_freshness": "on-demand",
        "sla_hours": 24 * 30,
        "asset_grain": "stock_code+trade_date",
        "asset_cadence": "on_demand",
        "coverage_policy": "workflow_dependent",
        "null_policy": "classified_required",
        "pit_policy": "not_model_input",
        "intended_use": "governance_context",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "diagnostics_or_context",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
        "deprecation_status": "deprecated",
        "deprecated_reason": "Phase ψ.5 removed — no live writer or current consumer remains",
        "replacement_table": None,
    },
    # 2026-06-22 health 漂移 curation (doctor 排查): reset 删物理表 + 源迁移 tushare 后, 12 红表登记仍 active
    # 致 data_health 假红。标 deprecated + replacement 指后继 (tushare/申万); tdx holder 表本 session 已切 tushare 主源,
    # 备援代码路径 (load_top10_holders._top10_tdx) 独立于注册表状态。owner=主会话 (doctor 排查 dim_data_asset 漂移)。
    "_cache_holder_search": {"purpose": "持仓搜索缓存 (reset 删)", "deprecation_status": "deprecated",
        "deprecated_reason": "reset 删, 瞬时缓存无 replacement", "replacement_table": None, "quality_gate_level": "monitor_only"},
    "dim_financial_indicator_latest": {"purpose": "财务指标快照 (akshare, reset 删/改名)", "deprecation_status": "deprecated",
        "deprecated_reason": "akshare 源退役, 财务指标切 tushare", "replacement_table": "raw_tushare_fina_indicator", "quality_gate_level": "monitor_only"},
    "financial_indicator_sync_state": {"purpose": "财务同步状态 (改名)", "deprecation_status": "deprecated",
        "deprecated_reason": "改名 financial_sync_state", "replacement_table": "financial_sync_state", "quality_gate_level": "monitor_only"},
    "mart_industry_pit_quality": {"purpose": "行业 PIT 质量 (派生 tdx industry, reset 删)", "deprecation_status": "deprecated",
        "deprecated_reason": "行业切申万 v_sw_industry_pit, 派生 mart 删", "replacement_table": "v_sw_industry_pit", "quality_gate_level": "monitor_only"},
    "mart_stock_industry_pit": {"purpose": "个股行业 PIT (派生 tdx industry, reset 删)", "deprecation_status": "deprecated",
        "deprecated_reason": "行业切申万 v_sw_industry_pit", "replacement_table": "v_sw_industry_pit", "quality_gate_level": "monitor_only"},
    "raw_aif10_financial_history": {"purpose": "aif10 财务史 (reset 删)", "deprecation_status": "deprecated",
        "deprecated_reason": "aif10 源迁移 tushare (M4)", "replacement_table": "raw_tushare_fina_indicator", "quality_gate_level": "monitor_only"},
    "raw_aif10_holder_count": {"purpose": "aif10 户数 (reset 删)", "deprecation_status": "deprecated",
        "deprecated_reason": "aif10 源迁移 tushare", "replacement_table": "raw_tushare_stk_holdernumber", "quality_gate_level": "monitor_only"},
    "raw_aif10_forecast_consensus": {"purpose": "aif10 一致预期 (stale)", "deprecation_status": "deprecated",
        "deprecated_reason": "aif10 源迁移; 业绩预告切 tushare forecast (2026-06-22 接入)", "replacement_table": "raw_tushare_forecast", "quality_gate_level": "monitor_only"},
    # fact_top10_holder_period 不在此 (2026-06-24 它是 LIVE 十大流通股东表, 主源已切东财妙想 aif10/source=miaoxiang;
    # 旧 2026-06-22 "切 tushare top10 降备援" 计划已被 aif10 决策取代, 此表非废弃, active 登记见上 EXTRA_UPSTREAM)
    "raw_tdx_f10_holder_research": {"purpose": "tdx F10 股东研究原文 (已物删 2026-06-26)", "deprecation_status": "deleted",
        "deprecated_reason": "2026-06-24 十大流通股东主源切东财妙想 aif10, tdx_f10 fact 行已物删; 本 raw 表暂留作恢复网, 物理退役 pending (updater 路由解耦)", "replacement_table": "fact_top10_holder_period", "quality_gate_level": "monitor_only"},
    "dim_capital_behavior_latest": {"purpose": "资本行为 (akshare 分红/回购/解禁, 已物删 2026-06-27)", "deprecation_status": "deleted",
        "deprecated_reason": "2026-06-27 通达信全删 M4: akshare 资本运作 7表+writer 物删 (用户决cut不迁移); 消费侧 scoring/signals_v2 已切; 档B 若需从 tushare dividend/repurchase/share_float 重接", "replacement_table": "raw_tushare_dividend", "quality_gate_level": "monitor_only"},
    "raw_profit_forecast_snapshot_daily": {"purpose": "盈利预测快照 (akshare, 已物删 2026-06-27)", "deprecation_status": "deleted",
        "deprecated_reason": "2026-06-27 通达信全删 M4: akshare 盈利预测快照物删 (用户决cut); 0 live 读者(声称consumer code中不存在); 档B 若需景气度走 tushare forecast/report_rc", "replacement_table": "raw_tushare_forecast", "quality_gate_level": "monitor_only"},
}

DEPRECATED_ASSET_BY_TABLE = EXTRA_DEPRECATED_ASSET_BY_TABLE


def registry_writer(table: str) -> str | None:
    found = _registry_table_metadata(table)
    if found is None:
        return None
    client, _ = found
    return client.module.replace(".", "/") + ".py"


def known_upstream(table: str) -> tuple[object, object]:
    """先查 client 注册表, 再查补丁. 返回 (upstream_source, source_tier)."""
    via_registry = _registry_upstream(table)
    if via_registry[0] is not None:
        return via_registry
    return EXTRA_UPSTREAM_BY_TABLE.get(table, (None, None))


# 期望刷新频率推断 (粗略, 仅作为 client_registry 没覆盖时的兜底)
def infer_freshness(table_name: str, layer: str) -> tuple[str, int]:
    """Returns (expected_freshness, sla_hours).

    优先级: 1) clients_registry 显式声明  2) 表名启发式  3) layer 默认值
    """
    via_registry = _registry_freshness(table_name)
    if via_registry is not None:
        return via_registry
    if table_name in EXTRA_FRESHNESS_BY_TABLE:
        return EXTRA_FRESHNESS_BY_TABLE[table_name]

    name = table_name.lower()
    if "kline" in name or "_daily" in name:
        return ("t+0", 24)
    if "quarterly" in name:
        return ("quarterly", 24 * 95)  # ~3 个月
    if name.startswith("dim_") and ("calendar" in name or "industry" in name or "block" in name or "alias" in name):
        return ("static", 24 * 30)
    if name.startswith("mart_data_health"):
        return ("t+0", 25)  # 每天 09:30 + 1h
    if name.startswith("fact_feature_panel"):
        return ("t+1", 36)
    if name.startswith("mart_daily_recommendation"):
        return ("t+0", 25)
    if name.startswith("mart_multidim_model"):
        return ("on-demand", 24 * 7 * 4)  # 训练 ~4 周
    if name.startswith("fact_") and "event" in name:
        return ("event", 48)
    if layer == "raw":
        return ("t+0", 30)
    if layer in ("fact", "mart"):
        return ("t+1", 48)
    return ("on-demand", 24 * 30)


GOVERNANCE_COLUMNS = [
    "asset_grain",
    "asset_cadence",
    "coverage_policy",
    "null_policy",
    "pit_policy",
    "intended_use",
    "model_eligibility",
    "strategy_eligibility",
    "frontend_visibility",
    "quality_gate_level",
]


def ensure_dim_data_asset_governance_columns(con) -> None:
    exists = con.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = 'dim_data_asset'
         LIMIT 1
        """
    ).fetchone()
    if not exists:
        return
    for column in GOVERNANCE_COLUMNS:
        try:
            con.execute(f"ALTER TABLE dim_data_asset ADD COLUMN {column} TEXT")
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                continue
            raise


def table_columns(con, table_name: str) -> set[str]:
    try:
        return {
            row["column_name"] if hasattr(row, "keys") else row[0]
            for row in con.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_name = ?
                """,
                (table_name,),
            ).fetchall()
        }
    except Exception:
        return set()


def registry_asset_contract(table: str) -> dict[str, str]:
    found = _registry_table_metadata(table)
    if found is None:
        return {}
    _, write_spec = found
    out: dict[str, str] = {}
    for column in GOVERNANCE_COLUMNS:
        value = getattr(write_spec, column, None)
        if value is not None:
            out[column] = str(value)
    return out


def infer_asset_contract(
    table_name: str,
    *,
    layer: str,
    freshness: str,
    upstream_source: object | None,
) -> dict[str, str]:
    name = table_name.lower()
    upstream = str(upstream_source or "").lower()
    freshness_norm = str(freshness or "").lower()

    contract = {
        "asset_grain": "table",
        "asset_cadence": "on_demand",
        "coverage_policy": "workflow_dependent",
        "null_policy": "classified_required",
        "pit_policy": "not_model_input",
        "intended_use": "governance_context",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "diagnostics_or_context",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "monitor_only",
    }

    if "trading_calendar" in name:
        contract.update(
            asset_grain="trade_date",
            asset_cadence="exchange_calendar",
            coverage_policy="complete_exchange_calendar",
            null_policy="no_null",
            pit_policy="pre_fetch_calendar_gate",
            intended_use="pipeline_gate",
            model_eligibility="not_model_input",
            strategy_eligibility="calendar_filter",
            quality_gate_level="blocking",
        )
    elif "kline" in name or "price_" in name:
        contract.update(
            asset_grain="stock_code+trade_date",
            asset_cadence="trading_day_daily",
            coverage_policy="dense_active_a_stock_trading_days",
            null_policy="no_null_for_ohlcv_after_calendar",
            pit_policy="same_day_market_data_after_close",
            intended_use="pricing_and_trend_source",
            model_eligibility="derive_features_only",
            strategy_eligibility="entry_exit_pricing_and_trend",
            quality_gate_level="blocking",
        )
        if "tdxhub" in upstream or "tdxhub" in name:
            contract["intended_use"] = "primary_pricing_source"
        elif "akshare" in upstream:
            contract["intended_use"] = "fallback_pricing_source"
            contract["quality_gate_level"] = "warning"
    elif "feature_panel" in name:
        contract.update(
            asset_grain="stock_code+signal_date",
            asset_cadence="trading_day_daily",
            coverage_policy="dense_active_a_stock_trading_days",
            null_policy="no_unclassified_nulls",
            pit_policy="feature_registry_required",
            intended_use="model_training_and_scoring",
            model_eligibility="registered_features_only",
            strategy_eligibility="scoring_and_horizon_selection",
            quality_gate_level="blocking",
        )
    elif any(token in name for token in ("lhb", "survey", "dzjy", "event", "plan", "trade", "trigger")):
        contract.update(
            asset_grain="stock_code+event",
            asset_cadence="event_driven",
            coverage_policy="sparse_event_presence_only",
            null_policy="no_event_is_absence_not_missing",
            pit_policy="source_notice_or_event_date_required",
            intended_use="attention_signal_or_context",
            model_eligibility="encoded_auxiliary_only",
            strategy_eligibility="attention_filter_context",
            quality_gate_level="warning",
        )
    elif any(token in name for token in ("holder", "qfii", "fund_holding")):
        contract.update(
            asset_grain="stock_code+report_period+holder",
            asset_cadence="periodic_or_event",
            coverage_policy="periodic_report_after_listing",
            null_policy="classified_required",
            pit_policy="source_notice_date_required",
            intended_use="institutional_ownership_context",
            model_eligibility="pit_validated_candidate_only",
            strategy_eligibility="institution_filter_context",
            quality_gate_level="blocking",
        )
    elif any(token in name for token in ("financial", "gpcw", "fundamental", "forecast")):
        contract.update(
            asset_grain="stock_code+report_period",
            asset_cadence="quarterly_or_announcement",
            coverage_policy="periodic_report_after_listing",
            null_policy="classified_required",
            pit_policy="source_notice_or_conservative_lag_required",
            intended_use="fundamental_context_or_candidate",
            model_eligibility="pit_validated_candidate_only",
            strategy_eligibility="quality_filter_context",
            quality_gate_level="blocking",
        )
    elif any(token in name for token in ("recommendation", "prediction", "model", "champion")):
        contract.update(
            asset_grain="run_id+stock_code",
            asset_cadence="on_demand_or_trading_day",
            coverage_policy="run_manifest_defined",
            null_policy="no_unclassified_nulls",
            pit_policy="inherits_input_policy_hash",
            intended_use="production_decision_output",
            model_eligibility="not_model_input",
            strategy_eligibility="production_output",
            quality_gate_level="blocking",
        )
    elif freshness_norm in {"event", "event_driven"}:
        contract.update(
            asset_grain="event",
            asset_cadence="event_driven",
            coverage_policy="sparse_event_presence_only",
            null_policy="no_event_is_absence_not_missing",
            pit_policy="source_event_date_required",
            intended_use="event_context",
            model_eligibility="encoded_auxiliary_only",
            strategy_eligibility="context_or_filter",
            quality_gate_level="warning",
        )
    elif freshness_norm in {"quarterly", "monthly", "weekly"}:
        contract.update(
            asset_grain="stock_code+period",
            asset_cadence=freshness_norm,
            coverage_policy="periodic_source_expected",
            null_policy="classified_required",
            pit_policy="source_available_date_required",
            intended_use="periodic_context",
            model_eligibility="pit_validated_candidate_only",
            strategy_eligibility="context_or_filter",
            quality_gate_level="warning",
        )
    elif layer in {"dim", "sys"}:
        contract.update(
            asset_grain="entity_key",
            asset_cadence="static_or_config",
            coverage_policy="complete_for_configured_universe",
            null_policy="no_null_for_keys",
            pit_policy="configuration_effective_date",
            intended_use="dimension_or_policy",
            model_eligibility="not_model_input",
            strategy_eligibility="filter_or_join_dimension",
            quality_gate_level="blocking",
        )
    elif layer in {"fact", "mart"} and freshness_norm == "on-demand":
        contract.update(
            asset_grain="run_id",
            asset_cadence="on_demand",
            coverage_policy="run_manifest_defined",
            null_policy="classified_required",
            pit_policy="inherits_input_policy",
            intended_use="research_or_monitoring",
            model_eligibility="not_model_input",
            strategy_eligibility="diagnostics_or_context",
            quality_gate_level="monitor_only",
        )

    contract.update(EXTRA_ASSET_CONTRACT_BY_TABLE.get(table_name, {}))
    contract.update(registry_asset_contract(table_name))
    return contract


def _should_preserve_manual_asset(prev, force_overwrite: bool) -> bool:
    return (
        prev is not None
        and not force_overwrite
        and not bool(prev.get("auto_discovered"))
    )


def _apply_manual_asset_overrides(
    prev,
    *,
    force_overwrite: bool,
    purpose: object | None,
    upstream: object | None,
    source_tier: object | None,
    freshness: object | None,
    sla: object | None,
    governance: dict[str, str],
) -> tuple[
    object | None,
    object | None,
    object | None,
    object | None,
    object | None,
    dict[str, str],
]:
    """Preserve curator-owned dim_data_asset fields unless forced."""

    merged_governance = dict(governance)
    if not _should_preserve_manual_asset(prev, force_overwrite):
        return purpose, upstream, source_tier, freshness, sla, merged_governance

    if prev.get("purpose"):
        purpose = prev["purpose"]
    if prev.get("upstream_source"):
        upstream = prev["upstream_source"]
    if prev.get("source_tier") is not None:
        source_tier = prev["source_tier"]
    if prev.get("expected_freshness"):
        freshness = prev["expected_freshness"]
    if prev.get("sla_hours") is not None:
        sla = prev["sla_hours"]
    for column in GOVERNANCE_COLUMNS:
        if prev.get(column):
            merged_governance[column] = prev[column]
    return purpose, upstream, source_tier, freshness, sla, merged_governance


def _backend_python_text_index() -> list[tuple[str, str]]:
    """Read backend Python files once for writer/reader discovery."""

    files: list[tuple[str, str]] = []
    for f in BACKEND.rglob("*.py"):
        rel = str(f.relative_to(REPO))
        if (
            "/tests/" in rel
            or "audit_stale_references" in rel
            or "seed_dim_data_asset" in rel
            or "data_health_snapshot" in rel
        ):
            continue
        try:
            files.append((rel, f.read_text(encoding="utf-8", errors="ignore")))
        except Exception:
            continue
    return files


def _rank_writer_path(rel: str) -> tuple:
    if "scripts/" in rel:
        tier = 0
    elif rel.endswith("services/db.py"):
        tier = 3
    elif "services/" in rel:
        tier = 1
    elif "routers/" in rel:
        tier = 2
    else:
        tier = 4
    return (tier, len(rel))


_SQL_IDENTIFIER_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"


def _qualified_table_capture(group_name: str) -> str:
    table_capture = rf'["`\']?(?P<{group_name}>{_SQL_IDENTIFIER_PATTERN})["`\']?'
    return rf"(?:{_SQL_IDENTIFIER_PATTERN}\.)?{table_capture}"


_BACKEND_WRITE_TABLE_GROUPS = (
    "write_table",
    "create_table",
    "register_table",
    "to_sql_table",
)
_BACKEND_WRITE_REFERENCE_PATTERN = re.compile(
    rf"\b(?:INSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO|UPDATE|DELETE\s+FROM|COPY)"
    rf"\s+{_qualified_table_capture('write_table')}\b"
    rf"|\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|TEMP\s+TABLE)"
    rf"(?:\s+IF\s+NOT\s+EXISTS)?\s+{_qualified_table_capture('create_table')}"
    rf"[\s\S]{{0,240}}\bAS\b"
    rf"|register\(\s*['\"](?P<register_table>{_SQL_IDENTIFIER_PATTERN})['\"]"
    rf"|\.to_sql\(\s*['\"](?P<to_sql_table>{_SQL_IDENTIFIER_PATTERN})['\"]",
    re.IGNORECASE,
)
_BACKEND_READ_REFERENCE_PATTERN = re.compile(
    rf"\b(?:FROM|JOIN)\s+{_qualified_table_capture('read_table')}\b",
    re.IGNORECASE,
)


def _matched_table_name(match: re.Match[str], group_names: tuple[str, ...]) -> str | None:
    groups = match.groupdict()
    for group_name in group_names:
        table = groups.get(group_name)
        if table:
            return table
    return None


def _iter_reference_tables(
    pattern: re.Pattern[str],
    group_names: tuple[str, ...],
    text: str,
):
    for match in pattern.finditer(text):
        table = _matched_table_name(match, group_names)
        if table:
            yield table


def _build_backend_table_reference_index(
    table_names: list[str],
    text_index: list[tuple[str, str]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build table writer/reader maps with one pass over backend files."""

    table_set = set(table_names)
    writer_hits: dict[str, list[str]] = defaultdict(list)
    reader_hits: dict[str, set[str]] = defaultdict(set)
    for rel, text in text_index:
        for table in _iter_reference_tables(
            _BACKEND_WRITE_REFERENCE_PATTERN,
            _BACKEND_WRITE_TABLE_GROUPS,
            text,
        ):
            if table in table_set:
                writer_hits[table].append(rel)
        for table in _iter_reference_tables(
            _BACKEND_READ_REFERENCE_PATTERN,
            ("read_table",),
            text,
        ):
            if table in table_set:
                reader_hits[table].add(rel)
    writer_map = {
        table: sorted(set(paths), key=_rank_writer_path)[0]
        for table, paths in writer_hits.items()
    }
    reader_map = {table: sorted(paths) for table, paths in reader_hits.items()}
    return writer_map, reader_map


def grep_writer(table_name: str, text_index: list[tuple[str, str]] | None = None) -> str | None:
    """全仓 grep, 找真 writer (INSERT/UPDATE 优先于 CREATE TABLE).

    db.py 里的 CREATE TABLE IF NOT EXISTS 是 schema 声明, 不是 writer.
    所以分两轮: 先找 INSERT/UPDATE, 找不到再退回到 CREATE.
    """

    # 表名前可能有 schema 前缀 (如 smartmoney.fact_feature_panel) 或反引号
    name_pat = rf"(?:[\w.]+\.)?{re.escape(table_name)}"
    pat_write = [
        # 含 INSERT OR REPLACE/IGNORE INTO X 等所有 INSERT 变体
        re.compile(rf"\bINSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO\s+{name_pat}\b", re.IGNORECASE),
        re.compile(rf"\bUPDATE\s+{name_pat}\b", re.IGNORECASE),
        re.compile(rf"\bDELETE\s+FROM\s+{name_pat}\b", re.IGNORECASE),
        re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|TEMP\s+TABLE).*?\b{name_pat}\b\s+AS\b", re.IGNORECASE),
        re.compile(rf'register\(["\']{re.escape(table_name)}["\']', re.IGNORECASE),
        re.compile(rf'COPY\s+{name_pat}\b', re.IGNORECASE),
        # Legacy table-helper writes by table name.
        re.compile(rf'\.to_sql\(\s*["\']{re.escape(table_name)}["\']', re.IGNORECASE),
        # SQLAlchemy 风格: con.execute(text("INSERT INTO X ..."))
        # 已被前面的 INSERT INTO 模式覆盖
        # f-string 拼接的写: f"INSERT INTO {var}" 用变量名 — 这种情况一定漏, 留 manual fix
    ]
    pat_schema = [
        re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|TEMP\s+TABLE)(?:\s+IF\s+NOT\s+EXISTS)?\s+{re.escape(table_name)}\b", re.IGNORECASE),
    ]

    def _scan(patterns):
        out = []
        for rel, text in text_index or _backend_python_text_index():
            if any(p.search(text) for p in patterns):
                out.append(rel)
        return out

    write_hits = _scan(pat_write)
    if write_hits:
        write_hits.sort(key=_rank_writer_path)
        return write_hits[0]
    schema_hits = _scan(pat_schema)
    if schema_hits:
        # 仅 schema 声明, 没真 writer → 返 None 或带提示
        # 这是 orphan_no_writer 候选
        return None
    return None


def grep_readers(table_name: str, text_index: list[tuple[str, str]] | None = None) -> list[str]:
    """全仓 grep, 找 FROM X / JOIN X / SELECT...X 命中的所有 .py 文件."""

    patterns = [
        re.compile(rf"\bFROM\s+{re.escape(table_name)}\b", re.IGNORECASE),
        re.compile(rf"\bJOIN\s+{re.escape(table_name)}\b", re.IGNORECASE),
    ]
    readers = []
    for rel, text in text_index or _backend_python_text_index():
        if any(p.search(text) for p in patterns):
            readers.append(rel)
    return readers


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _row_value(row, key: str, index: int):
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _table_count_union_sql(table_names: list[str]) -> str:
    return "\nUNION ALL\n".join(
        (
            f"SELECT {_sql_string_literal(name)} AS table_name, "
            f"COUNT(*) AS row_count FROM {_quote_identifier(name)}"
        )
        for name in table_names
    )


def _fetch_table_row_counts(con, table_names: list[str]) -> dict[str, int]:
    if not table_names:
        return {}
    try:
        rows = con.execute(_table_count_union_sql(table_names)).fetchall()
    except Exception:
        log.warning(
            "failed to batch table row counts; defaulting row_count to 0",
            exc_info=True,
        )
        return {}
    return {
        str(_row_value(row, "table_name", 0)): int(_row_value(row, "row_count", 1) or 0)
        for row in rows
    }


def get_all_tables(con) -> list[tuple[str, int]]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_type='BASE TABLE' "
        "ORDER BY table_name"
    ).fetchall()
    table_names = [str(_row_value(row, "table_name", 0)) for row in rows]
    row_counts = _fetch_table_row_counts(con, table_names)
    return [(name, row_counts.get(name, 0)) for name in table_names]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印, 不写库")
    parser.add_argument("--force-overwrite", action="store_true",
                        help="覆盖已有行的人工补字段 (默认保留)")
    args = parser.parse_args()

    con = get_conn()
    ensure_dim_data_asset_governance_columns(con)
    log.info("scanning all tables...")
    tables = get_all_tables(con)
    deprecated_tables = [
        tbl for tbl in EXTRA_DEPRECATED_ASSET_BY_TABLE
        if tbl not in {name for name, _row_count in tables}
    ]
    if deprecated_tables:
        tables = tables + [(tbl, 0) for tbl in deprecated_tables]
    log.info("total tables: %d (+%d deprecated assets)", len(tables) - len(deprecated_tables), len(deprecated_tables))
    text_index = _backend_python_text_index()
    log.info("indexed %d backend python files", len(text_index))
    writer_index, reader_index = _build_backend_table_reference_index(
        [tbl for tbl, _row_count in tables],
        text_index,
    )
    log.info(
        "reference index: writers=%d reader_sets=%d",
        len(writer_index),
        len(reader_index),
    )

    # 拿当前已注册的, 区分 auto vs manual
    existing = {}
    try:
        dim_cols = table_columns(con, "dim_data_asset")
        select_cols = [
            "table_name",
            "auto_discovered",
            "purpose",
            "upstream_source",
            "source_tier",
            "fallback_chain",
            "expected_freshness",
            "sla_hours",
            "consumed_by_views",
            "notes",
            "deprecation_status",
            "deprecated_reason",
            "replacement_table",
            *[column for column in GOVERNANCE_COLUMNS if column in dim_cols],
        ]
        for r in con.execute(
            f"SELECT {', '.join(select_cols)} FROM dim_data_asset"
        ).fetchall():
            existing[r["table_name"]] = r
    except Exception:
        pass
    log.info("existing dim_data_asset rows: %d", len(existing))
    table_names = {tbl for tbl, _row_count in tables}

    upserted = 0
    skipped = 0
    for tbl, row_count in tables:
        if tbl == "dim_data_asset" or tbl == "mart_data_health":
            # 自身不在自审计内 (避免循环)
            continue
        layer = detect_layer(tbl)
        deprecation = EXTRA_DEPRECATED_ASSET_BY_TABLE.get(tbl, {})
        writer = (
            deprecation.get("writer_module")
            or registry_writer(tbl)
            or EXTRA_WRITER_BY_TABLE.get(tbl)
            or writer_index.get(tbl)
        )
        readers = list(reader_index.get(tbl) or [])
        # 排除自引用
        readers = [r for r in readers if r != writer]
        upstream, source_tier = known_upstream(tbl)
        if deprecation:
            upstream = deprecation.get("upstream_source", upstream)
            source_tier = deprecation.get("source_tier", source_tier)
        # 派生表: writer 是脚本 + readers 多, upstream 通常是 derived
        if upstream is None:
            if layer in ("fact", "mart") and writer is not None:
                upstream = f"derived (writer: {writer})"
        freshness, sla = infer_freshness(tbl, layer)
        governance = infer_asset_contract(
            tbl,
            layer=layer,
            freshness=freshness,
            upstream_source=upstream,
        )
        purpose = None  # 留给人工补

        purpose, upstream, source_tier, freshness, sla, governance = (
            _apply_manual_asset_overrides(
                existing.get(tbl),
                force_overwrite=args.force_overwrite,
                purpose=purpose,
                upstream=upstream,
                source_tier=source_tier,
                freshness=freshness,
                sla=sla,
                governance=governance,
            )
        )
        if deprecation:
            purpose = deprecation.get("purpose", purpose)
            upstream = deprecation.get("upstream_source", upstream)
            source_tier = deprecation.get("source_tier", source_tier)
            freshness = deprecation.get("expected_freshness", freshness)
            sla = deprecation.get("sla_hours", sla)
        deprecation_status = deprecation.get("deprecation_status", "active")
        deprecated_reason = deprecation.get("deprecated_reason")
        replacement_table = deprecation.get("replacement_table")

        readers_json = json.dumps(readers, ensure_ascii=False)
        # consumed_by_views: 简单 grep frontend 找 fetch('/api/...) 含表名 (粗略)
        # 留给后续, 当前先空
        consumed_by_views = "[]"

        if args.dry_run:
            log.info(
                "[dry] %s | layer=%s | writer=%s | readers=%d | upstream=%s | "
                "tier=%s | freshness=%s | coverage=%s | model=%s",
                tbl,
                layer,
                writer,
                len(readers),
                upstream,
                source_tier,
                freshness,
                governance["coverage_policy"],
                governance["model_eligibility"],
            )
            continue

        # DuckDB binder 在 DO UPDATE SET 上下文里把 CURRENT_TIMESTAMP 当列名;
        # 用 now() 等价替换 (规则参见 CLAUDE.md #11/#12 + qfii_client.py:319 已有先例).
        # --force-overwrite: 用 EXCLUDED.X 直写 (registry 是真相源)
        # 否则: COALESCE 保留人工补字段
        if args.force_overwrite:
            update_clause = """
                layer = EXCLUDED.layer,
                writer_module = EXCLUDED.writer_module,
                reader_modules = EXCLUDED.reader_modules,
                purpose = COALESCE(EXCLUDED.purpose, dim_data_asset.purpose),
                upstream_source = EXCLUDED.upstream_source,
                source_tier = EXCLUDED.source_tier,
                expected_freshness = EXCLUDED.expected_freshness,
                sla_hours = EXCLUDED.sla_hours,
                deprecation_status = EXCLUDED.deprecation_status,
                deprecated_reason = EXCLUDED.deprecated_reason,
                replacement_table = EXCLUDED.replacement_table,
                asset_grain = EXCLUDED.asset_grain,
                asset_cadence = EXCLUDED.asset_cadence,
                coverage_policy = EXCLUDED.coverage_policy,
                null_policy = EXCLUDED.null_policy,
                pit_policy = EXCLUDED.pit_policy,
                intended_use = EXCLUDED.intended_use,
                model_eligibility = EXCLUDED.model_eligibility,
                strategy_eligibility = EXCLUDED.strategy_eligibility,
                frontend_visibility = EXCLUDED.frontend_visibility,
                quality_gate_level = EXCLUDED.quality_gate_level,
                last_updated_at = now()
            """
        else:
            update_clause = """
                layer = EXCLUDED.layer,
                writer_module = EXCLUDED.writer_module,
                reader_modules = EXCLUDED.reader_modules,
                purpose = CASE WHEN dim_data_asset.auto_discovered
                               THEN COALESCE(EXCLUDED.purpose, dim_data_asset.purpose)
                               ELSE COALESCE(dim_data_asset.purpose, EXCLUDED.purpose) END,
                upstream_source = CASE WHEN dim_data_asset.auto_discovered
                                       THEN EXCLUDED.upstream_source
                                       ELSE COALESCE(dim_data_asset.upstream_source, EXCLUDED.upstream_source) END,
                source_tier = CASE WHEN dim_data_asset.auto_discovered
                                   THEN EXCLUDED.source_tier
                                   ELSE COALESCE(dim_data_asset.source_tier, EXCLUDED.source_tier) END,
                expected_freshness = CASE WHEN dim_data_asset.auto_discovered
                                          THEN EXCLUDED.expected_freshness
                                          ELSE COALESCE(dim_data_asset.expected_freshness, EXCLUDED.expected_freshness) END,
                sla_hours = CASE WHEN dim_data_asset.auto_discovered
                                 THEN EXCLUDED.sla_hours
                                 ELSE COALESCE(dim_data_asset.sla_hours, EXCLUDED.sla_hours) END,
                deprecation_status = CASE WHEN dim_data_asset.auto_discovered
                                          THEN EXCLUDED.deprecation_status
                                          ELSE COALESCE(dim_data_asset.deprecation_status, EXCLUDED.deprecation_status) END,
                deprecated_reason = CASE WHEN dim_data_asset.auto_discovered
                                         THEN EXCLUDED.deprecated_reason
                                         ELSE COALESCE(dim_data_asset.deprecated_reason, EXCLUDED.deprecated_reason) END,
                replacement_table = CASE WHEN dim_data_asset.auto_discovered
                                         THEN EXCLUDED.replacement_table
                                         ELSE COALESCE(dim_data_asset.replacement_table, EXCLUDED.replacement_table) END,
                asset_grain = CASE WHEN dim_data_asset.auto_discovered
                                   THEN EXCLUDED.asset_grain
                                   ELSE COALESCE(dim_data_asset.asset_grain, EXCLUDED.asset_grain) END,
                asset_cadence = CASE WHEN dim_data_asset.auto_discovered
                                     THEN EXCLUDED.asset_cadence
                                     ELSE COALESCE(dim_data_asset.asset_cadence, EXCLUDED.asset_cadence) END,
                coverage_policy = CASE WHEN dim_data_asset.auto_discovered
                                       THEN EXCLUDED.coverage_policy
                                       ELSE COALESCE(dim_data_asset.coverage_policy, EXCLUDED.coverage_policy) END,
                null_policy = CASE WHEN dim_data_asset.auto_discovered
                                   THEN EXCLUDED.null_policy
                                   ELSE COALESCE(dim_data_asset.null_policy, EXCLUDED.null_policy) END,
                pit_policy = CASE WHEN dim_data_asset.auto_discovered
                                  THEN EXCLUDED.pit_policy
                                  ELSE COALESCE(dim_data_asset.pit_policy, EXCLUDED.pit_policy) END,
                intended_use = CASE WHEN dim_data_asset.auto_discovered
                                    THEN EXCLUDED.intended_use
                                    ELSE COALESCE(dim_data_asset.intended_use, EXCLUDED.intended_use) END,
                model_eligibility = CASE WHEN dim_data_asset.auto_discovered
                                         THEN EXCLUDED.model_eligibility
                                         ELSE COALESCE(dim_data_asset.model_eligibility, EXCLUDED.model_eligibility) END,
                strategy_eligibility = CASE WHEN dim_data_asset.auto_discovered
                                            THEN EXCLUDED.strategy_eligibility
                                            ELSE COALESCE(dim_data_asset.strategy_eligibility, EXCLUDED.strategy_eligibility) END,
                frontend_visibility = CASE WHEN dim_data_asset.auto_discovered
                                           THEN EXCLUDED.frontend_visibility
                                           ELSE COALESCE(dim_data_asset.frontend_visibility, EXCLUDED.frontend_visibility) END,
                quality_gate_level = CASE WHEN dim_data_asset.auto_discovered
                                          THEN EXCLUDED.quality_gate_level
                                          ELSE COALESCE(dim_data_asset.quality_gate_level, EXCLUDED.quality_gate_level) END,
                last_updated_at = now()
            """
        con.execute(f"""
            INSERT INTO dim_data_asset (
                table_name, layer, purpose, writer_module, reader_modules,
                upstream_source, source_tier, expected_freshness, sla_hours,
                consumed_by_views,
                deprecation_status, deprecated_reason, replacement_table,
                asset_grain, asset_cadence, coverage_policy, null_policy,
                pit_policy, intended_use, model_eligibility,
                strategy_eligibility, frontend_visibility, quality_gate_level,
                auto_discovered, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, now())
            ON CONFLICT (table_name) DO UPDATE SET
                {update_clause}
        """, (
            tbl, layer, purpose, writer, readers_json,
            upstream, source_tier, freshness, sla,
            consumed_by_views,
            deprecation_status, deprecated_reason, replacement_table,
            governance["asset_grain"],
            governance["asset_cadence"],
            governance["coverage_policy"],
            governance["null_policy"],
            governance["pit_policy"],
            governance["intended_use"],
            governance["model_eligibility"],
            governance["strategy_eligibility"],
            governance["frontend_visibility"],
            governance["quality_gate_level"],
        ))
        upserted += 1

    if not args.dry_run:
        con.commit()
        log.info("upserted %d rows into dim_data_asset", upserted)
    else:
        log.info("dry-run: would upsert %d rows", len(tables))

    # 摘要
    by_layer = defaultdict(int)
    for tbl, _ in tables:
        by_layer[detect_layer(tbl)] += 1
    log.info("=== layer summary ===")
    for k, v in sorted(by_layer.items(), key=lambda x: -x[1]):
        log.info("  %s: %d", k, v)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
