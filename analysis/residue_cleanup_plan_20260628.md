# 残留清理执行计划 (2026-06-28)

> 来源: 残留审计 workflow wzyo01mki (81 agent/5.2M tok, 26 唯一残留) + 补扫 wysx3nom4 (8 维度, pending)。
> 状态: 待执行 (用户决议=先补扫完成合并→一次性清, loop-until-dry 避免来回)。当前仅只读审计完成, 未改任何代码。
> 上下文: 纯数据平台, 唯一源 = tushare + aif10 例外 (CLAUDE.md §4.3)。本 session 已退役机构+事件 serving (commit 95542426)。

## 0. 用户拍板 (2026-06-28)

| # | 决策 | 拍板 |
|---|---|---|
| 1 | institution_survey + LHB 源归属 | **切 tushare 唯一** (§4.3); survey 先核证 stk_surv 参数语义(20260610 返0行疑点) 再切 |
| 2 | price_xdxr / price_kline / top10_floatholders 物删 | **全物删** (tushare 已有等价, archive 留底) |
| 3 | inst_holdings (+inst_institutions + gap_queue) | **退役物删** — 审计实测孤儿脏数据(一次性抓取/非PIT grain), 推翻上轮"保留Phase3用"决议 |
| 4 | 执行方式 | **先补扫9角度→一次性清** |

## 1. 关键发现 (执行时注意)

1. **check_dead_references 盲区**: B 扫正则只抓 `from/import` 语句, 抓不到 clients_registry 的 dataclass `module="services.xxx"` 字符串字面量 → 5 死 ClientSpec 漏网。**修门**: 扩 B 扫覆盖 `module=` / `module:` 字符串字面量指向不存在模块。
2. **data_routes.py 主动误导**: tdxhub/akshare/新浪/腾讯 全虚标 `status='connected'`, 前端 data-view.js:1019 还调 (404)。
3. **survey purge↔resurrect 死循环**: mart_stock_survey_activity 标 U5 物删但 acquire live sync 复活 = false tombstone。切 tushare 时 acquire Step 2i 必须一并退役避免循环。
4. **物删统一走** db_lifecycle_delete + mart_data_deletion_record (archive parquet, 可逆)。

## 2. A 类 — 可直接清 (§4.3 政策已决, 不需拍板)

### A1. akshare 热备链拔除 (违 §4.3 无热备红线) [P0]
- `lhb_client.py:148-173` `_fetch_lhb_akshare` (import akshare:154) + with_fallback → 纯 aif10 直调 (注: LHB 整体切 tushare 见 B, 此处先去 akshare)
- `qfii_client.py:182-204` `_fetch_qfii_akshare` (import akshare:184) + with_fallback → 纯 aif10; 修 QFII_SOURCE label `akshare_*`→`aif10_RPT_DMSK_HOLDERS`
- `institution_survey_client.py:164-191` `_fetch_survey_akshare`+`:195 _fetch_from_akshare` → (注: survey 整体切 tushare 见 B)
- `clients_registry.py:90/102/114` fallback_chain `["aif10","akshare"]`→`["aif10"]` + L52 注释删 `3=akshare-fallback`
- `source_watermarks.py:96/104/112` parser_version `aif10_or_akshare`→`aif10`
- `fallback.py` docstring(:4/:13-17) 删 3 capability akshare 声明; `sources/__init__.py:7` header 删 akshare 行
- 收尾: rg `import akshare` 全仓清零 + `requirements.txt:5 akshare>=1.12.0` 删 + 单测 + codegraph sync

### A2. 死代码 git rm [P1]
- `kline_source.py:350-443` `fetch_daily_akshare_fallbacks` (import akshare, 0 caller, K线已 tushare-only) + 连带孤儿 import
- `scripts/diagnose_update_bottleneck.py` (repo-root, import 6 已删模块, 0 caller)
- `backend/services/gap_queue.py` + `tests/test_gap_queue.py` (0 非test importer, 写 market_gap_queue 全库 ABSENT) + schema_core:364 CREATE + schema_migrations:59-60 idx + :184 ALTER
- `constants.py:80-87` CHANGE_MAP (东财 hold_change→事件, 0 外部消费, serving 已退役)

