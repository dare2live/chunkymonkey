# FEATURE_MAP — 机器生成功能地图

> 由 `scripts/chunkyctl map` (backend/scripts/build_feature_map.py) 重生成, **勿手改**。
> 只列机器可枚举事实 (入口/数据域/产表 writer/依赖热点/计数); 人工判断层 (坑/权重/状态) 在 `PROJECT_INDEX.md`。机器版: `data/reports/feature_map.json` (本地, 不入 git)。
> Snapshot: 2026-06-17 07:00

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
| com.chunkymonkey.nightly-data-audit | 02:00 | `.venv/bin/python scripts/launchd_job_wrapper.py nightly_data_audit .venv/bin/python backen…` |

### API 路由 (regex 口径: @router 装饰器)

| router | prefix | 端点数 |
|---|---|---|
| data_sources | `/api/data_sources` | 18 |
| etf | `/api/etf` | 9 |
| market | `/api/inst` | 1 |
| ops_manual_run | `/api/v3/ops` | 3 |
| signals | `/api/signals` | 10 |
| stock_graph | `/api/v3` | 3 |
| strategy_preset | `/api/inst/strategy` | 4 |
| updater | `/api/inst` | 13 |
| updater_lifeboat | `—` | 3 |
| v3_bestchoice | `/api/v3` | 4 |
| v3_config | `/api/v3` | 1 |
| v3_paper | `/api/v3/paper` | 5 |
| v3_picture | `/api/v3` | 3 |
| v3_portfolio_builder | `/api/v3/portfolio` | 5 |
| v3_selection | `/api/v3/selection` | 6 |
| workbench | `/api/workbench` | 10 |

端点全列表在 json (`routes` 键)。

## 2. 数据域 (sync_registry)

| 域 | 源 | api | 表 | 模式 | SLA(交易日) |
|---|---|---|---|---|---|
| adj_factor | tushare | adj_factor | raw_tushare_adj_factor | by_trade_date | 1 |
| balancesheet_advrecv | tushare | balancesheet | raw_tushare_balancesheet_advrecv | by_period | 5 |
| block_trade | tushare | block_trade | raw_tushare_block_trade | by_trade_date | 5 |
| cyq_perf | tushare | cyq_perf | raw_tushare_cyq_perf | by_trade_date | 1 |
| daily | tushare | daily | raw_tushare_daily | by_trade_date | 1 |
| daily_basic | tushare | daily_basic | raw_tushare_daily_basic | by_trade_date | 1 |
| dc_index | tushare | dc_index | raw_tushare_dc_index | by_trade_date | 2 |
| dc_member | tushare | dc_member | raw_tushare_dc_member | by_trade_date | 2 |
| dividend | tushare | dividend | raw_tushare_dividend | by_trade_date | 5 |
| express | tushare | express_vip | raw_tushare_express | by_period | 5 |
| fina_indicator | tushare | fina_indicator | raw_tushare_fina_indicator | by_ts_code | 5 |
| fina_mainbz | tushare | fina_mainbz | raw_tushare_fina_mainbz | by_ts_code | 130 |
| forecast | tushare | forecast | raw_tushare_forecast | by_trade_date | 5 |
| income | tushare | income | raw_tushare_income | by_trade_date | 5 |
| index_daily_benchmark | tushare | index_daily | raw_tushare_index_daily | by_code_list | 1 |
| index_dailybasic | tushare | index_dailybasic | raw_tushare_index_dailybasic | by_code_list | 1 |
| index_member_all | tushare | index_member_all | raw_tushare_index_member_all | by_code_list | 30 |
| index_member_all_hist | tushare | index_member_all | raw_tushare_index_member_all | by_code_list | 30 |
| limit_cpt_list | tushare | limit_cpt_list | raw_tushare_limit_cpt_list | by_trade_date | 2 |
| limit_list_d | tushare | limit_list_d | raw_tushare_limit_list_d | by_trade_date | 1 |
| moneyflow | tushare | moneyflow | raw_tushare_moneyflow | by_trade_date | 1 |
| moneyflow_dc | tushare | moneyflow_dc | raw_tushare_moneyflow_dc | by_trade_date | 1 |
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

统计: 表 165 张 | 单 writer 94 | 多 writer 71 | 动态表名写点 27 处 (16 文件)

口径免责: 静态正则扫描, 含历史/backfill 一次性脚本与字符串内 SQL 样例; **多 writer 计数 ≠ 违规待修清单** — 升级为问题需逐表人工确认运行时并发写。

