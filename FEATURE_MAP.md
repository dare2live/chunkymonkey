# FEATURE_MAP — 机器生成功能地图

> 由 `scripts/chunkyctl map` (backend/scripts/build_feature_map.py) 重生成, **勿手改**。
> 只列机器可枚举事实 (入口/数据域/产表 writer/依赖热点/计数); 人工判断层 (坑/权重/状态) 在 `PROJECT_INDEX.md`。机器版: `data/reports/feature_map.json` (本地, 不入 git)。
> Snapshot: 2026-07-26 10:18

## 1. 入口面

### chunkyctl 子命令

| 命令 | 说明 |
|---|---|
| `agent-boot` | one-page session-start context: git + moth summary + codegraph status + generated board; read-only projection. |
| `pre-knife` | mandatory L3 impact checklist: moth coupling --impact + codegraph explore callers (once). |
| `doctor` | project health snapshot, including manual-only automation residue enforcement. |
| `sync` | manual single-domain provider sync through the production runner and writer lock. |
| `derive` | S5/S7 qfq/form rebuild independent of acquire/accept; default = accepted-only |
| `map` | regenerate FEATURE_MAP.md machine-derived feature map; --check = drift gate only. |
| `pipeline` | manually run one declared data stage; full manual chain remains daily_update.sh. |
| `lineage` | generated dependency projection; impact <table> audits fan-in before delete/migrate. |

### launchd 定时任务

| Label | 时刻 | 入口 |
|---|---|---|

### API 路由 (regex 口径: @router 装饰器)

| router | prefix | 端点数 |
|---|---|---|
| decision_assist | `/api/v3/decision` | 7 |
| institution_profile | `—` | 3 |
| market_pulse | `/api/v3/pulse` | 9 |
| ops_manual_run | `/api/v3/ops` | 5 |
| paper_portfolio | `/api/v3/paper` | 5 |
| stock_dossier | `/api/v3/stock` | 1 |
| stock_screener | `/api/v3/screener` | 2 |

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
| daily_info | tushare | daily_info | raw_tushare_daily_info | by_trade_date | 2 |
| dc_daily | tushare | dc_daily | raw_tushare_dc_daily | by_trade_date | 2 |
| dc_index | tushare | dc_index | raw_tushare_dc_index | by_trade_date | 2 |
| dc_member | tushare | dc_member | raw_tushare_dc_member | by_trade_date | 2 |
| dividend | tushare | dividend | raw_tushare_dividend | by_trade_date | 5 |
| fina_indicator | tushare | fina_indicator | raw_tushare_fina_indicator | by_ts_code | 5 |
| forecast | tushare | forecast | raw_tushare_forecast | by_ann_date | 5 |
| hm_detail | tushare | hm_detail | raw_tushare_hm_detail | by_trade_date | 2 |
| hm_list | tushare | hm_list | raw_tushare_hm_list | full_refresh | 30 |
| income | tushare | income | raw_tushare_income | by_ts_code | 5 |
| index_daily_benchmark | tushare | index_daily | raw_tushare_index_daily | by_code_list | 1 |
| index_dailybasic | tushare | index_dailybasic | raw_tushare_index_dailybasic | by_code_list | 1 |
| index_member_all | tushare | index_member_all | raw_tushare_index_member_all | by_code_list | 30 |
| index_member_all_hist | tushare | index_member_all | raw_tushare_index_member_all | by_code_list | 30 |
| kpl_list | tushare | kpl_list | raw_tushare_kpl_list | by_trade_date | 2 |
| limit_cpt_list | tushare | limit_cpt_list | raw_tushare_limit_cpt_list | by_trade_date | 2 |
| limit_list_d | tushare | limit_list_d | raw_tushare_limit_list_d | by_trade_date | 1 |
| margin | tushare | margin | raw_tushare_margin | by_trade_date | 2 |
| margin_detail | tushare | margin_detail | raw_tushare_margin_detail | by_trade_date | 2 |
| moneyflow | tushare | moneyflow | raw_tushare_moneyflow | by_trade_date | 1 |
| moneyflow_dc | tushare | moneyflow_dc | raw_tushare_moneyflow_dc | by_trade_date | 1 |
| moneyflow_hsgt | tushare | moneyflow_hsgt | raw_tushare_moneyflow_hsgt | by_trade_date | 2 |
| moneyflow_ind_dc | tushare | moneyflow_ind_dc | raw_tushare_moneyflow_ind_dc | by_trade_date | 2 |
| moneyflow_mkt_dc | tushare | moneyflow_mkt_dc | raw_tushare_moneyflow_mkt_dc | by_date_range | 1 |
| report_rc | tushare | report_rc | raw_tushare_report_rc | by_ann_date | 3 |
| share_float | tushare | share_float | raw_tushare_share_float | by_ann_date | 3 |
| stk_holdernumber | tushare | stk_holdernumber | raw_tushare_stk_holdernumber | by_ann_date | 90 |
| stk_holdertrade | tushare | stk_holdertrade | raw_tushare_stk_holdertrade | by_ann_date | 30 |
| stk_limit | tushare | stk_limit | raw_tushare_stk_limit | by_trade_date | 1 |
| stk_surv | tushare | stk_surv | raw_tushare_stk_surv | by_ann_date | 5 |
| stock_basic | tushare | stock_basic | raw_tushare_stock_basic | full_refresh | 30 |
| stock_st | tushare | stock_st | raw_tushare_stock_st | by_trade_date | 1 |
| suspend_d | tushare | suspend_d | raw_tushare_suspend_d | by_trade_date | 3 |
| sw_daily | tushare | sw_daily | raw_tushare_sw_daily | by_trade_date | 1 |
| ths_hot | tushare | ths_hot | raw_tushare_ths_hot | by_ann_date | 2 |
| top_inst | tushare | top_inst | raw_tushare_top_inst | by_trade_date | 2 |
| top_list | tushare | top_list | raw_tushare_top_list | by_trade_date | 2 |
| trade_cal | tushare | trade_cal | raw_tushare_trade_cal | full_refresh | 30 |