### A3. 死 ClientSpec git 删/墓碑 (指已删模块) [P1]
- `clients_registry.py` 5 处: xdxr_client(:70-81) / akshare_client(:157-168, 唯一 writes price_kline, 连表退役见 B) / financial_client(:141-154) / build_fund_flow_rank(:240-255) / tdx_keep_challenger(:309-321)

### A4. UI/前端死引用 [P1]
- `ops_manual_run.py:37-46` 删 tdx_pool_refresh MANUAL_JOBS (argv 指已删 refresh_tdx_server_pool.py); 顺手 docs/design_specs/.../\_build_v5.py:88
- `data_routes.py` 整文件评估 git rm (唯一 importer 已删, 0 Python 消费, 虚标 connected 误导) + 前端 data-view.js:1019 死调用 + index.html:150 文案
- stale .pyc 清理: services/__pycache__/{xdxr,akshare}_client.pyc

### A5. 清 config 死表引用 [P1]
- `panel_pipeline_manifest.yaml` 整份墓碑 (全 build_script/output_table ABSENT) → 评估 git rm/归档
- `storage_retention.yaml` 删 tdx 死条目(:163-164/:177-202/:335-354) + storage_retention.py:352-353 + test fixture
- `field_dictionary.yaml` 删 dim_stock_tdx_industry_history(:70-82) + :138 gpcw 引用 + :60/:511 volume unit MIXED→tushare 单一
- `market_perception.yaml` git rm (0 live 消费, 真配置在外部 /stock/perception) + store.py:71 + main.py:142-145 guarded import (注: 姊妹 repo 不擅动)
- `pricing_label_policy.yaml` git rm (服务的 pricing_policy.py 已删, loader 硬置 None)
- `sync_registry.yaml:243-244` 清 tdxhub xdxr 备援注释
- `data_module_members.yaml:64` financial_client 行移除

### A6. repoint entity (provenance 失真) [P1]
- `data_access.yaml:216-226` holders_tdx: vendor `tdxhub`→`aif10/miaoxiang` (实测 fact_top10_holder_period 100% source=miaoxiang, 1725648 行新鲜; 0 消费方 repoint 安全); 注释改实情
- `data_routes.py:93-101` 十大流通股东 source `tdxhub`→`aif10`
- 可选: entity 重命名 holders_tdx→holders (须同步 lineage + edge_layer_implementation_plan KEEP 引用, 与 edge 计划对齐后再改)
- 可选门增强: check_serve_read_layer.py 校 vendor 匹配实际 writer source

### A7. 物删 0行/孤儿表 [P1]
- `financial_sync_state`(smartmoney, 0行) + `dim_holder_alias`(30) + `dim_data_source_priority`(10) → db_lifecycle_delete + deletion_record + 清 data_layers/schema_core DDL/lineage/FEATURE_MAP

## 3. B 类 — 数据源决策 (已拍板 → 转可执行)

### B1. institution_survey 切 tushare [拍板=切 tushare, 先核证]
- **先核证** stk_surv 参数语义 (实弹哪个 date param 返非0 + PIT 锚 surv_date/ann_date 防 look-ahead; sync_registry:276 "20260610 返0行"疑点)
- 正式注册 stk_surv → 改消费方读 raw_tushare_stk_surv (signals_v2 D8 已删? survey_count_90d / mart_stock_survey_activity 重算)
- 物删 raw_institution_surveys(14548) + mart_stock_survey_activity(3867) + 退役 institution_survey_client + acquire Step 2i (解 purge↔resurrect)
- 改 data_access.yaml:281-292 vendor tdxhub→tushare + 注释