### 动态表名写点 (f-string, 静态不可归属 — 这些文件写的表不在下方普查内)

| 文件 | 写点数 |
|---|---|
| backend/scripts/build_akshare_panel.py | 1 |
| backend/scripts/build_executive_trade_events.py | 2 |
| backend/scripts/build_feature_map.py | 1 |
| backend/scripts/build_feature_panel.py | 2 |
| backend/scripts/build_lhb_events.py | 1 |
| backend/scripts/build_price_kline_qfq_tushare.py | 1 |
| backend/scripts/build_sw_industry_view.py | 1 |
| backend/scripts/db_compact.py | 2 |
| backend/scripts/db_partition_migrate.py | 2 |
| backend/scripts/experiment_macd_episode_scan.py | 2 |
| backend/scripts/rally_ground_truth_scan.py | 2 |
| backend/scripts/seed_dim_data_asset.py | 1 |
| backend/services/aif10_capability_client.py | 3 |
| backend/services/data_sources/sync_runner.py | 3 |
| backend/services/perf/shard_runner.py | 1 |
| backend/services/tdx_f10_extra_client.py | 2 |

### 多 writer 表 (>1 文件写同一张表)

| 表 | writer 数 | writer 文件 |
|---|---|---|
| fact_controlling_shareholder | 4 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_top10_holder_period | 4 | backend/scripts/cleanup_holder_dup.py<br>backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| fact_shareholder_plan | 3 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| fact_shareholder_trade | 3 | backend/scripts/ingest_holders_tdxhub.py<br>backend/scripts/migrate_holders_to_tdxhub.py<br>backend/services/schema_core.py |
| mart_data_deletion_record | 3 | backend/scripts/db_lifecycle_delete.py<br>backend/services/data_deletion.py<br>backend/services/schema_marts.py |
| mart_etf_snapshot_latest | 3 | backend/services/etf_db.py<br>backend/services/etf_snapshot_manager.py<br>backend/services/schema_marts.py |
| mart_etf_snapshot_state | 3 | backend/services/etf_db.py<br>backend/services/etf_snapshot_manager.py<br>backend/services/schema_marts.py |
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
| fact_common_major_holder_stock | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_consumer_alpha_ic_scan | 2 | backend/scripts/build_experiment_store.py<br>backend/services/experiment_store.py |
| fact_experiment_verdict | 2 | backend/scripts/build_experiment_store.py<br>backend/services/experiment_store.py |
| fact_fund_holding_tdx_f10 | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_holder_count_period | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_holder_event | 2 | backend/services/holders_event.py<br>backend/services/schema_core.py |
| fact_institution_event | 2 | backend/services/event_engine.py<br>backend/services/schema_core.py |
| fact_risk_factors | 2 | backend/services/data_governance/etl_hook.py<br>backend/services/risk_factors.py |
| fact_shareholder_plan_tdx_f10 | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_shareholder_trade_tdx_b | 2 | backend/services/schema_core.py<br>backend/services/tdx_f10_extra_client.py |
| fact_stock_fundamental_stage_daily | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| fact_stock_technical_stage | 2 | backend/scripts/build_stage_formula_fitness.py<br>backend/services/formula_engine/ddl.py |
| fact_stock_type_daily | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| fact_tdx_gpcw_auto_feature_quarterly | 2 | backend/scripts/build_tdx_gpcw_auto_features.py<br>backend/services/schema_core.py |
| mart_audit_snapshot_state | 2 | backend/services/audit.py<br>backend/services/schema_marts.py |
| mart_current_relationship | 2 | backend/services/holdings.py<br>backend/services/schema_marts.py |
| mart_data_deprecation_record | 2 | backend/services/data_deprecation.py<br>backend/services/schema_marts.py |
| mart_data_health | 2 | backend/scripts/data_health_snapshot.py<br>backend/services/schema_marts.py |
| mart_data_source_reassignment_proposal | 2 | backend/scripts/audit_tdx_data_need_coverage.py<br>backend/services/schema_marts.py |
| mart_data_source_watermark | 2 | backend/services/schema_marts.py<br>backend/services/source_watermarks.py |
| mart_feature_drift | 2 | backend/services/ml_lifecycle/drift.py<br>backend/services/schema_marts.py |
| mart_institution_industry_stat | 2 | backend/routers/updater_institution.py<br>backend/services/schema_marts.py |
| mart_institution_profile | 2 | backend/routers/updater_profiles.py<br>backend/services/schema_marts.py |
| mart_macd_state_history | 2 | backend/scripts/build_macd_state_history.py<br>backend/services/formula_engine/ddl.py |
| mart_market_perception_audit_log | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_emotion_daily | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_leader_follower_daily | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_stock_context_daily | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_style_daily | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_theme_daily | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_market_perception_under_reaction_daily | 2 | backend/services/schema_marts.py<br>backend/services/schema_migrations.py |
| mart_model_lifecycle | 2 | backend/services/ml_lifecycle/registry.py<br>backend/services/schema_marts.py |
| mart_pipeline_run_manifest | 2 | backend/services/pipeline_manifest.py<br>backend/services/schema_marts.py |
| mart_pricing_label_data_readiness_gate | 2 | backend/services/pricing_policy_readiness.py<br>backend/services/pricing_schema.py |
| mart_pricing_label_policy | 2 | backend/services/pricing_policy_records.py<br>backend/services/pricing_schema.py |
| mart_pricing_label_policy_gate | 2 | backend/services/pricing_policy_records.py<br>backend/services/pricing_schema.py |
| mart_stage_formula_fitness | 2 | backend/scripts/build_stage_formula_fitness.py<br>backend/services/formula_engine/ddl.py |
| mart_stock_picture_daily | 2 | backend/scripts/build_picture_daily.py<br>backend/services/picture/ddl.py |
| mart_stock_trend | 2 | backend/routers/updater_trends.py<br>backend/services/schema_marts.py |
| mart_tdx_data_need_coverage | 2 | backend/scripts/audit_tdx_data_need_coverage.py<br>backend/services/schema_marts.py |
| mart_tdx_gpcw_file_manifest | 2 | backend/scripts/build_fundamental_quarterly.py<br>backend/services/tdx_affair_client.py |
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
| fact_daily_price_status | backend/services/primitives/ddl.py |
| fact_dzjy_event | backend/scripts/build_akshare_panel.py |
| fact_executive_trade_event | backend/scripts/build_executive_trade_events.py |
| fact_feature_panel_candidate | backend/services/schema_core.py |
| fact_feature_panel_tdx_keep_challenger | backend/services/schema_core.py |
| fact_financial_derived | backend/services/financial_client.py |
| fact_financial_indicator_ak | backend/services/financial_indicator_client.py |
| fact_fundamental_quarterly | backend/scripts/build_fundamental_quarterly.py |
| fact_hot_rank_daily | backend/scripts/build_akshare_panel.py |
| fact_hsgt_daily | backend/scripts/build_akshare_panel.py |
| fact_jgdy_event | backend/scripts/build_akshare_panel.py |
| fact_lhb_event | backend/scripts/build_lhb_events.py |
| fact_profit_forecast_daily | backend/scripts/build_akshare_panel.py |
| fact_research_report | backend/scripts/build_akshare_panel.py |
| fact_segment_panel | backend/scripts/build_segment_panel.py |
| fact_setup_snapshot | backend/services/schema_core.py |
| fact_stock_attention_snapshot | backend/services/external_attention.py |
| fact_stock_industry_context | backend/services/industry_context_engine.py |
| fact_stock_liquidity_daily | backend/services/primitives/ddl.py |
| fact_stock_market_cap_daily | backend/services/primitives/ddl.py |
| fact_stock_stage_features | backend/services/stock_stage_engine.py |
| fact_stock_style_daily | backend/services/primitives/ddl.py |
| fact_stock_turtle_features | backend/services/stock_turtle_engine.py |
| fact_technical_trigger | backend/services/formula_engine/ddl.py |
| mart_candidate_feature_set_contract | backend/services/data_quality.py |
| mart_candidate_walkforward_eval | backend/services/schema_marts.py |
| mart_data_processing_tool_issue | backend/services/data_processing_monitor.py |
| mart_data_processing_tool_run | backend/services/data_processing_monitor.py |
| mart_data_source_failure_queue | backend/services/source_watermarks.py |
| mart_dual_confirm | backend/services/sector_momentum.py |
| mart_ensemble_signals | backend/services/strategy_ensemble.py |
| mart_feature_availability_contract | backend/services/data_quality.py |
| mart_feature_candidate_score | backend/services/schema_marts.py |
| mart_feature_drift_histogram | backend/services/ml_lifecycle/drift.py |
| mart_feature_group_ablation | backend/services/schema_marts.py |
| mart_feature_null_policy | backend/services/data_quality.py |
| mart_feature_pit_audit | backend/services/schema_marts.py |
| mart_feature_retention_decision | backend/services/schema_marts.py |
| mart_follow_return_label_build | backend/services/pricing_schema.py |
| mart_follow_return_label_quality | backend/services/pricing_schema.py |
| mart_formula_horizon_evidence | backend/services/formula_engine/ddl.py |
| mart_global_data_quality_detail | backend/services/data_quality.py |
| mart_global_data_quality_gate | backend/services/data_quality.py |
| mart_lineage | backend/services/schema_marts.py |
| mart_market_perception_daily | backend/services/schema_marts.py |
| mart_model_feature_lineage | backend/services/schema_marts.py |
| mart_model_holding_topk_eval | backend/services/schema_marts.py |
| mart_model_selection_run | backend/services/schema_marts.py |
| mart_pipeline_lock | backend/services/pipeline_lock.py |
| mart_prediction_outcome | backend/services/prediction_outcome.py |
| mart_sector_momentum | backend/services/sector_momentum.py |
| mart_step_fingerprint | backend/services/event_engine.py |
| mart_stock_fund_flow_rank_snapshot_daily | backend/services/schema_marts.py |
| mart_stock_screening | backend/services/screening_engine.py |
| mart_stock_survey_activity | backend/services/institution_survey_client.py |
| mart_stock_trade_plan | backend/services/picture/ddl.py |
| mart_strategy_result_registry | backend/services/schema_marts.py |
| mart_tdx_challenger_report | backend/services/schema_marts.py |
| mart_tdx_f10_capability_matrix | backend/services/tdx_f10_extra_client.py |
| mart_tdx_gpcw_auto_challenger_report | backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_feature_cluster | backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_feature_score | backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_optuna_run | backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_pit_audit | backend/services/schema_marts.py |
| mart_tdx_gpcw_auto_retention_decision | backend/services/schema_marts.py |
| mart_tdx_gpcw_field_profile | backend/scripts/profile_tdx_gpcw_fields.py |
| mart_tdx_keep_promotion_gate | backend/services/schema_marts.py |
| mart_tdx_server_health | backend/services/tdx_source.py |
| mart_today_signal_cache | backend/services/signals_v2.py |
| mart_today_signal_cache_signal | backend/services/signals_v2.py |
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