## 3. 产表 writer (单 writer 契约审查素材)

统计: 表 33 张 | 单 writer 22 | 多 writer 11 | 动态表名写点 71 处 (24 文件)

口径免责: 静态正则扫描, 含历史/backfill 一次性脚本与字符串内 SQL 样例; **多 writer 计数 ≠ 违规待修清单** — 升级为问题需逐表人工确认运行时并发写。

### 动态表名写点 (f-string, 静态不可归属 — 这些文件写的表不在下方普查内)

| 文件 | 写点数 |
|---|---|
| backend/scripts/build_dc_industry_view.py | 2 |
| backend/scripts/build_feature_map.py | 1 |
| backend/scripts/build_price_kline_qfq_tushare.py | 3 |
| backend/scripts/db_compact.py | 2 |
| backend/services/calendar_builder.py | 2 |
| backend/services/data_sources/accepted_schema.py | 2 |
| backend/services/data_sources/calendar_acceptance.py | 2 |
| backend/services/data_sources/calendar_landing.py | 3 |
| backend/services/data_sources/calendar_schema.py | 3 |
| backend/services/data_sources/disclosure_dual_write.py | 1 |
| backend/services/data_sources/disclosure_event_partition.py | 6 |
| backend/services/data_sources/holders_top10_acceptance.py | 6 |
| backend/services/data_sources/margin_acceptance.py | 4 |
| backend/services/data_sources/margin_schema.py | 2 |
| backend/services/data_sources/security_day_partition.py | 6 |
| backend/services/data_sources/sync_runner.py | 2 |
| backend/services/dc_member_publish.py | 2 |
| backend/services/index_daily_publish.py | 2 |
| backend/services/market_pulse.py | 4 |
| backend/services/rally_gt.py | 6 |
| backend/services/stock_limit_publish.py | 2 |
| backend/services/stock_moneyflow_publish.py | 4 |
| backend/services/technical_states/__init__.py | 2 |
| backend/services/top_inst_seat_publish.py | 2 |

### 多 writer 表 (>1 文件写同一张表)

