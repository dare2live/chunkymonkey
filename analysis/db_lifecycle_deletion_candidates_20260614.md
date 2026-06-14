# smartmoney 表生命周期删除候选清单 (2026-06-14)

> 来源: data-lifecycle-classify workflow (13 agent 逐表实查 live引用+可重建性, 主会话收编)。
> 用户生命周期模型: L0 裸K线基准(永久) / L1 因子探索(临时大,可删) / L2 结论(永久小)。
> **执行前须用户拍板** (删除半破坏性); 全程走 mart_data_deletion_record 留痕 + db_compact 缩盘回收盘 + post-fix-audit。

## 执行状态 (2026-06-14, 用户"不留尾巴 + 删除中吸取教训")

**已执行: 删 68 表 + 3 悬挂视图** (`db_lifecycle_delete.py` 工具: live守护 + parquet归档 + mart_data_deletion_record留痕 + 残留扫描)。
- 主会话 **external-reader 精核**纠 workflow 盲区: 原 112 候选里 47 有 external 消费者 (workbench/研究/离线) → 只删 **0 真消费者的 68 张** (5.74M 行, 6 张归档 parquet)。
- 残留扫描抓 **3 悬挂视图** (workflow + 我的代码扫都漏了 DB 内 VIEW 定义) → 视图均 stale (lineage元数据/config/disabled-weight0 alpha) → DROP + paper_sim_ensemble.yaml 停用条目注释化。
- 缩盘两轮: 26.6G → 17.5G (删11表62M) → 16.3G (删68表)。265 表 / 1 视图。
- **教训沉淀** (见 db_management_design §13.6 / §4.5): (1) "有 reader ≠ 必留", 判据是**实际用途**, 过时表+过时reader整套退役 (用户纠偏); (2) live 守护须扫**代码 + DB内VIEW定义 + yaml config** 三处 (单扫代码漏视图/配置); (3) external-reader 区分 builder-self vs 真消费者。

**held 44 表** (有 external 消费者): 按用户"看实际用途, 过时则连reader一起删"指引, 走 `subsystem-retirement-analysis` workflow 做子系统级退役分析 (synergy/feature-search/drift-safe/formula-optuna/workbench/p0a-base), 结论另附。

---

## 分桶总览 (原 workflow 分类, 执行前)

| 桶 | 表数 | 行 | 处置 |
|---|---|---|---|
| KEEP | 217 | 71,632,800 | 不动 (live_cited / L2结论 / source镜像) |
| SAFE_DELETE | 100 | 2,186,900 | L1可重建+有builder → DROP (先迁experiment-tier的) |
| ARCHIVE_FIRST | 10 | 8,516,025 | 不可确定性重建 → EXPORT parquet 冷归档再删 |
| ALREADY_DEAD | 6 | 295,994 | 0引用+写器已摘 → 直接 DROP |

可回收合计 ~10,998,919 行 / 2.2M+8.5M+0.3M; 估省盘 ~1-2GB (须配 db_compact 缩盘才回收文件块)。

## ARCHIVE_FIRST (10 表 — 先冷归档再删)

