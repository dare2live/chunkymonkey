# FEATURE_MAP — 机器生成功能地图

> 由 `scripts/chunkyctl map` (backend/scripts/build_feature_map.py) 重生成, **勿手改**。
> 只列机器可枚举事实 (入口/数据域/产表 writer/依赖热点/计数); 人工判断层 (坑/权重/状态) 在 `PROJECT_INDEX.md`。机器版: `data/reports/feature_map.json` (本地, 不入 git)。
> Snapshot: 2026-06-12 22:01

## 1. 入口面

### chunkyctl 子命令

| 命令 | 说明 |
|---|---|
| `doctor` | project health snapshot, including storage payload recursion/size audit unless skipped. |
| `worktree` | read-only dirty worktree bucket report. |
| `docs` | docs graph + docs-cleanup worktree-slice readiness. |
| `preflight` | what gates must run before editing a task. |
| `audit` | scoped post-change checks; add --run to execute them. |
| `jobs` | provider-neutral experiment job contract / plan. |
| `map` | regenerate FEATURE_MAP.md machine-derived feature map; --check = drift gate only. |
| `data-status` |  |

### launchd 定时任务

| Label | 时刻 | 入口 |
|---|---|---|
| com.chunkymonkey.codex-monitor | 900 | `/bin/bash scripts/codex_monitor.sh` |
| com.chunkymonkey.concept-snapshot | 17:40 | `.venv/bin/python scripts/launchd_job_wrapper.py concept_snapshot .venv/bin/python backend/…` |
| com.chunkymonkey.daily-update | 17:00 | `.venv/bin/python scripts/launchd_job_wrapper.py daily_update /bin/bash scripts/daily_updat…` |
| com.chunkymonkey.nightly-data-audit | 02:00 | `.venv/bin/python scripts/launchd_job_wrapper.py nightly_data_audit .venv/bin/python backen…` |

### API 路由 (regex 口径: @router 装饰器)

| router | prefix | 端点数 |
|---|---|---|
| data_sources | `/api/data_sources` | 18 |
| etf | `/api/etf` | 9 |
| institution | `/api/inst` | 30 |
| market | `/api/inst` | 1 |
| ops_manual_run | `/api/v3/ops` | 3 |
| recommendation | `/api/rec` | 7 |
| screening | `/api/screening` | 7 |
| signals | `/api/signals` | 10 |
| stock_graph | `/api/v3` | 3 |
| strategy_preset | `/api/inst/strategy` | 4 |
| updater | `/api/inst` | 13 |
| updater_lifeboat | `—` | 3 |
| v3_bestchoice | `/api/v3` | 4 |
| v3_config | `/api/v3` | 1 |
| v3_market_perception | `/api/v3/market_perception` | 11 |
| v3_meta | `/api/v3` | 7 |
| v3_paper | `/api/v3/paper` | 5 |
| v3_perception_legacy | `/api/v3/perception` | 5 |
| v3_picture | `/api/v3` | 3 |
| v3_portfolio_builder | `/api/v3/portfolio` | 5 |
| v3_selection | `/api/v3/selection` | 6 |
| v3_views | `/api/v3/view` | 6 |
| workbench | `/api/workbench` | 10 |

端点全列表在 json (`routes` 键)。

## 2. 数据域 (sync_registry)

| 域 | 源 | api | 表 | 模式 | SLA(交易日) |
|---|---|---|---|---|---|
| adj_factor | tushare | adj_factor | raw_tushare_adj_factor | by_trade_date | 1 |
| cyq_perf | tushare | cyq_perf | raw_tushare_cyq_perf | by_trade_date | 1 |
| daily | tushare | daily | raw_tushare_daily | by_trade_date | 1 |
| daily_basic | tushare | daily_basic | raw_tushare_daily_basic | by_trade_date | 1 |
| dc_index | tushare | dc_index | raw_tushare_dc_index | by_trade_date | 2 |
| dc_member | tushare | dc_member | raw_tushare_dc_member | by_trade_date | 2 |
| dividend | tushare | dividend | raw_tushare_dividend | by_trade_date | 5 |
| fina_mainbz | tushare | fina_mainbz | raw_tushare_fina_mainbz | by_ts_code | 130 |
| limit_cpt_list | tushare | limit_cpt_list | raw_tushare_limit_cpt_list | by_trade_date | 2 |
| limit_list_d | tushare | limit_list_d | raw_tushare_limit_list_d | by_trade_date | 1 |
| moneyflow | tushare | moneyflow | raw_tushare_moneyflow | by_trade_date | 1 |
| moneyflow_hsgt | tushare | moneyflow_hsgt | raw_tushare_moneyflow_hsgt | by_trade_date | 2 |
| moneyflow_ind_dc | tushare | moneyflow_ind_dc | raw_tushare_moneyflow_ind_dc | by_trade_date | 2 |
| moneyflow_mkt_dc | tushare | moneyflow_mkt_dc | raw_tushare_moneyflow_mkt_dc | by_date_range | 1 |
| report_rc | tushare | report_rc | raw_tushare_report_rc | by_trade_date | 3 |
| stk_limit | tushare | stk_limit | raw_tushare_stk_limit | by_trade_date | 1 |
| stock_st | tushare | stock_st | raw_tushare_stock_st | by_trade_date | 1 |
| suspend_d | tushare | suspend_d | raw_tushare_suspend_d | by_trade_date | 3 |
| ths_hot | tushare | ths_hot | raw_tushare_ths_hot | by_trade_date | 2 |
| top_inst | tushare | top_inst | raw_tushare_top_inst | by_trade_date | 2 |
| top_list | tushare | top_list | raw_tushare_top_list | by_trade_date | 2 |
| trade_cal | tushare | trade_cal | raw_tushare_trade_cal | full_refresh | 30 |