> Codegraph: 节点 9,168 | calls 边 101,199 | imports 边 13,949 (每次 codegraph sync 波动, 不参与漂移判定)

### 被 import 最多的模块 (top 15)

| 模块 | import 处数 |
|---|---|
| services.db | 36 |
| services.utils | 34 |
| services.duck_adapter | 33 |
| services.database_manifest | 24 |
| services.market_db | 23 |
| services.experiment_store | 15 |
| services.industry | 15 |
| services.tdx_source | 15 |
| services.kline_source | 10 |
| services.pipeline_manifest | 8 |
| services.pricing_policy | 8 |
| routers.updater_runtime | 6 |
| services.constants | 6 |
| services.data_sources | 6 |
| services.etf_grid_engine | 6 |

### 跨文件 fan-in 最高的文件 (近似口径: 唯一定义名 + caller 实际 import 目标模块双过滤)

| 文件 | 调用方文件数 |
|---|---|
| backend/services/database_manifest.py | 23 |
| backend/services/duck_adapter.py | 16 |
| backend/services/experiment_store.py | 16 |
| bestchoice/compute.py | 9 |
| backend/routers/updater_runtime.py | 6 |
| backend/services/etf_grid_engine.py | 6 |
| backend/services/data_sources/base.py | 5 |
| bestchoice/execution_model.py | 5 |
| backend/services/etf_engine.py | 4 |
| backend/services/kline_source.py | 4 |
| bestchoice/formula_engine.py | 4 |
| bestchoice/scripts/formula_parameter_search.py | 4 |

### LOC top 10 (God module 候选)

| 文件 | 行数 |
|---|---|
| backend/services/data_quality.py | 4286 |
| backend/services/scoring.py | 2712 |
| backend/services/signals_v2.py | 2157 |
| backend/services/audit.py | 1745 |
| backend/services/financial_client.py | 1701 |
| backend/scripts/ingest_holders_tdxhub.py | 1545 |
| backend/services/tdx_f10_extra_client.py | 1478 |
| backend/scripts/build_price_kline_tdxhub.py | 1461 |
| backend/scripts/seed_dim_data_asset.py | 1303 |
| backend/scripts/audit_delivery_readiness.py | 1226 |

## 5. 概览

- chunkyctl 子命令 8 | launchd 任务 1 | router 16 (端点 98)
- sync_registry 数据域 33
- 产表 165 (多 writer 71)