### B2. LHB 切 tushare / 整体退役 [拍板=切 tushare]
- tushare top_list(142500)/top_inst(1783034) 已全量; 下游 fact_lhb_event 已删=0 reader
- 物删 raw_lhb_daily(58756) + 退役 lhb_client(含 akshare) + acquire Step 2d + clients_registry + data_routes lhb 行 + source_watermarks lhb 域
- 注: raw_tushare_top_list/top_inst 当前也 0 live consumer (edge 重建时接)

### B3. price_xdxr 物删 [拍板=物删]
- price_xdxr(market.duckdb, 173781) 物删 (复权已切 adj_factor, 0 live writer/reader)
- db_lifecycle_delete + 清 market_schema.py:33/55 DDL + market_db.py:203-290 replace_xdxr_rows/update_xdxr_sync_state + market_read.py:115-167 get_xdxr_events/get_all_xdxr_sync_states + __all__ + lineage

### B4. price_kline 物删 [拍板=物删]
- price_kline(market.duckdb, 1048, akshare HS300 benchmark) 物删 (canonical=price_kline_qfq_tushare, 0 消费方)
- 清 market_db.py:157-197 upsert_price_rows + PRICE_KLINE_ALLOWED_SOURCES + market_schema DDL
- 注: HS300 benchmark 将来从 raw_tushare_index_daily(000300.SH) 重建

### B5. top10_floatholders 退役 [拍板=物删]
- sync_registry.yaml:695-715 停域 (by_ann_date) + 物删 raw_tushare_top10_floatholders(1975788) + 退役 holders_top10 entity
- 理由: holder 主源已切 aif10 (财报季滞后~4月), 该 tushare 表 0 数据消费方

### B6. inst_holdings 退役物删 [拍板=退役]
- inst_holdings(34994 孤儿脏数据) + inst_institutions(240, 同簇核) + gap_queue(已在 A2) 退役物删
- 注: Phase3 机构档案以 aif10/tushare 重建, 旧抓取无 PIT 价值

## 4. 三轮审计汇总 (wzyo01mki 26 + wysx3nom4 62 + wjtfoml71 32 = ~120 逻辑项)

补充决策 (第二批 AskUserQuestion):
| # | 决策 | 拍板 |
|---|---|---|
| 5 | 旧 vanilla JS 前端 (基本全死, 打开app根路由即404) | **先参考设计然后全删** → 本次**不动前端**, React/Vue 重写完成后整体删 (含 index.html/assets/js/根路由/前端 contract test) |
| 6 | ETF 子系统 (etf.duckdb 7表/117MB) | **彻底退役物删** (7表+etf_db/build脚本+登记) |
| 7 | strategy_preset (rebuild曾KEEP) | **退役物删** (router+dim_strategy_preset表+前端; Phase1工作台从零设计参数管理, 不复用旧schema) |
| 8 | EM_MIAOXIANG_TOKEN (.env) | **删除** (用户自己改 .env; 0 消费方) |

### C 类 — 补扫 (wysx3nom4) 新发现 (62, 非前端部分本次清)
- **CI 红 16 项**: tests module-top `import institution_survey_client/lhb_client` (删源即 collection 红) + 10+ contract/widget 断言已删符号 → **删源/前端必同删测试** (feedback-run-full-ci-list-locally)
- 退役源残留: launchd plist `CM_TDX_SERVERS`(9 tdxhub IP) + sync_runner.py:275 jiaoch.site 注释 + storage_retention 4 tdx 表条目
- repo-root 死脚本 9: phase_a_recheck/validate_champion_paper_sim/post_retrain_pipeline/workflow_checkpoint/cm.sh部分/backfill_chain9{,b,c}/safe_panel_build
- chunkyctl data-status 断链 (route 已删 data_migration_status.py)
- perception router (main.py:137-145) DATA-DEAD (查已wipe mart, 0前端消费)
- register_modules etf/akquant 死基建 (main.py:118-129 + DB app_settings)
- 3 bak 库 ~5.3G + 孤儿 .pyc
- docs stale: 宪法§7/data_product_contract/MASTER_TOPLEVEL/AGENTS.md/quickstart (旧多源优先序+dual-run, aif10例外缺失)
- lineage graph.json drift gate FAIL exit2