## 3. 产表 writer (单 writer 契约审查素材)

统计: 表 302 张 | 单 writer 181 | 多 writer 121 | 动态表名写点 73 处 (38 文件)

口径免责: 静态正则扫描, 含历史/backfill 一次性脚本与字符串内 SQL 样例; **多 writer 计数 ≠ 违规待修清单** — 升级为问题需逐表人工确认运行时并发写。

### 动态表名写点 (f-string, 静态不可归属 — 这些文件写的表不在下方普查内)

| 文件 | 写点数 |
|---|---|
| backend/scripts/backfill_strategy_result_registry.py | 2 |
| backend/scripts/build_akshare_panel.py | 1 |
| backend/scripts/build_alpha158_duck.py | 2 |
| backend/scripts/build_concept_events.py | 2 |
| backend/scripts/build_executive_trade_events.py | 2 |
| backend/scripts/build_feature_map.py | 1 |
| backend/scripts/build_fund_flow_rank_snapshot_daily.py | 1 |
| backend/scripts/build_industry_beta_daily.py | 2 |
| backend/scripts/build_lhb_events.py | 1 |
| backend/scripts/build_market_cap_decile_daily.py | 2 |
| backend/scripts/build_mart_stock_pool_assignment.py | 2 |
| backend/scripts/build_mart_stock_regime_full.py | 1 |
| backend/scripts/build_stage_opt_pit.py | 2 |
| backend/scripts/build_unified_panel_v1.py | 1 |
| backend/scripts/cleanup_corrupt_oos_predictions.py | 1 |
| backend/scripts/db_split_execute.py | 1 |
| backend/scripts/import_bestchoice_phase1_candidates.py | 2 |
| backend/scripts/import_model_train_log_artifact.py | 1 |
| backend/scripts/import_phase5_remote_predictions.py | 3 |
| backend/scripts/optimize_per_formula_stage.py | 1 |
| backend/scripts/optimize_per_stock_stage_strategy.py | 1 |
| backend/scripts/retrain_lambdamart_v6.py | 4 |
| backend/scripts/run_daily_v7_inference.py | 2 |
| backend/scripts/run_paper_sim_lambdamart_v6_compare.py | 2 |
| backend/scripts/seed_dim_data_asset.py | 1 |
| backend/services/aif10_capability_client.py | 3 |
| backend/services/candidate_feature_set_gc.py | 1 |
| backend/services/data_sources/sync_runner.py | 2 |
| backend/services/industry_pit.py | 5 |
| backend/services/labels/build.py | 1 |
| backend/services/optimization/ddl.py | 4 |
| backend/services/optimization/governance.py | 1 |
| backend/services/perf/shard_runner.py | 1 |
| backend/services/shareholder_plan_family_walkforward.py | 4 |
| backend/services/shareholder_plan_feature_family_eval.py | 2 |
| backend/services/shareholder_plan_initial_event.py | 2 |
| backend/services/shareholder_plan_initial_feature_panel.py | 4 |
| backend/services/tdx_f10_extra_client.py | 2 |

### 多 writer 表 (>1 文件写同一张表)

