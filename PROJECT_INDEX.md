# PROJECT_INDEX.md — Chunky Monkey v2 项目地图 (context-only briefing)

> 用于防止对话压缩 / context 丢失导致重复发现项目结构 / 误解数据资产.
> 内容是**项目地图**, 不是规则 — Codex 规则在 `AGENTS.md`; 当前阶段计划在薄入口 `goal.md`; 历史状态/已完成证据在 `analysis/project_state_ledger.md`; `SESSION_HANDOFF.md` 是生成恢复快照; durable contract 在 `docs/README.md` 指向的 active docs; `CLAUDE.md` 是 legacy Claude-specific history.
> **文档保鲜 (2026-06-20)**: 全面审计后修死引用 (reset 删的 audit_*/build_* 脚本 → 改指现 gate moth coupling/check_doc_drift + 当前 build_rally_*/feature_panel 管道); `docs/implementation_plan.md` **2026-06-22 归档→`docs/archive/`** (deprecated+悬空引用, 修 C2 docs/ 11→10 + moth doc-governance 翻 PASS); `docs/chip_distribution_cyq_spec.md` 标 **deprecated** (留历史参考, 勿当现行命令源); `engineering_governance`/`chunkyctl_session_quickstart` 死引用改指现 gate/管道 + 陈旧头注。**机器化根治 (用户"保持最新避免污染")**: `check_doc_drift.py` **扩展扫全活文档** (活索引+AGENTS+docs/ 13 档, 非仅活索引 — 故原漏报 AGENTS/docs 死引用); lookbehind 防 mid-path 假阳性(bestchoice/scripts) + 整档 deprecated 头注豁免 + 行级 retired/历史叙事/模板豁免; 6 单测; moth `doc-drift` 断言守 (commit 前拦未来死引用)。现 PASS 悬空=0/13档, 2 deprecated 跳过。
> 2026-06-05 起，旧 GCP / GCS / phase5 monitor / cost tracker 条目只作历史证据，不是可恢复执行面。当前长任务/花钱任务必须走 `backend/config/experiment_jobs.yaml` + `scripts/chunkyctl jobs`，`local` active，`modal` active(端到端验证 2026-06-20)。
> **2026-06-20 D 按阶段因子矩阵 重定向 (用户纠偏; 前期买点 detour 全删)**: 前期 D-step 起涨点买点判别偏离计划 §0 诚实先验 (买点=secondary, 主攻=鱼身延续+鱼尾出场+仓位)，已全删。**正确路径 (用户确认)**: 在 `fact_rally_stage`(起涨/主升/顶部) 上逐阶段验因子, 优先级 **鱼尾出场 > 鱼身延续 > 鱼头买点**; 判据=**stage 窗内条件化持有(持到信号反转非固定调仓)→含成本绝对收益, IC 仅快筛非 AUC**; 鱼身=是否续持, 鱼尾=何时卖。先 CYQ 出货预警(鱼尾,0代码,cyq_perf 单峰→多峰)+多头排列/资金净入(鱼身)。owner=`data_validation_backtest_plan_20260619.md` §2.2-2.3。
>
> **目标**: 新接手 (无论 Claude 还是人) 读完此文档**不用看代码 / 不用查 DB** 就能理解:
> 项目业务 / 架构 / 技术路线 / 数据资产 / 当前进度 / 已知坑 / 常用操作.

最后更新: **2026-06-06** (TuShare no-persist exact-flow probe wiring + need_027 probe diagnostics hardening + storage retention owner/consumer policy contract + data-source capability router contract + need_027 candidate validation metadata + provider-neutral experiment job contract + execution-surface audit + retired GCP execution surface removal + architect-controller skill install + verify-verifier rule + Moth complexity path normalization + local complexity baseline refresh + data-health dry-run read-only fix + Moth evidence path sync + design-review preflight machine gate + Moth registry instruction-source sync + after-close data refresh + controller-agent preflight hard gate + retention dry-run inventory + storage payload cap recalibration + DB manifest attach policy + DB boundary static gate + holder replay safety + Codex instruction-source boundary + DuckDB capacity audit + need_027 exact-flow probe gate + stage-opt supply/readiness/schema contract + stage-opt signal-date K-line coverage evidence + stage-opt source-aware density diagnostics + stage-opt source freshness/window diagnostics + iFinD MCP research-only routing recheck).

## [INDEX] 最近增量 (只留 7 天, 历史在 analysis/project_index_changelog_archive_20260611.md + ledger)