### D 类 — 收敛轮 (wjtfoml71) 新发现 (32)
- **死 ClientSpec +5** (run_daily_topk/feature_selection_experiments/validate_tdx_feature_pit/run_candidate_walkforward_eval/build_feature_retention_decisions) → clients_registry 共 **14 条死 module 串**
- **孤儿 config +11** (formula 10-pack + stock_formula_optuna/formula_search_spaces/strategy_defaults/backtest_execution/portfolio_sizer_profiles/institution_alpha/shared_feature_bins/feature_drift_mitigation_panel/technical_stage/technical_states)
- **脚本死引用 +6** (session_status/sandbox/session_snapshot/install_resilience probe + cm.sh 子命令)
- **控制面误标 +10 (DA2, 最高危)**: PROJECT_INDEX §1-§13 正文 (line12 最后更新 2026-06-06) 把 45/47 已删表标 LIVE/KEEP, §3 "231.py"→实88, §5 "17 routers"→实3, §1 库统计 stale, §2.3 机构跟随当主alpha (已删)

### 系统根因 (is_dry=False 的死角 B, mio §7 流程根治)
机械门盲区致"残留反复": (1) check_dead_references 不扫 ClientSpec `module=` 字符串字面量 (14 死 ClientSpec 逃逸) (2) 不检测"无 py loader 的孤儿 yaml" (3) 不扫 repo-root .sh heredoc import (4) build_feature_map §4 无目录过滤泄漏 FROZEN bestchoice。

## 5. 执行批次 (按 CI 安全 + 子系统整批, 避免逐条 commit 红)

| 批 | 内容 | 物删? |
|---|---|---|
| **批0 流程根治 (先做, mio §7)** | 扩 check_dead_references 3 盲区 (module=字面量/孤儿yaml/.sh heredoc) + build_feature_map FROZEN 排除 + 单测 red→green | 否 |
| **批1 后端死代码/config/ClientSpec/脚本** | akshare热备链 + 14死ClientSpec + 孤儿config(R1+R3 ~22) + repo死脚本(R2+R3 ~15) + 死代码(kline_source/diagnose/gap_queue/constants) + perception router + register_modules etf + **CI红tests同批删** | 否(纯代码) |
| **批2 数据源切换 tushare** | institution_survey: 核证stk_surv参数→切消费方→物删 raw_institution_surveys+mart_stock_survey_activity+退役client+acquire Step2i(解purge↔resurrect); LHB: 切top_list/top_inst→物删raw_lhb_daily+退役lhb_client+acquire Step2d | 是 |
| **批3 物删表** | price_xdxr/price_kline/top10_floatholders/inst_holdings(+inst_institutions)/ETF 7表/孤儿表(financial_sync_state/dim_holder_alias/dim_data_source_priority) + repoint holders_tdx vendor | 是 |
| **批4 退役源残留+缩盘** | CM_TDX_SERVERS plist + jiaoch注释 + storage_retention tdx + 3 bak库(~5.3G)+ 孤儿.pyc | 物删bak |
| **批5 控制面重写 (主会话独占)** | PROJECT_INDEX §1-§13 对齐纯数据平台真相态 + 宪法§7/contract/MASTER/AGENTS/quickstart (tushare唯一+aif10例外) + lineage graph.json 重生 | 否 |
| **批6 前端** | **不动** (决策5: React/Vue 重写完成后整体删) | — |
| **批7 验收 (第4轮 sweep 验 dry)** | 全 CI offline + check_dead_references(扩门后) + moth + smoke + data_layer_audit + acquire --dry 无 degraded + 第4轮 workflow 确认 REAL=0 → 宣告 loop-until-dry 收敛 | — |

每批完成即 safe_commit + push (高频防丢)。物删走 db_lifecycle_delete + mart_data_deletion_record (archive parquet 可逆)。