| 表 | writer 数 | writer 文件 |
|---|---|---|
| mart_model_selection_run | 7 | backend/scripts/build_drift_safe_feature_candidates.py<br>backend/scripts/build_feature_drift_mitigation_panel.py<br>backend/scripts/build_feature_search_space.py<br>backend/scripts/build_hybrid_feature_panel.py<br>backend/scripts/run_optuna_feature_elimination.py<br>backend/scripts/run_optuna_feature_space.py<br>backend/services/schema_marts.py |
| mart_p0b_lambdamart_v6_predictions | 6 | backend/scripts/build_ensemble_v4_bc_stage_filtered.py<br>backend/scripts/build_ensemble_v4_bestchoice_predictions.py<br>backend/scripts/build_ensemble_v4_intersect_bc_phase7.py<br>backend/scripts/build_ensemble_v7_phase7_context.py<br>backend/scripts/import_bestchoice_phase3_predictions.py<br>backend/services/ml_ranking/ddl.py |
| fact_feature_panel_candidate | 5 | backend/scripts/build_candidate_feature_panel.py<br>backend/scripts/build_feature_drift_mitigation_panel.py<br>backend/scripts/build_hybrid_feature_panel.py<br>backend/scripts/build_tdx_gpcw_auto_feature_panel.py<br>backend/services/schema_core.py |
| fact_controlling_shareholder | 4 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_paper_position | 4 | backend/scripts/replay_paper_history.py<br>backend/scripts/replay_paper_history_signflip.py<br>backend/services/paper_engine/ddl.py<br>backend/services/paper_engine/driver.py |
| fact_top10_holder_period | 4 | backend/scripts/cleanup_holder_dup.py<br>backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| mart_p0b_oos_predictions | 4 | backend/scripts/run_p0b_lambdamart_v3.py<br>backend/scripts/train_p0b_lightgbm.py<br>backend/scripts/train_phase2_hierarchical.py<br>backend/services/ml_ranking/ddl.py |
| mart_p0b_walkforward_eval | 4 | backend/scripts/backfill_walkforward_eval.py<br>backend/scripts/run_p0b_lambdamart_v3.py<br>backend/scripts/train_p0b_lightgbm.py<br>backend/services/ml_ranking/ddl.py |
| mart_paper_nav | 4 | backend/scripts/replay_paper_history.py<br>backend/scripts/replay_paper_history_signflip.py<br>backend/services/paper_engine/ddl.py<br>backend/services/paper_engine/driver.py |
| fact_risk_factors | 3 | backend/scripts/backfill_risk_factors_history.py<br>backend/services/data_governance/etl_hook.py<br>backend/services/risk_factors.py |
| fact_shareholder_plan | 3 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| fact_shareholder_trade | 3 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| mart_etf_snapshot_latest | 3 | backend/services/etf_db.py<br>backend/services/etf_snapshot_manager.py<br>backend/services/schema_marts.py |
| mart_etf_snapshot_state | 3 | backend/services/etf_db.py<br>backend/services/etf_snapshot_manager.py<br>backend/services/schema_marts.py |
| mart_feature_pit_audit | 3 | backend/scripts/audit_registry_feature_pit.py<br>backend/scripts/validate_tdx_feature_pit.py<br>backend/services/schema_marts.py |
| mart_market_perception_audit_log | 3 | backend/scripts/build_market_perception_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_emotion_daily | 3 | backend/scripts/build_market_perception_emotion_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_leader_follower_daily | 3 | backend/scripts/build_market_perception_leader_follower_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_stock_context_daily | 3 | backend/scripts/build_market_perception_stock_context_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_style_daily | 3 | backend/scripts/build_market_perception_style_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_theme_daily | 3 | backend/scripts/build_market_perception_theme_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_under_reaction_daily | 3 | backend/scripts/build_market_perception_under_reaction_daily.py<br>backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_model_lifecycle | 3 | backend/scripts/bootstrap_model_lifecycle.py<br>backend/services/ml_lifecycle/registry.py<br>backend/services/schema_marts.py |
| mart_multidim_model | 3 | backend/scripts/run_multidim_walkforward.py<br>backend/scripts/train_multidim_model.py<br>backend/scripts/train_tdx_keep_challenger_model.py |
| mart_multidim_prediction | 3 | backend/scripts/run_multidim_walkforward.py<br>backend/scripts/train_multidim_model.py<br>backend/scripts/train_tdx_keep_challenger_model.py |
| mart_signal_ic | 3 | backend/scripts/build_signal_ic_daily.py<br>backend/services/paper_engine/ddl.py<br>backend/services/paper_engine/signal_ic.py |
| mart_stage_formula_fitness | 3 | backend/scripts/build_stage_formula_fitness.py<br>backend/scripts/rebuild_stage_formula_fitness.py<br>backend/services/formula_engine/ddl.py |
| raw_tdx_f10_holder_research | 3 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| dim_active_a_stock | 2 | backend/services/schema_core.py<br>backend/services/security_master.py |
| dim_data_asset | 2 | backend/scripts/seed_dim_data_asset.py<br>backend/services/schema_core.py |
| dim_data_source_priority | 2 | backend/scripts/audit_tdx_data_need_coverage.py<br>backend/services/schema_core.py |
| dim_fee_schedule | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_holder_alias | 2 | backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| dim_liquidity_threshold | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_listing_status | 2 | backend/scripts/build_dim_listing_status.py<br>backend/services/primitives/ddl.py |
| dim_market_segment | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_price_limit_rules | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_stock_stage_days | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| dim_stock_tdx_block | 2 | backend/services/block_client.py<br>backend/services/schema_core.py |
| dim_stock_tdx_industry | 2 | backend/services/schema_core.py<br>backend/services/tdx_industry_client.py |
| dim_style_factor | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_tdx_block_catalog | 2 | backend/services/block_client.py<br>backend/services/schema_core.py |
| dim_tdx_gpcw_field | 2 | backend/services/schema_core.py<br>backend/services/tdx_affair_client.py |
| dim_tdx_gpcw_field_semantic | 2 | backend/scripts/build_tdx_gpcw_auto_features.py<br>backend/services/schema_core.py |
| dim_trading_calendar | 2 | backend/routers/updater_calendar.py<br>backend/services/schema_core.py |
| dim_trading_rule | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_trading_session | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| fact_candle_pattern_daily | 2 | backend/scripts/build_candle_pattern_daily.py<br>backend/services/candle_pattern/ddl.py |
| fact_common_major_holder_stock | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_feature_panel_tdx_keep_challenger | 2 | backend/scripts/build_tdx_keep_challenger_panel.py<br>backend/services/schema_core.py |
| fact_fund_holding_tdx_f10 | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_holder_count_period | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_holder_event | 2 | backend/services/holders_event.py<br>backend/services/schema_core.py |
| fact_institution_event | 2 | backend/services/event_engine.py<br>backend/services/schema_core.py |
| fact_paper_sim_position | 2 | backend/services/paper_sim/ddl.py<br>backend/services/paper_sim/driver.py |
| fact_paper_sim_trade | 2 | backend/services/paper_sim/ddl.py<br>backend/services/paper_sim/driver.py |
| fact_shareholder_plan_tdx_f10 | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_shareholder_trade_tdx_b | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_signal_context | 2 | backend/scripts/build_signal_context.py<br>backend/services/formula_engine/signal_context_ddl.py |
| fact_stock_fundamental_stage_daily | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| fact_stock_selection_log | 2 | backend/services/selection/ddl.py<br>backend/services/selection/logger.py |
| fact_stock_technical_stage | 2 | backend/scripts/build_stage_formula_fitness.py<br>backend/services/formula_engine/ddl.py |
| fact_stock_type_daily | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| fact_tdx_gpcw_auto_feature_quarterly | 2 | backend/scripts/build_tdx_gpcw_auto_features.py<br>backend/services/schema_core.py |
| fact_technical_trigger | 2 | backend/scripts/build_formula_signals_history.py<br>backend/services/formula_engine/ddl.py |
| mart_audit_snapshot_state | 2 | backend/services/audit.py<br>backend/services/schema_marts.py |
| mart_candidate_walkforward_eval | 2 | backend/scripts/run_walkforward_feature_eval.py<br>backend/services/schema_marts.py |
| mart_current_relationship | 2 | backend/services/holdings.py<br>backend/services/schema_marts.py |
| mart_daily_formula_buys | 2 | backend/scripts/build_daily_formula_buys.py<br>backend/services/formula_engine/per_stock_ddl.py |
| mart_data_deletion_record | 2 | backend/services/data_deletion.py<br>backend/services/schema_marts.py |
| mart_data_deprecation_record | 2 | backend/services/data_deprecation.py<br>backend/services/schema_marts.py |
| mart_data_health | 2 | backend/scripts/data_health_snapshot.py<br>backend/services/schema_marts.py |
| mart_data_source_reassignment_proposal | 2 | backend/scripts/audit_tdx_data_need_coverage.py<br>backend/services/schema_marts.py |
| mart_data_source_watermark | 2 | backend/services/schema_marts.py<br>backend/services/source_watermarks.py |
| mart_feature_candidate_score | 2 | backend/scripts/run_optuna_feature_elimination.py<br>backend/services/schema_marts.py |
| mart_feature_drift | 2 | backend/services/ml_lifecycle/drift.py<br>backend/services/schema_marts.py |
| mart_feature_group_ablation | 2 | backend/scripts/run_feature_group_ablation.py<br>backend/services/schema_marts.py |
| mart_feature_retention_decision | 2 | backend/scripts/build_feature_retention_decisions.py<br>backend/services/schema_marts.py |
| mart_follow_return_label_build | 2 | backend/scripts/materialize_follow_return_labels.py<br>backend/services/pricing_schema.py |
| mart_follow_return_label_quality | 2 | backend/scripts/materialize_follow_return_labels.py<br>backend/services/pricing_schema.py |
| mart_formula_horizon_evidence | 2 | backend/scripts/build_formula_signals_history.py<br>backend/services/formula_engine/ddl.py |
| mart_formula_weight_history | 2 | backend/services/selection/ddl.py<br>backend/services/selection/feedback.py |
| mart_institution_industry_stat | 2 | backend/routers/updater_institution.py<br>backend/services/schema_marts.py |
| mart_institution_profile | 2 | backend/routers/updater_profiles.py<br>backend/services/schema_marts.py |
| mart_lineage | 2 | backend/services/data_lineage/run.py<br>backend/services/schema_marts.py |
| mart_macd_state_history | 2 | backend/scripts/build_macd_state_history.py<br>backend/services/formula_engine/ddl.py |
| mart_market_perception_daily | 2 | backend/scripts/build_market_perception_daily.py<br>backend/services/schema_marts.py |
| mart_model_composite_score | 2 | backend/services/research/composite_score.py<br>backend/services/research/ddl.py |
| mart_model_edge_flags | 2 | backend/services/research/ddl.py<br>backend/services/research/edge_flags.py |
| mart_model_feature_lineage | 2 | backend/services/model_feature_lineage.py<br>backend/services/schema_marts.py |
| mart_model_holding_topk_eval | 2 | backend/scripts/evaluate_holding_topk.py<br>backend/services/schema_marts.py |
| mart_p0a_label_panel | 2 | backend/scripts/rebuild_p0a_label_panel.py<br>backend/services/labels/ddl.py |
| mart_p1_optuna_trials | 2 | backend/scripts/run_p0b_lightgbm_optuna_v3.py<br>backend/scripts/run_p0b_lightgbm_optuna_v4.py |
| mart_paper_sim_kpi | 2 | backend/services/paper_sim/ddl.py<br>backend/services/paper_sim/reporter.py |
| mart_paper_sim_nav | 2 | backend/services/paper_sim/ddl.py<br>backend/services/paper_sim/driver.py |
| mart_pipeline_run_manifest | 2 | backend/services/pipeline_manifest.py<br>backend/services/schema_marts.py |
| mart_pricing_label_data_readiness_gate | 2 | backend/services/pricing_policy_readiness.py<br>backend/services/pricing_schema.py |
| mart_pricing_label_policy | 2 | backend/services/pricing_policy_records.py<br>backend/services/pricing_schema.py |
| mart_pricing_label_policy_gate | 2 | backend/services/pricing_policy_records.py<br>backend/services/pricing_schema.py |
| mart_research_reflection_log | 2 | backend/services/research/ddl.py<br>backend/services/research/reflection.py |
| mart_stock_formula_buy_signal_daily | 2 | backend/scripts/build_stock_formula_buy_signal_daily.py<br>backend/services/buy_signal/ddl.py |
| mart_stock_formula_optuna | 2 | backend/scripts/build_stock_formula_optuna.py<br>backend/services/formula_engine/per_stock_ddl.py |
| mart_stock_picture_daily | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| mart_stock_selection_outcome | 2 | backend/services/selection/ddl.py<br>backend/services/selection/outcome.py |
| mart_stock_selection_summary | 2 | backend/services/selection/ddl.py<br>backend/services/selection/summary.py |
| mart_stock_survey_features | 2 | backend/scripts/build_survey_features.py<br>backend/services/sentiment/ddl.py |
| mart_stock_trade_plan | 2 | backend/scripts/build_stock_trade_plan.py<br>backend/services/picture/ddl.py |
| mart_stock_trend | 2 | backend/routers/updater_trends.py<br>backend/services/schema_marts.py |
| mart_strategy_result_registry | 2 | backend/scripts/register_bc_ensemble_v4_bc.py<br>backend/services/schema_marts.py |
| mart_synergy_policy_candidate | 2 | backend/scripts/rerank_optuna_synergy_mtm.py<br>backend/scripts/run_optuna_synergy_search.py |
| mart_tdx_data_need_coverage | 2 | backend/scripts/audit_tdx_data_need_coverage.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_feature_cluster | 2 | backend/scripts/run_optuna_feature_elimination.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_feature_score | 2 | backend/scripts/run_optuna_feature_elimination.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_optuna_run | 2 | backend/scripts/run_optuna_feature_elimination.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_pit_audit | 2 | backend/scripts/validate_tdx_gpcw_auto_pit.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_retention_decision | 2 | backend/scripts/build_feature_retention_decisions.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_file_manifest | 2 | backend/scripts/build_fundamental_quarterly.py<br>backend/services/tdx_affair_client.py |
| mart_tdx_keep_promotion_gate | 2 | backend/scripts/evaluate_tdx_keep_promotion_gate.py<br>backend/services/schema_marts.py |
| raw_tdx_f10_extra_parse_status | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| raw_tdx_f10_holder_count_history | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| raw_tdx_gpcw_wide | 2 | backend/services/schema_core.py<br>backend/services/tdx_affair_client.py |

