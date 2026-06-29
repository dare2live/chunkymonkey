# FEATURE_MAP — 机器生成功能地图

> 由 `scripts/chunkyctl map` (backend/scripts/build_feature_map.py) 重生成, **勿手改**。
> 只列机器可枚举事实 (入口/数据域/产表 writer/依赖热点/计数); 人工判断层 (坑/权重/状态) 在 `PROJECT_INDEX.md`。机器版: `data/reports/feature_map.json` (本地, 不入 git)。
> Snapshot: 2026-06-29 09:38

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
| `pipeline` | §8 run one stage independently (acquire|clean|process|store); full chain still via daily_update.sh. |
| `lineage` | M5-T2 血缘路由中枢 (字典+总指挥): impact <table> = 删/迁前自动 fan-in (替代手 grep); build/provenance/dead/show. |

### launchd 定时任务

| Label | 时刻 | 入口 |
|---|---|---|
| com.chunkymonkey.nightly-data-audit | 02:00 | `.venv/bin/python scripts/launchd_job_wrapper.py nightly_data_audit .venv/bin/python backen…` |

### API 路由 (regex 口径: @router 装饰器)

| router | prefix | 端点数 |
|---|---|---|
| ops_manual_run | `/api/v3/ops` | 3 |
| strategy_preset | `/api/inst/strategy` | 4 |
| v3_config | `/api/v3` | 1 |

端点全列表在 json (`routes` 键)。

## 2. 数据域 (sync_registry)

| 域 | 源 | api | 表 | 模式 | SLA(交易日) |
|---|---|---|---|---|---|
| adj_factor | tushare | adj_factor | raw_tushare_adj_factor | by_trade_date | 1 |
| balancesheet | tushare | balancesheet | raw_tushare_balancesheet | by_ts_code | 5 |
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
| fund_adj | tushare | fund_adj | raw_tushare_fund_adj | by_trade_date | 1 |
| fund_daily | tushare | fund_daily | raw_tushare_fund_daily | by_trade_date | 1 |
| income | tushare | income | raw_tushare_income | by_ts_code | 5 |
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
| share_float | tushare | share_float | raw_tushare_share_float | by_trade_date | 3 |
| stk_factor_pro | tushare | stk_factor_pro | raw_tushare_stk_factor_pro | by_ts_code | 1 |
| stk_holdernumber | tushare | stk_holdernumber | raw_tushare_stk_holdernumber | by_ts_code | 90 |
| stk_holdertrade | tushare | stk_holdertrade | raw_tushare_stk_holdertrade | by_ann_date | 30 |
| stk_limit | tushare | stk_limit | raw_tushare_stk_limit | by_trade_date | 1 |
| stk_surv | tushare | stk_surv | raw_tushare_stk_surv | by_trade_date | 5 |
| stock_basic | tushare | stock_basic | raw_tushare_stock_basic | full_refresh | 30 |
| stock_st | tushare | stock_st | raw_tushare_stock_st | by_trade_date | 1 |
| suspend_d | tushare | suspend_d | raw_tushare_suspend_d | by_trade_date | 3 |
| sw_daily | tushare | sw_daily | raw_tushare_sw_daily | by_trade_date | 1 |
| ths_hot | tushare | ths_hot | raw_tushare_ths_hot | by_trade_date | 2 |
| top10_floatholders | tushare | top10_floatholders | raw_tushare_top10_floatholders | by_ann_date | 90 |
| top_inst | tushare | top_inst | raw_tushare_top_inst | by_trade_date | 2 |
| top_list | tushare | top_list | raw_tushare_top_list | by_trade_date | 2 |
| trade_cal | tushare | trade_cal | raw_tushare_trade_cal | full_refresh | 30 |

## 3. 产表 writer (单 writer 契约审查素材)

统计: 表 49 张 | 单 writer 37 | 多 writer 12 | 动态表名写点 16 处 (9 文件)

口径免责: 静态正则扫描, 含历史/backfill 一次性脚本与字符串内 SQL 样例; **多 writer 计数 ≠ 违规待修清单** — 升级为问题需逐表人工确认运行时并发写。

### 动态表名写点 (f-string, 静态不可归属 — 这些文件写的表不在下方普查内)

| 文件 | 写点数 |
|---|---|
| backend/scripts/build_dc_industry_view.py | 2 |
| backend/scripts/build_etf_kline_qfq_tushare.py | 1 |
| backend/scripts/build_feature_map.py | 1 |
| backend/scripts/build_price_kline_qfq_tushare.py | 1 |
| backend/scripts/db_compact.py | 2 |
| backend/scripts/db_partition_migrate.py | 2 |
| backend/scripts/migrate_reference_db.py | 1 |
| backend/services/aif10_capability_client.py | 3 |
| backend/services/data_sources/sync_runner.py | 3 |

### 多 writer 表 (>1 文件写同一张表)

| 表 | writer 数 | writer 文件 |
|---|---|---|
| fact_top10_holder_period | 3 | backend/scripts/cleanup_holder_dup.py<br>backend/services/holders_aif10.py<br>backend/services/schema_core.py |
| mart_data_deletion_record | 3 | backend/scripts/db_lifecycle_delete.py<br>backend/services/data_deletion.py<br>backend/services/schema_marts.py |
| dim_fee_schedule | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_liquidity_threshold | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_market_segment | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_price_limit_rules | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_style_factor | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_trading_rule | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| dim_trading_session | 2 | backend/services/primitives/ddl.py<br>backend/services/primitives/seed.py |
| mart_data_health | 2 | backend/scripts/data_health_snapshot.py<br>backend/services/schema_marts.py |
| mart_data_source_watermark | 2 | backend/services/schema_marts.py<br>backend/services/source_watermarks.py |
| mart_pipeline_run_manifest | 2 | backend/services/pipeline_manifest.py<br>backend/services/schema_marts.py |