| 行 | 表 | 原因 |
|---|---|---|
| 4,052,975 | `fact_feature_panel_tdx_keep_challenger` | TDX keep 挑战者实验面板 (feature-set challenger), max_date 2026-05-06 stale 5+ 周, 无 live/champion/serving 引用 (仅自身 builder 读), 被 |
| 3,695,375 | `mart_p0a_feature_label_panel` | L1 探索期因子面板, 被 _v3/_v4/_v5 全面取代 (not_model_input, schema=v1, stale 47 天), 非 live (daily_update 走 v4/v5)。无现存 builder 能确定性重 |
| 312,607 | `mart_unified_v1_wf_oos_predictions` | WF OOS predictions 孤儿表 (signal_date 2024-07..09 单季), 0 writer 0 reader 非 live 非 source; 模型 OOS 预测产物且无 builder 可确定性重建 (上游 |
| 144,483 | `raw_executive_trade` | raw 镜像但 0 消费者 (全库无 SELECT/JOIN), 源=akshare:stock_ggcg_em (§4.3 淘汰源不稳定), 非 tushare/market 稳定 raw, 重拉非确定性 (限频/接口变/无 PIT 快照 |
| 93,011 | `raw_aif10_valuation_quantile` | 外部 aif10/eastmoney F10 估值分位镜像, latest-snapshot 无时间字段 → 历史无法从 tushare_raw/market 确定性重放 (regenerable=false)。已被 feature_joi |
| 90,879 | `fact_stock_attention_snapshot` | 每日由 live 链 WRITE 但从不被 live READ (serving 读并行写的 dim_stock_attention_latest, 本表只累积 PIT 历史+健康检查)。akshare 外部实时关注度快照, 历史无法确定性 |
| 90,800 | `raw_capital_dividend_summary` | akshare 分红/融资历史汇总镜像 (deprecated 源, §4.3 正被 tushare 替换), snapshot_date 镜像外部 API, 历史快照无法从 tushare_raw/market 确定性重放 (regene |
| 30,072 | `fact_stock_quality_features` | 个股质量特征 (46 cols), governance_context/not_model_input 无 live reader; dim_data_asset writer 字段空 → 重建路径不确定 (regenerable=fal |
| 5,805 | `dim_stock_sw_industry` | 申万行业映射 dim, reader 空 + 仅 1 引用 + writer 字段空 → 重建路径不确定 (是否仍有 sync 未知)。行业映射类基础 dim 删错影响面大 (生存者/宇宙构建潜在依赖), 不可确定性重建。先 EXPORT  |
| 18 | `mart_paper_sim_lambdamart_v6_kpi_compare` | lambdamart v6 paper_sim KPI 对比结论, writer=None (无确定性 builder, 历史 paper_sim run 产出)。KPI redline 审计读, L2 结论且不可确定重建。先 EXPORT |

## ALREADY_DEAD (6 表 — 直接 DROP)

| 行 | 表 | 原因 |
|---|---|---|

## SAFE_DELETE (100 表 — DROP, builder 可重建)

| 行 | 表 | builder |
|---|---|---|
| 471,217 | `fact_holder_event` | python backend/scripts/rebuild_holder_events.py |
| 337,960 | `mart_stock_formula_optuna_v2` | build_stock_formula_optuna_v2 (services.optimization 中央层重跑) |
| 308,928 | `mart_stock_formula_optuna` | build_stock_formula_optuna (services.optimization 重跑) |
| 154,812 | `mart_stock_pool_assignment` | build_stock_pool_assignment (月度 anchor + adv60 分池脚本) |
| 136,182 | `mart_model_portfolio_curve` | python backend/scripts/backtest_model_portfolio.py |
| 126,000 | `mart_model_walkforward_prediction` | python backend/scripts/run_multidim_walkforward.py (从 fact_f |
| 88,764 | `fact_shareholder_trade_tdx_b` | ingest/rebuild from raw_tdx_f10_holder_research (holders_res |
| 80,869 | `fact_policy_trade` | run_portfolio_mvp |
| 64,207 | `fact_policy_equity_curve` | run_portfolio_mvp |
| 63,731 | `fact_fundamental_quarterly` | rebuild from raw_gpcw_* (financial 派生 builder) |
| 43,832 | `mart_architecture_dependency_edge` | build_architecture_inventory |
| 41,309 | `mart_synergy_policy_mtm_daily_path` | synergy policy mtm builder 重跑 |
| 30,769 | `fact_shareholder_plan_tdx_f10` | ingest_holders_tdxhub (from raw_tdx_f10_holder_research) |
| 26,935 | `raw_tdx_f10_extra_parse_status` | tdx_f10_extra_client 重跑 |
| 25,462 | `fact_shareholder_trade` | ingest_holders_tdxhub |
| 20,567 | `fact_jgdy_event` | akshare jgdy ingest 重建 |
| 19,120 | `fact_stock_turtle_features` | stock_turtle_engine |
| 16,821 | `mart_global_data_quality_detail` | data_quality 重算 |
| 16,658 | `fact_shareholder_plan` | ingest_holders_tdxhub |
| 9,677 | `mart_shareholder_plan_initial_event` | shareholder_plan_initial_event builder |
| 9,286 | `mart_today_signal_cache_signal` | signals_v2 重算 |
| 8,983 | `fact_sector_predicted_ret_daily` | train_sector_rotation |
| 7,323 | `mart_architecture_inventory_asset` | build_architecture_inventory |
| 7,105 | `mart_feature_bucket_effect` | temporal synergy builder |
| 6,456 | `market_gap_queue` | gap_queue 重算 |
| 5,759 | `fact_financial_indicator_ak` | financial_indicator_client (akshare) |
| 5,514 | `financial_sync_state` | financial_client 重跑 |
| 5,412 | `mart_data_health` | data_quality 重算 |
| 5,201 | `fact_controlling_shareholder` | ingest_holders_tdxhub |
| 5,188 | `mart_stock_fund_flow_rank_snapshot_daily` | fund_flow_rank snapshot builder (akshare) |
| 4,707 | `mart_feature_relevance_stability` | temporal synergy builder |
| 4,059 | `mart_feature_exclusion_reason` | feature catalog builder |
| 3,774 | `mart_feature_redundancy_pair` | temporal redundancy builder |
| 3,516 | `mart_feature_interaction_candidate` | feature interaction builder (从特征 panel) |
| 3,516 | `mart_feature_pair_synergy` | feature pair synergy builder |
| 2,542 | `mart_tdx_gpcw_field_profile` | tdx_gpcw field profile builder |
| 2,422 | `mart_feature_drift_root_cause` | drift root cause builder |
| 2,054 | `mart_feature_conditional_synergy` | conditional synergy builder |
| 1,815 | `mart_optuna_synergy_trial` | services.optimization synergy study 重跑 |
| 1,647 | `mart_etf_snapshot_latest` | etf snapshot builder |
| 1,409 | `mart_feature_association_fold` | feature association builder |
| 1,230 | `mart_feature_temporal_relevance` | temporal relevance builder |
| 540 | `mart_feature_drift_histogram` | drift.py |
| 514 | `mart_model_stability_search_trial` | model stability search 重跑 |
| 499 | `mart_feature_rank_matrix_proxy_stat` | feature rank matrix builder |
| 493 | `mart_feature_search_space` | feature search space builder |
| 463 | `mart_feature_association_stat` | feature association builder |
| 336 | `mart_feature_correlation_cluster` | feature correlation builder |
| 298 | `mart_drift_safe_candidate_feature` | drift safe candidate builder |
| 284 | `mart_per_stock_optuna_best` | per-stock MACD optuna 重跑 |
| 243 | `mart_optuna_feature_space_trial` | feature space optuna 重跑 |
| 183 | `mart_feature_cluster_redundancy` | feature cluster builder |
| 27 | `mart_tdx_data_need_coverage` | tdx data need coverage audit 脚本 |
| 27 | `mart_tdx_f10_source_date_section_audit` | tdx f10 source date audit builder |
| 24 | `mart_lineage` | lineage run (从 schema) |
| 23 | `mart_drift_safe_candidate_batch_eval` | drift safe candidate batch builder |
| 22 | `mart_pricing_label_data_readiness_gate` | pricing label readiness gate builder |
| 19 | `mart_data_source_failure_queue` | watermark service 再生 |
| 17 | `mart_feature_drift_root_cause_summary` | drift root cause summary builder |
| 14 | `mart_architecture_inventory_summary` | build_architecture_inventory |
| 14 | `mart_data_source_reassignment_proposal` | data source reassignment 审计脚本 |
| 14 | `mart_synergy_policy_mtm_rerank` | synergy mtm rerank builder |
| 12 | `mart_tdx_gpcw_file_manifest` | tdx gpcw file manifest builder |
| 10 | `mart_model_ablation_run` | model ablation 重跑 (从 panel) |
| 10 | `mart_synergy_policy_mtm_strategy_sweep` | synergy mtm strategy sweep builder |
| 9 | `mart_pricing_label_policy_gate` | pricing label policy gate 服务 |
| 9 | `mart_temporal_research_panel_quality` | temporal research panel builder |
| 8 | `mart_drift_safe_candidate_batch_summary` | drift safe candidate batch summary builder |
| 8 | `mart_feature_panel_prune_run` | feature panel prune 重跑 |
| 8 | `mart_feature_search_space_summary` | feature search space summary builder |
| 7 | `mart_drift_safe_candidate_summary` | drift safe candidate summary builder |
| 7 | `mart_feature_rank_matrix_benchmark` | feature rank matrix benchmark builder |
| 7 | `mart_tdx_f10_capability_matrix` | tdx f10 capability matrix builder (探测源) |
| 3 | `mart_p1_optuna_trials` | services.optimization p1 study 重跑 |
| 3 | `mart_synergy_policy_mtm_rerank_summary` | synergy mtm rerank summary builder |
| 2 | `mart_feature_rank_matrix_cache_manifest` | feature rank matrix cache 重建 |
| 1 | `mart_audit_snapshot_state` | audit 服务再生 |
| 1 | `mart_etf_snapshot_state` | etf snapshot manager 再生 |
| 1 | `mart_pricing_label_policy` | pricing label policy 服务 |
| 1 | `mart_synergy_policy_mtm_strategy_sweep_summary` | synergy mtm sweep summary builder |
| 1 | `mart_today_signal_cache` | signals_v2 重算 |
| 0 | `mart_candidate_walkforward_eval` | candidate walkforward 重跑 |
| 0 | `mart_champion_candidate_evaluation` | champion candidate eval builder |
| 0 | `mart_data_processing_tool_issue` | monitor 服务再生 |
| 0 | `mart_data_processing_tool_run` | monitor 服务再生 |
| 0 | `mart_feature_candidate_coverage` | feature candidate coverage builder |
| 0 | `mart_feature_candidate_score` | feature candidate score builder |
| 0 | `mart_feature_drift_mitigation_panel_build` | drift mitigation panel builder |
| 0 | `mart_feature_group_ablation` | feature group ablation 重跑 |
| 0 | `mart_hybrid_feature_panel_build` | hybrid feature panel builder |
| 0 | `mart_model_composite_score` | model composite score 服务 |
| 0 | `mart_model_edge_flags` | model edge flags 服务 |
| 0 | `mart_model_holding_topk_eval` | model holding topk eval builder |
| 0 | `mart_p1_ablation_result` | p1 ablation 重跑 |
| 0 | `mart_research_reflection_log` | research reflection 服务 |
| 0 | `mart_tdx_gpcw_auto_feature_cluster` | tdx gpcw auto feature cluster builder |
| 0 | `mart_tdx_gpcw_auto_feature_score` | tdx gpcw auto feature score builder |
| 0 | `mart_tdx_gpcw_auto_optuna_run` | tdx gpcw auto optuna 重跑 |
| 0 | `mart_tdx_gpcw_auto_retention_decision` | tdx gpcw auto retention builder |
| 0 | `mart_tdx_server_health` | tdx_source 探活再生 |

## 执行硬闸 (workflow 对抗核证的风险, 必守)

- 裸 K 线 L0 基准目前不存在 = S0 待建, 严禁把现有 panel 误当 baseline: database_manifest.yaml 无 production_control/disposable_scratch retention_class; 唯一标 protected_baseline_panel 的 mart_p0a_feature_label_panel 是 143 列特征面板 (本批 ARCHIVE_FIRST), 不是裸 K 线对照。真正的'裸 K 线基准面板'(用于 ablation 对照'特征到底有没有增量')需在 S0 alpha 验证程序里新建 (Task #9 alpha_validation_program), 别拿 v3/v4/v5 任一现有 panel 顶替, 否则 ablation 对照失真→真金白银下高估特征价值。
- 硬删不立即省盘 — 必须配 db_compact: DuckDB DROP/DELETE 不回收文件块 (db_compact.py docstring 明示)。SAFE_DELETE 100 表 DROP 后 smartmoney.duckdb 文件不缩, 必须跑 db_compact.py --db smartmoney --execute 整库 ATTACH-copy 保真重写才回收盘; 且严禁 CTAS (06-12 约束 315→1 反例)。两张 >1M 行的 ARCHIVE_FIRST 表删除触发 large_delete_row_threshold gate, 需 explicit_execute_flag。
- ARCHIVE_FIRST 的 fact_stock_attention_snapshot 仍被 live 链 WRITE (虽不被 READ): 直接 EXPORT+删会被次日 daily_update 重新写回→归档失效。归档前必须先确认/停掉 write 路径, 否则陷入'归档→重生→再归档'循环。
- experiment-tier 路由 vs 硬删的执行顺序: 约 14 张 SAFE_DELETE 表已登记 db_partition_tiers.yaml experiment tier (target=experiment_store.duckdb, status=planned)。若先在 smartmoney DROP 再迁移, 与 db_partition_migrate.py 的迁移清单冲突。正确序: 先 migrate 到 experiment_store 再在新库 TRUNCATE, 一次到位避免二次搬 — 别在 smartmoney 里抢先 DROP 这批。
- storage_retention.yaml protected 标志 vs SAFE_DELETE 冲突 (2 张需复核): mart_tdx_f10_capability_matrix (在 protected_artifact_tables, 源治理证据) 与 mart_feature_rank_matrix_cache_manifest (cache_manifest_evidence + delete_gates) 被列入 SAFE_DELETE 但 storage_retention.yaml 有显式保护/删除门禁。删前必须过对应 delete_gates, 否则降 ARCHIVE_FIRST; 不要无视 yaml 治理标志直接删。
- ALREADY_DEAD 的 fact_concept_event 写器确已物理摘除 (已核实): ops_manual_run.py:34-36 注释证实 concept_snapshot E7 2026-06-13 物理摘除且 build_concept_events 未接入任何 live entrypoint, 唯一 reader 是离线 experiment_lf_v0。可直接 DROP。但删前确认无在跑的 LF 实验持有句柄 (backfill_history_chain9b/9c.sh 历史脚本曾引用)。
- 所有删除走 mart_data_deletion_record 留痕 (合规闸) + post-fix-audit 0 residue 验证: 项目红线 validation artifacts 不可静默消失。每张删除前 row_count/schema snapshot + copied_duckdb dry_run, 删后扫 downstream stale (view/JOIN 悬挂引用) + 进程/cache 残留。批量删 100+ 表风险面大, 建议分批 (先 0 行 dead → 再小表 → 最后 >100K 大表), 每批独立 commit + doctor 验证不回退。