### 单 writer 表

| 表 | writer |
|---|---|
| dim_capital_behavior_latest | backend/services/capital_client.py |
| dim_financial_indicator_latest | backend/services/financial_indicator_client.py |
| dim_financial_latest | backend/services/financial_client.py |
| dim_schema_version | backend/services/schema_versions.py |
| dim_stock_attention_latest | backend/services/external_attention.py |
| dim_stock_industry_context_latest | backend/services/industry_context_engine.py |
| dim_stock_stage_latest | backend/services/stock_stage_engine.py |
| dim_stock_tdx_industry_history | backend/services/tdx_industry_client.py |
| dim_stock_turtle_latest | backend/services/stock_turtle_engine.py |
| dim_strategy_preset | backend/routers/strategy_preset.py |
| fact_capital_flow_pit_daily | backend/scripts/backfill_capital_flow_pit.py |
| fact_daily_price_status | backend/services/primitives/ddl.py |
| fact_dzjy_event | backend/scripts/build_akshare_panel.py |
| fact_executive_trade_event | backend/scripts/build_executive_trade_events.py |
| fact_feature_panel | backend/scripts/build_feature_panel_duck.py |
| fact_financial_derived | backend/services/financial_client.py |
| fact_financial_indicator_ak | backend/services/financial_indicator_client.py |
| fact_financial_pit_daily | backend/scripts/backfill_financial_pit.py |
| fact_fundamental_quarterly | backend/scripts/build_fundamental_quarterly.py |
| fact_hot_rank_daily | backend/scripts/build_akshare_panel.py |
| fact_hsgt_daily | backend/scripts/build_akshare_panel.py |
| fact_institution_follow_backtest | backend/scripts/run_follow_backtest.py |
| fact_jgdy_event | backend/scripts/build_akshare_panel.py |
| fact_lhb_event | backend/scripts/build_lhb_events.py |
| fact_model_train_log | backend/services/ml_ranking/ddl.py |
| fact_model_train_log_window | backend/services/ml_ranking/ddl.py |
| fact_orderbook_snapshot | backend/scripts/build_orderbook_snapshot.py |
| fact_policy_equity_curve | backend/scripts/run_portfolio_mvp.py |
| fact_policy_eval | backend/scripts/run_portfolio_mvp.py |
| fact_policy_trade | backend/scripts/run_portfolio_mvp.py |
| fact_profit_forecast_daily | backend/scripts/build_akshare_panel.py |
| fact_research_report | backend/scripts/build_akshare_panel.py |
| fact_sector_momentum_daily | backend/scripts/backfill_sector_momentum_history.py |
| fact_sector_predicted_ret_daily | backend/scripts/train_sector_rotation_predictor.py |
| fact_setup_snapshot | backend/services/schema_core.py |
| fact_stock_attention_snapshot | backend/services/external_attention.py |
| fact_stock_industry_context | backend/services/industry_context_engine.py |
| fact_stock_liquidity_daily | backend/services/primitives/ddl.py |
| fact_stock_market_cap_daily | backend/services/primitives/ddl.py |
| fact_stock_stage_features | backend/services/stock_stage_engine.py |
| fact_stock_style_daily | backend/services/primitives/ddl.py |
| fact_stock_turtle_features | backend/services/stock_turtle_engine.py |
| mart_architecture_cleanup_plan | backend/scripts/plan_architecture_cleanup.py |
| mart_architecture_dependency_edge | backend/scripts/build_architecture_inventory.py |
| mart_architecture_inventory_asset | backend/scripts/build_architecture_inventory.py |
| mart_architecture_inventory_summary | backend/scripts/build_architecture_inventory.py |
| mart_bestchoice_context_exit_policy_v1 | backend/scripts/build_bestchoice_context_exit_policy.py |
| mart_candidate_feature_set_contract | backend/services/data_quality.py |
| mart_challenger_evidence_bundle | backend/scripts/build_challenger_evidence_bundle.py |
| mart_champion_candidate_evaluation | backend/scripts/evaluate_champion_candidate.py |
| mart_champion_model | backend/services/portfolio/champion.py |
| mart_daily_blended_recommendation | backend/services/selection/blended_recommendation.py |
| mart_daily_ensemble_picks_v4_bc_v1 | backend/scripts/run_daily_ensemble_v4_bc.py |
| mart_daily_formula_candidate_bestchoice_v1 | backend/scripts/build_bestchoice_phase2_daily_feed.py |
| mart_daily_position_recommendation | backend/scripts/build_daily_position_recommendations.py |
| mart_daily_position_recommendation_pit_diagnostic | backend/scripts/build_daily_position_recommendations.py |
| mart_daily_recommendation | backend/scripts/run_daily_topk.py |
| mart_daily_recommendation_explanation | backend/scripts/run_daily_topk.py |
| mart_daily_recommendation_risk | backend/scripts/run_daily_topk.py |
| mart_daily_topk_view_cache | backend/scripts/run_daily_topk.py |
| mart_data_processing_tool_issue | backend/services/data_processing_monitor.py |
| mart_data_processing_tool_run | backend/services/data_processing_monitor.py |
| mart_data_source_failure_queue | backend/services/source_watermarks.py |
| mart_decision_outcome | backend/services/paper_engine/ddl.py |
| mart_drift_safe_candidate_batch_eval | backend/scripts/run_drift_safe_candidate_batch.py |
| mart_drift_safe_candidate_batch_summary | backend/scripts/run_drift_safe_candidate_batch.py |
| mart_drift_safe_candidate_feature | backend/scripts/build_drift_safe_feature_candidates.py |
| mart_drift_safe_candidate_summary | backend/scripts/build_drift_safe_feature_candidates.py |
| mart_dual_confirm | backend/services/sector_momentum.py |
| mart_ensemble_optimal | backend/scripts/optimize_ensemble_full.py |
| mart_ensemble_signals | backend/services/strategy_ensemble.py |
| mart_etf_sector_rotation | backend/scripts/build_etf_sector_rotation.py |
| mart_etf_strategy_comparison | backend/scripts/backtest_etf_strategies.py |
| mart_feature_association_fold | backend/scripts/build_feature_association_duck.py |
| mart_feature_association_stat | backend/scripts/build_feature_association_duck.py |
| mart_feature_availability_contract | backend/services/data_quality.py |
| mart_feature_bucket_effect | backend/scripts/build_temporal_synergy_research.py |
| mart_feature_candidate_coverage | backend/scripts/build_feature_retention_decisions.py |
| mart_feature_catalog_current | backend/scripts/build_feature_catalog_current.py |
| mart_feature_cluster_redundancy | backend/scripts/build_temporal_redundancy_clusters.py |
| mart_feature_conditional_synergy | backend/scripts/build_temporal_synergy_research.py |
| mart_feature_correlation_cluster | backend/scripts/build_feature_association_duck.py |
| mart_feature_drift_histogram | backend/services/ml_lifecycle/drift.py |
| mart_feature_drift_mitigation_panel_build | backend/scripts/build_feature_drift_mitigation_panel.py |
| mart_feature_drift_root_cause | backend/scripts/build_feature_drift_root_cause.py |
| mart_feature_drift_root_cause_summary | backend/scripts/build_feature_drift_root_cause.py |
| mart_feature_exclusion_reason | backend/scripts/build_feature_catalog_current.py |
| mart_feature_interaction_candidate | backend/scripts/build_temporal_synergy_research.py |
| mart_feature_null_policy | backend/services/data_quality.py |
| mart_feature_pair_synergy | backend/scripts/build_temporal_synergy_research.py |
| mart_feature_panel_prune_run | backend/scripts/prune_feature_panel_to_canonical_kline.py |
| mart_feature_panel_validation | backend/scripts/build_feature_panel_duck.py |
| mart_feature_pit_coverage_summary | backend/scripts/audit_registry_feature_pit.py |
| mart_feature_pit_join_plan | backend/scripts/build_feature_catalog_current.py |
| mart_feature_rank_matrix_benchmark | backend/scripts/build_feature_rank_matrix_duck.py |
| mart_feature_rank_matrix_cache_manifest | backend/scripts/build_feature_rank_matrix_duck.py |
| mart_feature_rank_matrix_proxy_stat | backend/scripts/build_feature_rank_matrix_duck.py |
| mart_feature_redundancy_pair | backend/scripts/build_temporal_redundancy_clusters.py |
| mart_feature_relevance_stability | backend/scripts/build_temporal_synergy_research.py |
| mart_feature_search_space | backend/scripts/build_feature_search_space.py |
| mart_feature_search_space_summary | backend/scripts/build_feature_search_space.py |
| mart_feature_temporal_relevance | backend/scripts/build_temporal_synergy_research.py |
| mart_forecast_upside_live | backend/scripts/compute_forecast_upside_live.py |
| mart_global_data_quality_detail | backend/services/data_quality.py |
| mart_global_data_quality_gate | backend/services/data_quality.py |
| mart_hybrid_feature_panel_build | backend/scripts/build_hybrid_feature_panel.py |
| mart_institution_score_daily | backend/scripts/build_institution_score_daily.py |
| mart_model_ablation_run | backend/scripts/run_feature_ablation.py |
| mart_model_explanation | backend/scripts/run_daily_topk.py |
| mart_model_portfolio_curve | backend/scripts/backtest_model_portfolio.py |
| mart_model_portfolio_summary | backend/scripts/backtest_model_portfolio.py |
| mart_model_stability_context_diagnostic | backend/scripts/build_model_stability_context_diagnostics.py |
| mart_model_stability_context_summary | backend/scripts/build_model_stability_context_diagnostics.py |
| mart_model_stability_search_summary | backend/scripts/run_optuna_model_stability_search.py |
| mart_model_stability_search_trial | backend/scripts/run_optuna_model_stability_search.py |
| mart_model_walkforward_fold | backend/scripts/run_multidim_walkforward.py |
| mart_model_walkforward_portfolio_summary | backend/scripts/backtest_walkforward_portfolio.py |
| mart_model_walkforward_prediction | backend/scripts/run_multidim_walkforward.py |
| mart_optuna_feature_space_trial | backend/scripts/run_optuna_feature_space.py |
| mart_optuna_synergy_study_summary | backend/scripts/run_optuna_synergy_search.py |
| mart_optuna_synergy_trial | backend/scripts/run_optuna_synergy_search.py |
| mart_p0a_feature_label_panel_v3 | backend/services/labels/feature_join_v3.py |
| mart_p0a_feature_label_panel_v3_ext | backend/services/labels/feature_join_v3_ext.py |
| mart_p0a_feature_label_panel_v4 | backend/services/labels/feature_join_v4.py |
| mart_p0a_feature_label_panel_v5 | backend/services/labels/feature_join_v5.py |
| mart_p1_ablation_result | backend/scripts/run_p1_ablation.py |
| mart_p2_composite_result | backend/scripts/run_p2_composite_search.py |
| mart_p3_acceptance_result | backend/scripts/run_p3_final_holdout.py |
| mart_p3_holdout_freeze | backend/services/portfolio/final_holdout_freeze.py |
| mart_per_stock_optuna_best | backend/scripts/optuna_per_stock_macd.py |
| mart_per_stock_strategy_optimal | backend/scripts/optimize_per_stock_strategy.py |
| mart_pipeline_lock | backend/services/pipeline_lock.py |
| mart_prediction_outcome | backend/services/prediction_outcome.py |
| mart_research_schedule_plan | backend/scripts/plan_research_schedule.py |
| mart_sector_momentum | backend/services/sector_momentum.py |
| mart_sniper_score_daily | backend/scripts/build_sniper_score_daily.py |
| mart_step_fingerprint | backend/services/event_engine.py |
| mart_stock_formula_optuna_v2 | backend/scripts/build_stock_formula_optuna_v2.py |
| mart_stock_fund_flow_rank_snapshot_daily | backend/services/schema_marts.py |
| mart_stock_horizon_candidate_gate | backend/scripts/build_stock_horizon_profile.py |
| mart_stock_horizon_feature_effect | backend/scripts/build_stock_horizon_profile.py |
| mart_stock_horizon_profile | backend/scripts/build_stock_horizon_profile.py |
| mart_stock_horizon_selection | backend/scripts/build_stock_horizon_profile.py |
| mart_stock_screening | backend/services/screening_engine.py |
| mart_stock_survey_activity | backend/services/institution_survey_client.py |
| mart_synergy_policy_evidence_bundle | backend/scripts/validate_synergy_policy_candidate.py |
| mart_synergy_policy_gate | backend/scripts/validate_synergy_policy_candidate.py |
| mart_synergy_policy_mtm_daily_path | backend/scripts/validate_synergy_policy_mark_to_market.py |
| mart_synergy_policy_mtm_evidence_bundle | backend/scripts/validate_synergy_policy_mark_to_market.py |
| mart_synergy_policy_mtm_gate | backend/scripts/validate_synergy_policy_mark_to_market.py |
| mart_synergy_policy_mtm_position | backend/scripts/validate_synergy_policy_mark_to_market.py |
| mart_synergy_policy_mtm_rerank | backend/scripts/rerank_optuna_synergy_mtm.py |
| mart_synergy_policy_mtm_rerank_summary | backend/scripts/rerank_optuna_synergy_mtm.py |
| mart_synergy_policy_mtm_strategy_sweep | backend/scripts/sweep_synergy_mtm_strategy.py |
| mart_synergy_policy_mtm_strategy_sweep_summary | backend/scripts/sweep_synergy_mtm_strategy.py |
| mart_synergy_policy_walkforward | backend/scripts/validate_synergy_policy_candidate.py |
| mart_tdx_challenger_report | backend/services/schema_marts.py |
| mart_tdx_f10_capability_matrix | backend/services/tdx_f10_extra_client.py |
| mart_tdx_f10_source_date_section_audit | backend/services/tdx_f10_source_date_audit.py |
| mart_tdx_gpcw_auto_challenger_report | backend/services/schema_marts.py |
| mart_tdx_gpcw_field_profile | backend/scripts/profile_tdx_gpcw_fields.py |
| mart_tdx_server_health | backend/services/tdx_source.py |
| mart_temporal_research_panel | backend/scripts/build_temporal_synergy_research.py |
| mart_temporal_research_panel_quality | backend/scripts/build_temporal_synergy_research.py |
| mart_today_signal_cache | backend/services/signals_v2.py |
| mart_today_signal_cache_signal | backend/services/signals_v2.py |
| mart_unified_v1_oos_predictions | backend/scripts/train_unified_ranker_v1.py |
| raw_aif10_financial_history | backend/services/aif10_capability_client.py |
| raw_capital_allotment_detail | backend/services/capital_client.py |
| raw_capital_dividend_detail | backend/services/capital_client.py |
| raw_capital_dividend_summary | backend/services/capital_client.py |
| raw_capital_repurchase | backend/services/capital_client.py |
| raw_capital_unlock | backend/services/capital_client.py |
| raw_executive_trade | backend/scripts/build_executive_trade_events.py |
| raw_gpcw_detail | backend/services/tdx_affair_client.py |
| raw_gpcw_financial | backend/services/financial_client.py |
| raw_institution_surveys | backend/services/institution_survey_client.py |
| raw_lhb_daily | backend/services/lhb_client.py |
| raw_profit_forecast_snapshot_daily | backend/scripts/ingest_profit_forecast_snapshot.py |
| raw_qfii_holding_quarterly | backend/services/qfii_client.py |
| raw_tdx_industry_file_snapshot | backend/services/tdx_industry_client.py |