- **2026-06-27 §9 reference 拆库 Phase 0 fan-in 审计 DONE (四地基最后一块 #2 gating 前置)**: 通达信全删收口不变量4后, 主线转 §9(完成不变量#2 库分区)。workflow wf_df52c6c6 (5agent/387k) 全清 4 dim 表(dim_active_a_stock/all_ever_listed/listing_status/trading_calendar)在 4 连接模型 73 读/写点(get_conn+注入46/direct_connect7/bestchoice_attach10/writers+ddl10) + 对抗验证抓 7 漏点(services 之外: v3_selection router/build_rally_entry_pit+negatives/data_health_snapshot/audit_panel_leakage/build_macd_state_history/realdb test) → **stage_e_safe=FALSE**。**关键机制 reframe**: 根因=services/db.py get_conn 无 reference ATTACH → alias-routing(架构师选)需 73点逐个rewire走 data_access; 验证者荐中央 get_conn ATTACH 根治但=曾否决 view+ATTACH → 机制真 tradeoff 须焦点 session re-grill。Stage A(reference 4表已建 5208/5210/5210/5343 + smartmoney 双副本)DONE。**[2026-06-27 执行起步]** 机制 grill 实测定案(alias-routing/限定名唯一干净路, view+ATTACH磁盘污染坐实) + 设计裁决(专用reference-dim读/写路, dim不塞PIT data_access entity) + **resolver.connect_rw infra DONE**(dim writer写reference RW路)。**scope 实测校准**: 每 dim=10-15 reader文件 whole-app(dim_active 12直读者+writer+JOIN+DDL; 全链universe/scoring/dossier/routers); 40-60文件连接重构, 安全做=writer dual-write→reader逐文件迁验证→Stage E物删, 逐dim多commit。**[执行进行中 dim_active]**: security_master.refresh **writer dual-write DONE**(reference+smartmoney各5208一致, _write_dim_active helper; 无test注入无污染)→reference同步, 下步逐迁12直读者→Stage E。owner=analysis/section9_phase0_fanin_checklist_20260627.md + verified plan。

- **2026-06-27 零残留收尾 (autonomous loop + ultracode Workflow)**: 残留审计 Workflow(wf_8585638e, 6agent 467K tok 扇出+对抗验证)揭示残留远超 task#13 (gpcw/tdx_f10/price_kline_tdxhub/server_health/holders 散落 dead refs + 3 HIGH live_reference)。**逐 batch 物删 (对抗验证 0-live-caller)**: [#13a DONE] financial_client dead sync body 整段物删 1699→715行 (gpcw sina/akshare 抓取+sync_financial_data, 0caller; 保 calc_financial_derived/ensure_tables/summarize_history_gap_state + deps; audit fin_raw_count 不查已删 raw_gpcw_financial)。[#13b DONE] tdx_source server_health DB 函数整段物删 (1部分→547行: ensure/record/load+helper, 保 live circuit-breaker call_tdx_quotes_with_retry/iter/_mark_*; import 收尾删 datetime/Any 孤儿)。[C1 DONE] dead脚本物删 (holders_resolver+test/migrate_holders_to_tdxhub+test/check_sina_tdxhub_overlap/profile_tdx_gpcw_fields.pyc, 全0-live-importer; holder走aif10/tushare) + 清 duckdb_connect_policy/test_tool_registry(holders runner trim+删2 stale tdx_f10条目)/data_module_members/claims.yaml(holder断言去dead-script ref, 修coupling orphan)。[C3 DONE] price_kline_tdxhub死代码(market_db.upsert_price_kline_tdxhub_rows 0caller删 / risk_factors lineage / audit_data_completeness→v_price_kline_qfq / source_watermarks 删tdxhub_quote spec+akshare死primary) + gpcw config(seed raw_gpcw_dividend/mart_tdx_gpcw_auto retention / clients_registry candidate upstream) + feature_registry cross_sectional industry(dim_stock_tdx_industry GONE→*_tdx_l1_rel research_only_source_gap)。[C4 DONE] schema_core raw_tdx_f10_extra_parse_status CREATE块删 + schema_migrations 5行(index+4 ALTER)删 (表GONE+未声明=filter丢CREATE→index跑gone表风险, 同gpcw index类; schema-init smoke验 f10_extra不建/邻表完好)。[进行] workbench server_health view(guarded耦合contract test) / 测试fixture。**[修正] C3 source_watermarks kline_daily 过界回滚** (误删 tdxhub_quote spec 破 self-contained test; kline_daily watermark=M3 territory, table_missing优雅降级故留, CI offline 90回绿)。**loop-until-dry round1**: 0 live import已删模块/0 server_health调用/0 financial sync调用; 全包import体检发现 1 坏包 market_perception.regime_engine(缺.utils, 0 importer孤儿死=预存在reset残留非通达信全删, spawn_task交接)。[FLAG 低优先/inert/separate-track] schema_marts mart_tdx_gpcw_auto DDL(filtered从不执行) / tdxhub adapter capabilities(M3) / tdx_data_need audit子系统(live脚本) / 档B alpha metadata(panel_manifest/field_dictionary capital_flow_pit) / source_watermarks+audit kline_daily(M3 repoint tushare) / workbench server_health view(guarded耦合contract test) / _enrich_events_with_gpcw(实为live非死)。[FLAG 低优先/separate-track] schema_marts mart_tdx_gpcw_auto inert DDL(filtered从不执行) / tdxhub adapter capabilities(M3) / tdx_data_need audit子系统(live脚本) / 档B alpha metadata(panel_manifest/field_dictionary capital_flow_pit)。owner=workflow wf_8585638e + task#13/15/16。

- **2026-06-27 全清: 退役 build_fundamental_quarterly + 物删 fact_fundamental_quarterly (gpcw派生L1)**: gpcw派生 fact_fundamental_quarterly(60528行 L1)源已删不可重建+0 live reader(scoring/signals不读)+fundamentals feature组全production_ready:false inactive → 物删 build_fundamental_quarterly.py(dead builder, 唯一DDL+writer)+test + db_lifecycle_delete fact_fundamental_quarterly(archive+deletion_record) + 清config(data_layers/schema_versions/seed/test_tool_registry/feature_registry组source→[]保feature定义注档B重接tushare)。验:GONE+feature_registry load OK+data_layer_audit PASS+moth45。

- **2026-06-27 全清: 修 mart-lineage 子系统独立 bug (解 db_compact 阻塞 + 2 pre-existing红测试转绿)**: schema_marts 定义 mart_lineage(表)+mart_data_lineage(视图 FROM mart_lineage)+mart_strategy_result_registry(表) 但**未在 data_layers 声明 → filter_schema_sql 丢弃其 CREATE TABLE → 表从未物化 → mart_data_lineage 视图 invalid(依赖缺) → db_compact 视图重建卡死**(消费方 check_panel_lineage/check_registry_promote on-demand脚本)。修=data_layers 补声明 mart_lineage+mart_strategy_result_registry(infra) → ensure_mart_schema(live) 建2表(空) → 现存invalid视图变valid。验: 2表建(BASE TABLE)+视图live可查+data_layer_audit PASS(0stale/0untagged)+test_mart_data_lineage_compat 2过。db_compact 阻塞解除(留最后一次性缩盘)。

- **2026-06-27 通达信全删 单元6/7 xdxr/server 退役 (通达信 7 数据单元全物删完成)**: 物删 price_kline_tdxhub_adjustment_event(735行)+mart_tdx_server_health(117行)(market库, archive+deletion_record lifecycle_tdxhub_xdxr_retire_20260627)。退役 build_price_kline_tdxhub.py(dead builder 0caller)+mini_market.py(dead fixture 0消费方)+test_build_price_kline_tdxhub。移 PRICE_KLINE_TDXHUB_DDL(market_schema def+ensure_market_schema executescript + market_db/market_read import/__all__ + test_canonical/_setup; 防僵尸)。撤 update_watermark_sla xdxr SLA + seed mart_tdx_server_health + test_real_data 删xdxr测试 + test_tool_registry 清已删测试。tdx_source server_health DB函数(TDX_SERVER_HEALTH_DDL/ensure/load/record 仅builder调=dead)标RETIRED**不删**(与 live in-memory circuit-breaker[call_tdx_quotes_with_retry 用]共享helper, surgically删有炸通达信连接池风险; ensure仅builder调故不僵尸); workbench server_health view _relation_exists守卫优雅降级保留。验: 0残留+circuit-breaker完好+ensure_market_schema无僵尸+moth PASS45+data_layer_audit PASS(0stale)+CI offline 90过。剩=通达信客户端dead代码整段物删(financial_client sync body/tdx_source server_health/build_fundamental, 均RETIRED dead不僵尸)+akshare M4+mart-lineage独立bug。owner=analysis/tdxhub_full_retire_plan_20260626.md 单元6/7。

- **2026-06-27 修 main 上 K线测试红 + 揭真生产 bug (worktree 谬误澄清后, agent a69b 诊断)**: 用户问"K线worktree为啥blocked"→实测 git worktree list **无worktree**, 4 红测试就在 main HEAD (worktree=transient机制误入文档, 见 memory)。诊断3根因: **(根因1 真生产bug Fix A) `price_kline_qfq_tushare` 从不被schema-init建但canonical view硬依赖它 → `ensure_market_schema` 空market.duckdb必崩 (live DB被builder早跑过掩盖; M1 commit f5b41e71引入)** → market_schema 加 `PRICE_KLINE_QFQ_TUSHARE_DDL` 在view前建表 (IF NOT EXISTS, builder DROP+CREATE覆盖, 不碰数据); (根因2 Fix B) M3 efa6b971 退役 upsert_price_kline_tdxhub_rows(无条件raise)+物删price_kline_tdxhub 但没改测试 → 删2废弃tdxhub-upsert测试(test_market_db_canonical_kline)+1 e2e(test_kline_write_calendar_lint)+清import, 修test_canonical_kline_reads(移已删表insert); (我gpcw退役遗留) test_audit_financial 移 raw_gpcw_financial insert。验: K线测试 11 passed + CI offline 90 + moth45 + data_layer_audit PASS。**剩pre-existing非本范围red (报用户单独处理)**: test_audit_financial其余setup gap(dim_stock_dc_industry, CI-excluded重测试) + mart-lineage子系统(mart_lineage/mart_strategy_result_registry未声明致filter_schema_sql丢DDL=独立生产bug, 需strategy-promotion域context)。

- **2026-06-26 通达信全删 gpcw整体退役 (财务迁移已promote上线后, 用户"退役然后继续主线")**: 财务迁移 promote DONE(live dim/fact 已 tushare 正确值)。gpcw 簇物删 fan-in 审计: gpcw 摄取流水线全 dead(tdx_affair_client 0import / sync_financial_data 0caller / build_tdx_gpcw_auto_features 0caller 未物化), 唯一 live 消费方=signals_v2(raw_gpcw_detail D1/D3 过滤器)。**用户决"现在careful增量删完"**。**Stage1 DONE**: 切 signals_v2 gpcw 依赖(D1/D3过滤器优雅 no-op) + 删 data_access financial_gpcw entity。**sync确认全DEAD**(sync_financial_data/tdx_affair_client/build脚本 0 caller; daily财务sync走registry tushare)。**Stage2 DONE(代码/DDL退役)**: 物删 tdx_affair_client.py+build_tdx_gpcw_auto_features.py+profile_tdx_gpcw_fields.py(3 dead文件) + schema_core 删4 gpcw DDL块(保dim_data_source_priority/fact_feature_panel_candidate) + financial_client ensure_tables 去 raw_gpcw_financial DDL(防僵尸)+sync body标RETIRED(保_bootstrap/_parse_*共享工具) + db_health 去 gpcw REDUNDANT_INDEXES/WATCHED + test_financial_client(留2 calc测试删10 sync)+test_db_health(repoint fact_top10) + clients_registry/data_routes/data_module_members 清。验: ensure_core_schema/ensure_tables smoke(gpcw残留0,邻表保留) + 6财务/db_health测试+92广扫过(1预存在mart失败非我)。**Stage3 DONE — gpcw 7表物删完整收口**: 清 data_layers(删7声明)/storage_retention/seed/schema_versions config + 单元5 build_fundamental_quarterly 标RETIRED(保fact_fundamental_quarterly L1冻结) + **db_lifecycle_delete 物删7表**(raw_gpcw_financial 22769/raw_gpcw_detail 66736/raw_tdx_gpcw_wide 66736/dim_tdx_gpcw_field 580/dim_tdx_gpcw_field_semantic 580/mart_tdx_f10_capability_matrix 7/mart_tdx_gpcw_file_manifest 12; archive parquet冷备+deletion_record run_id=lifecycle_gpcw_retire_20260627) + DROP shadow验证产物。验: 0 gpcw残留 + data_layer_audit PASS(87表/0 stale_tag) + config_refs PASS + moth PASS 45/0/0 + schema-init无僵尸 + 财务/db_health 6测试过。**gpcw 簇 = GONE**。**CI修复 (物删后残留运行时gpcw引用)**: schema_migrations 删3 gpcw index(SCHEMA_MAINTENANCE_SQL; CI Stage2失败根因, Stage3删data_layers声明后filter过滤转绿但SQL仍残留; 注意删行整删不留`--`注释否则无;被;-split粘下句破filter_schema_sql) + audit.py 财务历史就绪raw_gpcw_financial→fact_financial_derived(修try块失败reset致daily审计误报财务覆盖0) + source_watermarks 删financial_gpcw_8q条目。验: CI offline 90过+smoke import+moth45。残留低风险follow-up: financial_client dead sync body + build_fundamental_quarterly 整段代码物理移除(标RETIRED不zombie); tdx_data_need_coverage/field_dictionary gpcw软引用(gates绿不阻塞); db_compact 缩盘回收156k行。owner=analysis/tdxhub_full_retire_plan_20260626.md。
- **2026-06-26 通达信全删 单元4 财务迁移 rewrite + 对抗验证 + contract BLOCKER修复 (shadow验证DONE, 待escalate promote)**: `financial_client.calc_financial_derived` 重写 SQL化, 源 gpcw快照→tushare周期模型(ATTACH tushare_raw RO, 源按(ts_code,end_date)去重[fina实测21467组重复], 写 fact_financial_derived[74192行]+dim_financial_latest[5202行])。**两真金白银修复**: roe←`roe_yearly`(年化, scoring绝对阈值0.18/0.10为年度标定, 旧累计Q1≈1/4压最低档); gross_margin←`grossprofit_margin`(毛利率%, 非gross_margin金额陷阱=gpcw错根因)。**对抗验证 workflow wuxnownvm(3lens)**: A独立重导12股×15列=0 mismatch; B消费方(scoring/screening/stock_stage)**零代码改**(表名不变, codegraph impact仅3符号)+财务分向正确(gross中位 0.755 garbage→0.242); **C抓 contract_to_revenue 期间口径混合 BLOCKER**(contract_liab时点÷累计YTD营收, 多数股落Q1虚高4.5-6.8x, 茅台落FY口径掩盖, scoring压0分33%)。**BLOCKER已修(FY-restriction锁年报期1231)**: 茅台0.047不变/601628 70.93→10.36(与验证者独立FY值精确吻合)/压0分33%→10.5%。单测+2(12过)。可接受限制: 亏损股ocf NULL(语义正确)/银行无毛利率/roe_yearly季节失真(PIT权衡)/holder环比窗口不等(MEDIUM defer)。owner=analysis/tdxhub_full_retire_plan_20260626.md。
- **2026-06-26 通达信全删 单元4 值比对 — 救出 gpcw 财务数据质量问题 (真金白银级发现)**: 用户坚持"先gpcw-vs-tushare值比对"。balancesheet 回填 DONE(117084行/4987股/contract_liab非空111226)。fina_indicator 扩2020受阻=**API现完全不返update_flag**(grain依赖, 既有表有=漂移, 独立live bug须修grain)。**值比对 spot-check 茅台坐实 gpcw 财务数据错**: gross_margin gpcw=8.7% vs fina=91.2%(真实~91%高端白酒); 根因 gpcw revenue字段虚高~15x(显示1.28万亿/实际~1700亿)。net_margin/debt_ratio gpcw≈fina(巧合/接近)。**=> 当前财务打分用的gpcw gross_margin是错的, tushare正确; 迁移反修复数据质量但显著改scoring/screening输出(向正确)**。单元4 redesign 真复杂度: gpcw有质量问题须以tushare为准 + fina update_flag须先修 + 快照→周期重设计 + 改财务打分值须escalate用户。值比对正是物删前救出真相(真金白银安全网)。owner=analysis/tdxhub_full_retire_plan_20260626.md。
- **2026-06-26 通达信全删 单元4 财务迁移 — 值比对prep揭示模型重设计 (注册 balancesheet 域)**: 用户选"回填balancesheet保留contract_to_revenue + 先gpcw-vs-tushare值比对"。prep实测揭示**财务迁移=数据模型重设计非字段重映射**: (1) balancesheet API 要 ts_code → 域改 by_ts_code(同fina_indicator), 注册 backfill 2020+ 进行中(实弹核证茅台contract_liab 2026Q1=30.27亿); (2) **fina_indicator仅2023+**(gpcw 2020-12-31~)→ 须扩到2020否则丢2020-2022财务; (3) **gpcw快照模型**(report_date=F10抓取日'2026-04-07'非季末)**vs tushare周期模型**(ts_code×end_date季末)→ calc_financial_derived(:1554) snapshot-YoY ≠ period, derivation链须重设计; (4) 单位差 fina roe=%(25.0) vs gpcw分数(0.25)须归一。**=> 单元4=真金白银财务链重设计(2源by_ts_code backfill到2020 + derivation重写 + 值比对 + 验dim_financial_latest 7消费方), 聚焦工程项目**。值比对prep正是物删前救出真复杂度(真金白银安全网)。owner=analysis/tdxhub_full_retire_plan_20260626.md。
- **2026-06-26 通达信全删 Batch2 DONE (单元1 增减持意向, 物删1表, 用户拍板覆盖归档)**: 物删 `fact_shareholder_plan_tdx_f10`(30769行)→ archive+deletion_record(run_id=lifecycle_tdxhub_unit1_20260626)。增减持意向(target/progress/reason)无 tushare/aif10 等价(都实际成交非意向), 用户知情接受永久丢失 + 覆盖早先"归档冻结保留"决议。fan-in全清: schema_core DDL块+schema_migrations 2索引 + feature_registry 删7声明特征+组级source_tables(8处) + test_feature_registry 删断言 + data_layers/retired note/seed_dim(archived→deleted)。7特征仅声明未populate无LIVE消费。KEEP: fact_shareholder_plan/_trade(旧reset表不同). 验证 schema smoke+test 2绿+data_layer/config_refs PASS+lineage155表+moth45/0/0。**剩单元4/5(财务簇需balachesheet backfill)/6+7(xdxr/server子系统SLA门)**。
- **2026-06-26 通达信全删 Batch1 DONE (单元2户数 + 单元3十大股东raw, 物删3表)**: 用户拍板户数深史物删后执行。物删 `raw_tdx_f10_holder_research`(45430行)+`raw_tdx_f10_holder_count_history`(903435行)+`fact_holder_count_period`(277560行, deprecated tdx户数表) → archive归档+deletion_record(run_id=lifecycle_tdxhub_batch1_20260626)。fan-in 全清: schema_core 删3 DDL块 + schema_migrations 删4索引(防僵尸§4.5) + audit.py neuter research freshness读 + data_layers/storage_retention(bounded+protected+inventory 3处)/coverage/panel_manifest upstream/seed_dim(2行删+2 deprecation→deleted)/data_routes 串。**KEEP不动**: fact_top10_holder_period(100% aif10派生)+fact_common_major_holder_stock。验证: schema-init smoke(建31表/删3表0 in DDL/KEEP表在) + data_layer_audit PASS(DDL+DB+config一致) + config_refs PASS + lineage重生(156表/3表消失) + moth45/0/0。**剩单元1(增减持,feature_registry纠缠)/4(财务簇需balachesheet backfill)/5(F10元数据共享writer)/6+7(xdxr/server子系统)**。
- **2026-06-26 通达信全删迁移计划 (用户决议+对抗验证, owner=analysis/tdxhub_full_retire_plan_20260626.md)**: 用户"通达信全删用妙想找替代"。ultracode workflow wjk2g1yl7 (15 agent) 7单元 fan-in 审计+对抗验证。**对抗抓4翻案(原审说安全实则炸)**: 僵尸表复活(server_health 被存活 builder CREATE IF NOT EXISTS 重建)/越界掐 L1 写路径(build_fundamental_quarterly 是域外 fact_fundamental_quarterly 唯一writer)/orphan测试崩CI/漏第二JOIN消费点(macd_optuna:121)。**替代映射**: tushare优先(户数→stk_holdernumber/复权→adj_factor/财务→income+fina_indicator)/妙想aif10补F10(十大股东已100%aif10)/无等价则删(server_health/F10元数据)。**2 不可逆损失用户已拍板物删**: 增减持意向(tushare/aif10都无"意向"等价, 覆盖早先"归档冻结保留"决议)+ 户数1997-2017深史(tushare仅2018+, 0 live消费方)。**7单元全授权物删**, 按 Phase A(可逆code-fix)→B(验收)→C(物删escalate-已授权)。复杂度: 全表有schema_core+schema_migrations DDL(不清=僵尸§4.5)+ 单元4需balancesheet backfill(contract_to_revenue断源)+ 单元6/7=xdxr/server子系统退役(撤preflight SLA门)。**执行 checklist 见 plan 文档**; 逐单元谨慎(清fan-in→删DDL→db_lifecycle_delete→验门→commit), 非快批。
- **2026-06-26 macd_episode GT outcome 防误接守门 (PIT 审计唯一隐患 + GT契约抽公共)**: PIT 审计 (workflow wecndwk2n) 标的唯一隐患 = `fact_macd_episode_ground_truth` 的 forward outcome 列 (peak_gain_pct/peak_offset_days/max_dd_pct) 0 消费方但若未来误当训练 X = HIGH leakage。建 `backend/config/macd_episode_gt_columns.yaml` 列角色契约 (entry_anchor=event_date / label=is_win / outcomes_forbidden_as_x / meta) + **抽公共 `services/gt_label_contract.py`** (rally + macd 共用加载/执法, CLAUDE §3 同逻辑2次抽公共; rally_labels.py 委托保 API 不变) + moth `macd-episode-gt-outcome-quarantine` 硬门 + 6 单测 (macd 守门 raise + rally 委托不破)。下游训练只许 JOIN feature_panel 的 PIT 因子, assert_no_outcome_leakage 守。moth 45/0/0。
- **2026-06-26 血缘中枢 T3 修复 — dead-detection 根治假死 (Gap1 W2 暴露的工具缺陷, owner=services/lineage)**: (1) **dead_tables Occam根因修** (query.py): 有 acquire 边(vendor同步源)或 raw_ 前缀 = L0 源永不"死"(retention=permanent re-sync重建, 能删必删只适用派生表) → `lineage dead` 19→0, 16 raw_ 假阳性(income/trade_cal等)全消; (2) **T3-a entity consume 边** (builder.py `_git_grep_entity_consumers`): SERVE entity 别名消费(`DataAccess.get("holders_top10")`)对表名 grep 不可见 → 并入 consume 边(set去重, over-match偏保守=删除决策更安全), `impact(raw_tushare_top10_floatholders)` 现正确含 dossier.py; (3) **`_`前缀瞬态锁表排除** (builder + data_layer_audit): pipeline_lock 的 _lock_probe/_rw_probe 建/即删, build/audit 偶遇致 graph.json 非确定性(drift门flicker)/moth false-FAIL → 两处均排除。T3-b(全库扫描)/T3-c(多表脚本)实为 workflow agent 手工审计误判非 lineage 工具 bug(`_live_tables_by_db` 本就扫全6库), 留手工审计 caution。验证: 11单测(+test_dead_excludes_l0_acquired_table 等2新)+ 确定性连跑2次一致 + moth44/0/0。
- **2026-06-26 Gap1 收口 — PIT审计0泄漏 + 物删3真死表 + 暴露血缘4缺陷 (ultracode workflow wecndwk2n, 21agent)**: 用户授权"删无用数据/流程"。**W1 PIT堵漏**: 6 个 build_ 成员(segment_panel/signal_panel/macd_state/rally_gt+entry_pit/rally_neg/macd_episode_gt 直读canonical)逐个对抗验证 PIT 锚 → **全 pit_clean survives, 0 真泄漏**。对抗 agent 挖了 numpy 负索引 wraparound / qfq f_latest 尺度不变性(金叉布尔 c=1/f_latest 同乘不改不等号)/ warmup 窗 / outcome 隔离 —— 全守住; controller 独立核 sma/range_pos/stage 全 trailing-causal 一致。唯一隐患=macd_episode GT outcome列(peak_gain/max_dd/is_win)0消费方但若误当X=HIGH, 标黑名单防误接。**W2 物删**: 血缘 dead 19表 **16假阳性**(L0 raw 经视图/SERVE间接消费 grep漏判→KEEP, 含income利润表/trade_cal灾难红线/top10_floatholders经holders_top10 entity), 仅 3 真死工件物删(mart_etf_sector_rotation 20行/mart_etf_strategy_comparison 7882行/mart_kline_gcs_sync_run 1行, archive归档+deletion_record留痕, run_id=lifecycle_gap1_deadclean_20260626)。**对抗验证翻案2错误提案**: build_price_kline_tdxhub.py 不可删(是存活表 price_kline_tdxhub_adjustment_event[735行,xdxr热备§4.3,preflight SLA门消费]唯一writer); build_executive_trade_events 提案查错库(称fact已删, 实活smartmoney 67920行)。janitorial: 清 duckdb_connect_policy 孤儿行 + panel_manifest sync_script repoint qfq_tushare + migration doc校正。**血缘中枢4 T3缺陷(假死根因, owner=services/lineage)**: T3-a不追SERVE entity/视图 / T3-b只扫market非全6库 / T3-c多表脚本一表死≠脚本死 / T3-d watermark/SLA门算消费方。验证 moth44/0/0+data_layer PASS。**剩 needs_review 4项**(executive_trade物删/dzjy→block_trade/lhb_event保鲜/tdx_gpcw簇)走M4逐表定夺。owner=workflow wecndwk2n action_plan。
- **2026-06-26 Gap1 不变量4 闭环 (roster补全→消费者绕过0 + 闭环06-25预存债)**: 实测纠正 stale 清单 (serve_bypass_inventory.md 列 P0=data_loaders/build_segment_panel/build_signal_panel 早已迁/入成员; build_ 前缀=加工成员非违规)。`--bypass-scan` 实测当前 4 非成员绕过**全是 roster 漏登的合法成员**, 非真泄漏 (architect rule7 验证器: 证据反常先核): ① `lineage/builder.py`= **我建 M5 引入**, 读 information_schema 元数据非 PIT 数据 → 入 `member_dirs: lineage/`; ②③④ `lhb_client/org_holding_aif10/qfii_client` = 采集层 sync 路读自身 raw watermark/count (acquire 行为) → 入 `member_service_files` (vendor 迁移属 task#56 正交)。修后 `consumer_bypass_violations=0` (moth `serve-consumer-bypass-zero` 棘轮真绿)。**同闭环 06-25 标记预存债** (PROJECT_INDEX 行下 "moth ... 预存债非本次引入"): data-layer-integrity 4 untagged — `_lock_probe/_rw_probe` (0行无源码引用=§9锁测 transient scratch 残留) DROP; `mart_today_signal_cache(_signal)` (signals_v2 serving 缓存, routers/signals.py 服务UI) 声明 `display` 层。验证: data_layer_audit PASS(0 untagged) + **moth 44/0/0 全绿** + 8单测 + config_refs PASS。**教训沉淀**: 降级模型长上下文期间中间 rg 输出被串改 (`--bypass-scan`→`--n` / `check_serve_read_layer.py`→`ln.py` / `mart_today_signal_cache`→`ln`), 我据此误判 moth 门"死门失效"差点去改本正常的验证器 — 读全权威文件 (cat/git diff) 才救下 (mythos§14 + architect rule7: 改验证器前必读全文件非信中间枚举)。owner=data_module_members.yaml + serve_bypass_inventory.md(已 stale 待刷)。
- **2026-06-26 M5-T2 血缘路由中枢 (字典+总指挥, 用户拍板"先造")**: 架构师 re-anchor 蓝图后定"第一步未完成"=M5血缘中枢(North-Star)0建。建 `backend/services/lineage/` (model 确定性序列化 / builder 缝合器 / query)：**acquire 边**←sync_registry 42源.api→target_table+PIT锚; **consume 边**←确定性 `git grep -w` fan-in(词边界天然处理前缀碰撞, mythos按值grep非agent枚举); **表节点**←information_schema 6库(跳alpha158/experiment_store); **layer**←data_layers。`backend/scripts/lineage_cli.py` → `chunkyctl lineage build|impact|provenance|dead|show`; `data/lineage/graph.json`(472节点[162表/42源/268消费方]/1191边); `check_lineage_drift.py`(剔generated_at, 连跑2次图体逐字一致, safe_commit Step3.96 **informational WARN非block**—consume边随任何引用文件漂移, 硬闸排T4转正门); 10单测(合成图测query+真实build确定性+killer集成)。**killer 已验**: `lineage impact <table>` 删/迁前自动 fan-in(替代手grep, 根治本session tdx迁移反复手工漏判LIVE消费方的痛—impact(raw_tushare_fund_daily)=5消费方/etf_price_kline_qfq_tushare=8); **dead 检测 19 张已落库未用表**(mart_etf_sector_rotation等, 停采候选/档B待挖)。owner=analysis/data_lineage_routing_hub_design_20260624.md(状态升T2 DONE)。余 T3字段级transform/T4 display+domain+drift硬闸/§8前端图。
- **2026-06-26 §9 reference拆库 ultracode 对抗验证 (workflow wxs0iyxin 8agent/670k + controller 2进程锁实测)**: 用户 ultracode 授权 de-risk §9。**前提裁决成立** (锁实测 A: smartmoney RW 不阻塞 reference RO ATTACH 读=主痛 facts写vs universe读真解耦; B: dim写vs dim读残留但罕见短可接受; 推翻对抗lens"拆库无用只搬家"的 overstate)。但 3-lens 对抗验证**全 plan_sound=false** —— **§9 不是搬4表是"4套并行连接模型收口"** (get_conn/直连duckdb.connect/注入conn/bestchoice _attach_smart_db AS sm), 原计划 view+ATTACH on get_conn 会在 ≥8 处炸生产: B1 bestchoice/compute.py JOIN sm.dim_active_a_stock (第4套连接不挂reference, 实测 schema-error, 计划完全漏) / B2 schema_migrations:291 CREATE INDEX on view 硬炸 / B4 5+直连audit脚本 / B5 注入conn一大类未枚举 / B6 duck_adapter attach 吞异常(§4.4, L160-166) / 磁盘污染 / B7 读触发写(get_active_a_stock_codes cache过期→refresh写dim) / B8 check_universe_filter闸 / B9 删除工具db_lifecycle名未确认。修正序: Phase 0 连接收口(真前提, dual-write中间态安全不切view)→Stage C切换→Stage D验收(测对竞争: 写reference+ATTACH 非假绿backfill+seed, 含bestchoice链+全pytest)→Stage E物删escalate。机制 alias-routing(resolver, 最Occam但仅覆盖SERVE) vs view+ATTACH 收口后定。第一性原理留: 拆库 vs 仅隔离universe读连接 哪个更Occam, Phase 0 实测定。owner=analysis/section9_reference_split_verified_plan_20260626.md。**= 高blast 焦点session 待用户拍板执行窗口**。
- **2026-06-24 删旧 updater (20文件路由DAG, 用户"直接删+UI可重做全是补丁", 增量进行中)**: 旧 updater(routers/updater*.py, FastAPI UI 路径)做获取+清洗+加工+存储 = 用户感"太复杂耦合深"; 实测 daily_update 新 pipeline 已不用它的加工/calc 步(纯UI)。删它前须 (a) 解耦 4 复用点 + (b) 迁 updater 独有但 pipeline 没接的 LIVE sync 步 (先迁后删, 用户拍板)。
(b) **[DONE] sync 步迁 pipeline**: lhb(搬 lhb_client.sync_lhb_incremental)/ aif10 valuation_quantile+peer_valuation(Step2i3 调 sync_capability, v3_picture消费)/ QFII(Step2j 搬 qfii_client.sync_qfii_incremental); forecast_consensus 不迁(已deprecated走profit_forecast)。qfii real-test 跑通。**发现既有问题(非本次引入)**: raw_aif10_valuation_quantile(93011行/v3_picture在读)缺 UNIQUE/PK 约束(疑 COPY FROM DATABASE 丢, mythos§12)→ sync_capability upsert ON CONFLICT 炸 → valuation/peer refresh 一直停(数据保留未刷新), 迁移步 degraded-graceful; 约束重建=follow-up。**(c) [2026-06-24] org_holding aif10 接 acquire Step 2j2**: `_sync_org_holding` 调 `org_holding_aif10.sync_org_holding_incremental` (季度增量, 水位=最近足量披露季度末已有则跳, 照 _sync_qfii 模式) → 写 `raw_org_holding_aif10` (非公募机构持仓分桶, 复核确认真·独有 gap)。
(a) **[DONE] 物删旧 updater UI 簇 22 文件**: routers/updater*.py(20)+etf.py+data_sources.py git rm; main.py 3 挂载去除(updater/etf/data_sources)。爆炸半径实测=仅 main.py(全 leaf, 无 service/幸存router依赖)。etf/data_sources 也删(它们建在 updater UI 基础设施[日志/DAG]上, 用户"UI可重做")。2 个过时 test(test_updater_registers_sync_lhb/qfii)改测 pipeline 接线。**验证**: main.py import OK(81 routes)+ 全 backend 包 import 0坏 + daily_update --dry 4段跑通 + lhb/qfii 17 test 通过。**PK bug [DONE]**: raw_aif10_valuation_quantile/peer 重命名→重建带 PK→重抓 fresh(93021/5608行, 约束=PRIMARY KEY), valuation/peer refresh 恢复, _bak 已清。**[DONE] 孤儿 tdx F10 client 清理**: git rm tdx_f10_extra_client.py + update_tasks.py + ingest_holders_tdxhub.py(旧 updater 唯一 caller 已删=dead, 0 live importer)+ 2 过时 test + clients_registry 条目; 全 backend import 0坏/main 81 routes。**剩余**: 增减持 backfill(运行中)完 + tdx 产品(户数/增减持)重指向 data_quality/registry consumers→tushare 表 + 退役 tdx 产品表 + daily_update 真跑验证。剩余: 解耦②③④ → 去 FastAPI 挂载 → 删20文件+tdx F10 client(tdx_f10_extra_client/ingest_holders_tdxhub)+raw表 → daily 验证。增量做不 big-bang。**产品3 同大股东个股决议**: 不迁不丢 → 机构档案"某机构持哪些股"实测可从 aif10 holder 数据 holder_name 分组 derive(香港中央结算持3462股/社保2442/摩根343), 退役 tdx 产品, 用例 derive。
- **2026-06-24 tdx F10 多产品迁移 (用户选"迁tushare/aif10后退役", 进行中)**: 退役 tdx F10 客户端 = 它写的 3 个有数据产品须先迁: ① **股东户数** fact_holder_count_period(277k)→tushare `stk_holdernumber`(域已注册, backfill 中); ② **增减持** fact_shareholder_plan_tdx_f10(30k)→tushare `stk_holdertrade`(本次注册 by_ann_date 域, 实测71行/期; backfill 队列, 注: tushare 是实际增减持交易 vs tdx 是计划, 语义近似); ③ **同大股东个股** fact_common_major_holder_stock(76k, 有 peer_stock_code= 一致行动人/关联股关系, **非十大股东**)→ aif10 候选 RPT_F10_EH_RELATION(待核)或 §4.3 无等价丢弃(无 live 派生消费方)。3 产品**消费方仅 data_quality(审新鲜)+clients_registry(元数据), 无 live 策略/特征消费**。tushare top10_holders 实测 0 行(同 top10_floatholders 季中滞后)故大股东不用它。backfill 串行(同写 tushare_raw.duckdb 单写锁)。**剩余**: 串行 backfill → 重指向 infra 消费方 → 退役 tdx 产品 → 退役 tdx F10 client/raw 表 → daily_update 验证。**[2026-06-24 现状校正]** 实测后三表归宿明确 (详 CLAUDE §4.3 + master plan §1.6): ① 户数 fact_holder_count_period → tushare stk_holdernumber (迁移); ② 增减持计划(意向)fact_shareholder_plan_tdx_f10 + ③ 关联个股网络 fact_common_major_holder_stock (94% peer≠self=主要股东跨公司持股网) → **归档冻结** (aif10 SHAREHOLDER_CHANGE=实际变动非意向 / MAIN_ORGHOLDDETAIL=机构持仓≠跨公司网络 / RELATION=实控人单关系, 均无源可重接; 仅浅史无 live 消费方)。另起 aif10 机构持仓明细 = NEW 源 (org_holding_aif10, 真 gap, 见上表)。**[坑 mythos§10]** stk_holdernumber by_ts_code 回填漏传 start_date → tushare 只返最近~8期 → 94min 回填 0 净新增白跑; 修 sync_runner backfill 注入 start_date=data_start (test_sync_runner 防回退)。**[2026-06-24 DONE 3表收尾]** 户数回填2019+全史(284951行/5013股, bug修复后); 3表全0真实消费方→ data_quality freshness 剔除 + dim_data_asset 标记 (户数=deprecated→raw_tushare_stk_holdernumber, 增减持意向+关联网络=archived 保留唯一数据)。户数物删暂缓(pre-2019唯一+0消费, 待用户确认)。**[2026-06-24 DONE 2表triage收口]** fact_fund_holding_tdx_f10(reset已物删17145行)/fact_shareholder_trade_tdx_b(孤儿空壳无写入)→ 彻底退役: 删源DDL(schema_core CREATE+schema_migrations idx/ALTER, 断重建路径)+ 退役整个已死的 `_check_tdx_f10_source_availability` 机器(3检查对象全死: 2表不存在+plan_table=None+initial mart无builder不在DAG; 5单测中4已红=前会话半退役债)+ 删5死测试 + clients_registry死builder元数据清退役表名。无迁移(无live消费方+无数据)无归档(无数据可冻)。验证: schema init端到端实测2表absent/2归档表present(活层)/无错 + test_global_data_quality 31绿 + test_workbench_read 13绿。owner=analysis/tdx_f10_freshness_retire_20260624.md。**flag独立后续 (task_61004e2b)**: 前端workbench tdx_f10_source DQ视图(查死domain优雅空降级)+ mart_shareholder_plan_initial_event死家族(6表全不存在+builder/test文件全删)。**[2026-06-25 后端registry/config子集 DONE]** 清 schema_versions(6死表版本登记)+pipeline_performance_policy(2死builder预算)+test_tool_registry(2全悬空块, test文件全GONE); 亲核6表duckdb实查全不存在+builder/test ls全GONE; C2 legacy-flow gate PASS+import OK+YAML合法。**剩(产品面)**: ②shareholder_plan读层(死表全不存在, 独立dead, 焦点pass可删: workbench_research_read/shareholder_plan_*_read+seed_dim_data_asset+前端JS+contract/smoke fixture)。**[2026-06-25 单元2 DONE — shareholder_plan死家族整体退役]** workflow wl175iv85 审计+对抗复核(safe_to_execute=true): 6死mart(initial_event/initial_feature_panel/_quality/feature_family_eval/family_walkforward/_summary, 7库实查全不存在+4builder已删commit 639e0dfb)整族退役 — git rm 3 read-service(workbench_shareholder_plan_read/_initial_read/_family_eval_read) + workbench_research_read解线(import+3 dict key 同commit防/research 500) + research_meta_read删3死表名 + seed_dim_data_asset删6死mart×4dict(EXTRA_WRITER/UPSTREAM/FRESHNESS/ASSET_CONTRACT) + 前端JS(renderResearchView面板+buildResearchModel字段+9个renderShareholderPlan*函数) + 测试(read死mart fixture+断言/render_smoke死块+纯度断言/frontend_contract 2死字符串断言/pipeline_performance_policy 2死budget=修预存RED)。**禁删保留**: feature_registry.yaml 7个LIVE shareholder特征(读KEEP源fact_shareholder_plan_tdx_f10)+test_feature_registry + legacy fact_shareholder_plan + _tdx_f10归档contract。实跑核证: build_workbench_research死key除/LIVE key在(11)、build_workbench_data_sources(14)、seed_dim import OK=无500; 38测试绿(含修复的RED)。**[2026-06-25 ①深挖纠错: 不是删2死视图, 是stale子系统级决策]** ①tdx_f10_source视图(build_tdx_f10_source_dq_view+build_f10_source_date_audit_view)属**整个 workbench global-DQ-detail 子系统**, 该子系统由 `record_global_data_quality_gate` 喂 (写 mart_global_data_quality_gate[128行/最新2026-05-12]+mart_global_data_quality_detail[全库MISSING]), 而**该机制不在当前pipeline**(现 daily 用 run_post_sync_audit→data_audit_latest.json 是另一套)→ 整个 data-sources DQ-detail workbench 面 stale(最后喂数2026-05-12)非仅tdx_f10。**=子系统决策(重接workbench DQ到run_post_sync_audit OR 整体退役DQ-detail面), 非自主piecemeal删, 待用户拍板**。改前充分审计救下错删(上轮审计图把stale子系统误标"2死视图")。**[2026-06-25 单元1 DONE — 仅退最窄真死片 tdx_f10_source_dq]** workflow wl175iv85 审计+对抗复核(对抗抓审计漏判: frontend_contract L273-276 是 LIVE 字符串契约断言, 盲删 JS 会留红): tdx_f10_source_dq (查死domain 'tdx_f10_source_availability', 写入方 commit 7646b316 已删 + 读表 mart_global_data_quality_detail live 不存在 = 永空) 整链退役 — build_tdx_f10_source_dq_view删 + import链解(tdx_read/data_source_read) + dict key + 死表名 mart_tdx_f10_source_dq_detail + 前端JS(renderTdxF10SourceDq/buildTdxF10SourceDateDqModel/section/export) + 5处测试(read setup+3断言/render_smoke fixture+子串+纯度测/frontend_contract 4断言)。**保留 build_f10_source_date_audit_view (不同LIVE函数) + 整个 DQ-detail 子系统(global gate机制)决策仍待用户拍板**。35测试绿+JS语法OK。(注: moth serve-consumer-bypass=3 + data-layer-integrity untagged mart_today_signal_cache×2 = 预存债非本次引入, 已 stash-baseline 核证)。**[DONE] daily真跑验证** (4段+holder/估值/qfii/org_holding sync全执行). **[2026-06-24 DONE data_audit cry-wolf triage]** 3 个曾 FAIL 逐个真数据核证 (owner=analysis/kline_completeness_crywolf_fix_20260624.md): ① **kline_completeness** 真cry-wolf已修 — 旧口径 clean比全交易日历(假设每股每天交易)→ 停牌/退市股误报1711股/31.5%; 正解 clean-vs-source(M2无损职责, 实测0丢失行)→ PASS。② **kline_consistency** 真cry-wolf已修 — 移calendar-gap判定(停牌合法)保留dup+非交易日行 → PASS。③ **cross_table_consistency "209 codes not in universe"** = 第3个cry-wolf已修 (我一度误判为survivorship gap要补dim表, 用户纠正→回universe.py真相源): universe.py明示"**不需要dim_all_ever_listed**", universe=K线近90天有交易+前缀白名单+非ST三规则硬控(assert_universe_clean), 退市股数据本在K线史(survivorship正确)由规则3 PIT排除, **不靠dim表枚举**=不存在漏退市bias。旧口径"kline⊆dim表"违第一性原理+重造第二套universe判定; re-point到services.universe.classify_exclusion(前缀身份单一真相源), 退市A股(合法板块)pass, 仅北交所/三板/指数leak进A股K线才flag→PASS。**data_audit 现 7/7 PASS** (3 cry-wolf全消灭, 门恢复可信; 不补任何退市数据/builder不动)。 **[2026-06-24 §9 reference拆库 Stage A DONE]** `migrate_reference_db.py` 保真建 `data/reference.duckdb` (dim_active_a_stock/all_ever_listed/listing_status/trading_calendar 4表, 5件套验收PASS, smartmoney未动可逆) + `database_manifest` 注册 reference alias; Stage B-E(get_conn ATTACH+view切换+写方repoint+sync_runner读reference+物删)=高风险动get_conn中央工厂(33消费方)焦点执行待做 (计划 owner=analysis/data_module_architecture_20260624.md §9.5)。
- **2026-06-24 十大流通股东主源改 = 东方财富妙想 aif10 (用户拍板, 破 §4.3 例)**: 实测裁决 tushare/同花顺/tdxhub 都缺季中 ad-hoc 权益变动期(600388 紫金入主龙净 6/8 这条 tushare 只到 3/31 滞后~4月; 同花顺 MCP 也只季末; tdxhub F10 有 6/8 但仅~4期史浅)。**东财妙想 aif10 三维全胜**(全市场 + 含 6/8 + 2003史深 + 结构化 + 变化100%)。抓取实测对比: aif10 datacenter = 干净 JSON API, requests 直连 0.43s vs crawl4ai 浏览器 3.33s(慢7.7x+JSON包进574KB DOM)→ **不用 crawl4ai(JSON API 上纯降级), crawl4ai 留给将来抓公告/研报全文(JS子域)**。东财妙想 skill api(用户有 em_ key, 存 .env EM_MIAOXIANG_TOKEN)= 交互式投研工具配额限制, 不做批量源。**新代码(按新 pipeline 分层 获取/清洗/加工/存储)**: `services/holders_aif10.py`(_fetch_raw获取 / _clean清洗字段映射+K线范围 / _derive_exits加工period-diff退出 / sync_holders_aif10存储 + sync_holders_aif10_incremental 按披露日增量) + `scripts/ingest_holders_aif10.py`(薄CLI backfill) + pipeline `acquire.py` Step2i2 `_sync_holders_aif10`(范例=institution_survey) + test_holders_aif10 6测。**退出行**: aif10 当期快照无显式退出 → period-diff 推导(上期在榜/本期不在=退出, change_status='退出', is_exit_row=True)跟踪机构投资周期(用户目的, 实测 600388 6/8 香港中央结算+中金离场)。**历史对齐 K线**: 只回 20181231(price_kline 2019-01-02 起), 不抓更早(用户)。写 fact_top10_holder_period source='miaoxiang' tier=1, 30消费方读表名不变零改。**PIT 修复(真金白银)**: miaoxiang 行设 `availability_source='page_update_date'`(披露日=可用日锚), 否则 event_engine 按 availability_source 排序会把 NULL 行压最低优先级 + PIT 可用日退到 fetched_at 算错; 退出行可用日=当期披露日(获知离场那刻)。**增量规则(用户讨论对齐, 水位驱动)**: 水位=存量 MAX(披露日 page_update_date), 扫 UPDATE_DATE>=水位-7天 的股→per-stock抓全期→退出推导→幂等覆盖。盯披露日非报告期(报告期新必带新披露日=盯披露⊇盯报告期; 东财修正旧期刷披露日但报告期不变=盯报告期漏修正→披露日充分; 实测每期披露日>=报告期); 水位自适应手动不规则运行(间隔越长回退越远不漏)。写入优化: 单股 (stock,source) 一次删替 per-row(backfill 1.4M→5208次, 0.84s/股~75min全市场)。**[DONE] backfill** (5189股/172.5万行/32.8万退出/49min, **覆盖率99.6%**, 缺19=次新股aif10 0行自愈) + **物删 fact_top10_holder_period 的 tdx_f10 行(594k)** (fan-in 核无 source='tdx_f10' 硬过滤 + smoke PASS, 现 miaoxiang 单源消除双源重复计数)。**[DONE 2026-06-24] 修 asset metadata bug**: seed_dim_data_asset 把 live `fact_top10_holder_period` 误标 deprecated(旧2026-06-22"切tushare top10"计划残留)→ 改 active+源=东财妙想aif10; raw_tdx_f10_holder_research replacement 改指 fact_top10_holder_period; re-seed 验 status=active。**[REVIEW-followup, 物理删 defer 原因=纠缠]**: ① tdx F10 代码织进 **live updater 路由 DAG**(`tdx_f10_extra_client.py` 被 updater_sync 调且**还管股东增减持计划**非只 holder; `ingest_holders_tdxhub.py` 被 update_tasks/updater_sync import)→ 物删=路由层手术+处理股东计划产品=mythos"禁big-bang"(已不在 daily pipeline=休眠, 仅手动端点); ② tushare top10 域 by_ann_date 代码与 fina_indicator report-period helper 共享 + raw_tushare_top10_floatholders 表实测**不存在**(域注册未backfill); ③ audit.py 读 raw 表已 try/except 优雅降级。结论: 物理删=须 review 的增量解耦, 不自主莽删; raw_tdx 暂留作恢复网。owner=services/holders_aif10.py + analysis/miaoxiang_aif10_source_decision_20260624.md。
- **2026-06-23 十大股东增量改 by_ann_date (按公告日抓最新, 修谄媚死)**: 根因 — by_ts_code 域 --all-due 被跳过 + --resume 跳整股 = 存量股新更新永不补。**关键实测: top10 有 ad-hoc 非季末更新**(600388 报告期 20231011 / 全库 1810 非季末期/2902 股 = tushare 把事件触发快照以非季末报告期给出, 已加工好含新进/退出/增减持), 故我先做的"按季度截止日 by_report_period"**错**(漏 ad-hoc), 已弃。改 **by_ann_date** (sync_runner: `_calendar_days` 全日历日含周末 + by_ann_date batch_mode + page_limit 分页 + drain wiring): tushare 支持 ann_date 查全市场, watermark=MAX(ann_date) 增量前向抓新公告日, 覆盖季报+ad-hoc。现存数据已完整(实测 600388 与 tushare 一致到 20260331)。fina_indicator 不可按 ann_date 查(per-stock interface)保留 by_report_period(misses restatement)。11 单测+门全过+moth44/0/0。**教训: tushare 已加工好, 我只需按公告日取最新+MERGE, 别自造季度逻辑反复横跳**。owner=sync_runner.py。

- **2026-06-23 既定倒推方案按现状细化 (用户: 删源是为达成方案不变量4, 非脱离方案)**: data_module_toplevel_design §1.6 新增 — 根(4不变量)+3件(清洗/加工/展示)现状全 DONE; 编排层=daily_update四阶段管线 DONE; **删源 worklist 锚到不变量4(单一真相源)**: 简化规则(tushare有就用+删旧/没有就删, 删"双轨"仪式)+ per表状态(通达信/旧K线 DONE / holders待迁consumers / 财务簇gpcw+aif10+akshare残留待迁); 编排器剩 by_ts_code增量+每节点门(AcquisitionPlanner)。owner=analysis/data_module_toplevel_design_20260622.md §1.6。
- **2026-06-23 数据底座架构 v2 (= 顶层设计 §1.5.2 编排层 operationalized, 两模块解耦 + 采集规划器)**: owner=analysis/data_module_architecture_v2_20260623.md。**① 数据更新模块**(全部域: 交易日历定目标→diff存量定增量→抓+清洗+正确存储+每节点RULE ZERO)与**② 数据加工模块**(读存量按需加工供展示+前端API)**解耦, 独立 entry**。**核心缺口实测**: by_ts_code 事件数据(十大股东/财报by股 4域)无"按上一公告日扫增量"逻辑(现全量重拉或--resume跳整股=存量股新季永不补=谄媚死); by_trade_date(26)/by_period(2)有 watermark 增量。设计: AcquisitionPlanner.plan(domain) 统一增量(by_ts_code 新建 plan_ts_code_increments 每股 MAX(ann_date)→扫新期) + assert_node_clean 每节点核日历+排除列表+数量。Phased: P1 holders物删(双轨99.62%过)→P2 采集规划器+RULE ZERO门(核心)→P3 update/process解耦entry→P4 加工API收口→P5删源续。
- **2026-06-23 删源程序 holders_tdx 双轨核对 (read-only证据→回补gated)**: tushare top10_floatholders vs tdx fact_top10_holder_period 双轨实测 (analysis/非tushare源_双轨_holders_20260623.md): tushare 活跃覆盖 95.41% < 铁律11 的99%, 220活跃股仅tdx(219是ST)。根因实测: `_by_ts_code_batches` get_active_universe 默认 include_st=False 排ST (非 universe_filter — 写入门只按前缀排北交所/三板不排ST), tushare API 实探有数据可回补。修: sync 加 **per-domain include_st 开关** (top10_floatholders 设 true, holder=参考数据含ST; 其他域默认排ST不变), 实测纳+223活跃ST股。下步: 回补 → 重核≥99% → gated 删 tdx fallback+物删 fact_top10_holder_period。用户决: 纳ST回补再删。
- **2026-06-23 daily_update 重设计: 获取/清洗/加工/存储 各司其职 (用户"嵌套太多, 清爽简洁重新设计")**: 旧 `scripts/daily_update.sh` 469 行 bash 套 python heredoc(编号 2.96b/2.96c 塞补丁 + 四职能交错)重组为四阶段 Python 管线 `backend/services/pipeline/`(context/preflight/acquire/clean/process/store/run, 各模块一职责, 可单测)。`daily_update.sh` 瘦到 ~50 行只设 env + `exec python -m services.pipeline.run`。**逻辑零改纯重组**(faithful port): 获取(Step2~2.95 sync→L0)/清洗(2.96 qfq+3c audit, L0→L1)/加工(risk/东财行业/macd, L1→L2)/存储(watermark/retention/报告/告警)。degraded 续跑+汇总送达模型保留。顺手清死 regime 块(strategies.regime.regime_state reset 删, 旧 report 恒 {error})。pipeline 入 data_module_members roster(member_dir, serve-bypass=0)+ watermark-refresher-wired 断言改指 store.py + run_date calendar-gate allowlist。5 单测 + --dry 端到端 + moth 44/0/0 + 全套净零新增失败。owner=backend/services/pipeline/ + docs/data_management_framework.md(层模型)。**[2026-06-25 §8 切片a 阶段独立化]**: `backend/services/pipeline/stage_runner.py` (run_stage 单跑一阶段, 复用 run.py 同款 4 函数+PipelineContext, degraded→exit1) + `scripts/chunkyctl pipeline acquire|clean|process|store` case (→ `python -m services.pipeline.stage_runner`); daily_update 仍全链便捷入口(非唯一)。运维可单跑某阶段(如修完源只重跑clean)。单测 test_pipeline_stage_runner 4绿 + bash接线smoke(invalid/required/help)。owner=analysis/data_module_architecture_20260624.md §8.1。**[2026-06-25 §8 切片b/c-lite 件1]**: `backend/services/pipeline/stage_status.py` (阶段状态复用 mart_pipeline_run_manifest pipeline_name=`pipeline.stage.<s>`, **不新建 pipeline_stage_status 中间表** — grill 裁决: manifest 已是run级状态账本+pipeline步已写它, 新表=第二状态源违单一真相源, 同survivorship教训; stale=派生计算 upstream.started_at>this 不存flag)。record_stage/get_stage_status/upstream_ok 纯conn函数 + 5单测(not_run/取最新/upstream门/派生stale/拒未知)。owner=analysis/stage_status_design_20260625.md。**[2026-06-25 §8 切片b/c-lite 件2/3 DONE]**: 件2 run_and_record(ctx,stage,fn) — run.py全链+stage_runner单跑 每阶段 best-effort 记状态(degraded前后delta判check_pass/fail; manifest写try/except不破链; dry跳过; clean的gate_result=data_audit overall)。件3 stage_runner refuse-if-upstream-not-pass(_upstream_refusal: 上游非check_pass→refuse exit2+提示; 状态读失败=放行best-effort门非硬安全)+ --force绕过。验证: 18 pipeline单测全绿(run_and_record状态/dry跳/best-effort不raise/upstream门/force绕)+ daily_update --dry --skip-sync 全链 exit0 不破(日志确认run_and_record执行)。**§8 backend(b/c-lite)完整**。**§8 余**: 前端阶段控制卡片(index.html view-data vanilla JS, 产品面=用户拍板范围再建, 已问)。每阶段门拆分M1/M3评估=不做(data_audit已是M2门7/7+watermark已守acquire freshness, grow-on-proven-need别过度建)。
- **2026-06-23 东财全套迁移 Stage④ 物删收口 (申万当前dim + 通达信6表 + 深层源码)**: ④-1 物删 dim_stock_sw_industry(孤儿, 双轨 L1=98.5/L2=97.3/L3=97.3%, 深史兜底 v_sw_industry_pit 保留); ④-2 通达信 6 表全物删(smartmoney5+market3)+ 退役 tdx_industry_client/tdx_industry_names/block_client/stock_detail_read(死代码)+ 清 DDL/索引/registry/DAG(防 schema-init 重建循环)。CI 修: schema 注释含 `;` 被 executescript 劈裂(教训入 §11)。行业/概念全切东财单一供应商。owner=analysis/dc_full_migration_plan_20260623.md。
- **2026-06-23 展示 read-model seam (seed §7.1 档A, 3件最后一件起步)**: 用户选"数据模块收尾(展示+删源)"。建 `backend/services/read_model.py` — `stock_slice(code, as_of)` 返 (stock_code,as_of)-键去规范化展示切片(kline/holders_top10/capital/cyq/limits 8维度并列), **经 services.dossier(SERVE 读层装配 DataAccess.get)取数 0 内联裸查**(SERVE 之上展示投影非第二取数路径, 不变量4)。§7.2 写锁安全=只读不裸查热写库。seed §7.3 明确: read-model 契约=档A seam(现立), cube 跨档案立体视图(股票×机构×公式 JOIN 切片)=P4/档B 押后。smoke 验证 000001 切片(kline 1808点/holders tushare源). moth `read-model-serve-fed`(0内联裸查)守, 44全绿。余展示: 机构/公式档案切片 + stock_detail_read 迁 read-model(删源后)。owner=backend/services/read_model.py + seed §7.
- **2026-06-23 D4 PIT偏序锁 (seed §0.2 死亡条款 D4 [WARN]散落→锁)**: 每个写 feature_store 的 L2 特征 panel builder 必声明 PIT 立场 — build_feature_panel(assert_pit_clean 运行时门, 已有)/ build_segment_panel/build_signal_panel(加 `# pit-safe-by-construction` 标记: feat[i]只用<=i, EMA递推/金叉当bar确认, 无forward/outcome列). moth `build-time-pit-declared` 守(写feature_store的=0未声明; red→green实证注入无声明panel变红/还原绿). **门scope校正**: build_akshare_panel=采集脚本(写fact_*事件表非L2特征, PIT在读时asof_gate)→排除(glob过宽被门自身抓出即修). 防新L2 builder不声明PIT带lookahead泄漏入panel无人察. moth 43全绿. owner=build_*_panel.py + build-time PIT.
- **2026-06-23 不变量4 执法棘轮: 把伪绿 D1 门做真 (member roster + 全量 bypass-scan, 照出真相只 2 违规)**: 核证 D1 硬门只扫 dossier 1 文件=伪绿。建 `backend/config/data_module_members.yaml` 成员 roster(成员=数据模块子模块 data_access/data_sources/perf/技术派生/build_*加工/audit/adapter/writer/universe真相源 可读 raw; 非成员策略/信号/展示消费者内联 raw=违规)+ `check_serve_read_layer.py --bypass-scan` 全量扫(WARN默认 exit0 不破现 dossier 硬门保 moth 绿; --strict 升 exit1)。**全量照真相 = 真实违规仅 2**(signals_v2 MIN notice_date 聚合边缘 + stock_detail_read 展示 P2)+ 3 源退役临时成员(financial/institution_survey/tdx_affair client, 随删源退役)——data_loaders 迁 SERVE 后清洗特征流水线已实质完成, 剩边缘。moth `serve-consumer-bypass-zero` 断言 **==0 硬门**(对标 universe assert_universe_clean)。**违规已 2→0 清零**: signals_v2 MIN→DataAccess.coverage_start 元数据原语(前后一致 2025-10-22); stock_detail_read 展示→`# serve-exempt:`(纯展示+源退役表 raw_gpcw_detail+P4 read-model未建); scan 加 serve-exempt evidence 机制。源退役 3 client 不计(删源后退役)。**清洗单读路执法完整**(0 非成员消费者绕过, moth 42全绿)。**[预存回归标注](本次测试照出非本轮引入)**: test_signals_v2 6测红(test_today_signals_*), stash signals_v2→HEAD 仍红=早前 P0-1/2(wall-clock CURRENT_DATE→latest_closed)留下, 当时未跑全 signals_v2 单测即提交=纪律失守; 根因疑测试 seed today 但引擎用 latest_closed→today 事件当未来过滤; 下步专修。owner=check_serve_read_layer.py --bypass-scan + data_module_members.yaml。
- **2026-06-23 seed 清洗迁移 P0 第一迁: data_loaders 走 SERVE 单一读路 (不变量4单一读路/不变量1统一主键)**: data_loaders.py 3 loader(load_kline/load_moneyflow/load_quality_reports)从直连 duck_connect(market/tushare_raw)改走 `DataAccess.get()` SERVE 单一清洗执行点 — code→6位/asof_col date→ISO 归一由 SERVE cleaner 统一(消第二清洗点), loader 更薄。end→as_of(kline asof_col=date)/limit_stocks→distinct_codes/total_flow(8档求和)+roe_dt选列=加工从SERVE服务列算。补 data_access.yaml: moneyflow +8档买卖额 / fundamentals +roe_dt。**前后一致验证(纯口径统一不该改值)**: kline/quality 精确一致; moneyflow net 精确, total_flow 仅浮点求和最后位差(rel 2.2e-16=机器epsilon, 复刻原SQL NULL传播后)。test_data_loaders fixture 补 fundamentals 全声明列(SERVE preflight要求), 7测pass + moth41全绿。feature_panel 取数底座迁后自动走单一清洗点。余 serve_bypass_inventory P0: return_engine/build_segment_panel。owner=data_loaders.py + seed §1.5。
- **2026-06-23 数据模块边界法 4 决议 (用户拍板, 推翻部分旧策略)**: (1) **数据源唯一 tushare, 无热备无冷备, 删所有非 tushare 源** — 推翻 §4.3 旧热备策略(tdxhub/miaoxiang 不再保留); 有 tushare 等价→双轨核对≥99%→切主源→物删 raw+退役 client; 无等价(akshare 关注度/部分 tdx F10)→丢弃数据; 接受 tushare 单点(简单>韧性)。CLAUDE.md §4.3 已改, 旧条款 deprecated 存档。(2) **无合法直连豁免, 只有数据模块功能能连数据源** — 边界从"豁免清单"改"数据模块成员": 属于(采集/清洗/加工/serving/审计子模块, 含 adapter/writer/reader)→能碰库; 不属于(策略/回测/前端/信号消费)→只 DataAccess.get() 取成品。(3) **所有加工/计算在数据模块对应子模块** — build_feature_panel/risk_factors/macd = 数据模块加工子模块非策略代码; 消费者只调用不加工。(4) **前端边界=语义 vs 展示(非简单 vs 复杂)**: 前端 OK=展示计算(格式/排序/筛选/可视化已 served 值, 客户端零 DB 负担); 必数据模块=任何新语义值(因子/信号/PIT 聚合/决策输入, leakage 风险)。负担答案: 算一次物化(daily_update)+读多次(廉价), display mart 供 UI 就绪聚合。owner=CLAUDE.md §4.3 + analysis/serve_bypass_inventory.md。执行: 删源程序(双轨→物删) + 执法程序(成员边界扫→迁消费者→棘轮硬门), 续 loop。
- **2026-06-23 数据模块顶层架构讨论 + config最小手术 (用户轻量化原则: 模块/表/配置最小化, 一模块只说一件事, 变大才拆子配置)**: 3 Workflow实测(架构现状9-agent审计 wphg9x95d + A/B config对抗设计panel wry13dfr0)。**源迁移实测纠错**: SERVE层20/23(87%)已tushare(eastmoney/申万算tushare家族), 3 tdxhub热备(financial_gpcw/holders_tdx/institution_survey)tushare路径已注册待切SERVE开关, 0 akshare/aif10在active SERVE — 非我先前估的"差得远"(measured纠estimate)。采集层尚有几个非SERVE主的akshare/aif10(hs300备援/external_attention/institution_survey)。**真债非config = Gap1 builder(feature_panel/segment_panel)绕过SERVE读层直连raw → PIT锚强制被绕=leakage真金白银风险** + Gap2 daily_update混3计算步(risk_factors/macd/qfq build)违"纯采集"。**config结构裁决**: 对抗panel证伪我"A不可行"(假二难, A可行但违原则#2一条说5件事)+"B推荐"(B双身份map自造vendor/layer重复=过度工程); 现状真痛仅3处低频重复(层词汇两套L0/L0_source · table+vendor双写 · SLA双写), A/B全量重构=用大重构解小不一致违奥卡姆 → 用户拍板**最小手术(3引用+删1, 0新文件, 4步可逆)**。**[OK] Step1 层词汇单一真相源**(data_layers加alias短码L0_source→L0..L4, data_access全部layer值0悬空) + **[OK] Step4 check_config_refs.py机械门**(red→green实证注入L99变红/还原绿, moth `config-refs-clean` 41断言全绿, 锁层词汇防回潮)。**Step2 SLA单源=假阳性跳过**(measured纠panel: sync_registry freshness_sla[40采集域→update_watermark_sla水位] vs data_audit_rules[4派生表→post-sync审计]=不同机制不相交对象集, 非重复; 删会破水位SLA — verify-the-verifier抓agent浅核)。**Step3 table/vendor去双写=边缘+最险(改SERVE loader)deferred**(慢变+漂移即fail-loud真值小; 实测真重复只2处非panel说3处=更Occam)。**config最小手术实质收尾: 真修=层词汇1处+执法门, 极简**。主线转 Gap1 builder走SERVE+每节点审计。**主线(config后)**: Gap1 builder走SERVE+**每节点数据审计**(用户加: K线获取后核交易日历+排除列表日期/数量=RULE ZERO从读时扩到每节点强制) + 3 tdxhub双轨核对切主源 + Gap2剥计算步。owner=task#54-57 + panel wry13dfr0/wphg9x95d。
- **2026-06-22 数据模块 conformance 审计 (9-agent Workflow) → 44项 worklist + P0 第1批清**: 8维度 fan-out 对照顶层设计扫全"类似28处"问题(calendar/universe/层声明/坏import/fail-silent/源迁移/SERVE/审计规则), 59 findings 去重 44, 排 P0(阻塞跑通)/P1(符合方案)/P2(收尾)。**P0 已清 (本批)**: (B坏import) market_perception(reset删6engine+.utils)/perf(删2模块) __init__ 残留死import → import-safe 空壳/去死import, 全 backend/services 包 import 体检 0 坏包; (A红门) data-layer-integrity FAIL 根因=6活表(fact_risk_factors/technical_trigger/formula_horizon/stage_formula→L2 · stock_attention_snapshot→L1 · dim_stock_attention_latest→display)未声明 data_layers.yaml → 补声明 + attention_snapshot 补 storage_retention(C3 append-only防膨胀), moth 39/39全绿; (验证器) check_calendar_usage 加 B3 模式抓 `<=CURRENT_DATE` 上界锚(architect rule7: 旧正则漏报 signals_v2:242/1495 真上界锚 bug + 误报良性窗口)。**P0-C 真PIT/资金bug 进度**: [OK] P0-3 data_access get() as_of=None+有asof_col+conn=None → 默认 latest_closed_or_raise(日历真相源)闭合 fail-silent 无界返全史洞(measured核证: stk_limit/cyq/moneyflow/index/ann_date实体 0未来行=daily结果不变, 唯 share_float.float_date 208k未来但锚ann_date=前瞻本意); [OK] P0-4 dossier 4loader(limits/capital/cyq/benchmark)经 P0-3 默认锚自动日历界定(审计 over-flag: 这4个daily实体0未来行无现行泄漏, 但闭合latent); [OK] P0-1/2 signals_v2 live信号 wall-clock CURRENT_DATE 上界×2(_source_max_notice_date:242 + build_today_signals:1499/1506)→ latest_closed_or_raise 参数化(8测过/import净/calendar-usage 21→18)。[OK] P0-9 market_read `_relation_has_column` 去 except→False(relation不可访问吞错→静默factor=1.0不复权资金错价)→DESCRIBE失败raise(实测不可访问relation raise CatalogException); [OK] P0-5 update_watermark_sla financial死表 fact_financial_pit_daily→fact_financial_derived; [OK] P0-6 _sync_registry_queries 硬编MAX(trade_date)→读freshness_date_column, 6域无trade_date(report_rc→report_date/stk_surv→surv_date/share_float→ann_date + forecast/income/dividend→freshness_no_probe季报域)修, dry-run 0 BinderException(原6域SLA盲区全消)。[OK] P0-12 check_universe_filter 两盲区: 默认扫改全量(旧 git diff main 无diff假绿) + 加 KLINE_UNIVERSE_SCAN 抓 `DISTINCT code FROM price_kline` 派生宇宙无过滤(§4.5污染根因, 区别WHERE code=?数据加载) + UNIVERSE_CALL_PATTERN 认 assert_universe_clean/in_active_universe; red→green 实测(合成无universe文件→抓), 全量1343文件CLEAN(1命中=updater monthly writer覆盖检查 evidence豁免)。**全部 P0 清完** (A红门+B坏import+C真PIT/资金7项+验证器盲区2 全清, moth 39/39)。下: **P1 源迁移** + P2 收尾 + 终验(flip calendar/universe --strict硬门)。**P1 进度**: [OK] P1-2 data_audit 嵌入fallback dict 清4死表+min_start_date→2019(与YAML对齐, 闭合YAML缺失死门复发, audit运行不崩实测)。余 P1: P1-1 signals_v2内联gpcw/surveys迁data_access / P1-3 ETF M2(网络回填) / P1-4 月线硬编price_kline(耦合M3, 无tushare月线源待决) / P1-5 财务config preferred=tdxhub_gpcw→tushare / benchmark akshare sync_hs300→tushare index_daily。**P2 进度**: [OK] data_layer_audit MANAGED_DBS 验证器修(STALE_SCAN_DBS 扩 market/etf, stale 检查扫全库消除 price_kline_qfq_tushare/mart_etf_snapshot 假阳; untagged 仍只 MANAGED_DBS 不要求声明raw镜像) + 50 张 reset-wiped 表声明从 data_layers.yaml 清除(单一真相源只列活表, _notes 记录, 重建declare-on-build) → stale_tag 53→0/untagged 0/moth 40全绿。**calendar门已升--strict硬门**(见上)。[OK] P1-1部分 signals_v2 `_load_gpcw_feature_maps` 内联 raw_gpcw_detail → data_access financial_gpcw entity(传conn避同库重开冲突, report_date全ISO一致, 实测66736 map, SERVE conformance+lineage); 同步**修 P2 commit(34bce019)漏验的 data_layers.yaml YAML回归**(_notes `tables:` 冒号被当嵌套mapping致 data_layer_audit/legacy-flow moth ERROR, 本轮moth当场抓; 教训=验证须在最后编辑后)。survey loader 续迁(翻案: notice_date 实测全ISO非混格式, signals_v2代码是防御性处理): [OK] `_load_survey_by_stock` 内联 raw_institution_surveys → data_access institution_survey entity(notice_date ISO/code plain/传conn, 实测3股map正常); coverage_start MIN聚合留inline(data_access不建模聚合,非门控)。**signals_v2 SERVE迁移实质完成**(gpcw+survey走单读路)。余 P2: 死源物删(sync_hs300/tdxhub K线 M3, 耦合benchmark+月线price_kline resample, 需daily_update验) / 终验。owner=task#53 + tasks/wm22zvuqg.output。owner=task#53 + tasks/wm22zvuqg.output。

- **2026-06-22 交易日历"第零条强制"执法补缺 (用户根治: 日历总被静默绕过)**: 诊断根因=执法不对称 — **universe 有强制使用门** (check_universe_filter pre-commit hook 拦内联前缀绕过 + assert_universe_clean 运行时 raise), **交易日历无任何使用门** (只 moth calendar-floor 查数据起点 2005, 不查代码是否用日历) + accessor 低层 fn fail-silent (return None → caller 退化 wall-clock/日历天)。建 `backend/scripts/check_calendar_usage.py` (镜像 check_universe_filter): 扫 B1 wall-clock 当最新交易日 (datetime.now/date.today) + B2 SQL 日历天 cutoff (CURRENT_DATE - INTERVAL), 豁免 import services.calendar 的文件 / evidence 注释。**首扫 28 处绕过** (含 universe.py:137 真相源自身 CURRENT_DATE 日历天 + dossier SERVE 默认 as_of=date.today() wall-clock PIT 锚 bug ×4)。当前 scanner 模式 (exit 0 报清单); triage 进行中 (真 PIT/freshness bug vs 合法日历天窗口) → 修/allowlist → --strict 硬门 wired pre-commit (达 universe 同档执法)。**进度 (loop #53 治理中)**: 第1批 dossier as_of 4处修(`_resolve_asof` 日历真相源)+2窗口evidence (28→22); 第2批 events.py:73 PIT锚修(latest_closed_or_raise)+audit.py:391 lag统一latest_market_date+regime/prediction/universe 5处evidence (22→15)。剩 15 待 triage (窗口类: external_attention/industry_context/institution_survey/sector_momentum/stock_stage/stock_turtle/qfii/v3_paper/signals_v2/update_watermark_sla)。**loop 顺带暴露 2 独立 pre-existing 问题** (均已修: market_perception/__init__ 死import P0-7清 / data-layer-integrity P0-10声明6表清)。**[OK] 2026-06-23 calendar 门升硬门 (--strict)**: 真PIT锚全修(signals_v2/dossier/events as_of→latest_closed_or_raise)+18合法窗口全 evidence(载史/recency/展示/SLA-age/provenance)→ calendar-usage 全量 CLEAN(0绕过); wired safe_commit Step3.95 --staged --strict + moth `calendar-usage-clean`(40断言全绿)= **交易日历达 universe 同档第零条硬门执法**。owner=backend/scripts/check_calendar_usage.py。

- **2026-06-22 P1 SERVE 读层 gate 全绿 (DONE) + P2 清库① bestchoice 死代码物删**: (P1) 计划 §10 P1 验收门 'read-no-inline-table/read-no-self-asof/feature-from-l2+preflight' 落地单一执法点 `check_serve_read_layer.py` (D1-D5: dossier 0内联/0自写asof/preflight接线/21entity声明链齐全/L2-bypass关闭), red→green 实证+5pytest+moth `serve-read-layer-p1-doors`+wired safe_commit 3.9; dossier 18内联裸查全迁 data_access (duck_connect=0)。(P2清库①) 物删 v3_bestchoice serving 栈 5 文件 (routers/v3_bestchoice + services/bestchoice_read+config + config/bestchoice_pipeline.yaml + test): 路由查的 mart_*_bestchoice_v1/paper_sim 表 reset 已删=死代码; fan-in 审计仅 main.py 挂载+0前端调用+phase脚本族早删; bc_absorbed 另案保留。main.py import OK(122 routes)/moth 39PASS/doc-drift 0悬空。(P2 M3-prep) daily_update xdxr 热备(§4.3保留)code列表源 price_kline_tdxhub→canonical price_kline_qfq_tushare 解耦(双轨核对: canonical 近45天5203 vs tdxhub 5209, 差6码实测=000300指数+000638退市+5只停牌, canonical排除更正确非回退); 解开 xdxr 热备对将退役 tdxhub K线表的依赖。**P2 剩余=耦合 live 迁移**: M2 ETF(网络回填+5服务) / M3 tdxhub K线物删(仍耦合 xdxr SLA + M2阻塞) / M4 财务簇双轨, 逐步 deliberate 推进非一次性。(P2 M3-A 真bug修复) **daily_update 新增 Step2.96 build canonical qfq K线**: 发现 M1 serving 切 price_kline_qfq_tushare 但其 build(raw_tushare_daily×adj_factor→qfq)从未接进 daily flow(sync_registry daily 域自承"消费链切换独立大手术须review 先 raw 落库双轨")→ serving K线只手动重建会 stale。接 Step2.95 raw sync 后全量 rebuild(qfq因latest-adj rebase须全量, CTAS秒级), 实跑 8.29M行/2019~06-18/5431股 verdict PASS。owner=build_price_kline_qfq_tushare.py。(daily_update 实跑验证暴露 — 死门修复) **data_audit (Step3c post-sync 质量门) 之前崩溃=死门审0项**: `_check_smartmoney_freshness` 的 `_scalar` 无 try/except, 一条引用 reset 删表的规则 (fact_sector_momentum_daily/capital_flow_pit/sniper_score/institution_score 模型层已删) 抛 CatalogError 崩掉整个 audit。修: ①防御 `_scalar` 包 try/except (坏规则记FAIL续跑, mythos§14 崩溃门=死门) ②删4条死表规则。audit 现完成不崩。**③ kline_checks repoint tdxhub→canonical**: 全部 source_table/kline_source_table (config 2处 + 代码 9处 default) market.price_kline_tdxhub→market.v_price_kline_qfq (tushare-only 2019+/freq+adjust合成), min_start_date 2022→2019 — audit 审实际 serving 源 + 解阻 tdxhub drop。重跑 2PASS/5FAIL 均对 canonical。**④ 日历 lag 修复 (交易日历=强制前置真相源, 不自算)**: date_range/smartmoney_freshness 之前误报根因 = `_trading_index` key 是 dim_trading_calendar 的 VARCHAR ('2026-12-31') 与 `_to_date` 产 date 对象类型不匹配 → `_trading_lag_days` 永返 None → 退化日历天 (.days) 把周末/假日算进虚高 (06-18→06-22=4日历天但仅1交易日, 06-19假+周末)。修: `_trading_index` key 归一 `_to_date`, date_range 走 `_trading_lag_days` 交易日口径 (缺日历=FAIL 不 fallback)。重跑 date_range+freshness PASS (4PASS/3FAIL)。**剩 3 FAIL = 停牌严格性 follow-up** (completeness 0.0阈值 + consistency gap_max 把停牌股[000004/000016 等]缺日当FAIL; 需 raw_tushare_suspend_d 交叉引用区分停牌vs数据洞 — 同'用tushare真相源'原则)。owner=backend/services/data_audit.py + config/data_audit_rules.yaml。

- **2026-06-17 P0 数据域注册 (tushare 选股潜力研究后)**: 单日实弹核证后注册 5 域进 sync_registry (口径对齐项目 行业=申万/概念=东财, 禁同花顺第三套): sw_daily(申万行业日线 by_trade_date) / share_float(解禁 by_trade_date date_param=ann_date, float_date前瞻PIT) / stk_factor_pro(261技术因子 by_ts_code, 不支持单日全量) / stk_holdernumber(户数 by_ts_code)。**by_ts_code 取码改走 services.universe 单一真相源**(白名单60/00/30/68+非ST+非退市, 实测4969码0排除股漏入; 原内联tdxhub+前缀=第二套定义漏ST已退役)。namechange 撤销(退市/ST导向违排除列表, ST由stock_st覆盖)。拉取走 sync_runner --domain <d> --backfill。研究 owner=analysis/tushare_alpha_potential_research_20260617.md。**[2026-06-25 M2 Stage A]** 注册 fund_daily+fund_adj (ETF场内K线→raw_tushare_fund_daily/fund_adj, by_trade_date[避2000-cap], data_start 20190102); sync_runner 加 **config-driven `universe_filter_prefixes` 覆盖机制** (默认A股60/00/30/68, ETF域覆盖场内15/51/56/58, 不污染services.universe个股真相源); 1-ETF实弹GO(510300 qfq=fund_daily×adj对齐mootdx 4/5精确, 且揪出mootdx ETF分红未复权bug=本迁移顺修); 20 sync_runner单测(含2新ETF前缀防回退)。**[2026-06-25 Stage B DONE]** fund_daily+fund_adj --backfill 后台(2019+, fund_daily 1.36M行/fund_adj 1.40M行/各1811 trade_date==交易日历[2019-01-02..2026-06-24] 0缺日/1826码); min_rows误报修(历史ETF增长2019:323→2026:1406, min_rows 1200校2026误判早年合法批, 改300 floor<323; 数据完整靠日历gap核证非min_rows ok标志)。**[2026-06-25 Stage C DONE]** build_etf_kline_qfq_tushare.py 建 etf.duckdb.etf_price_kline_qfq_tushare (qfq=fund_daily.close×fund_adj/latest_per_code, OHLC同乘, vol不×100/amount千元×1000, source='tushare'); 1.36M行/1826码/2019-2026, 510300逐码1811==raw MATCH/0码缺; 对账vs mootdx收益 avg 8.7e-05/>50bp 0.13% PASS, max异常9.0经查=mootdx glitch非tushare(511030 mootdx ret 900% vs tushare 0.08%)再证tushare质量更高; **复权验证口径精修**: qfq除息日收益=当日真实总收益(非恒0, 510300 20240118市场真涨1.46%→qfq=1.46%), 单测改合成数据隔离纯分红除息(raw-3%=分红/adj+3%→qfq收益≈0); 7单测全绿(rebase/纯分红≈0/无分红日==raw/latest按code分区/单位/OHLC齐调)+data_layer_audit PASS(L1k tagged)。**[2026-06-25 Stage D DONE]** 3数据读全repoint etf_price_kline→etf_price_kline_qfq_tushare (etf_engine.calc_etf_momentum:410/etf_snapshot_manager._price_coverage_summary:117/etf_mining_engine._load_price_rows:44, 两表同库etf.duckdb无跨库, 列兼容核); fan-in审计=3readers+1writer(etf_db写路Stage E退役); etf_sync_state状态面板读保留(报mootdx健康, Stage E调和)。**真金白银**: 旧mootdx etf_price_kline陈旧到2026-04-13(2.5月)+仅2023+/1622码 vs tushare新鲜2026-06-24+2019+/1826码 → 同修正确性+新鲜度+覆盖; 20d diff大是陈旧(对齐同末日diff仅0.17%)。36 ETF测试全绿(修test_kline_sources fixture补建新表)。新鲜度不退化(ETF流无自动刷新器, 前后都手动build, 未耦合进daily_update)。**[2026-06-25 Stage E 用户授权物删, 拆2 commit]** E1(可逆代码退役 DONE): etf_engine.sync_etf_universe删sync_kline块(-81行)+kline参数+dead imports(fetch_etf_kline/upsert_price_rows/update_sync_state/asyncio/timedelta)仅留资产池刷新; etf_db删etf_price_kline DDL+upsert_price_rows+update_sync_state(-54行, 仅kline块用=全死, etf_sync_state表保留承asset_universe); akshare删fetch_etf_kline wrapper(共享helper保留); snapshot状态面板从etf_sync_state price_kline改读tushare qfq表逐码派生。fan-in审计闭合(0活引用, sync_hs300用market_db非etf_db无纠缠, etf_price_kline 100%mootdx+tx无benchmark)。19单测绿+2防回退断言。**E2(不可逆物删 DONE)**: build脚本cross_check(读mootdx)→coverage_check(vs raw fund_daily); db_lifecycle_delete加`db:`别名支持(M3/M4复用, 留痕入目标库deletion_record); manifest物删etf_price_kline(action=archive: 929,334行→parquet 17M留底+2条deletion_record+DROP); 清etf_sync_state price_kline 1622孤儿行; db_compact etf.duckdb 205M→112M(省93M)。验证: etf_price_kline不存在/qfq完好1.36M/0活引用/build PASS/19单测绿/0悬挂视图。**M2收口完成: ETF K线=tushare单源(etf_price_kline_qfq_tushare), mootdx/tx链全退役, 修分红bug+陈旧+glitch三病**。follow-up非阻塞: ETF qfq日更未接daily_update(现手动build); M3退役4失败测试已spawn_task。owner=analysis/m2_etf_tushare_migration_20260625.md。
- **2026-06-22 数据模块顶层设计 constitution v2 (用户: 该重建就重建/覆盖盲点/前瞻扩展)**: owner=`analysis/data_module_toplevel_design_20260622.md`。13-agent Workflow(7调研+3竞争提案+对抗盲点+磁盘核证)+ controller 亲验综合。**核心诊断**: 写侧(sync_registry)+存侧(分层库)成熟, **读侧(SERVE)完全未建=5功能面所有silo/双源同一根**。**4 地基不变量**(倒推): 统一主键+PIT锚 / 读写边界=写锁=库分区(全read_only) / 可扩展分层(加因子=加列加切片非加表) / 单概念单真相源(SERVE单读路+4 moth门)。**编排**=模块化子模块+薄DAG(节点幂等可续, 非长链, daily_update退化为--all入口)。**血缘**=单一路径自描述(声明链config+携带provenance信封+复用pipeline_artifact_lineage)。**L2=b分表**(feature_panel连续/signal_panel事件同键)。**edge-gating裁决(亲验experiment_store 0 confirmed)**: 档A(SERVE+signal_assembler最小子集验edge)立即; 档B(因子全量/立方体/cube展示)BLOCK直到sandbox证含成本可交易signal。**清库REVISE**: L0/L1k保留(先探针), 旧源+污染派生清, 并轨M1-M4非big-bang。分阶段P0止血→P1 SERVE读层(task#52)→P2清库→P3协议化+转正门→P4展示→P5档B扩展。
- **2026-06-22 doctor 恢复 + 排查修复 (用户"排查")**: (1) **doctor 最小重建** (`backend/scripts/chunkyctl.py`, reset commit 639e0dfb 删原1660行+依赖): 只串幸存4 gate (moth assert / alert_flags / universe / data_health), 已删模块不复活 (尊重 reset), retired 子命令 graceful; test_chunkyctl_doctor 3测。(2) **doctor FAIL→WARN 排查**: universe FAIL → data_audit.py:124/320 硬编码白名单前缀切 services.universe.ACTIVE_A_SHARE_PREFIXES 单一真相源 (CLEAN 1335文件); data_health 12红表 = reset删物理表+源迁移 dim_data_asset 注册仍active 假红 → seed_dim_data_asset.EXTRA_DEPRECATED_ASSET_BY_TABLE 加12表 deprecated+replacement (akshare/aif10/tdx→tushare后继 fina_indicator/forecast/top10_floatholders/stk_holdernumber, mart_industry→申万v_sw_industry_pit), 重种 → 红表12→0 (green82/yellow19/red0), doctor verdict WARN (19黄=manual daily_update SLA非阻塞)。tdx holder 标deprecated但备援fallback代码 _top10_tdx 独立保留 (§4.3)。
- **2026-06-17 全库清排除股 + universe 写入门 (用户: 排除列表硬真相源, 北交所/新三板/老三板)**: (1) **一次性 purge** (DB侧, 验剩排除=0): 个股级表删非白名单前缀(60/00/30/68外)行 ~4.9M (tushare_raw 4.33M 含 daily/cyq_perf/adj_factor/stk_limit/moneyflow_dc/top_inst + dc_member 按 con_code 745k / market 295k / smartmoney 41k / feature_store 220k)。**假阳性避开 (mythos§14)**: dc_index/dc_member(ts_code)/moneyflow_ind_dc/sw_daily/index_* 的"100%排除"是指数/概念/行业代码(BK/801/399)非个股, 未删。(2) **写入门防回潮**: `sync_runner._write_batch` 加 universe 写入门 (`spec.universe_filter=true` 写前丢非白名单前缀行, dc_member 用 `universe_filter_col: con_code`); 26 个 stock-level 域加 `universe_filter: true`, 指数/概念/日历域不设(防误删399/801); by_trade_date 域重拉全市场不再加回北交所。实测 share_float 拉取丢53北交所行/落库0排除前缀。(3) **ST/退市不整删 (PIT)**: ST 可摘帽/退市股有交易期合法历史, 整删=丢合法史+生存者偏差 → 由 universe.assert_universe_clean + is_st_on PIT 消费侧排除。
- **2026-06-19 universe 身份真相源切 tushare stock_basic (退役 akshare dim_active 前缀猜)**: 根因实证 — 旧 `get_active_universe` = K线90d活性 ∩ 前缀(00/30/60/68) − ST, **不与真股清单交集** → 双向 bug: 漏入指数 benchmark 000300(沪深300, 与00前缀共号段)+ 漏掉真股(001393 等 8 只, 旧 akshare 快照 stale 24天)。`dim_active_a_stock` 旧由 **akshare** `stock_info_a_code_name`(bare码 + `_market_from_code` 前缀猜市场)建。修: (1) 注册 `stock_basic` 域 (tushare, full_refresh, list_status=L, **不设 universe_filter**=身份真相源本身, raw 保全市场); (2) `security_master.refresh_active_a_stock_master` 改读 raw_tushare_stock_basic 重建 dim (stock_code=symbol, market=ts_code后缀权威SH/SZ, 排北交所market='北交所', source='tushare_stock_basic'), 删 akshare 调用 + `_market_from_code`/`_disable_proxy_env`/os import 孤儿; (3) `get_active_universe` 加**身份交集** (K线 ∩ dim_active_a_stock 真股清单 − ST), 前缀降 defense-in-depth。实测: dim 5201(akshare)→5208(tushare, +8真股/-1退市000638\*ST万方06-03退市), universe 000300出局/4978干净码/0非白名单漏入; test_universe 16 passed (+1 防回退: 指数不在真股清单必剔)。19 消费者只 ingest_holders_tdxhub.py 读 market 列(仍SH/SZ)零改。全盘点见 analysis/non_tushare_source_inventory_20260619.md (非tushare源 akshare22/tdxhub18/aif10 13, M2-M4 逐簇双轨退役)。
- **2026-06-19 非tushare孤儿表退役 (逐表对抗验证 wf_39200ec2, 11表→SAFE_TO_DROP 6/KEEP_MIGRATE 5)**: 验证抓住 aif10 valuation_quantile(3消费者 v3_picture)/peer_valuation/price_kline(4消费者 regime/return) 是 **LIVE** → 不可 bulk-drop (mythos§14)。已物删 4 表 (0 live消费者): **fact_orderbook_snapshot**(market 100, 污染残留)· **raw_fund_flow_daily**(86k, 被 tushare moneyflow 替代)· **raw_aif10_holder_count**(742k, 转 tushare stk_holdernumber)· **raw_aif10_financial_history**(5713, 探针孤儿)。aif10 shared writer 删2留3 (aif10_capability_client + updater DAG 5文件精细手术, 3 KEEP capability/DAG完好)。fact_hsgt_daily(2767, build_akshare_panel 删 build_hsgt_daily 留其余5表 + institution_alpha northbound块退役)。fact_financial_indicator_ak+dim_financial_indicator_latest+sync_state(git rm dedicated writer financial_indicator_client + financial_client caller改stub; scoring/audit try/except安全降级dormant层保留)。**[OK] 6/6 SAFE_TO_DROP 全退役 (~750k行)**, data_layer_audit PASS(87表)/moth fail=0。剩 KEEP_MIGRATE_FIRST 5表(aif10 valuation/peer/forecast live + dividend_summary + price_kline)走 M3/M4 双轨先迁后删。退役日志 owner=盘点doc §3.5。

- **2026-06-17 清验证墓地 + 恢复干净地基 + universe 升交易日历级真相源** (用户决议): (1) **清理** (不可逆, 已确认): 删旧 LGBM/ensemble 验证墓地 196M (data/reports/optuna 153M + v7_retrain + msaf_ensemble_*/phase4_gate_* + data/optuna 公式工厂 studies + multidim_models) + 分层孤儿 (calc.duckdb 0B 未声明 + concept_snapshots 8.7M + portfolio_backtest_nav.csv); wipe experiment_store 探索裁决 (25+10+9 行); 保 live infra (daily_*/data_audit/leakage_audit/底座6库)。(2) **恢复干净地基**: data_layers L2/L3/L4 声明为空 (06-14 reset 已清, 无模型层残留); drop 旧 GT 两表 (rally 43202 含北交所 3.1%+ST污染突破def / macd 280324)。(3) **universe 升交易日历级硬真相源** (`services/universe.py` 单一计算点): 加 `assert_universe_clean()` 硬验证器 (前缀级, 排除股进任何 GT/回测/选股 = raise `UniverseContaminationError`) + PIT ST 日历 `load_st_calendar/is_st_on` (raw_tushare_stock_st, 历史 t 真相源) + `classify_exclusion` (北交所/三板/ETF taxonomy 进 universe_rules.yaml); kline 源切 tushare。**三道门**: 代码门 `check_universe_filter.py` 拦内联白名单前缀绕过 (污染根) / 数据门 moth `rally-gt/macd-gt-universe-clean` (GT 0 排除股) + `universe-hard-gate-present` / 运行时门 builder 调 assert_universe_clean。(4) **结构型 GT 重建** (新 D1 锚): `build_rally_ground_truth.py` (用户图样型 长底+多头排列+平滑+底→顶>60%, 漏斗21687→9070主升浪/4347股, universe硬门PASS) + `build_macd_episode_ground_truth.py` (金叉峰值>30%, 311291 episode/5197股); 删超期 experiment_zhushenglang_swing_def + experiment_macd_episode_scan。test_universe 15 passed (5 新硬门防回归)。002484(江海)实测命中主升浪 = 定义验证 (具体 forward 收益属探索期数字, 不入索引)。

- **2026-06-16 重启 (清探索污染 + 立方法论 owner)**: 用户决议清掉本轮无锁方案的 alpha 探索污染重新开始。**精准删除** (保数据底座+基础设施改进): DB 清 experiment_store 留档行 / drop feature_store L2 探索面板(8.17M)/缓存+0行表; 删本轮 16 个 alpha 探索 runner + episode 引擎 + 49 个 analysis 验证结果 json + 11 探索设计稿 + 探索方法论 doc + 4 探索 config + consumer_alpha family + 6 探索 moth 断言。**保留**: sync 限流修复 / tushare catalog(241接口) / mio 收编 / G2-G3 治理 / 全数据底座(raw/dim/K线/财报/行业/serving)。**立权威方法论** `docs/alpha_discovery_methodology.md` (用户口述监督式范式: 裸K线扫主升浪>60% / MACD episode>30% = ground truth → 入场点 PIT 因子逐层叠 → 分层 → train≤2025-06/OOS→2026-06 → Modal; 高积分高价值因子优先 hk_hold/stk_holdertrade/moneyflow_dc 等)。cyq 实测与 tushare qfq 同复权坐标可用(C0 FAIL=审计比错基准非数据错), 本地 2023+ 用 2018 需回填。 **耦合检查工具** `moth coupling` (引擎全局子命令) (用户: 删除暴露 表↔代码↔配置↔DB↔文档↔测试 耦合): --impact <name> 删前看 fan-in 爆炸半径 / 默认扫孤儿引用 (pytest --co 真实 collection 崩 + moth 文件悬空) → moth 断言 coupling-no-orphan-refs。CI 修复: 删 experiment 脚本漏删的 2 孤儿测试 (collection 崩根因)。方法论并入 MASTER §5 (docs 11→10)。 CI 第3处: ci.yml 硬编码测试清单/family 断言悬空 (修+耦合工具 T5)。CI 第4处: 误删 formula_search_spaces/candidates config (被保留优化层 plan_validator/features 消费, 非探索) → 恢复; consumer_alpha_matrix/phaseD_search_space 真探索仍删。本地全量 CI offline 91/91 passed。


## 30 秒速览 — 这是什么项目

**Chunky Monkey v2** = A 股**自动选股 + 实盘模拟**系统. 用户(私人投资者)用它筛 5 只股票 / 月度轮换.

**用户目标 (硬指标, 一切优先级以此为锚)**:
- 年化 ≥ **+30%**
- max_drawdown ≥ **-20%**
- 超额 vs HS300 > 0

**数据基础**: 6,618 股 A 股 K 线 (2022-01 起) + 70K+ 财报 + 35K 机构事件 + 53K 龙虎榜 + 68K 高管增减持 + 大盘 regime + 4 阶段技术形态分类.

**架构主线 (alpha pipeline)**:
```
原始数据 → 公式信号 + PIT 因子 → Optuna 调参 (walk-forward) → mart 表
       → paper_sim selector (按 ensemble score 排名)
       → simulate_trade (T+1 入场, 含 tx_cost + 涨跌停)
       → NAV 曲线 → KPI 验证 (6 类 20+ 指标)
```

**当前最强发现**: 无 — 2026-06-16/17 地基-reset + 清验证墓地后, 所有 reset 前/污染期 alpha 结论 (reversal sharpe / 二次突破超额 / 鱼身 / lgbm 模型 / frontier) 已作废清除。当前态 = **unknown**, 以 `goal.md` Active Priority Board 为准 (CLAUDE §4.2: 不引用文档旧数字, doctor --fast 实测为准)。

**下一步**: 监督式 episode-first 结果倒推 — 结构型主升浪 GT (已重建, 底→顶>60%+长底+多头排列+平滑, universe 硬门 clean) → 逐数据 alpha 验证 (因子对起涨/持仓/出场判别力) → train≤2025-06 / OOS→2026-06 → 含成本 paper_sim → KPI。

## 维护责任 (Rule 9.5 沉淀)

**每次完成一个 phase / commit / 数据 backfill 后, 都要更新本文档**. 具体 checkpoints:
- 新加数据表 → 加进 §2 (数据资产)
- 新加 service 模块 / script 入口 → 加进 §3-4
- 新加 yaml config → 加进 §6
- 解决了已知坑 → §8 标 [PASS] + 短说明
- 跑出新 OOS 数据 → 加进 §10
- 踩了新坑 → §11 + CLAUDE.md Rule 9
- 加 §14 增量日志 (本 session 做了啥)

不维护 = 下次 session 又要重新摸索 = 用户最大抱怨

---

## 0. 用户终极目标 (锚)

> "短期内资产最大幅度增值不缩水"

3 个 PASS 标准:
1. 年化 ≥ +30%
2. max_dd ≥ -20%
3. 超额 vs HS300 > 0

基线: 2023-01-03 起, 100 万初始, HS300 benchmark.

---

## Pipeline 数据流图 (端到端架构)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 0. 原始数据层 (data sources)                                         │
│   - akshare (K 线 / 财报 / 龙虎榜)  - tdxhub (qfq 复权 K 线)         │
│   - aif10 (估值 / 一致预期)         - tdx F10 (机构持仓)             │
│   - 内部模拟器 (event_simulator)                                     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. raw_ 层 (smartmoney.duckdb): 70K 财报 / 53K 龙虎榜 / 35K 机构事件  │
│    market.duckdb: 6M K 线 / 158K xdxr 事件                           │
└──────────────────────────────────────────────────────────────────────┘
        │ sync (POST /api/inst/update/smart) — 含 watermark
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. fact_ 层 (PIT 时序事实表):                                        │
│    - fact_stock_technical_stage (2.4M, Stan Weinstein 4 stage)       │
│    - fact_signal_context (3.3M, vol_r20/price_pos/drawdown_60d/stage)│
│    - fact_technical_trigger (公式信号触发, 含 strength)              │
│    - fact_risk_factors (4.8M, Phase ψ.β.1 PIT mom/sharpe/vol)        │
│    - fact_financial_pit_daily (3.7M, Phase ψ.β.2 PE/PB/ROE/yoy)      │
│    - fact_capital_flow_pit_daily (858K, Phase ψ.β.3 lhb/exec/holder) │
│    - fact_regime_state (775, 大盘 bull/bear/sideways)                │
└──────────────────────────────────────────────────────────────────────┘
        │ Optuna 调参 (R1 walk-forward, expanding_monthly / train_end_forward)
        │ governance 守门 (sharpe>5/win>0.95/avg>0.5 reject)
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. mart_ 业务层 (调参 / 寻优结果):                                   │
│    - mart_per_formula_stage_optimal (426 OOS 行,                     │
│         per formula × stage × train_end_date, 最强 setup ↓)          │
│    - mart_per_stock_stage_strategy_optimal (per-stock × stage 旧表)  │
│    - mart_formula_horizon_evidence (per formula × hp 全市场)         │
│    - mart_stock_trend (主 alpha 88 列, 但 ⚠ latest 快照无 PIT)       │
│    - fact_optuna_governance_log (reject 审计)                        │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. paper_sim selector (3 mode):                                      │
│    - "backtest" 单公式排名 (按 mart_per_formula_stage.oos_sharpe)    │
│    - "ensemble" 10 alpha zscore 加权 + regime gate (Phase ψ.β.4)     │
│    - "production" 走 mart_daily_position_recommendation (实盘)        │
│    选 top 5 + 流动性过滤 (vol_60d ≤ 40% / amount_20d ≥ 5000万)       │
└──────────────────────────────────────────────────────────────────────┘
        │ T+1 VWAP 入场
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. simulate_trade (services/backtest/realistic_engine.py):           │
│    - T+1 入场 (buy_offset=1, 一字涨停延迟 1 次)                      │
│    - 5 出场触发: stop_loss > target_arm > trailing > hp_expired      │
│         > stage_deterioration                                        │
│    - 含 tx_cost (佣金 0.025% + 印花税 0.05% + 滑点 0.1%)              │
│    - 含涨跌停 reject_buy (一字涨停不买) / 退市暂停过滤                │
└──────────────────────────────────────────────────────────────────────┘
        │ 每日 NAV 更新, swap 决策, 跨日 trailing arm
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 6. paper_sim 输出 + KPI:                                             │
│    - fact_paper_sim_nav (NAV 时序)                                   │
│    - fact_paper_sim_position (持仓快照)                              │
│    - fact_paper_sim_trade (BUY/SELL/SWAP_OUT/SWAP_IN)                │
│    - mart_paper_sim_kpi (6 类 KPI: A 用户标准 / B anti-churn         │
│         / C robustness / D ablation / E sensitivity / F reality)     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼ 决策: 6 类 KPI 全过 → 上线 / 一类不过 → 不上线
┌──────────────────────────────────────────────────────────────────────┐
│ 7. 实盘上线 (待 — 还没满足用户 +30%/-20%/超额 HS300)                  │
└──────────────────────────────────────────────────────────────────────┘
```

## 1. 三个 DuckDB 数据库

> 权威清单 = `backend/config/database_manifest.yaml` (含 retention_class 生命周期分类, 见 db_management_design §13)。
| DB | 路径 | 用途 | retention_class |
|---|---|---|---|
| `smartmoney.duckdb` | `data/smartmoney.duckdb` | **2.5G / 85表** (2026-06-14 地基-reset: 删整个模型/特征/寻优层144表, 只留基础数据+纯K线中间+档案展示+治理; 26.6→2.5G; 参数寻优重做; 退役实验知识→config/experiments/retired_experiments.yaml) | production_control(地基) |
| `market.duckdb` | `data/market.duckdb` | K 线 + 行情 (`v_price_kline_qfq`) | canonical_source |
| `tushare_raw.duckdb` | `data/tushare_raw.duckdb` | TuShare raw 镜像 (raw_tushare_*), sync_runner 独占写, 写锁隔离 | canonical_source (mirror) |
| `alpha158.duckdb` | (planned, 旧panel 2026-06-14 删) | qlib Alpha158 K线因子库; 旧 panel(418万行/3.5G, PIT不可信)删, 验证Alpha158时干净重算+pit_guard核证 (manifest planned; daily_update Step2c重建循环已切) | rebuildable_feature |
| `etf.duckdb` | `data/etf.duckdb` | ETF 专用 | governed_source |
| `experiment_store.duckdb` | active (S0 建, 执行器接入) | alpha 验证实验输出 (verdict/IC scan/lineage/pit_audit), 与 live 隔离; 写入器=experiment_consumer_alpha_validation.py | transient_experiment |
| `data/scratch/*.duckdb` | (约定) | 测试/探索一次性库, 用完即删, gitignore | disposable_scratch |

**约束** (AGENTS.md / engineering governance DuckDB 段):
- 永远走 `services.duck_adapter.connect` / `services.db.get_conn`
- 单写锁, 一次 ATTACH, 不要直接 `duckdb.connect()`
- raw `duckdb.connect` 允许清单现在 config-owned (`backend/config/duckdb_connect_policy.yaml`) 用于跟踪历史 call sites；新增生产 raw connect line 由 `backend/scripts/check_rule_compliance.py` 默认阻断，确需例外必须有同行/上一行 evidence 注释并进入 review。
- 新增 `data/*.duckdb` / `.duckdb` 文件名字面量默认阻断；DB 路径应进入 `backend/config/database_manifest.yaml` 或专属 config。

---

## 2. 数据资产 — 6 大维度 (完整盘点)

> ⚠ Claude 容易误以为"项目主要数据是 K 线". 错. 6 大维度全有.

### 2.1 大盘 / 指数

| 表 / 字段 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `v_price_kline_qfq` (market.duckdb) canonical 日线 qfq | **8.29M 行 / 5431 股 / 2019-01 → 2026-06** | 实时(view) | **2026-06-22 切 tushare-only** (M1, owner=market_schema.py): 旧 tier-1=price_kline_tdxhub 的 qfq **系统性算错** (复权因子当后复权式乘数抬高分红股历史价, 茅台/比亚迪 raw×adj_factor/latest 重建证 tushare对/tdxhub错最高89%分叉); 去 akshare/tdxhub fallback (用户"没有备用源只有tushare"); primary=price_kline_qfq_tushare (标准前复权, 严格超集 tdxhub 2022+). DDL 内禁 `--` 注释(executescript 截断). moth `kline-canonical-tushare-only` 守. 派生表(macd_state/picture/segment)建旧错qfq上 STALE 待重建. **2026-06-23 M3 物删 price_kline_tdxhub (5.3M行/5211股): 0 serving读者(grep FROM/JOIN全空+canonical视图tushare-only); build_price_kline_tdxhub从daily_update移除 + market_schema CREATE删(断§4.5重建循环) + sync_hs300 tdx-write neuter + upsert fail-loud守 + data_audit kline_source repoint canonical; adjustment_event(xdxr热备§4.3)保留; builder/updater_market_data=orphan(main.py已退役)留dormant. moth40/40 PASS** |
| `price_kline_qfq_tushare` (market.duckdb) **回测前复权主源** | 856万行 / 5755 股 / **2019-01 → 2026-06** | build_price_kline_qfq_tushare.py | 2026-06-15 §4.3 消费链切换: raw_tushare_daily×adj_factor 前复权(rebased, 单位对齐tdxhub); 与tdxhub重叠期收益对账 avg 0.03%一致(max差=tdxhub 2022-12-30 glitch, tushare正确); load_kline 已 repoint; 解锁2020+多regime回测; data_layers=L1k |
| `etf_price_kline_qfq_tushare` (etf.duckdb) **ETF前复权 K线 tushare 单源 (M2 收口)** | 136万行 / 1826 ETF / **2019-01 → 2026-06** | build_etf_kline_qfq_tushare.py | 2026-06-25 §4.3 M2: raw_tushare_fund_daily×fund_adj 前复权(rebased, OHLC同乘, vol不×100/amount千元×1000); 510300逐码1811==raw MATCH; **修mootdx ETF分红未复权bug+陈旧+glitch**; 7单测(合成数据验复权公式); data_layers=L1k。**M2 全收口**: 3消费方(etf_engine/snapshot/mining)已repoint读此表; 旧 etf_price_kline(mootdx/tx 929k行)已物删archive→parquet留底; mootdx取数链(fetch_etf_kline/sync_kline块)退役。owner=analysis/m2_etf_tushare_migration_20260625.md |
| `fact_feature_panel` (**feature_store.duckdb** L2) **LIVE** | **8,173,577 行 / 5427股 / 2019-01-30~2026-06-12** | build_feature_panel.py (services.data_loaders + formula_engine 5因子; pit_guard 物化门) | 2026-06-19 A0 重建物化: mom_60/reversal_20/vol_20/mf_trend_20/roe_dt_asof PIT 宽表。**对抗验证**: universe 0违规 / 0 NaN-inf / 独立重算 600519 1784日 0泄漏 / **entry点JOIN覆盖正负样本100%**(命中点 mom100%/mf83%/roe45%)。roe/mom 尾值留 D 阶段 winsorize; data_layers=L2_feature live |
| `fact_signal_panel` (**feature_store.duckdb** L2) **b分表事件信号面** | **8,293,212 行 / 5431 股 / 2019-01→2026-06** | build_signal_panel.py | 2026-06-22 用户"b分表"裁决: L2 连续因子(feature_panel)与事件信号(signal_panel)分两张同键并列 panel(code×date), signal_assembler 同键JOIN取(排名+公式条件门). 首个公式 macd_golden_cross(金叉326222/3.9%, PIT无前视红线单测); 加公式=加列(invariant#3). 与 feature_panel 同键重叠8.17M(100%对齐). data_layers=L2_feature; moth signal-panel-b-split-parallel 守 |
| `fact_segment_panel` (**feature_store.duckdb** L2) **形态/分层面板** | (重建中) | build_segment_panel.py (+config segment_panel.yaml) | 直读 price_kline_qfq_tushare 复用 classify_technical_stage 物化 PIT 形态轴: stage(Weinstein5态)+range_pos+dif/dea/macd_hist/macd_above_zero+board; forward 不入表(防 outcome-as-feature); Arrow批插。判别力结论=unknown(逐数据 alpha 验证重做, 见 goal.md); data_layers=L2_feature |
| `fact_rally_ground_truth` (**smartmoney.duckdb** L1) **D1 主升浪 ground truth 标签y** | **9,070 主升浪 / 4,347股 / 2019+** | build_rally_ground_truth.py (services.universe 硬门 clean) | 2026-06-17 结构型重建(用户图样型, episode-first D1 锚): 底→顶>60% + 长底 + 多头排列(MA5>10>20>30>60) + 平滑(途中max_dd>-30%), 排北交所/ST/退市(assert_universe_clean); event_date=底(bottom_date PIT锚, 特征<=t/label后验); 下游 D2-D4 因子判别力=unknown(逐数据 alpha 验证待跑); data_layers=L1_foundation |
| `fact_macd_episode_ground_truth` (**smartmoney.duckdb** L1) **D1 MACD金叉峰值 ground truth** | **311,291 episode / 5,197股** | build_macd_episode_ground_truth.py (services.universe 硬门 clean) | 2026-06-17 重建(用户口径: 金叉=买点, 卖点=金叉后波峰探索): 金叉峰值>30%=is_win; peak_gain_pct/peak_offset_days/max_dd_pct; 出场规则判别力=unknown(逐数据验证); data_layers=L1_foundation |
| `fact_rally_entry_pit` (**smartmoney.duckdb** L1) **GT entry-PIT 侧 (标签拆)** | **9,070 episode / 4,347股 / fwd_complete 90.5%** | build_rally_entry_pit.py (+契约 config/rally_gt_columns.yaml + 守卫 services/rally_labels.py) | 2026-06-19 A0#c: 从 GT 剥出 entry-PIT 侧防 outcome 当训练 X=leakage。entry_signal_date=bottom_date(PIT锚, JOIN fact_feature_panel 键) + base_days(唯一 PIT 入场特征) + **fwd_complete**(bottom+250交易日是否<=数据边缘2026-06-12, False=右删失全在2025/2026); **outcome 列(gain/peak/dd/bull_aligned)不入此表**留 GT 表禁做X (rally_labels.assert_no_outcome_leakage 守门, 单测 red→green)。陷阱: bull_aligned 拉升期测=forward 非入场态; data_layers=L1_foundation |
| `fact_rally_entry_negative` (**smartmoney.duckdb** L1) **GT hard-negative 对照组** | **35,198 / 4,846股 / pos:neg≈1:3.9** | build_rally_negatives.py (+共享原语 services/rally_detect.py) | 2026-06-19 A0#d: 结果倒推判别器对照组。框架=**hard-negative**(holding PIT-setup恒定隔离涨不涨信号, 非全市场随机): 同结构 pivot-low + 长底(base>=40, 与正样本同 PIT setup) + fwd_complete + **未涨**(forward gain<60%) + purge 同股正样本±250根。无锁生成(K线 market + GT/日历 smartmoney, 不碰 tushare_raw); ST 留消费侧 PIT 硬门(is_st_on)。验证: 0正负重叠/0北交所/0 outcome列/base_days min40。下游 UNION fact_rally_entry_pit(y=1)训练; data_layers=L1_foundation |
| `fact_rally_episode_strata` (**smartmoney.duckdb** L1) **episode PIT 分层** | **9,070 episode** | build_rally_episode_strata.py | 2026-06-20 C#48: episode 按 PIT 维分层(可live conditioning, 非outcome): **申万sector** as-of join index_member_all(in/out_date PIT, is_new Y+N避§4.5 latest-snapshot)覆盖99% + **市值** daily_basic 底日total_mv 覆盖100%(daily_basic 回补2019对齐 K线/GT, data_start 20190102)→微/小/中/大盘桶 + **base_days** 短/中/长底桶。分布: 小盘+微盘60%+(主升浪小盘主导), 机械/化工/电子/医药板块聚集。form/gain=outcome留GT join不入表; data_layers=L1_foundation |
| `fact_rally_stage` (**smartmoney.duckdb** L1) **episode 阶段切分(鱼头/鱼身/鱼尾)** | **1,507,894 行 / 9070 episode** | build_rally_stage.py (+config rally_stage.yaml, +单测 test_rally_stage) | 2026-06-20 C#48 step2 (用户核心缺口"没研究鱼头鱼尾"): 每 episode [底,峰] per-date 切 **起涨/主升/顶部**, progress=(close-底)/(峰-底) 首次跨阈(launch_end0.30/main_end0.85, pre-reg config)划连续时间段(单调防pullback错标)。分布 主升50%/起涨42%/**顶部仅8%**(底后慢启动+近峰快冲顶=入场窗宽/出场窗窄, 契合出场最重要)。stage=POST-HOC(依赖peak)分析用非live; 跨库 join feature_panel 三阶段100%命中→D 按stage查PIT因子; data_layers=L1_foundation |
| `raw_tushare_moneyflow` (tushare_raw) | **738万行 / 5620股 / 2020-01-02→2026-06-12 [DONE]** | sync_runner --domain moneyflow --backfill | 2026-06-15 用户"拉齐2020"回补完成: data_start 20220104→20200101 + min_rows 4000→3000(2020 universe~3740股); .venv/bin/python + source .env (env PATH双前提) |
| `raw_tushare_moneyflow_dc` (tushare_raw) **东财个股资金流** | **384万行 / 6219股 / 2023-09→2026-06 / 665日 [DONE]** | sync_runner --domain moneyflow_dc --backfill | 2026-06-16 用户"全拉初评有用数据": net_amount/net_amount_rate 东财口径(补 order-size moneyflow); 实测起点~2023-10(东财个股近年才有, data_start 20230901, 前置~20空日 ok:false 但真数据完整); rate=分钟级150/200无日上限 |
| `raw_tushare_index_member_all` (tushare_raw) **申万行业 PIT** + `v_sw_industry_pit` 视图 | **7787行 (5847当前Y + 1940历史剔除N) / out_date填1940 / 同股多区间1609** | sync_runner --domain index_member_all(_hist) + build_sw_industry_view.py (**2026-06-23 东财迁移后退化为只建 v_sw_industry_pit 深史PIT兜底视图**; 当前行业 serving 已切东财 daily_update Step 2.96c=build_dc_industry_view) | 2026-06-15/16 **行业迁移 S1+S2**: S1 原只拉 is_new='Y' → out_date 100% NULL = latest-snapshot leakage; 加 `index_member_all_hist` 域 (is_new='N' 补真 PIT 区间)。**S2** 建 `v_sw_industry_pit` as-of 视图。**S3** [DONE 2026-06-16] live serving 切申万: build_sw_industry_view.py 加建 smartmoney `dim_stock_sw_industry` 当前快照(5847股, tdx_l* 列名=位置别名/值申万, L1_foundation); industry.py INDUSTRY_TABLE→dim_stock_sw_industry; signals_v2 7 处 JOIN 走 {INDUSTRY_TABLE} 常量(no-hardcode); resolve_industry ref_date 缺陷标注(serving=当前, as-of走视图)。验证 59测试pass/moth32/load_industry_map返申万。**S4** [DONE] 删 STALE 孤儿 mart_stock_industry_pit+quality (4消费者全guard降级/移声明/residue0)。**S6** 初次双轨: 申万5847>通达信5624股, taxonomy不同非系统错位(迁移sound)。**S7** 通达信降tdxhub热备不物删(§4.3)。**迁移功能完成** (serving+探索+KPI全申万PIT)。剩跟进: S6完整1周/S8 index_classify/申万readiness面板重建 (owner=analysis/industry_migration_tdx_to_sw_20260615.md; 06-11 ANOVA 已定 申万L2 主口径)。taxonomy 桶 13→31 历史不可比 (§4.5)。**S8 [DONE 2026-06-23] 删源收尾 (用户规则: 删源≠删数据, tushare 唯一)**: daily_update **Step2j (通达信 sync) 删** (原还崩在 market_gap_queue 缺表) + **申万物化 build_sw_industry_view 接进 DERIVE Step 2.96c** (修原孤儿物化步→serving 申万自 06-16 stale, 现每日 CREATE OR REPLACE 刷新, 实跑 5530→5847股/今天); **6 消费方 + 6 测试 fixture repoint** dim_stock_tdx_industry→dim_stock_sw_industry (sector_momentum/scoring/institution_l2_metrics/stock_graph_read/industry_context_engine; 列名 tdx_l* 别名兼容零字段改; 114 相关测试 pass); **watermark 双配置** (source_watermarks + update_watermark_sla) industry_sw 改指申万表 + 删 stock_blocks 域 + 清 tdxhub_block stale 水位行 (SLA industry_sw=OK)。核对 (铁律11): 申万 5847>通达信 5222 股 / L1-3 全 / 14 次新股申万未收录→NULL 可接受。**S9 [DONE 2026-06-23 东财全套迁移] 申万行 serving 退役**: 行业/概念/资金流单一供应商改东财 (dim_stock_dc_industry/dc_concept), build_sw 退化只建深史视图; 申万当前快照 dim 物删 (Stage④-1) + 通达信 6 表全物删 + 深层源码退役 (Stage④-2: _step_sync_industry/tdx_industry_client/block_client/stock_detail_read 删, DDL/DAG/registry 清防重建循环)。**index_member_all + v_sw_industry_pit 保留供深史2025前PIT兜底** (东财同套桶, 非混口径)。owner=analysis/dc_full_migration_plan_20260623.md |
| `fact_regime_state` | 775 行 / 2023-02 → 2026-04 [PASS] | 历史可用 | trade_date / regime_id / regime_label (bull/bear/sideways) / regime_prob_json / transition_signal |
| `dim_market_segment` | dim 表 | 静态 | 市场分段 |

### 2.2 行业 / 板块

| 表 | 数据量 | freshness | 用途 |
|---|---|---|---|
| **`dim_stock_dc_industry`** | dim 5211股(1:1) | **新建 2026-06-23 (build_dc_industry_view)** | **东财行业映射** (serving 新真相源, 列 tdx_l1/l2/l3=东财行业=申万对齐, level按申万名映射31/127/334). 全项目单一供应商=东财迁移 (owner=analysis/dc_full_migration_plan_20260623.md): **Stage①②完成** (物化+6消费方/6测试/build_industry_stat JOIN/daily_update Step2.96c/watermark industry_dc 全切dc, 115测试pass); **Stage④完成 2026-06-23** (④-1 物删申万当前快照dim 双轨L1=98.5/L2=97.3/L3=97.3% 深史兜底v_sw_industry_pit保留; ④-2 通达信6表全物删+深层源码退役[tdx_industry_client/block_client/stock_detail_read删+DDL/DAG清]; ④-3 block前端fork 解除=stock_detail_read 死代码已被dossier取代). **东财全套迁移收口** (行业/概念/资金流单一供应商=东财, 通达信+申万当前dim 物删, 深史PIT申万视图兜底) |
| `dim_stock_dc_concept` / `v_dc_industry_pit` | dim 5210股/490概念 + as-of视图 | 新建 2026-06-23 | 东财概念成员 + 东财成员PIT(仅2025+; 深史走v_sw_industry_pit) |
| ~~`dim_stock_sw_industry`~~ | **已物删 Stage④-1 (2026-06-23)** | db_lifecycle_delete 5847行留痕 | 东财迁移后退役物删 (孤儿/0 live读); **index_member_all + v_sw_industry_pit (tushare_raw) 保留供深史2025前PIT兜底, 东财同套桶; 重建=v_sw_industry_pit WHERE out_date IS NULL** |
| ~~`dim_stock_tdx_industry`/`_history`/`dim_stock_tdx_block`/`dim_tdx_block_catalog`/`raw_tdx_industry_file_snapshot`~~ | **已物删 Stage④-2 (2026-06-23)** | db_lifecycle_delete (smartmoney 5表) + market 3表 | 通达信行业/板块全残留物删 (孤儿; 行业切东财 dim_stock_dc_industry, 概念切 dim_stock_dc_concept); 深层源码全退役 (tdx_industry_client/tdx_industry_names/block_client/stock_detail_read 删 + DDL/索引/registry/DAG step 清, 防重建循环); deletion_record run_id=dc_migrate_stage4_tdx_20260623 |
| `fact_stock_industry_context` | 个股行业上下文 | 取决于跑批 | 衔接 sector_momentum 到个股 |
| **`mart_sector_momentum`** | **⚠ 只 41 行 / 2026-04-17 → 2026-05-13** | ⚠ **没历史, 不能历史回测** | sector_name/code/level, ma20/60, macd, momentum_score, return_1m/3m/6m/12m, excess_1m |
| `mart_industry_pit_quality` | ? | PIT | 行业质量 |
| `mart_stock_industry_pit` | ? | PIT | 个股行业 PIT 评分 |
| `mart_institution_industry_stat` | ? | — | 机构 × 行业统计 |
| `research_inst_industry_performance` | 6,564 行 | — | 机构 × 行业 win_rate_10d/30d/60d/120d, avg_gain_10d/30d/60d/120d |

### 2.3 机构跟随 (项目主 alpha, **权重 0.40**)

| 表 | 内容 |
|---|---|
| **`mart_stock_trend` (主 alpha, 88 列)** | inst_count_t0/t1/t2 / inst_cap_t0/t1/t2 / inst_trend / cap_trend / latest_events / external_attention_signal / **stock_gate** / turtle_setup_state |
| `fact_institution_follow_backtest` | cohort × params Grid 回测 (**已 train/holdout 切分** — split='train'/'holdout', cohort_scheme='institution_L2_pit_20240930') |
| `fact_institution_event` | 机构调研/持仓事件 (KEEP; `fact_jgdy_event` akshare 已物删 2026-06-27 通达信全删 M4) |
| `mart_institution_industry_stat` | 行业级机构统计 |

### 2.4 基本面 / 质量

| 表 | 内容 |
|---|---|
| **`fact_stock_archetype` (22K 行 / 53 列)** | snapshot_date / **net_profit_positive_8q** / **operating_cashflow_positive_8q** / revenue_yoy_positive_4q / profit_yoy_positive_4q / eps_yoy_positive_4q / **high_quality_hits** / growth_hits / cycle_flags |
| `fact_financial_derived` / `fact_fundamental_quarterly` | 财务衍生 / 季度 |
| `fact_stock_fundamental_stage_daily` | 基本面阶段 daily |
| `fact_stock_quality_features` | 质量特征 |
| `raw_aif10_financial_history` / `raw_gpcw_detail` / `raw_tdx_gpcw_wide` | 财务原始 |
| `raw_aif10_valuation_quantile.percentile_fifty` | 估值 10Y 分位 (aif10 源, task#37 待迁/退役; 原 strategy_ensemble 消费者已退役 2026-06-19) |
| `raw_aif10_forecast_consensus.compre_rating_num` | 一致预期评分 (aif10 源, task#37 待迁/退役; 原 strategy_ensemble 消费者已退役 2026-06-19) |
| `raw_aif10_peer_valuation` | 同业估值 |
| **`raw_tushare_forecast`** (业绩预告, 2026-06-14 接入) | **PEAD 预期差事件因子** (alpha 验证程序 S1 第一个基本面接口): type(预增/预减/扭亏/首亏) + p_change_min/max(净利变动幅度) + net_profit_min/max + ann_date(PIT 锚, 早于正式财报). grain=[ts_code,end_date,ann_date]; 实测 17042 行 (2023-2026) |
| **`raw_tushare_income`** (正式利润表, 2026-06-14 接入) | 96 列全套利润表 (total_revenue/revenue/oper_cost/各费用/operate_profit/n_income/ebit/ebitda...) = 质量/成长因子料 (PEAD 后段慢信号). grain=[ts_code,end_date,f_ann_date,update_flag] (uf=0原始/1订正双推送), PIT 锚 f_ann_date 取 uf=1; by_trade_date date_param=ann_date; 实测 4月 10578 行/5305 股. **express/fina_indicator 已注册** (express=express_vip by_period [sync_runner 加 by_period 分支+单测]; fina_indicator=by_ts_code 2023-2026窗口避100条截断), 回填排队 income 后 (单写锁) |
| **`raw_tushare_balancesheet_advrecv`** (预收账款/合同负债, 2026-06-16 注册) | 用户提议"预收账款"前瞻需求因子: adv_receipts + contract_liab (2020 后迁入) + total_assets. PIT 锚 ann_date; by_period (V0 取每期最新修订). **当前落库 7 期非连续止 2020Q3 = 不可用** (allow_empty=true 旧配置静默吃间歇空响应 + 配额墙截断双因); 已配 allow_empty=false + min_rows_per_batch=1000, 待配额恢复重拉连续季报. debate 裁决档C: 修源前禁入 panel |

### 2.5 资金流 / 事件

| 表 | 内容 |
|---|---|
| `fact_hsgt_daily` | 北向资金 daily |
| `raw_lhb_daily` / `fact_lhb_event` | 龙虎榜 |
| ~~`fact_executive_trade_event`~~ | 高管增减持 (akshare; **已物删 2026-06-27 通达信全删 M4**, 用户决cut; 67920行archive留底; 档B 若需从 tushare stk_holdertrade 重接) |
| `fact_shareholder_trade` | 股东交易 (注: tdx_b 变体 2026-06-24 退役; fact_shareholder_trade 自身当前无数据) |
| `fact_holder_event` / `fact_top10_holder_period` / `fact_holder_count_period` | 持股人结构 |
| ~~`fact_dzjy_event`~~ | 大宗交易 (akshare 旧源; **已物删 2026-06-27 通达信全删 M4**, 用户决cut; 2062行archive留底; 档B 若需从 tushare block_trade 重接) |
| **`raw_tushare_block_trade`** (大宗交易, 2026-06-16 注册) | 用户提议: 机构折价/大单方向, stage 内 alpha 增强候选 (moneyflow 抓不到的机构维度). grain=[ts_code,trade_date,price,vol] (同股同日多笔全留), PIT 锚 trade_date (盘后披露, 决策用 t-1); by_trade_date 2023+. **表未建** (配额墙), 配置就绪待拉. debate 裁决档B: 做事件 confirmation 不做连续因子 |
| ~~`raw_capital_*` + `dim_capital_behavior_latest` + `capital_detail_sync_state`~~ | 配股/分红/回购/解禁 (akshare 源). **2026-06-27 通达信全删 M4 已物删 (7表)**: 用户决"cut"不迁移 — 消费侧切 (scoring quality_capital→0 / signals_v2 D5 解禁门→不过滤) + 源头 capital_client+writer 物删 + config 清; archive parquet 留底 25,370 行 (deletion_record run_id=tdx_full_delete_M4_akshare_capital_20260627). 档B 若需从 tushare dividend/repurchase/share_float 重接 |
| `raw_institution_surveys` | 机构调研 raw |
| `raw_qfii_holding_quarterly` | QFII 季度持仓 |
| `raw_org_holding_aif10` | 机构持仓明细 (东财妙想 aif10 MAIN_ORGHOLDDETAIL; 非公募机构 基金/保险/券商/法人/QFII 分桶持本股, report_date 报告期 + 法定披露截止 available_date PIT 锚; 2026-06-24 aif10 例外扩展, 复核确认真·独有 gap=tushare fund_portfolio 仅公募). owner=services/org_holding_aif10.py; data_layers=L0_source (2026-06-24 补 tag, 修 data-layer-integrity FAIL) |

### 2.6 技术 / 形态 / 信号

| 表 | 内容 |
|---|---|
| **`fact_signal_context`** | stock × date / vol_r20 / amt_r20 / amount_20d_avg / price_pos_60d / price_pos_120d / drawdown_60d / **technical_stage** (1/1.5/2/3/4) / built_at |
| **`fact_stock_technical_stage`** | Stan Weinstein 4 stage (1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌) |
| `fact_stock_stage_features` | 阶段特征 |
| `fact_stock_turtle_features` | 海龟特征 |
| **`fact_technical_trigger`** | 公式信号触发 (stock × date × formula_id × variant × strength × state × reason_codes_json) |
| `fact_stock_archetype` (53 列) | 形态原型 (跟基本面共用此表) |
| `fact_setup_snapshot` | ⚠ **0 行 / 未启用** |

### 2.7 Phase ψ 治理 / 调参产物

| 表 | 用途 |
|---|---|
| **`mart_per_stock_stage_strategy_optimal`** | per-stock × variant × stage Optuna 寻优 (Phase ψ R1 后含 OOS 列, 但稀疏信号下大量 governance reject) |
| **`mart_per_formula_stage_optimal`** (Phase ψ.α B) | per-formula × stage × train_end_date 严格 walk-forward 寻优 (反转因子用此表) |
| `mart_formula_horizon_evidence` | per (formula × hp) 全市场合并真实历史涨跌 (无 Optuna 调参, 最干净) |
| `mart_stage_formula_fitness` | cohort fitness (fund × tech × formula × hp) |
| `mart_stock_formula_optuna_v2` | 旧 per-stock × formula × hp 全宇宙 (337K 行) |
| `fact_optuna_governance_log` | Phase ψ governance reject 审计 |
| `mart_market_perception_daily` | Market Perception P1 daily snapshot: regime_score / breadth_state / volatility_state / sentiment_phase, PIT cutoff and built_at |

---

## 3. Service 模块 (231 个 .py 文件, 21 个子包)

### 3.1 调参 / 寻优 (Phase ψ)

| 模块 | 文件 | 作用 |
|---|---|---|
| `services/optimization/` | config.py | yaml loader (governance/walk_forward/search_space/composite/constraints/execution/output) |
| | governance.py | enforce_pre_optimize / enforce_pre_insert (50≤n_trials≤500, sharpe ≤ 5, win ≤ 0.95) |
| | walk_forward.py | split_dispatch (none/holdout/expanding/expanding_monthly/**train_end_forward**) + assert_no_temporal_leak + list_month_ends |
| | oos_aggregator.py | aggregate_oos_metrics (multi-window OOS trades 合并) |
| | composite.py | CompositeWeights.from_config() (7 个权重 ∑=1.0) |
| | constraints.py | HardConstraints (max_dd, streak, worst_loss, min_traded) |
| | objectives.py | 8 个 metric (sharpe/calmar/sortino/pain/ulcer/tail/stability/cvar) |
| | ddl.py | mart_per_stock_*_optimal / mart_per_formula_stage_optimal / fact_optuna_governance_log DDL; log_governance_violations(**manage_txn**: False=与业务表同事务原子提交防 orphan governance, 06-14 D0 发现) |
| `services/backtest/` | optimize.py | optimize_stock_strategy (R1 expanding_monthly 主流程) |
| | realistic_engine.py | simulate_trade (T+1 入场, intraday stop/target, 含 tx_cost) |
| | search_space.py | 5 维 SearchSpace.from_config() (hp/stop/target/trailing/buy_offset) |
| | objective.py | make_objective Optuna 目标函数工厂 |
| | filters.py | is_index_code 等 |

### 3.2 公式 (formula_engine, 4+3 = 7 公式)

| 公式 | 文件 | 类型 |
|---|---|---|
| macd_golden_cross | macd_golden_cross.py | 动量 (DIF 上穿 DEA, variant=above/below_zero, **裸金叉无量能**) |
| turtle_breakout_20/55 | turtle_breakout.py | 动量 (突破 + **量能 > MA20 × 1.3**) |
| dynamic_ma_iterative_cross | dynamic_ma_iterative.py | 动量 (用户 MQL, 4 均线 + 加权重心 + **1 轮迭代过滤假突破**) |
| **reversal_1m_mild** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 4-15% + 60 日低波 + 量比正常) |
| **reversal_1m_deep** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 10-30%) — 验证结论=unknown (reset 清, 以 goal.md 为准) |
| **reversal_1w** (Phase ψ.α) | reversal_short_term.py | **反转** (5 日跌 2-10%) |
| technical_stage (4 stage) | technical_stage.py | classify_technical_stage(closes, volumes) — Stan Weinstein |

### 3.4 Paper Sim v2 (Phase ψ)

| 模块 | 作用 |
|---|---|
| | selector.py | backtest mode 查 mart_per_formula_stage_optimal (Phase ψ.α B), 0 selection leakage; **Phase ψ.β.5 L2**: ensemble mode 可按 vol_60d 缩放 stop/target/trailing per-stock (`_vol_aware_params`, config flag `selection.vol_aware.enabled`); **Phase ψ.γ.2 L3**: ensemble mode 可 JOIN mart_per_stock_stage_strategy_optimal (24K 行 9-dim OOS) 用 per-stock × stage params 覆盖 default (`_load_per_stock_stage_optimal`, config flag `selection.per_stock_stage.enabled`). 优先级: per_stock_stage > vol_aware > default_holding. |
| | driver.py | walk-forward 主循环 + VWAP 成交 + swap 决策 |
| | exit_rules.py | 5 触发优先级 (stop > target_arm > trailing > hp_expired > stage_deterioration) |
| | swap_rules.py | compute_fulfillment / candidate_can_close_gap / evaluate_swap |
| | sizer.py | wilson_kelly position sizing |
| | tx_cost.py | 佣金 + 印花税 + 滑点 |
| | reporter.py | 6 类 KPI (A 用户标准 / B anti-churn / C robustness / D ablation / E sensitivity / F reality_check) |
| | ddl.py | 4 张 paper_sim 专表 (nav / position / trade / kpi) |

### 3.5 候选 / 推荐 / 选股

| 模块 | 作用 |
|---|---|
| `services/buy_signal/` | classify_tier + factor_aggregator + scoring + reasoning + configs + ddl — **6 因子综合 score, 输出 mart_stock_formula_buy_signal_daily** |
| `services/selection/` | logger / outcome / feedback / summary — 选股事件追踪 |
| `services/portfolio_walk_forward/` | metrics.py (CAGR / sharpe / max_dd / calmar / monthly_win_rate), liquidity, ... |
| `services/portfolio_sizer/` | profiles.py 不同风格 sizing |
| `services/trade_plan/builder.py` | 交易计划生成 |
| `services/candle_pattern/` | features (6 维 + 1 突破强度) / evaluator / search_space (4 维 Optuna 阈值) |
| `services/market_perception/` | Market Perception P1: `compute_regime_for_date/range`, PIT-strict market context features written to `mart_market_perception_daily` |

### 3.6 机构 / 行业 / 阶段

| 模块 | 作用 |
|---|---|
| `services/institution_l2_metrics.py` | institution_l2_score_cte (train_best/holdout pair CTE) |
| `services/institution_read.py` / `institution_scoring_read.py` / `institution_write.py` | 机构数据 R/W |
| `services/industry_context_engine.py` | sector_momentum 衔接到个股 fact_stock_industry_context |
| `services/industry.py` / `industry_pit.py` / `industry_overview_read.py` | 行业 PIT + UI 读取 |
| `services/stock_stage_engine.py` | 阶段特征中间事实层 |
| `services/stock_turtle_engine.py` | 海龟形态特征 |
| `services/data_access/` + `config/data_access.yaml` | **SERVE 读侧统一层** (2026-06-22 P1, 数据模块顶层设计 v2 §4, task#52): 读侧唯一取数+PIT+口径清洗点, 消费者全走 `DataAccess.get(entity,codes,start,as_of)->DataResult(rows,provenance)`。薄分发器(__init__)+内部分层防god-module: `keys`(code↔ts_code+日期归一 canonical 单一源, 不变量#1)/`spec`(yaml载入+EntitySpec+provenance信封)/`resolver`(manifest路由+connect read_only 不变量#2+preflight schema自校验)/`asof`(PIT≤t 读层单一执行点)/`cleaner`(code6位/asof ISO 归一)/`drivers/generic`(简单entity取数, 加entity=加yaml条目+可加driver不改本体)。data_access.yaml **21 entity**(kline_qfq/moneyflow/valuation/index_daily/moneyflow_dc/cyq/stk_limit/share_float/block_trade/report_rc/fundamentals/forecast/sw_daily/top_list/top_inst/holders_top10/holders_tdx/sw_industry/dc_member/dc_index/limit_list_d); code_input 3模式(plain/ts_from_plain/ts_passthrough 含指数码+con_code)。血缘=provenance信封声明链。**P1 DONE (2026-06-22): dossier 18 内联裸查全迁完(duck_connect=0), 含 _top10 ann_date 版本锁/sector_membership 3表RANGE-PIT JOIN/market_regime COUNT(DISTINCT)聚合/lhb LIKE 等复杂 loader, 全 parity 验证(逐字或multiset+消费者order/format依赖实测)。** 验收门 = `check_serve_read_layer.py` 单一执法点 (D1 read-no-inline / D2 read-no-self-asof / D3 preflight-wired / D4 lineage-complete / D5 feature-from-l2), red→green 实证 + 5 pytest + moth `serve-read-layer-p1-doors` + wired safe_commit Step 3.9。**门 consumer scope=dossier(P1只迁dossier); signals_v2/routers 等未迁 consumer = P2/P3 债** |
| `services/data_loaders.py` | **feature_panel 物化输入层** (2026-06-19 A0: 从已删 experiment_* 移进 services, 可注入 conn 可测): `load_kline`(L1k market price_kline_qfq_tushare)/`load_moneyflow`(L0 raw_tushare_moneyflow, net+total_flow)/`load_quality_reports`(L0 raw_tushare_fina_indicator, ann_date ISO PIT 锚)/`in_active_universe`(services.universe config 驱动替内联前缀)。分层契约: build 唯一 L0-read 点, 探索只读物化后 panel (L2-bypass lesson)。**注: 与上 data_access 读层职责分工 — data_loaders 是 feature_panel build 专用 L0 物化输入(写侧), data_access 是消费侧统一读层; P2 待评估 load_kline/moneyflow 是否并入 data_access** |

### 3.7 数据源 / 客户端 / sync

| 模块 | 作用 |
|---|---|
| `services/data_sources/` | base / clients_registry / data_routes / fallback / registry — 数据源中央。**tushare 代理网关 2026-06-17 切 tinyshare** (旧 jiaoch.site 反刷量墙弃用): `sources/tushare.py:_pro_api` = `import tinyshare as ts; ts.set_token(授权码); ts.pro_api()` (tinyshare 自带网关, 去 _DataApi__http_url monkeypatch); 授权码进 gitignored .env (TUSHARE_TOKEN); 旧网关解封 stk_surv 机构调研(实测 316 行/日)。sync_registry 已注册 stk_surv 域。**限流(tinyshare): 单接口 120次/分, 多接口 200次/分, 并发 2**(旧 tushare 150/200)。**强制 = config 驱动主动节流**(2026-06-19): 限额在 `sync_registry.yaml defaults.rate_limit`, `sync_runner._RateLimiter` 读 config 每次 fetch_raw 前滑窗节流(撞墙前先睡, no-hardcode 改限额只动 yaml); 瞬态限流措辞退避 `_is_transient_ratelimit`→transient_backoff 作兜底, 真当日墙 `_is_quota_wall` 才停链。**socket 超时根治 hung** (2026-06-19, defaults.fetch_timeout_seconds=120; run_domain `socket.setdefaulttimeout`; 反例: stk_factor_pro 重试 hung 在无超时 socket 71min)。**by_ts_code 断点续拉** `--resume` (`_existing_ts_codes` 跳 target 已有 ts_code, 省重拉)。owner doc=`sources/tushare.py` docstring |
| `services/akshare_client.py` / `tdx_*_client.py` / `block_client.py` / `lhb_client.py` / `xdxr_client.py` / etc. | 各种数据源 client (注: `capital_client.py` 已物删 2026-06-27, 通达信全删 M4 akshare 资本运作退役) |
| `services/kline_source.py` / `market_db.py` | K 线源 + market DB 入口 |
| `services/duck_adapter.py` / `db.py` / `db_health.py` | DuckDB 安全包装 |
| `services/source_watermarks.py` / `source_policy.py` | sync watermark + policy |

### 3.8 其他

- `services/sentiment/` — **情绪因子框架** (factor_registry + bin_assigner + window_calculator + survey_builder). 未集成到主选股
- ~~`services/external_attention.py`~~ — 关注度因子 **已物删 2026-06-27 (通达信全删 M4: akshare 东财人气/关注度退役, 用户决cut, 无tushare等价=永久丢)**; 表 fact_stock_attention_snapshot(16511)/dim_stock_attention_latest(5504) archive留底; scoring `external_attention_score`/`external_crowding_penalty` 恒None(优雅降级, mart_stock_trend 列保留但NULL); 档B 若需关注度信号无 tushare 来源
- `services/event_simulator.py` / `event_engine.py` — 事件模拟引擎 (用于机构跟随 backtest)
- `services/shareholder_plan_*` (3 文件) — 股东计划相关 alpha
- `services/feature_registry.py` / `feature_labels.py` / `feature_retention.py` — 特征工程
- `services/data_lineage/` — 数据血缘
- `services/ml_lifecycle/` — drift / registry
- `services/etf_*` — ETF 子系统 (独立, 不影响个股 alpha)
- `services/trading_config/` — 真实执行模型 (buy_pricing / sell_pricing / slippage / filters / execution_model)

---

## 4. Scripts 入口 (135 个)

> 机器枚举的完整入口/产表/依赖清单 → `FEATURE_MAP.md` (`scripts/chunkyctl map` 重生成,
> 勿手改)。本节只保留人工策展 (哪些重要/怎么用/坑在哪), 计数以 FEATURE_MAP 为准。

按主题分组:

| 主题 | 数量 | 例子 |
|---|---|---|
| `build_*` | 49 | build_formula_signals_history, build_signal_context, build_stock_formula_buy_signal_daily, build_daily_position_recommendations, build_picture_daily, build_architecture_inventory |
| `formula_*` | 1 | **formula_limit_up_pullback.py** (涨停回调十字星选股, S/A/B 三档, YAML 配置 `config/formula_limit_up_pullback.yaml`) |
| `run_*` | 17 | run_follow_backtest (机构跟随), run_optuna_*, run_portfolio_mvp |
| `validate_*` | 10 | validate_exclusion_rules 等 |
| `audit_*` | 5 | **audit_end_to_end.py** (23 项检查) |
| `backfill_*` | 5 | 各种回填 |
| `optimize_*` | 4 | **optimize_per_stock_stage_strategy.py** (Phase ψ R1), **optimize_per_formula_stage.py** (Phase ψ.α B), **optimize_ensemble_full.py** (Phase ψ.γ.1, **20 维 ensemble Optuna**: 13 alpha weights + 2 regime + 3 sigma + hp + max_vol, constrained sharpe, holdout train/test, mart_ensemble_optimal 入库) |
| `rebuild_*` | 2 | rebuild_stage_formula_fitness |
| `replay_*` | 2 | replay_paper_history_signflip |
| `evaluate_*` / `train_*` | 4+2 | 各种评估 + 训练 |
| `cron_*` | — | cron_daily.py (HTTP wrapper for sync) |

### 4.1 主流水线 (顺序严格)

```
1. optimize_per_stock_stage_strategy.py    Optuna 9-dim per (stock × variant × stage)  ~16 min
   或 optimize_per_formula_stage.py        Phase ψ.α B 全局 walk-forward          ~28 min
2. rebuild_stage_formula_fitness.py        fitness 聚合                          ~1s
3. build_stock_formula_buy_signal_daily    buy_signal × technical_trigger        快
4. build_daily_position_recommendations    最终推荐 + 价格                       快
5. audit_end_to_end.py                     23 项检查 (0 FAIL 才算通过)           ~1 min
6. portfolio_backtest.py / run_paper_sim_v2.py   walk-forward NAV + KPI         30 min
```

---

## 5. Routers / API (17 个)

| Router | 主功能 |
|---|---|
| `routers/recommendation.py` | 选股推荐 API |
| `routers/screening.py` | 筛选 |
| `routers/signals.py` | 信号 |
| `routers/institution.py` | 机构数据 |
| `routers/market.py` | 行情 |
| `routers/etf.py` | ETF |
| `routers/updater.py` | sync 入口 (POST /api/inst/update/smart) |
| `routers/workbench.py` | 工作台 |
| `routers/strategy_preset.py` | 策略预设 |
| `routers/v3_*` | v3 系列 (meta / paper / picture / portfolio_builder / selection / views) |

---

## 6. Config 文件 (yaml)

| 文件 | 控制什么 |
|---|---|
| `backend/config/optuna_config.yaml` | Optuna 治理 (Phase ψ Rule 7/8) — governance/walk_forward/search_space/composite/constraints/execution/output |
| `backend/config/field_dictionary.yaml` | **Phase ψ.γ.dict.1** 字段字典 (3 DB × 12 核心表 × 100+ 字段 + 单位 + PIT key + outlier cap + JOIN 模板) — 防 VWAP unit bug 类故障 |
| `backend/config/recommendation_universe.yaml` | 选股宇宙 |
| `backend/config/db_partition_tiers.yaml` | **DB 多库分区 tier** (源/特征/服务/实验) + 原子写簇 (关联性检查); 驱动 `backend/scripts/db_partition_migrate.py` (保真迁移引擎: 原 DDL 含 PK + INSERT SELECT, 非 CTAS; dry-run 默认 + 前后验证[行数/EXCEPT/约束/索引] + 绝不 DROP 源; D1a experiment_store 25 表迁验 PASS [暂缓 repoint, live 耦合重]; **D2-minimal feature_store 2 表 fact_feature_panel+validation 迁验 PASS** [解决 build_feature_panel vs daily_update 写锁竞争, repoint 待定]) — owner=analysis/db_management_design_20260614.md |
| `backend/scripts/db_compact.py` | **整库保真缩盘** (删行后回收盘): ATTACH-copy 逐表原 DDL 含 PK + INSERT + 重建索引 + 视图按定义重建 (依赖容忍重试), **绝不 CTAS** (避 06-12 约束 315→1); dry-run 默认; 验证前 DETACH src (information_schema/约束/索引跨 attach 库会双计) + 逐表行数对账全等才换名, 旧库留 `_precompact_bak`。2026-06-14 实测 smartmoney 26.6G→17.5G (-34%, 333表/4视图/821约束/333索引全等) — owner=db_management_design §13.4 |
| `backend/scripts/db_dead_table_audit.py` | **死表守门** (0行 AND 0字面引用才判死, 保守防误删); 大表过时判定走 lifecycle 分析非本工具 — owner=db_management_design §12 |
| `backend/scripts/db_lifecycle_delete.py` | **生命周期删除执行器** (可复用): 读删除 manifest, 4 道闸 — (1) live守护 word-boundary grep daily_update脚本集+serving/ensemble/routers, 命中REFUSE (`--force` 跳过用于有意删 live 层如地基-reset); (2) action=archive 先 COPY parquet 再删 (drop 则不归档); (3) mart_data_deletion_record 留痕; (4) 残留扫描悬挂视图 + view 处理 + 周期 CHECKPOINT 防 catalog stale。dry-run默认。2026-06-14 地基-reset 删 144 表/视图 — owner=db_management_design §13.6 |
| `backend/config/data_layers.yaml` + `backend/scripts/data_layer_audit.py` + `backend/services/schema_layer_filter.py` | **数据层级框架** (2026-06-14 地基-reset 后立, owner=docs/data_management_framework.md): 8层声明式注册表(L0_source/L1_foundation/L1k_kline/display/infra/L2_feature/L3_model/L4_experiment), 144表全声明layer=单一真相源根治"层级隐式→反复推导+耦合"; audit `--check` 未声明=FAIL强制新表声明; schema_layer_filter 让 schema-init 只建活层表(梳理"删表后启动空重建"recreation loop, 接 schema_core/marts/migrations); moth 断言 data-layer-integrity/minimal-module-main-routers/no-new-godfile 自动执法。**2026-06-15 扩 feature_store 纳管**: audit `_live_tables` 从只扫 smartmoney → 扫 MANAGED_DBS=(smartmoney,feature_store), 否则 L2 分区(fact_feature_panel)静默不受层级执法; fact_feature_panel 声明 L2_feature, L2 层 status partial_rebuild |
| `backend/scripts/check_legacy_flow_integrity.py` | **老流程污染防回潮 gate** (2026-06-14 工具化 reset 6 教训, owner=framework §6): C1 daily_update 无缺失脚本调用(删层必删caller, 防静默degraded假活)/C2 无 wiped 表孤儿引用(238处实测)/C3 append-only(*_history/*_snapshot)必 storage_retention 声明(防无界膨胀=DB巨大根因)。覆盖 schema_layer_filter 之外的污染面(daily_update/散落DDL/config)。moth `legacy-flow-no-pollution` 守; **重构验收 gate**: 重构前红=问题实锤, 老daily_update退役+清孤儿+加retention 转绿。进度 (owner=analysis/refactor_execution_plan_20260614.md): 2026-06-14 A2 完成→**C3 append_only_retention PASS** (3表 retention 声明: dim_stock_tdx_industry_history/raw_profit_forecast_snapshot_daily/raw_tdx_industry_file_snapshot); A3d gate 精度修 (grep 加 -w 词边界防 substring 假阳性[fact_shareholder_plan 误匹配活表 _tdx_f10] + -I/--exclude-dir 跳 __pycache__ 二进制, 238→179); A3a 删 2 真孤儿 config (model_search/champion_registry)→C2 残 149; **A1 daily_update 重写 855→457 行 (删 Step4-8 model/paper_sim/champion + 19 缺失脚本调用, 保留 sync/L1k macd + 加 retention plan/data-health report; DRY 实跑通过)→C1 PASS**; A3b 退役 7 死 serving router (v3_market_perception bundled fallback / recommendation / institution / screening / v3_meta / v3_views / v3_perception_legacy + main.py 注册, app import OK 124 routes)→**C2 149→70**; A3c schema_versions 删 23 wiped version 条目 (版本注册表非 DDL; import/summary 验证 220→195) + 7 config 18 处 wiped ref 加 @archived 标记 (gate 认可豁免; 表均核实 wiped+DB 不存在; yaml 全 valid)→**C2 70→29**; 余 updater_* 死 feature 步骤(29, 子系统被 data_sources/etf live import 故外科清非整体退役) 待。A5 bloat: 删 phase5.duckdb 57M+phase5_exports 101M 死 model 工件 + manifest 去 phase5 分区; archive/ 3.4G reset 回滚网保留待重建 KPI 验证后用户定。**2026-06-15 C2 gate 修(重建表识别)**: `_live_tables`(复用 data_layer_audit, managed-DB live 集) 排除已重建为 live 的 wiped 层表 — fact_feature_panel 重建后 layer 仍 L2 但已 live, 不再误判孤儿(否则其 manifest/config 引用刷爆 stale 41>29); C2 stale 28<=29 ratchet PASS |
| `backend/scripts/check_strategy_validation_integrity.py` | **策略验证完整性 gate** (2026-06-15 P0 制度先行, 8-lens 对抗复审根因 R1/R2 + 判断法典 C-WinReturn 反哺; owner=docs/strategy_validation_contract.md 判断法典节): 4 检查 — anomaly_symmetric(C-R1: experiment_harness 有 tradability_verdict 对称门)/promotion_needs_money(C-R1: record_verdict 拒无含成本证据转正)/kpi_joint_codex(C-WinReturn: kpi_verdict 联合年化+max_dd+胜率×盈亏比)/engine_execution_aware(C-R2: 单一引擎含涨跌停/非对称成本/容量/T+1 open)。验证器纪律 mythos §13: 引擎检查取单文件全维满足非多文件并集 (防旧 portfolio_backtest 残留 marker 污染)。**P0 gate=P1 引擎验收尺**: engine 检查在引擎重建前 FAIL=预期红色规格。moth `validation-r1-symmetric-gate`/`validation-promotion-needs-money`/`validation-winreturn-codex` 守 |
| `backend/scripts/audit_panel_leakage.py` + `backend/config/leakage_consumers.yaml` | **泄漏审计 + 消费者注册表** (2026-06-15 post-reset 去硬编码): audit_panel_leakage 原硬编码 default 目标 (mart_p0a_v4[wiped]/build_feature_panel_duck.py[已删]) → **改 config 驱动** (读 leakage_consumers.yaml `audit_panels`, 空=PASS 无幻影审计; CLI `--panel` 仍可显式覆盖), 修"能不硬编码就不硬编码"违纪 + 解 Step3.5 幻影 BLOCK。leakage_consumers post-reset 对账: `consumers: []`(3 历史消费者脚本+面板 reset 全删)/`audit_panels: []`(旧SQL面板已wipe; fact_feature_panel 是 Python builder+code/date schema, SQL-JOIN 审计不适用)。experiment-discipline moth 门加识别 phaseD_signal_eval.evaluate_signal(共享 harness 满足留档+anomaly)+check_split_discipline(leakage门)。待: dim_stock_tdx_industry 非PIT(通达信)→tushare申万PIT 行业迁移 |
| `backend/services/sandbox_guard.py` + `scripts/sandbox.sh` + `sandbox/` | **探索沙盒边界硬门** (2026-06-17 用户根治探索散进主代码; owner=sandbox/README.md + engineering_governance §Exploration Sandbox): enable_sandbox_guard() monkeypatch duckdb.connect 挡 rw 开主6库 raise SandboxBoundaryError (审计实测沙脚本曾裸连写 market 库) + read_only_main(只读正路) + sandbox_scratch(per-exp); sandbox.sh new/wipe/check (probe 模板带 guard); sandbox/ gitignored 用完删, 唯一跨删存活=experiment_store verdict. moth exploration-isolated-in-sandbox/sandbox-boundary-guard-present 守; test_sandbox_guard 5 测 |
| `backend/scripts/check_sandbox_isolation.py` | **沙盒隔离门 — 实验室产物只留实验室** (2026-06-21 立, 4+次隔离失守根治: sandbox脚本隔离了但产物[主库表/builder/控制面KPI/裁决]在方法确认前 promote 进主项目=污染): C1 backend引用sandbox(FAIL) / C2 控制面文档嵌未promote(confirmed_by_owner=0)实验结果(WARN) / C3 探索runner漏主脚本(FAIL)。wired into sandbox.sh check + safe_commit Step 3.8; test_check_sandbox_isolation 3测(含C2 red→green); promotion纪律 owner=sandbox/README.md |
| `docs/stock_dossier_master_design.md` | **股票档案系统 顶层设计 (立法 owner; 2026-06-21 用户"顶层设计统筹规划")**: 给任一股多维度可解读档案(形态/板块概念/资金/筹码/…可无限叠加), 是主升浪猎手的**认识论地基**(看懂股→选股)。**三层信息架构(2026-06-21定稿, 用户"每日更新vs会变"判据 + 4视角Workflow+3lens对抗融合)**: L1价格形态[已建]/L2每日盘面行为(成交量/量比/换手/主力大单净/量价背离/筹码/RS/个股vs板块相对)[部分]/L3会变属性背景(板块概念行业/基本面/估值/分析师预期/事件催化龙虎榜大宗解禁股东/市场regime门)[待建]; 边界=每日更新本股量价数值流(L2内生t-1) vs 慢变会调整外部属性(L3外生锚ann_date); **daily_basic按字段拆**(换手量比→L2/PE-PB-市值→L3 防估值背书误当形态确认=leakage), **板块按用途拆**(个股vs板块相对位置→L2/板块自身regime资金轮动→L3), **regime是横切门非单股因子**(stage-conditional最外层留Optuna学权重)。核心抽象=**维度解读器协议**(interpret/series/compare/screen+config, 加维度=加模块+config 不动框架); 创世层(感知/判断/谄媚死)+判断法典种子(J1人话参数/J2边界耦合同步/J3默认列表+趋势线/J4维度互不耦合); **后续维度数据底座全已在库**(申万PIT v_sw_industry_pit/东财概念dc_member/moneyflow/cyq_perf); 前端=FastAPI+HTML(趋势线非K线+板块对比叠加+交互调参+before/after叠加)。6阶段roadmap(P1形态成熟[声明式人话config+边界耦合]→P2前端→P3/4/5板块/资金/筹码→P6接回选股fact_rally_stage)。Verdict=PROCEED |
| `backend/services/technical_states/` + `config/technical_states.yaml` | **技术形态识别工具 (形态地基, 后续因子叠加的基础)** (2026-06-21 promote, 经 sandbox state_quantify v1/P0/P1/v3 验证: 全宇宙5421股各状态/子态语义准确+OOS稳+100%覆盖): 给任一股任一时点(日/周/月)识别技术状态+子态+量化特征。**9主态 (D1 下跌侧修复 2026-06-21, probe_v4_descent 重调 软可分0.816)**: 低位横盘/放量突破/上升通道/缩量上涨/**中继平台(新, 填pctile0.475-0.717 GAP, 8.4%)**/高位滞涨/下跌通道(改纯缩量阴跌)/**放量下跌(新, 用户点名缺态, 位置子态 高位出货/低位见底SC/中位延续)**/缩量回踩(瞬时近死态0.01%, 升势回踩=前序态依赖→D4上下文层接管暂占位)。**各自细分**(上升通道→温和/震荡/加速; 放量下跌→位置消歧)。**D2 子态全config驱动**(消除_sub_state硬编码, 评审critical): config 子态规则段(声明式{则,条件:[{指标,大于/小于:阈值名}]})+修饰指标段, classifier 通用evaluator按序匹配, 加/改子态=改config不动代码。**D3 A股涨停修正**(limits.py + config涨停段, 评审critical): 封涨停=缩量被vol_ratio误判'无量假突破'方向反 → raw_tushare_stk_limit硬真相源(up/down_limit已编码板块tier±10/20%不必分板路由)→ 涨停日设需求proxy量比使放量突破不误判 + is_up_limit/is_one_word描述标志 + 涨停突破子态(配config子态规则, classifier零改动); dossier.load_limits接入interpret_stock日线enrich; 实测000513涨停突破子态触发。**D4 上下文层(context.py 两遍架构, 评审决策点1)**: 缩量回踩瞬时死态(0.007%)本质=前序态依赖→pass-2用前序态(as-of≤t-1)refine: 前序窗口主导属升势态+当前mild回调→缩量回踩复活(真实股200股 8.53%); 标 prior_trend(升/平/跌)供位置消歧; refined_dominant=context_state或瞬时dominant。PIT三时点契约(decision_date=t用前序≤t-1+当前t无未来; 边界事件trigger/confirm分离立契约待D5)。dossier interpret_stock apply_context日线; test_context_pit_no_lookahead+test_context_revives_pullback。**D5a 单日K线形态(candles.py)**: 单根开高低收几何→命名(十字星/大阳大阴/锤子/上吊/倒锤/流星/纺锤/一字板); **位置消歧**(锤子=下跌末看多 vs 上吊=上涨末看空 同形, 用prior_trend区分)+ **A股一字板特判**(排假十字星); config单日形态阈值; dossier today_candle; 定位=短期构件非独立alpha。**D5b 命名形态(patterns.py)**: 态序列模板匹配(老鸭头=上升通道→缩量回踩→中继平台→放量突破出水; 圆弧底突破; 顶部派发转跌), 派生纯函数标签(不进软隶属/零独立参数), **PIT三时点(完成bar命中不回贴历史鸭头段)**, provenance主观性分级(中文实战/西方Bulkowski); config命名形态段加形态=改config不动代码; dossier recent_patterns; 实测000027老鸭头+圆弧底突破。**模块化收口**: __init__ 清晰公共API(__all__)+模块架构docstring(features/classifier/coupling/limits/context/candles/patterns 各单一职责)。**D6 前端**(dossier_view): 9态+前序趋势+单日K线+命名形态+RS badge 展示, 浏览器实测截图。**RS 相对强度维度(rs.py, 评审HIGH盲点)**: Mansfield RS 个股 vs 大盘(HS300 000300.SH 真相源 raw_tushare_index_daily)= 强于/弱于/同步大盘, 直服超额HS300>0 KPI(防纳入大盘普涨弱势齐涨股); 正交置信度维度不改7态; PIT(RS只用≤t); config RS段(基准/窗口可换中证500等); dossier.load_benchmark+rs字段; 实测600519强于大盘/000027弱于大盘。横截面RS rank(IBD)留选股层。test_relative_strength。**资金维度③(capital.py)**: moneyflow主力净额20日累计趋势(主力净流入/流出)+ daily_basic换手率自身分位(换手活跃/低迷); config资金段。**筹码维度④(chips.py)**: cyq_perf winner_rate获利盘(0-100量纲)+ 集中度((cost95-cost5)/cost50, 单峰集中/多峰分散)+ 价位(收盘vs weight_avg=获利/套牢)+ 获利盘20日变化(鱼尾派发预警); config筹码段。**筹码精细化①(2026-06-22, 长江《筹码分布因子》分盈亏+goal鱼尾CYQ出货, cyq_perf第一手衍生无重建误差)**: 套牢盘(100-winner_rate=亏损筹码,论文亏损筹码预测力更强)+成本偏度((weight_avg-cost_50)/半宽,右偏=上方套牢)+集中度20日变化(单峰→多峰)+**派发预警(鱼尾出货=高获利盘+集中度松动+价位获利)**; config加集中松动门0.1; 前端L2筹码行加套牢盘/派发预警/偏度。华泰VWAP三角+换手递推重建筹码分布(筹码龄/精细分盈亏统计量)POC已验证(spearman0.826 vs cyq, sandbox/chip_rebuild)留未来增强。test_chip_distribution_warn。 **成交量/量能维度② L2(2026-06-22, vol.py)**: 量比(vol/前20日均量,排当日防泄漏)放量/缩量 + **量价配合**(价涨量增=健康真突破/价涨量缩=顶部背离预警/价跌量增=恐慌出货/价跌量缩=缩量企稳); config成交量段; L1 features已有vol_ratio放量突破内部判据, L2独立成维=量能解读行(双栖不矛盾); __init__ export volume_signals; dossier vol字段+前端L2量能行; test_volume_signals。dossier load_capital/load_cyq + capital/chips字段 + 前端多维度卡; 实测600519主力净流出/低获利盘惜售 vs 000513主力净流入/单峰集中。**模块10个**(+capital+chips), DRY _iso helper。test_capital_and_chip_signals。**主力意图+量价背离(capital.capital_intent+zhuli_intent, 替代旧mingan_flow伪暗盘, 2026-06-21裁决)**: 旧TDX暗盘公式(X_8路径权重×中小单)=伪维度已砍(详见上"暗盘伪维度裁决"); 新设计=明盘(主力大单净net_amount)×价格 量价背离代理, 6象限主力意图(config主力意图段, 明盘×价格方向: 洗盘低吸/缩量阴跌/诱空吸筹建仓/拉高派发诱多/主力推升看多/主力做空出逃) + 量价背离标签(隐性承接/隐性派发/量价一致)。真暗盘需L2逐笔(need_027 order-flow BLOCKED无源)。dossier mingan字段+前端动向行(标日度近似)。test_capital_intent+test_zhuli_intent_minga_price(量价背离6象限)。**模块11个**。**主力资金口径裁决(reconcile wf_e6a0e9e8, 真金白银)**: tushare net_mf_amount **不是主力净额**(=厂商净主动流vol×VWAP, 跟中小单/动量, 与大单主力档常反向corr镜像); 真主力净额=capital.mainforce_net(elg+lg大单净)=东财dc.net_amount同构念(corr0.961)。修: capital_signals+mingan明盘统一走mainforce_net单一真相源(600519实测单日同源9891万); data_loaders docstring纠错(net_mf谎称lg+elg). **2层架构(用户)**: 基础层=价格形态/第二层=量价资金筹码(确认形态)。暗盘=日度粗近似(非真L2 L2_AMO).**同花顺截图核对(15股06-18, 用户暗盘追踪官方账号)**: tushare按供应商区分确认(moneyflow标准/moneyflow_dc东财/moneyflow_ths同花顺); tushare net_mf==moneyflow_ths(同源); 三套主力净额(net_mf/大单净/东财)+暗盘追踪明盘全不同口径(暗盘追踪=L2专有, 天孚ths0.45 vs 截图明盘7.22亿, 日度源复现不了)。主力资金**统一东财单一源 moneyflow_dc.net_amount**(用户决: 与项目概念=东财同源, flow-vendor=membership-vendor红线口径自洽; net_amount≡buy_elg+buy_lg大单净, buy_*为净额万元, 2023-09起)。**暗盘伪维度裁决(2026-06-21 measured, sandbox/mingan_redesign, 用户两轮质疑后双向对抗)**: 同花顺暗盘=L2逐笔专有, 日度moneyflow任何口径不可近似(净额零和镜像→中小单净≡−大单净54%随机; gross买入96%是假象=同花顺只发流入票选择偏差+中小单买入恒正平凡一致, 排序spearman0.283不相关, 全市场分位0/25无判别)。砍伪暗盘(旧mingan_flow X_8×中小单), 资金维度改 **明盘(主力大单净, 89%对齐同花顺明盘)×价格 量价背离代理**: capital_intent+zhuli_intent 6象限(洗盘低吸/缩量阴跌/诱空吸筹建仓/拉高派发诱多/主力推升看多/主力做空出逃, config主力意图段, 明盘×价格方向), 量价背离(隐性承接/隐性派发/量价一致)诚实代理非伪造暗盘金额; 三因子分离(明盘/背离/意图独立)。真暗盘需L2源(need_027 BLOCKED)。弃net_mf/tushare桶+双源flag(单源不需交叉, 去flag顺带消numpy.bool route500)。板块概念行业资金=东财moneyflow_ind_dc(在库,概念+行业, 与个股dc+概念dc_member全东财自洽)。东财数据经tushare API拉(网关聚合东财/同花顺/自有三套)。暗盘14截图标定: **路径权重X_8主驱动**(纯X_8 corr0.830, 换桶0.80几乎不变), 现公式0.806近最优, 误差2.6亿幅度不可复现, 诚实=暗盘方向≈当日K线路径函数与价格部分冗余。iFinD MCP无暗盘(模糊匹配主力净流入), 其主力净流入≠暗盘追踪明盘(同花顺两产品口径不通).**声明式人话 config (P1, J1)**: 状态**公式结构本身进 config**(状态: 人话条件列表 {指标,判断:高于/低于/平缓,阈值:人话单位如"均线斜率高于6.55%",锐度}), 加/改状态=改config不动代码; classifier.py 只持通用 evaluator(解释条件列表成 sigmoid 软门取积)+软隶属度+子态+多TF。**软隶属度**(softmax 温度, 一股可部分属多态+报熵)替 argmax(覆盖70→100%); **多时间框架**(日主+周/月确认 mtf_aligned, 窗口按TF缩放, _asof bisect); PIT(特征≤t, 量基准排当日)。**边界耦合 resolver (P1, J2; coupling.py)**: config 边界耦合 段声明状态间边界关联(上升退出↔下跌进入互补对称/低位<高位价格分位互斥/放量>缩量量比中性带), `apply_coupling` 调一个→镜像同步+人话变化说明, `with_overrides` 产 effective config 供 before/after 重分类, `list_tunables` 枚举可调边界(前端滑块来源)。API: compute/classify_bar/classify_series/classify_stock/classify_multi_timeframe/apply_coupling/with_overrides/list_tunables。test_technical_states 8测(PIT无前瞻+声明式evaluator语义+J2耦合镜像/override/枚举)。真实股600800回归100%covered/7态合理/多TF186天一致; J2 demo放宽斜率阈→上升通道180→296天。待: per-TF tune(现周月用日线参数=近似)+物化fact_stock_technical_state+FSM破位门 |
| `backend/services/dossier.py` + `backend/routers/dossier.py` + `routers/static/dossier_view.html` | **股票档案前端视图 (P2; 维度①form 解读器 + FastAPI + 自包含HTML)** (2026-06-21, owner=docs/stock_dossier_master_design.md): form 维度解读器 (interpret_stock 单股多TF解读+趋势线+可调参数 / screen_pattern 列符合形态的股票 / trend_series 趋势线非K线按主态着色) + 路由 `/api/dossier/{stock,screen,tunables,view}` (注册 main.py) + 自包含 HTML 档案视图。**实现用户J3/J5/J2/前端要求**: 多TF解读卡(日/周/月状态+人话描述+子态) / 趋势线非K线(SVG分段着色 绿升红跌灰横盘) / 形态筛选列符合股票+mini趋势线 / 滑块调参(⇄标耦合) / 边界耦合同步+人话变化说明 / before-after叠加(实线浅+虚线亮)。J5诚实报冲突: 600800 日跌/周回踩/月升=多框矛盾并列不藏。**默认值/恢复/全体对比 (2026-06-21 用户要求+扩展)**: 默认值=config当前值(探索v2, 前端显版本+每滑块标默认), 恢复默认全部 + 点数值复位单项(resetOne) + 偏离默认高亮计数; 修改vs默认 单股趋势叠加(实线浅默认/虚线亮调整后)。**扩展覆盖盲点** (compare_distribution + /compare): 调参真实影响在**全体层面**非单股 — 扫200股双config分类报每形态股票数Δ+翻转股票+盲点提醒banner(Δ暴涨=阈值松涌入垃圾股); 实证 放宽上升通道斜率→镜像下跌通道松→下跌通道+11/低位横盘-10。性能: compare/screen只载近600日(非全史)scan200约10s。**浏览器实测**(preview)截图证。test_dossier 4测。**前端浅色重做+机构档案L3 (2026-06-21 用户决: 按reset前v3浅色配色方案设计当前项目, 机构档案在三层语境整体设计)**: dossier_view 切 claude_design 浅色系统(--bg#FAFAFA/--brand#3657D8/--ink#111827/语义色ok绿warn橙bad红/圆角pill); 三层卡可视化(L1价格形态brand蓝/L2每日盘面ok绿/L3属性背景warn橙); **L3机构维度接入**(dossier.load_top10_holders读smartmoney.fact_top10_holder_period十大流通股东free口径+本季动向新进/退出/增持/减持+机构动向加减仓=用户最初设想的跟随策略基础; 数据坑: report_date格式不统一带/不带横线须REPLACE规范化; 600519那季全退出行=数据缺返None前端graceful)。实测000513减仓/000651格力减仓/as_of06-18。注: reset前完整v3 React前端(design/含institution-view)git历史在但不恢复(用户决), 按其配色重设计. **L3板块/概念sector_context落地(2026-06-22 ③, sector_context.py)**: dossier.load_sector_membership(申万行业 v_sw_industry_pit PIT in/out_date + 东财概念 dc_member 最近快照≤t JOIN dc_index 取概念名/热度; **坑: dc_member.name=个股名非板块名**, 概念名/热度须 JOIN dc_index)+load_sector_kline(sw_daily ts_code=l1_code 直接对应无后缀转换); interpret_stock 接 sector_regime(申万一级行业指数 vs HS300 超额+趋势=风口在不在)+个股vs板块(复用 rs.relative_strength bench 换行业指数 + bench_label='板块' 参数)+concept_labels(热度 top-N+热门标记); 前端 sectorCard(行业归属/板块regime/个股vs板块/概念热度). **数据底座修复(真金白银 grill 抓)**: sw_daily 断档(全表仅2019全+2020到7月+2026年6月, 2021-2025整段缺)→ `sync_runner --domain sw_daily --drain` 日历 gap 回填. 口径红线 J6=行业申万/概念东财. test_sector_regime+test_concept_labels. **L3④ 基本面/估值/分析师预期落地(2026-06-22, fundamentals.py)**: dossier.load_fundamentals(fina_indicator **ann_date PIT** 锚最近已公告财报, roe_yearly年化跨期可比/netprofit_yoy成长/grossprofit_margin/debt_to_assets) + load_valuation(daily_basic trade_date PIT, pe_ttm/pb 自身历史分位低估<30/高估>70) + load_analyst_reports(report_rc report_date PIT 近6月, **tp单位实测=0.0001元 loader/10000归一化** mythos§8, median+sane band抗outlier); fundamental_signals(优质/一般/偏弱) + valuation_signals(低估/合理/高估) + analyst_expectation(看好/分歧, A股研报正向偏看上行空间, imp_dg全NULL不做评级调整); 前端 fundamentalsCard; config 基本面/估值/预期 段. **业绩预告forecast (2026-06-22, fundamentals.forecast_signal)**: raw_tushare_forecast(已在库零拉取) type现成方向(预增/扭亏/略增/续盈=利好; 预减/首亏/续亏/略减=利空)+ p_change净利同比中值 → 利好/利空 + 高增长前瞻(利好+中值>=50); dossier.load_forecast **ann_date PIT**(取≤t最新公告; 同日多版本冲突18股-期如002141略减vs预增 → 不伪造方向标"存疑中性" measured-not-estimated); PEAD前瞻=dossier最大空白(鱼头催化, 实测300308主升浪期预增+69.6%高增长前瞻); 前端fundamentalsCard业绩预告行. test_fundamental_signals+test_valuation_signals+test_analyst_expectation+test_forecast_signal. **L3⑤ 机构切tushare+事件催化落地(2026-06-22, events.py)**: (a)**机构切tushare**(用户点名): 注册 sync_registry top10_floatholders(api top10_floatholders, by_ts_code全拉, grain[ts_code,end_date,holder_name], ann_date PIT, universe_filter)→backfill→ dossier.load_top10_holders **切 tushare top10_floatholders主源**(_top10_tushare: **动向走tushare现成hold_change**(2026-06-22 用户"能获取的就不计算"; 实测核证 NaN=新进无前值/0=持平/+=增持/-=减持, 不自己跨季算)+ **退出唯一须跨季diff**(离榜股东当期快照给不出)) + **tdx F10降备援**(_top10_tdx, §4.3旧源热备不删, tushare空fallback). (b)**事件催化**: events.py 3纯函数 lhb_signal(龙虎榜top_list净买+top_inst机构专用席位净买→共振抢筹/净卖)+block_signal(大宗block_trade成交价vs收盘折溢价→折价抛压/溢价承接)+unlock_signal(解禁share_float **float_date前瞻事件** ann_date≤t已知0泄露→未来90日解禁占比预警); dossier load_lhb/load_block_trade/load_share_float(trade_date PIT); 前端eventsCard; config 事件段. test_lhb_signal+test_block_signal+test_unlock_signal. **L3⑥ 市场regime门落地(2026-06-22, regime.py)=三层最后一维**: market_regime 纯函数(大盘指数 raw_tushare_index_daily 000300.SH 真MA斜率+价位→牛市/震荡市/熊市 + 涨停情绪 raw_tushare_limit_list_d[limit U涨停/D跌停/Z炸板] 净涨停U-D+炸板率Z/(U+Z)→情绪强/中/弱); dossier.load_market_regime(横切非单股 market-wide, PIT trade_date<=t, 所有股共享当日大盘环境); 前端 layer3Rest 占位换 regimeCard; config regime段(趋势窗口/情绪强弱门/炸板率门 yaml-back). 横切=stage-conditional策略最外层门(大盘环境决定form/factor权重). test_market_regime. **三层架构 L1价格形态+L2每日盘面+L3属性背景 全维度完整**. 待: 多套Optuna预设 + 接主升浪D下游选股 |
| `backend/scripts/build_experiment_store.py` + `data/experiment_store.duckdb` | **S0 实验台留档基建** (alpha验证程序, owner=alpha_validation_program_spec §8): 隔离 L4 库 (与 live 写锁/数据隔离防污染) 4 留档表 — fact_experiment_verdict(verdict/prereg_hash/judges) / fact_consumer_alpha_ic_scan(data_snapshot×consumer×metric PIT as-of) / pipeline_artifact_lineage(input/output hash 防回溯泄漏) / experiment_pit_audit_log(每步PIT校验); manifest active。**实验三段纪律固化 (2026-06-15 用户)**: `services/experiment_store.py` 共享留档写入器 (每实验 import+调 record_ic_cells/record_verdict/record_pit_check/record_artifact, 路径走 manifest, 防散落JSON) + `services/experiment_harness.py` (leakage_gate 事前 pit_guard 行为门不过BLOCK / anomaly_verdict 事后 §4.2 红线标 pending_ablation 不直接用/弃) + moth `experiment-discipline-tooled` 强制每个算 OOS IC 的 experiment_*.py 三段全走 (缺任一 FAIL); 5 实验全 retrofit, IC cells 留档 16→101。**R1/R2 制度化加固 (2026-06-15 P0)**: experiment_harness 加 `tradability_verdict` (C-R1 对称门: IC>0 但含成本净收益≤0→IC_POSITIVE_BUT_UNTRADABLE, 补 anomaly 单边盲点) + `kpi_verdict` (C-WinReturn 联合门: 年化 AND max_dd AND 月胜率 AND 胜率×盈亏比期望, 胜率=诊断量); experiment_store.record_verdict 加 C-R1 转正 guard (`confirmed_by_owner=1` 无含成本证据 raise). **C-LEAK 转正门 + leakage 门去自批 (2026-06-15 用户拷问"自批skip=门是摆设")**: record_verdict 加 `_has_leakage_clean` guard (confirmed_by_owner=1 须带 leakage-clean 证据[judges 含 leakage_gate/pit_audit 显 clean] 否则 C-LEAK BLOCK — commit-skip 够不到的转正门强制) + phaseD_signal_eval 把 gate 带入 judges; safe_commit Step3.5/3.6 **移除 SKIP_LEAKAGE_AUDIT 自批逃生**改硬 exit (误报=修 verifier 非 skip, verifier-only commit 不触发门=无死锁); 防御纵深=commit硬门+转正门+CI(终极). moth `validation-promotion-needs-leakage-clean`/`leakage-gate-no-self-bypass`; red→green 测试 (money但无leakage→C-LEAK raise). **P2 阶梯 R1 加固 (2026-06-15)**: experiment_harness 加 `block_bootstrap_return_null` (N1 armory: 含成本持有期收益块自助 -> P(累计<=0), 与 rank 显著性正交的绝对收益 null); Gate2 (experiment_ablation_gate2) 两级转正 (N3: REAL_EDGE->STAT_EDGE_CONFIRMED 排序统计显著非 money, confirmed_by_owner=0, money 转正须 tier2) + cohort/top-K 绝对 forward 报告 (N1); cell-scan (experiment_layered_segment_ic) 加 DSR 多重比较去偏 (N17: n_trials=实际cell数, n_eff=n_days/horizon 重叠校正 N15)。25 单测 (test_experiment_harness_codex + test_portfolio_execbacktest). owner=docs/strategy_validation_contract.md 判断法典 |
| `backend/scripts/experiment_consumer_alpha_validation.py` + `backend/config/experiments/consumer_alpha_matrix.yaml` | **S0 consumer_alpha 验证执行器** (config 驱动, reset 后重建; 复用对象 optimization/walk_forward runner 已删故新建非复活 god-dispatcher): 读 (数据x消费者) 矩阵 yaml (6 候选→7 cell, 映射铁律 event/fundamental/chip/infra→feature_ic, technical→formula_signal) + `experiment_jobs.yaml` `consumer_alpha_validation` family 契约 → gate-before-run (plan().blocked_reasons) → 枚举 cell → S0 dry 空矩阵 (不写假IC) → 写 verdict/lineage/pit_audit 留档 + verdict JSON 落 analysis/。死亡条款守: 矩阵轴走config(判断死, moth `consumer-alpha-axes-in-config-not-code`)/prereg_hash+`--check-prereg`(谄媚死)/PIT每步落档(泄漏死)/dry不造假(估计死)。IC计算留 S3。`backend/services/experiment_jobs.py` 契约loader 同恢复 (337L薄/纯yaml校验/误删, 修4处悬空import) |
| `backend/config/experiments/formula_candidates.yaml` + `l0_bare_kline_baseline_spec_20260614.md(已删·重启清理)` | **L0 裸K线基准 + 公式候选库** (用户 2026-06-14: 公式全保留为 config 备选省重建 + 裸K线寻优最佳OOS参数作基准 + **不要过拟合**): 9 公式索引 (全 ohlcv_only, 信号参数 yaml 全幸存, 评估器 macd live/其余 recoverable@639e0dfb~1), active 子集 4 (防过拟合池子小) / 其余 candidate 待解锁; L0 spec 定义=walk-forward OOS 寻优最佳参数标尺, 防过拟合第一约束 (OOS选参/DSR/pre-reg/限维度/诚实报弱, 复用幸存 optuna_config.yaml 治理; moth `optuna-require-walk-forward`/`optuna-realistic-sharpe-cap`/`l0-baseline-pool-bounded` 固化); 待重建 walk_forward OOS 引擎+治理层 (reset 删) |
| `backend/services/portfolio_walk_forward/oos_ic.py` | **L0 walk-forward OOS RankIC 核心** (Tier-1 引擎心脏, reset 删 runner 后重建): 纯函数无 DB 耦合 — forward_returns(PIT前向收益,只用未来不回看)/cross_sectional_ic(单日截面 spearman, numpy rank 不依赖 scipy, 样本<3→None)/expanding_monthly_windows(R1: min_train6月/forward1月/min_total12月)/oos_rank_ic(只用 OOS test 聚合日度IC→oos_rank_ic+ic_ir, embargo_days 切窗末跨界天[对抗审计修死闸], ic_ir 无偏 ddof=1, 无足够窗→None标unknown)。防过拟合: 选参只看 OOS 不看 train; unknown 不当 0。两层引擎共享窗口+标注原语。14 单测 (red→green PIT + 审计回归 embargo/完整窗) 入 CI |
| `backend/services/formula_engine/features.py` | **裸K线公式→连续PIT特征提取器** (L0 Tier-1): active 4 公式从核心机制派生连续特征 (MACD柱/MA距离/Donchian通道位置/反转), param 驱动读 formula_*.yaml; feature[i] 只用 bars[:i+1] (PIT); warmup→None。**2026-06-19 A0-1**: 加 3 主升浪 stage 因子 (feature_momentum 鱼身延续/feature_moneyflow_trend 资金确认/feature_asof_quality 财务as-of), 从已删 experiment_* 恢复进 services **消除 build_feature_panel→experiment 倒挂** (单测 test_stage_factors 5 passed PIT边界) |
| `backend/services/portfolio_walk_forward/pit_guard.py` | **PIT 行为门** (防泄露固化, 黄金标准前瞻检测): feature[i] 对追加未来 bar 不变否则=lookahead泄漏; 公式无关, 抓任何 rolling/EMA/未来引用 bug; red→green 测验它能抓植入泄漏 |
| `backend/scripts/experiment_l0_baseline.py` | **L0 裸K线基准驱动** (Tier-1 RankIC): v_price_kline_qfq→PIT特征→前向收益→walk-forward OOS RankIC→experiment_store (consumer_id=L0_baseline_<formula>)。**防泄露 3 门固化内联** (门1 PIT行为/门2 切分纪律 check_split_discipline/门3 异常红线 check_metric_anomaly, 任一失败BLOCK, moth `l0-leakage-gates-wired` 反孤儿守)。默认参数=测量; **`--search` 寻参模式** (#17 已实现): 经 plan_validator 闸+search_formula 网格寻 best-OOS-params+DSR, 写 L0_search_*。pre-reg d80e8ce 冻结+grill 后 RUN; **标尺=reversal +0.064 (lookback=20)**, 寻参佐证默认近最优 |
| `backend/services/portfolio_execbacktest.py` + `backend/config/backtest_execution.yaml` + `backend/tests/test_portfolio_execbacktest.py` | **Tier-2 execution-aware 回测引擎 (2026-06-15 P1 重建; 旧 portfolio_returnbacktest[clean但 R2 缺陷:close 无条件成交]已删, 旧 portfolio_backtest.py[5-07]退役标P2)**: 根因 R2 "信号!=可交易头寸"修复 — T+1 **open** 入场(非close, N14) + 涨停一字板剔篮/跌停顺延(N8/N12) + **非对称成本栈**(卖方印花, N13, config 镜像 paper_sim_momentum tx_cost) + 停牌冻结(缺价不剔篮不归零, N11) + 容量诊断(参与度 vs ADV + 大单溢价, N10, 不编造冲击系数守 measured) + **仓位 policy**(equal/rank/inverse_vol + 空槽留现金 = 连续 exposure 雏形, N4/N6); 联合 metrics(年化/max_dd/sharpe/calmar/月胜率/段胜率/盈亏比/正期望, C-WinReturn)。微结构真相源=backtest_execution.yaml(涨跌停镜像 universe_rules/dim_price_limit_rules, 成本镜像 paper_sim_momentum, 防双真相源)。14 单测手算证伪门(T+1 open/一字板剔篮/非对称成本/停牌冻结/容量/sizing/联合metrics/config加载/**trailing多窗**)。**trailing_metrics (2026-06-15 用户)**: 分 近3/6/12/18/24月/3年/5年/全期 窗口报 年化+月胜率+max_dd, 看策略趋势衰减 (全期均值掩盖: mf_trend 全期+2.53% 但近3m -27%/近24m +14.6% = 近期失效); harness evaluate_signal 自动打印趋势表+入 json。moth `validation-engine-execution-aware`/`validation-integrity-gate-green` 守; gate check_strategy_validation_integrity 4/4 PASS。P3 含成本裁决具体回测数字 reset 清(见 goal.md); 留机制: IC≠可交易(R1)/R2 四类摩擦(T+1 open/一字板剔篮/非对称成本/容量) |
| `backend/services/optimization/` (deflated_sharpe/plan_validator/formula_param_search) + `backend/config/experiments/formula_search_spaces.yaml` | **L0 寻参治理层** (reset 后最小重建, 只 L0 RankIC 寻参所需非复活策略机器): deflated_sharpe(Bailey-LdP DSR 多重比较去过拟合, stdlib 替 scipy=erf+Acklam) + plan_validator(搜索空间非空闸, 防 29/34 白跑反例, 空→raise) + formula_param_search(网格穷举寻参, 目标 OOS RankIC, 只读 OOS, DSR deflate, 受 plan_validator 闸); search_spaces 小网格(每公式3-9组合=防过拟合)。moth `l0-search-governance-wired` 反孤儿守。寻参 RUN=task#17 (pre-reg+grill, 大计算 Modal) |
| `backend/config/experiments/retired_experiments.yaml` | **退役实验知识库** (实验模块 config 子目录): 模型/寻优层删全表时把"用了什么(字段族/年限/工具/结论)"留这替代留全表 (用户 2026-06-14: challenger 只留摘要不留全表); 参数寻优重做的历史参照; 14 子系统 (公式工厂/p0a-p0b/multidim/synergy/drift/paper_sim/stage-opt/horizon/market_perception/特征搜索/research_chains 等) |
| `backend/config/pipeline_performance_policy.yaml` | step budget 预算 |
| `backend/config/data_sources.yaml` | 数据源 |
| `backend/config/storage_retention.yaml` | 保留期 |
| `backend/config/pricing_label_policy.yaml` | 定价标签 |
| `backend/config/feature_registry.yaml` | 特征注册 |
| `backend/config/tdx_data_need_coverage.yaml` | TDX 数据需求/source priority/迁移建议 catalog，供 `audit_tdx_data_need_coverage.py` 物化到治理表 |

---

## 常用命令 cheatsheet (复制即可跑)

### 安装 (新人首次)
```bash
git clone https://github.com/dare2live/chunkymonkey.git
cd chunkymonkey
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
pip install pre-commit && pre-commit install   # 强制 PROJECT_INDEX 同步检查
```

### 数据 backfill / Optuna / paper_sim 运行手册

> **2026-06-14 地基-reset 移除**: 模型/特征/寻优/paper_sim 层 (build_signal_context /
> backfill_risk_factors / optimize_per_formula_stage / run_paper_sim_v2 等) 已删, 参数寻优从零重做。
> 数据获取 (raw/dim 同步) 走 `sync_runner` (sync_registry.yaml); 重建路线见 `goal.md` 重建路线 +
> `alpha_validation_program_spec_20260614.md(已删·重启清理)`。地基同步: `scripts/daily_update.sh` (手动)。

### 数据查询 (常用诊断)
```bash
# 查 mart 表最强 setup
duckdb data/smartmoney.duckdb -c "
SELECT formula_id, stage_filter, COUNT(*) AS n,
       ROUND(AVG(oos_sharpe),3) AS avg_sh,
       ROUND(AVG(oos_win_rate)*100,1) AS win
  FROM mart_per_formula_stage_optimal
 GROUP BY 1, 2 ORDER BY avg_sh DESC LIMIT 10"

# 查 PIT 数据 freshness
duckdb data/smartmoney.duckdb -c "
SELECT 'risk_factors' AS t, MIN(calc_date), MAX(calc_date), COUNT(*) FROM fact_risk_factors
UNION SELECT 'financial', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_financial_pit_daily
UNION SELECT 'capital_flow', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_capital_flow_pit_daily
UNION SELECT 'signal_context', MIN(date), MAX(date), COUNT(*) FROM fact_signal_context"
```

### 测试 / 验证
```bash
# 全部单测 (paper_sim + optuna + backtest + ...)
cd backend && PYTHONPATH=. pytest tests/ -q

# 地基模块测试 (db/层级/同步)
cd backend && PYTHONPATH=. pytest tests/test_db.py tests/scripts/test_db_compact.py tests/test_source_watermarks.py -q
```

### Pre-commit 测试 (避免 hook reject)
```bash
# 改完代码后 staged
git add backend/services/your_file.py

# 测 hook (会告诉你需不需要改 PROJECT_INDEX)
python3 backend/scripts/check_project_index_sync.py; echo "exit=$?"

# 如果 exit=1 → 改 PROJECT_INDEX.md 加进 §14, 然后 git add PROJECT_INDEX.md
# 如果 exit=0 → 可以 commit
```

## 7. CLAUDE.md 规则栈 (现 9 条)

```
Rule 1: Think Before Coding         — 列假设, 不确定就问, push back
Rule 2: Simplicity First            — 最少代码, 不 speculative
Rule 3: Surgical Changes            — 只改必须改的
Rule 4: Goal-Driven Execution       — 定义成功, 循环验证
Rule 5: Root Cause Over Patches     — 不打补丁, 找根因
Rule 6: Measured, Not Estimated     — 不估算, 必须实测
Rule 7: Anti-Look-Ahead / Leakage   — 普适, 时间维度诚实
Rule 8: Optuna 治理                 — Rule 7 在调参层落地, config-driven
Rule 9: 真金白银 / 第一性原理       — 用户视角严苛门槛
```

---

## 8. 已知坑 / 未启用 / 需要修

| 项 | 状态 |
|---|---|
| **vendor rank 字段 = 分页伪 rank** | [陷阱-永久] `moneyflow_ind_dc.rank` 是每 50 行循环的分页序号 (三评委独立复现 vs 自算全量 rank spearman 仅 0.07-0.084)。**一切 vendor rank/序号类字段必须自算全量截面 rank**, 禁止直接当因子 (E9 纪律件, 2026-06-11) |
| `mart_sector_momentum` 只 41 行 (2026-04 起) | [BLOCKED] 没历史回测能力, **需 rebuild 全期** |
| `fact_setup_snapshot` 0 行 | [BLOCKED] 未启用 |
| **5 alpha 主源数据 PIT 时序** | [PASS] β.1 fact_risk_factors / β.2 fact_financial_pit_daily / β.3 fact_capital_flow_pit_daily backfill 完成 (跨 2023-01 → 2026-05) |
| **fact_institution_event 主 alpha** | ⚠ 只 1 年 (2025-04 起), 无法做 800 天 backfill — β.3 改用 lhb+exec+holder 替代 |
| **mart_stock_trend.action_score (机构跟随主 alpha)** | [BLOCKED] 仍是 latest 快照 — 未做 PIT 重建 (依赖 fact_institution_event 1 年限制) |
| **aif10 估值/一致预期** | [BLOCKED] 全 latest 快照, 无 PIT, β.2 改用 fact_financial_derived 替代 |
| **case-based / k-NN 历史相似回测** | [BLOCKED] 未建. 数据基础已有 (fact_signal_context + archetype) |
| **`fact_regime_state` 在 paper_sim** | [PASS] Phase ψ.β.4: ensemble selector regime_gate (bear 0.3x / sideways 0.7x / bull 1.0x) |
| sentiment/ 包未集成 | ⚠ 8 文件框架, 未对接 |
| 大盘指数 K 线 在 paper_sim 当 benchmark | [PASS] 已用作 excess vs HS300 |
| **fact_signal_context 早期数据缺** | [PASS] Phase ψ.β.4.5 backfill 完成 (2024-03 起, 66% valid_stage) |
| **fact_stock_technical_stage 早期缺** | [PASS] Phase ψ.β.4.5 backfill 完成 (2023-09-12 起, 2.4M 行) |
| **mart_per_formula_stage_optimal train_end 范围** | ⏳ 正在重跑 (1260 任务, 5 worker, 含 7 公式 × stage × 35 train_end) |
| **Optuna 跑批 8h 慢** | [PASS] Phase ψ.β.perf 修 hotspot: _idx O(1) cache + backtest_signals_with_trades 避免重跑 simulate_trade. 重跑预估 3-4h |
| `fact_stock_archetype` (基本面质量) 只 2026-04 几天 | ⚠ 未 backfill 历史 (待后续 audit) |
| `fact_financial_derived.revenue_yoy` 对部分股 (如 000001) null | ⚠ derived 表本身 sparse, 不影响其他股 |

---

## 9. 关键术语速查

| 术语 | 含义 |
|---|---|
| **IS** | In-Sample, 调参用的数据 |
| **OOS** | Out-of-Sample, 调参后**没看过**的数据上的表现 (实盘只能 OOS) |
| **R1** | 严格 walk-forward — 用户指定标准 |
| **expanding_monthly** | R1 严格模式: 每月底切, 累积 train + 当月 OOS |
| **train_end_forward** | Phase ψ.α B: train < d, test = [d, d+forward_days], 写多行支持 paper_sim point-in-time 选 |
| **leakage** (selection) | t 时选股用了 t+ 才能算的指标 (例 mart.sharpe 全期合并) |
| **leakage** (look-ahead) | 特征用了未来 K 线 |
| **CAGR** | (final/initial)^(252/n_days) - 1 — 复利年化 (不是单笔 × N) |
| **technical_stage** | 1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌 (Stan Weinstein) |
| **mart_** | 业务表 (报表 / 聚合) |
| **fact_** | 事实表 (实际发生) |
| **raw_** | 原始数据源 |
| **dim_** | 维度表 (静态 / 缓变) |

---

## 10. 已实测数据点

> 2026-06-17 清验证墓地: reset 前/污染期所有 OOS 数字 (reversal sharpe / per-stock×stage 表 / momentum) 已作废清除 — 建于已删模型/寻优层 + 污染 universe。当前实测态 = **unknown**; 结构型主升浪 GT 已重建, 逐数据 alpha 验证待跑。以 `goal.md` + `scripts/chunkyctl doctor --fast` 实测为准, 不引用文档旧数字 (CLAUDE §4.2)。

---

## 11. 我 (Claude) 容易踩的坑 (Rule 9.5 沉淀)

| 坑 | 教训 |
|---|---|
| "项目主要数据是 K 线" | **全错**. 6 大数据维度都有. 下结论前先 grep 所有 fact_/mart_/raw_ 表 |
| "momentum 公式失效 → 项目无 alpha" | 错. 项目还有机构跟随 (0.40 主 alpha) + 估值 + 一致预期 + 情绪 + 行业 + 大盘 regime |
| "MACD 是裸的" | 错. 跑 Optuna 时叠加 4 维 K 线形态过滤, 不是裸金叉 |
| "上升趋势 (stage=2) 反转完全无效" | 错. 是**粗糙公式**判 stage=2 回调失败, stage=2 回调本身是合理买点, 需要更精细 |
| "估算 2 min 跑完" → 实际 28 min | Rule 9.5: 不实测就估算 = 失败. 估时间也要小样本先测 |
| **paper_sim selector 用 mart_per_stock_*_optimal sharpe 排名** | 这是 selection leakage. 修正: walk-forward selector (Phase ψ.α B 已修, 但只对 reversal). 整体业务应走 ensemble |
| "对话压缩后还在用旧 context" | 修正: 每次启动**先读这个文档 + CLAUDE.md** |
| **schema SQL 串里 `--` 注释含 `;`** | executescript 朴素按 `;` 切语句, 注释内 `;` 把注释劈成两半 → 后半非法 SQL ParserException (2026-06-23 Stage④-2 我加的 `Stage④; 防...` 炸 CI test_db schema-init). 修: schema_core/schema_migrations 的 SQL 注释**禁含分号** (line195 已记 DDL 禁 `--` 截断, 此为分号变体) |
| **删表/改SQL后失败按单一模式 grep 一刀切判 pre-existing** | 2026-06-23 我 grep 表名判"32失败全pre-existing", 漏 `syntax error at 防` 这类非表名错 (实际我引入3个). 教训: measured-not-estimated, 删表/改SQL后失败**逐个核因**, 不靠单 pattern; 不确定的用 git stash 跑 baseline 对比 |

---

## 11.5 待办 / 当前 Phase

> 2026-06-17: reset 前的待办清单 / Performance Profile 跑批时间 / Phase ψ 进度 (绑定已删 build_signal_context / risk_factors / optimize_per_formula / paper_sim_v2 流水线) 已清除。当前阶段板 + backlog 以 `goal.md` Active Priority Board 为唯一真相源; 完成项 / 历史证据在 `analysis/project_state_ledger.md`。

---

## 13. 写本文档的源数据 (供刷新)

```sql
-- 项目自己维护的架构 inventory (smartmoney.duckdb)
SELECT * FROM mart_architecture_inventory_summary ORDER BY built_at DESC LIMIT 1;
SELECT * FROM mart_architecture_inventory_asset WHERE run_id = ?;
SELECT * FROM mart_data_health;
SELECT * FROM mart_data_source_watermark;
```

---

## 14. Session 增量更新日志 (已归档)

> 246 条历史增量已移至 `analysis/project_index_changelog_archive_20260611.md` (2026-06-11 文档治理)。
> 新增历史叙事写 `analysis/project_state_ledger.md`; 本文件只维护上方活索引与最近 7 天增量。