| 表 | writer 数 | writer 文件 |
|---|---|---|
| mart_data_deletion_record | 4 | backend/scripts/db_lifecycle_delete.py<br>backend/services/data_deletion.py<br>backend/services/holders_landing_retention.py<br>backend/services/schema_marts.py |
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
| dim_schema_version | backend/services/schema_versions.py |
| dim_stock_segment_daily | backend/services/segments.py |
| fact_consumer_alpha_ic_scan | backend/scripts/build_experiment_store.py |
| fact_controlling_shareholder | backend/services/schema_core.py |
| fact_daily_price_status | backend/services/primitives/ddl.py |
| fact_experiment_verdict | backend/scripts/build_experiment_store.py |
| fact_inst_episode | backend/services/institution_profile.py |
| fact_risk_factors | backend/services/data_governance/etl_hook.py |
| fact_setup_snapshot | backend/services/schema_core.py |
| fact_shareholder_plan | backend/services/schema_core.py |
| fact_shareholder_trade | backend/services/schema_core.py |
| fact_stock_liquidity_daily | backend/services/primitives/ddl.py |
| fact_stock_market_cap_daily | backend/services/primitives/ddl.py |
| fact_stock_style_daily | backend/services/primitives/ddl.py |
| mart_data_deprecation_record | backend/services/schema_marts.py |
| mart_data_source_failure_queue | backend/services/source_watermarks.py |
| mart_inst_profile | backend/services/institution_profile.py |
| mart_inst_profile_dim | backend/services/institution_profile.py |
| mart_lineage | backend/services/schema_marts.py |
| raw_org_holding_aif10 | backend/services/org_holding_aif10.py |
| raw_qfii_holding_quarterly | backend/services/qfii_client.py |

## 4. 依赖热点 (codegraph 派生)

> Codegraph: 节点 10,291 | calls 边 12,032 | imports 边 3,389 (每次 codegraph sync 波动, 不参与漂移判定)

### 被 import 最多的模块 (top 15)

| 模块 | import 处数 |
|---|---|
| services.duck_adapter | 70 |
| services.data_sources | 29 |
| services.universe | 22 |
| services.data_sources.accepted_schema | 20 |
| services.data_sources.security_day_partition | 19 |
| services.data_sources.holders_top10_schema | 18 |
| services.institution_follow_b0_measure | 18 |
| services.institution_follow_edge_gates | 17 |
| services.data_sources.margin_schema | 15 |
| services.source_watermarks | 14 |
| services.data_access | 13 |
| services.data_sources.nominal_ohlcv_schema | 13 |
| services.database_manifest | 13 |
| services.data_sources.calendar_schema | 12 |
| services.data_sources.stk_holdertrade_schema | 12 |

### 跨文件 fan-in 最高的文件 (近似口径: 唯一定义名 + caller 实际 import 目标模块双过滤)

| 文件 | 调用方文件数 |
|---|---|
| backend/services/duck_adapter.py | 32 |
| backend/services/universe.py | 18 |
| backend/services/institution_follow_edge_gates.py | 17 |
| backend/services/source_watermarks.py | 14 |
| backend/services/data_sources/disclosure_boundaries.py | 11 |
| backend/services/database_manifest.py | 10 |
| backend/services/data_sources/contracts.py | 9 |
| backend/services/tier12_consumer_cutover.py | 9 |
| backend/services/tier12_publish_writer.py | 9 |
| backend/services/institution_follow_b0_measure.py | 8 |
| backend/services/pipeline/context.py | 8 |
| frontend/src/components/Card.tsx | 8 |

### LOC top 10 (God module 候选)

| 文件 | 行数 |
|---|---|
| backend/services/data_sources/sync_runner.py | 4422 |
| backend/services/market_pulse.py | 1597 |
| backend/scripts/check_continuity_integrity.py | 1124 |
| backend/services/data_sources/holders_top10_acceptance.py | 800 |
| backend/scripts/check_foundation_done.py | 799 |
| backend/services/org_holding_aif10.py | 797 |
| backend/services/pipeline/acquire.py | 797 |
| backend/services/research_runtime.py | 780 |
| backend/services/data_sources/security_day_partition.py | 779 |
| backend/services/universe.py | 761 |

## 5. 概览

- chunkyctl 子命令 8 | launchd 任务 0 | router 7 (端点 32)
- sync_registry 数据域 44
- 产表 33 (多 writer 11)