## 4. 依赖热点 (codegraph 派生)

> Codegraph: 节点 19,268 | calls 边 207,573 | imports 边 20,330 (每次 codegraph sync 波动, 不参与漂移判定)

### 被 import 最多的模块 (top 15)

| 模块 | import 处数 |
|---|---|
| services.db | 176 |
| services.duck_adapter | 86 |
| services.pipeline_manifest | 62 |
| services.schema_versions | 52 |
| services.market_db | 48 |
| services.utils | 41 |
| services.model_feature_schema | 22 |
| services.tdx_source | 20 |
| services.paper_sim.config | 19 |
| services.pricing_policy | 19 |
| services.industry | 18 |
| services.bestchoice_config | 15 |
| services.feature_registry | 15 |
| services.optimization.config | 13 |
| services.backtest.result | 12 |

### 跨文件 fan-in 最高的文件 (近似口径: 唯一定义名 + caller 实际 import 目标模块双过滤)

| 文件 | 调用方文件数 |
|---|---|
| backend/services/data_sources/registry.py | 27 |
| backend/services/duck_adapter.py | 22 |
| backend/services/industry.py | 17 |
| backend/services/model_feature_schema.py | 16 |
| backend/services/optimization/config.py | 13 |
| backend/services/ml_lifecycle/registry.py | 11 |
| backend/services/pipeline_manifest.py | 11 |
| backend/services/schema_versions.py | 10 |
| backend/services/bc_absorbed/compute.py | 9 |
| backend/services/optimization/walk_forward.py | 9 |
| backend/scripts/train_multidim_model.py | 7 |
| backend/services/etf_grid_engine.py | 7 |

### LOC top 10 (God module 候选)

| 文件 | 行数 |
|---|---|
| backend/services/data_quality.py | 4286 |
| backend/services/bc_absorbed/compute.py | 3303 |
| backend/services/scoring.py | 2712 |
| backend/scripts/build_feature_panel_duck.py | 2310 |
| backend/services/signals_v2.py | 2156 |
| backend/scripts/audit_stage_opt_candidate_supply.py | 2005 |
| backend/services/audit.py | 1745 |
| backend/services/financial_client.py | 1701 |
| backend/scripts/chunkyctl.py | 1661 |
| backend/scripts/build_temporal_synergy_research.py | 1637 |

## 5. 概览

- chunkyctl 子命令 8 | launchd 任务 4 | router 23 (端点 171)
- sync_registry 数据域 22
- 产表 302 (多 writer 121)