### 单 writer 表

| 表 | writer |
|---|---|
| dim_active_a_stock | backend/services/security_master.py |
| dim_data_source_priority | backend/services/schema_core.py |
| dim_holder_alias | backend/services/schema_core.py |
| dim_listing_status | backend/scripts/build_dim_listing_status.py |
| dim_schema_version | backend/services/schema_versions.py |
| dim_strategy_preset | backend/routers/strategy_preset.py |
| dim_trading_calendar | backend/scripts/migrate_reference_db.py |
| fact_common_major_holder_stock | backend/services/schema_core.py |
| fact_consumer_alpha_ic_scan | backend/scripts/build_experiment_store.py |
| fact_controlling_shareholder | backend/services/schema_core.py |
| fact_daily_price_status | backend/services/primitives/ddl.py |
| fact_experiment_verdict | backend/scripts/build_experiment_store.py |
| fact_risk_factors | backend/services/data_governance/etl_hook.py |
| fact_setup_snapshot | backend/services/schema_core.py |
| fact_shareholder_plan | backend/services/schema_core.py |
| fact_shareholder_trade | backend/services/schema_core.py |
| fact_stock_liquidity_daily | backend/services/primitives/ddl.py |
| fact_stock_market_cap_daily | backend/services/primitives/ddl.py |
| fact_stock_style_daily | backend/services/primitives/ddl.py |
| mart_candidate_feature_set_contract | backend/services/data_quality.py |
| mart_data_deprecation_record | backend/services/schema_marts.py |
| mart_data_processing_tool_issue | backend/services/data_processing_monitor.py |
| mart_data_processing_tool_run | backend/services/data_processing_monitor.py |
| mart_data_source_failure_queue | backend/services/source_watermarks.py |
| mart_etf_snapshot_latest | backend/services/etf_db.py |
| mart_etf_snapshot_state | backend/services/etf_db.py |
| mart_feature_availability_contract | backend/services/data_quality.py |
| mart_feature_null_policy | backend/services/data_quality.py |
| mart_global_data_quality_detail | backend/services/data_quality.py |
| mart_global_data_quality_gate | backend/services/data_quality.py |
| mart_lineage | backend/services/schema_marts.py |
| mart_pipeline_lock | backend/services/pipeline_lock.py |
| mart_stock_survey_activity | backend/services/institution_survey_client.py |
| raw_institution_surveys | backend/services/institution_survey_client.py |
| raw_lhb_daily | backend/services/lhb_client.py |
| raw_org_holding_aif10 | backend/services/org_holding_aif10.py |
| raw_qfii_holding_quarterly | backend/services/qfii_client.py |

## 4. 依赖热点 (codegraph 派生)

> Codegraph: 节点 4,582 | calls 边 6,610 | imports 边 1,290 (每次 codegraph sync 波动, 不参与漂移判定)

### 被 import 最多的模块 (top 15)

| 模块 | import 处数 |
|---|---|
| services.duck_adapter | 29 |
| services.db | 9 |
| services.pipeline_manifest | 7 |
| services.database_manifest | 6 |
| services.utils | 6 |
| services.data_sources | 5 |
| services.lineage.model | 5 |
| scripts.formula_parameter_search | 4 |
| services.data_processing_monitor | 4 |
| services.market_db | 4 |
| services.calendar | 3 |
| services.lineage | 3 |
| services.storage_retention | 3 |
| services.data_deletion | 2 |
| services.data_governance.config | 2 |

### 跨文件 fan-in 最高的文件 (近似口径: 唯一定义名 + caller 实际 import 目标模块双过滤)

| 文件 | 调用方文件数 |
|---|---|
| backend/services/duck_adapter.py | 22 |
| bestchoice/compute.py | 9 |
| backend/services/pipeline_manifest.py | 5 |
| bestchoice/execution_model.py | 5 |
| backend/services/database_manifest.py | 4 |
| backend/services/lineage/model.py | 4 |
| backend/services/pipeline/context.py | 4 |
| bestchoice/formula_engine.py | 4 |
| bestchoice/scripts/formula_parameter_search.py | 4 |
| backend/services/data_access/keys.py | 3 |
| backend/services/data_processing_monitor.py | 3 |
| backend/services/market_db.py | 3 |

### LOC top 10 (God module 候选)

| 文件 | 行数 |
|---|---|
| backend/services/data_quality.py | 3699 |
| backend/services/storage_retention.py | 1061 |
| backend/services/data_sources/sync_runner.py | 985 |
| backend/scripts/data_health_snapshot.py | 730 |
| backend/services/data_audit.py | 613 |
| backend/services/schema_migrations.py | 580 |
| backend/services/source_watermarks.py | 570 |
| backend/services/lhb_client.py | 424 |
| backend/scripts/build_feature_map.py | 420 |
| backend/services/qfii_client.py | 420 |

## 5. 概览

- chunkyctl 子命令 10 | launchd 任务 1 | router 3 (端点 8)
- sync_registry 数据域 43
- 产表 49 (多 writer 12)

