# §9 Phase 0 fan-in 执行清单 + 机制 reframe (2026-06-27)

> 来源: workflow wf_df52c6c6 (5 agent / 387k tok, 4 连接模型 fan-out + 对抗验证完整性)。
> 关联: section9_reference_split_verified_plan_20260626.md (Stage 计划) + goal.md 不变量#2。
> **状态: Phase 0 gating 前置 (fan-in 全清单) DONE; 73 点执行 + 机制 re-decide = fresh 焦点 session (本 session 已长, 不鲁莽起步)。**

## 裁决: stage_e_safe = FALSE (物删前置未满足)

73 点读/写 (get_conn+注入 46 / direct_connect 7 / bestchoice_attach 10 / writers+ddl 10) + **对抗验证抓 7 漏点 (services 之外: routers/scripts/tests)**。

## 关键架构发现 (机制 reframe — 须焦点 session re-grill)

**根因**: `services/db.py get_conn()` / current_db_paths() **完全无 reference ATTACH** → get_conn/smart_conn/business_conn 上的裸 `FROM dim_*` 看不到 reference.dim_*。
- fan-in 里大量 "resolver-routable (alias-routing via data_access)" 标注 = **Phase0 待办动作, 非现状** (这些点现在裸 FROM dim_*, 不走 data_access resolver)。
- **机制 tradeoff 被现实重新 reframe**:
  - **alias-routing (架构师 0626 选)**: 只覆盖走 data_access resolver 的点 → 需把 73 点**逐个 rewire** 到 data_access entity = 73 点中央连接重构。
  - **中央 get_conn ATTACH+view (验证者 0627 荐, 架构师曾否决)**: get_conn 统一补 reference RO ATTACH + smartmoney 留 view → 裸 FROM dim_* 自动跟随, 改动面小; 但有 view 磁盘污染/魔法/cross-catalog 风险 (plan §4 否决理由)。
  - **→ 焦点 session 必须先 re-grill 机制** (73 点 rewire 成本 vs view+ATTACH 风险), 给 fan-in 现实后这是真 tradeoff, 非已定。验证者明指 "逐点改 scripts/router/test 会反复漏 = 正是 M5 血缘中枢要根治的反复手工 fan-in 痛点"。

## 7 漏点 (services 之外, 直接炸 OR try/except 静默降级)

- **routers/v3_selection.py:206** [dim_active_a_stock] — /v3/board API get_conn 裸 FROM, Stage E 后 Catalog Error (直接炸)
- **scripts/build_rally_entry_pit.py:62** + **build_rally_negatives.py:86** [dim_trading_calendar] — rally builder connect(SMART) 裸 FROM, Stage E 断 (直接炸)
- **scripts/build_macd_state_history.py:63** [dim_trading_calendar] — 间接经 latest_completed_trade_date helper, get_conn 裸 FROM (直接炸)
- **scripts/data_health_snapshot.py:171** [dim_trading_calendar] — **try/except 静默降级**: 失败→None→回退 wall-clock (正是 check_calendar_usage 防的反模式)
- **scripts/audit_panel_leakage.py:629** [dim_all_ever_listed] — **try/except 静默**: 失败→ever_listed=None→生存者偏差审计变 no-op (自废武功, 违 §4.4)
- **tests/realdb/test_real_data_consistency.py:49** [dim_trading_calendar] — realdb 测试裸 FROM, Stage E 后 CI 炸

## Phase 0 执行序 (focus session, 机制定后)

0. **[机制 re-grill]** alias-routing 73点 vs 中央 get_conn ATTACH+view — 给 fan-in 成本现实重判。
1. data_access entity 基建: 4 dim 纳 SERVE entity (db 别名可切 reference) — 现状 0 entity。
2. list_read 点 (大多数, resolver-routable) 收口走 data_access。
3. cross-db JOIN 硬点 (universe:206/212, stock_graph:219, regime_engine:425/476[market_perception], data_quality:1379, data_audit:411/523): ATTACH reference 或拆 "先取list再filter"。
4. writers repoint reference RW: security_master:103/106 (dim_active 唯一writer DELETE+INSERT), build_dim_listing_status:119/124。
5. DDL 移 reference 建库: schema_core:263/275, primitives/ddl.py:129, schema_migrations:277 (idx_daas_updated)。
6. **7 漏点必纳** (routers/scripts/tests) + B6 (duck_adapter attach 失败 raise, 防静默降级)。
7. Stage C (view/alias 切换 escalate) → D (验收: 写reference+ATTACH RO竞争+bestchoice链+全pytest) → E (物删 _bak escalate)。

## 全 73 点清单

| file:line | dim | kind | conn_source | phase0_action |
|---|---|---|---|---|
| backend/services/security_master.py:41 | dim_active_a_stock | list_read | 参数 conn 注入 (caller=get_active_a_stock_co | alias-routing via data_access entity (security_master domain → referen |
| backend/services/security_master.py:48 | dim_active_a_stock | count_audit | 参数 conn 注入 (_cache_is_fresh; 上游 get_conn | 无需改 (cache freshness COUNT, 随 reference repoint 自动跟随) |
| backend/services/security_master.py:103 | dim_active_a_stock | write | 参数 conn 注入 (refresh_active_a_stock_maste | 写方 repoint reference RW — DELETE 写点, dim_active_a_stock 的唯一 writer, 必须 |
| backend/services/security_master.py:106 | dim_active_a_stock | write | 参数 conn 注入 (refresh_active_a_stock_maste | 写方 repoint reference RW — INSERT 写点, 与 line103 DELETE 同事务, 同指 referenc |
| backend/services/universe.py:104 | dim_active_a_stock | in_sql_join | docstring 示例 (非执行) — sql_where_no_st 返回  | 无需改 (docstring 示例; 真 JOIN 落在调用方文件如 universe.py:206) |
| backend/services/universe.py:150 | dim_active_a_stock | list_read | 参数 conn 注入 (get_active_universe(conn=... | alias-routing via data_access entity (universe → reference) — 身份真相源交集取 |
| backend/services/universe.py:161 | dim_active_a_stock | list_read | 参数 conn 注入 (get_active_universe(conn=... | alias-routing via data_access entity (universe → reference) — ST name  |
| backend/services/universe.py:206 | dim_active_a_stock | in_sql_join | 参数 conn 注入 (audit_strategy_universe_cont | 加 ATTACH reference 或拆"先取list再filter" — fact表(table=strategy preds) × d |
| backend/services/universe.py:212 | dim_all_ever_listed | in_sql_join | 参数 conn 注入 (audit_strategy_universe_cont | 加 ATTACH reference 或拆list — fact表 × dim_all_ever_listed JOIN (e.is_act |
| backend/services/screening_engine.py:537 | dim_active_a_stock | list_read | 参数 conn 注入 (run_all_screens(smart_conn,  | alias-routing via data_access entity — code→name 全量取 list/map, resolve |
| backend/services/recommendation_universe.py:82 | dim_active_a_stock | list_read | 参数 conn 注入 (_stock_names(conn, stock_cod | alias-routing via data_access entity — code→name lookup (WHERE stock_c |
| backend/services/stock_trends_read.py:84 | dim_active_a_stock | in_sql_join | 参数 conn 注入 (load_stock_trends_payload(co | 无需改/同库 (excluded_stocks e LEFT JOIN dim_active_a_stock d 均在 smart 同库;  |
| backend/services/stock_graph_read.py:89 | dim_active_a_stock | list_read | 参数 conn 注入 (stock graph read fn(conn,... | alias-routing via data_access entity — 单股 name lookup, resolver-routab |
| backend/services/stock_graph_read.py:219 | dim_active_a_stock | in_sql_join | 参数 conn 注入 (related-stocks fn(conn,...)) | 加 ATTACH reference 或拆 name-lookup — dim_stock_dc_industry d LEFT JOIN  |
| backend/services/institution_write.py:110 | dim_active_a_stock | list_read | 参数 conn 注入 (resolve_stock_name(conn, sto | alias-routing via data_access entity — COALESCE 子查询单股 name lookup, res |
| backend/services/calendar.py:63 | dim_trading_calendar | list_read | 参数 conn 注入 (latest_completed_trade_date( | alias-routing via data_access entity (calendar → reference) — MAX(trad |
| backend/services/return_engine.py:55 | dim_trading_calendar | list_read | 参数 conn 注入 (_next_trading_day(biz_conn,. | alias-routing via data_access entity (calendar → reference) — next tra |
| backend/services/return_engine.py:86 | dim_trading_calendar | list_read | 参数 conn 注入 (_resolve_cost_window(biz_con | alias-routing via data_access entity (calendar → reference) — 报告日前20交易 |
| backend/services/return_engine.py:271 | dim_trading_calendar | list_read | 参数 conn 注入 (_price_after_n_days(biz_conn | alias-routing via data_access entity (calendar → reference), resolver- |
| backend/services/return_engine.py:301 | dim_trading_calendar | list_read | 参数 conn 注入 (_get_nth_trade_date(biz_conn | alias-routing via data_access entity (calendar → reference), resolver- |
| backend/services/return_engine.py:374 | dim_trading_calendar | list_read | 参数 conn 注入 (_TradingCalendarCache.__init | alias-routing via data_access entity (calendar → reference) — 全日历一次性载内 |
| backend/services/holder_availability.py:80 | dim_trading_calendar | list_read | 参数 conn 注入 (next_trading_day_after(conn, | alias-routing via data_access entity (calendar → reference) — MIN/MAX  |
| backend/services/holder_availability.py:93 | dim_trading_calendar | list_read | 参数 conn 注入 (next_trading_day_after(conn, | alias-routing via data_access entity (calendar → reference), resolver- |
| backend/services/market_perception/regime_engine.py:274 | dim_trading_calendar | list_read | 参数 conn 注入 (_extended_start_day(conn,... | alias-routing via data_access entity (calendar → reference) — 起点交易日回溯, |
| backend/services/market_perception/regime_engine.py:425 | dim_trading_calendar | in_sql_join | 参数 conn 注入 (_load_breadth fn(conn,...)) | 加 ATTACH reference — trade_days CTE(dim_trading_calendar) 与 market.v_p |
| backend/services/market_perception/regime_engine.py:476 | dim_trading_calendar | in_sql_join | 参数 conn 注入 (_load_breadth_range fn(conn, | 加 ATTACH reference — trade_days CTE × market.v_price_kline_qfq 同 query |
| backend/services/market_perception/regime_engine.py:635 | dim_trading_calendar | count_audit | 参数 conn 注入 (_validate_snapshot trading-d | alias-routing via data_access entity (calendar → reference) — 单日 is_tr |
| backend/services/market_perception/regime_engine.py:650 | dim_trading_calendar | list_read | 参数 conn 注入 (_trading_days(conn, start, e | alias-routing via data_access entity (calendar → reference) — 区间交易日 li |
| backend/services/data_quality.py:1209 | dim_trading_calendar | count_audit | 参数 conn 注入 (_check_calendar(conn,...); v | alias-routing via data_access entity (calendar → reference) — table_ex |
| backend/services/data_quality.py:1227 | dim_trading_calendar | count_audit | 参数 conn 注入 (_check_calendar(conn,...)) | alias-routing via data_access entity (calendar → reference) — 完整性 COUN |
| backend/services/data_quality.py:1235 | dim_trading_calendar | count_audit | 参数 conn 注入 (_check_calendar dup 子查询(conn | alias-routing via data_access entity (calendar → reference) — duplicat |
| backend/services/data_quality.py:1379 | dim_trading_calendar | in_sql_join | 参数 conn 注入 (_check_calendar_alignment(co | 加 ATTACH reference 或拆 — 任意 fact表 {table} t LEFT JOIN dim_trading_calen |
| backend/services/data_quality.py:2993 | dim_active_a_stock | count_audit | 参数 conn 注入 (mart 投资性校验 fn(conn,...)) | alias-routing via data_access entity — table_exists 守卫(随后 non_investab |
| backend/services/data_quality.py:3794 | dim_trading_calendar | count_audit | 参数 conn 注入 (record_pipeline_run input_ta | 无需改 (lineage input_tables 字符串清单, 仅审计登记表名) |
| backend/services/data_sources/sync_runner.py:255 | dim_trading_calendar | list_read | get_conn() (via _smartmoney_conn() at li | alias-routing via data_access entity (calendar → reference) — _trading |
| backend/services/workbench_overview_read.py:166 | dim_trading_calendar | list_read | 参数 conn 注入 (_latest_trading_day(conn,... | alias-routing via data_access entity (calendar → reference) — MAX(trad |
| backend/services/workbench_overview_read.py:242 | dim_trading_calendar | count_audit | 参数 conn 注入 (_read_model_meta(conn,'overv | 无需改 (read_model_meta 表名清单参数, 仅列读模型涉及表; meta 探查随 reference repoint 跟随) |
| backend/services/workbench_data_source_watermark_read.py:103 | dim_trading_calendar | list_read | 参数 conn 注入 (latest_trading_day(conn,...) | alias-routing via data_access entity (calendar → reference) — MAX(trad |
| backend/services/workbench_data_source_read.py:221 | dim_trading_calendar | count_audit | 参数 conn 注入 (_read_model_meta(conn,'data- | 无需改 (read_model_meta 表名清单参数, 仅列读模型表) |
| backend/services/schema_core.py:263 | dim_active_a_stock | ddl | 参数 conn 注入 (ensure_core_schema(conn) → c | DDL 移 reference 建库 — dim_active_a_stock CREATE TABLE 若 dim 迁 reference |
| backend/services/schema_core.py:275 | dim_trading_calendar | ddl | 参数 conn 注入 (ensure_core_schema(conn)) | DDL 移 reference 建库 — dim_trading_calendar CREATE TABLE 须在 reference 库执 |
| backend/services/primitives/ddl.py:129 | dim_listing_status | ddl | 模块常量 DIM_LISTING_STATUS_DDL (由 primitive | DDL 移 reference 建库 — dim_listing_status CREATE TABLE 须在 reference 库执行 |
| backend/services/schema_migrations.py:277 | dim_active_a_stock | ddl | 参数 conn 注入 (_apply_schema_maintenance(co | DDL 移 reference 建库 — idx_daas_updated 索引随 dim_active_a_stock 迁 referen |
| backend/services/data_audit.py:411 | dim_active_a_stock | in_sql_join | _open_conn() 自连 (duckdb.connect(SMART,re | 加 ATTACH reference (已是 attach 模型) — market.v_price_kline_qfq p INNER J |
| backend/services/data_audit.py:522 | dim_all_ever_listed | list_read | _open_conn() 自连 (direct_connect+attach)  | 加 ATTACH reference (若 dim_all_ever_listed 迁 reference) — inactive 码集 l |
| backend/services/data_audit.py:209 | dim_trading_calendar | list_read | _open_conn() 自连 (direct_connect+attach)  | 加 ATTACH reference — _trading_index 全日历 list, _open_conn 多 ATTACH refe |
| backend/services/data_audit.py:184 | (connection setup) dim_trading_calendar / dim_active_a_stock / dim_all_ever_listed | list_read | duckdb.connect(str(SMART_DB_PATH), read_ | 加ATTACH reference (READ_ONLY): _open_conn() 现仅 ATTACH market + tushare |
| backend/services/data_audit.py:209 | dim_trading_calendar | list_read | duckdb.connect(SMART_DB) via _open_conn  | 加ATTACH reference 后改 reference.dim_trading_calendar (此 conn 来自 _open_c |
| backend/services/data_audit.py:411 | dim_active_a_stock | in_sql_join | duckdb.connect(SMART_DB) via _open_conn  | 加ATTACH reference + 把 active_table 默认/yaml 值改 'reference.dim_active_a_ |
| backend/services/data_audit.py:523 | dim_all_ever_listed | in_sql_join | duckdb.connect(SMART_DB) via _open_conn  | 加ATTACH reference + inactive_table 默认/yaml 改 'reference.dim_all_ever_l |
| backend/scripts/build_dim_listing_status.py:119 | dim_listing_status (写/DDL) + dim_all_ever_listed (读源) | ddl | duckdb.connect(str(db_path)) 默认 db_path= | 写方 repoint reference RW: 此脚本是 dim_listing_status 的唯一 builder — CREATE/ |
| backend/scripts/build_dim_listing_status.py:57 | dim_listing_status | ddl | duckdb.connect(SMART_DB) (同 L119 conn 传入 | DDL移reference建库: _add_dim_listing_status_column / ensure_dim_listing_s |
| backend/scripts/build_dim_listing_status.py:123 | dim_all_ever_listed | count_audit | duckdb.connect(SMART_DB) (同 L119 conn) | 随 L119 一并迁: source_count = COUNT(*) FROM dim_all_ever_listed; 跨 refere |
| bestchoice/compute.py:606 | dim_active_a_stock | in_sql_join (helper definition: ATTACH smartmoney AS sm on a connect(MARKET_DB) connection — this is the connection-model-4 root) | _attach_smart_db(con) — con = duckdb.con | bestchoice 这套单独评估 (最硬点 B1). _attach_smart_db 是 4 套连接模型里第 4 套, 从不挂 refe |
| bestchoice/compute.py:1266 | dim_active_a_stock | in_sql_join (facts × dim 同 query, cross-db): v_price_kline_qfq(MARKET_DB) INNER JOIN sm.dim_active_a_stock(smartmoney) — universe 过滤全量 K线) | mkt = duckdb.connect(str(MARKET_DB), rea | 同步 ATTACH reference: helper 改后把 sm.dim_active_a_stock → ref.dim_active |
| bestchoice/compute.py:1743 | dim_active_a_stock | in_sql_join (facts × dim 同 query, cross-db): ranked CTE 内 v_price_kline_qfq INNER JOIN sm.dim_active_a_stock (current-raw 最近 N bars universe 过滤) | mkt = duckdb.connect(str(MARKET_DB), rea | 同步 ATTACH reference + sm.dim_active_a_stock → ref.dim_active_a_stock ( |
| bestchoice/compute.py:3039 | dim_active_a_stock | in_sql_join (cross-db, 全 dim 来自 smartmoney): sm.dim_active_a_stock LEFT JOIN sm.dim_stock_archetype_latest LEFT JOIN sm.dim_financial_latest (元数据: 名称/行业/archetype/holder_chg). 此处 3 表全在 sm, 但 dim_active_a_stock 是 reference 拆库目标, 另 2 表非 reference 集 → 拆库后会变成 ref × sm cross-db JOIN) | mkt = duckdb.connect(str(MARKET_DB), rea | 硬点变体: dim_active_a_stock 走 reference, dim_stock_archetype_latest/dim_f |
| bestchoice/scripts/formula_parameter_search.py:74 | dim_active_a_stock | in_sql_join (facts × dim 同 query, cross-db): v_price_kline_qfq INNER JOIN sm.dim_active_a_stock (universe 过滤跑公式寻优) | con = duckdb.connect(str(MARKET_DB), rea | 随 compute._attach_smart_db helper 收口自动受益 (此脚本复用同一 helper). repoint 后 s |
| bestchoice/scripts/macd_golden_cross_backtest.py:67 | dim_active_a_stock | in_sql_join (facts × dim cross-db, L67) + list_read (L72: SELECT stock_code, stock_name FROM sm.dim_active_a_stock = 取码名 list) | mkt_con = duckdb.connect(str(MARKET_DB), | 内联 ATTACH (没用 _attach_smart_db helper) → helper 收口覆盖不到, 需单独改: 加 ATTACH |
| bestchoice/scripts/macd_optuna_backtest.py:109 | dim_active_a_stock | in_sql_join (facts × dim cross-db, L109: v_price_kline_qfq INNER JOIN sm.dim_active_a_stock) + 第二 query L119 FROM sm.dim_active_a_stock (list/meta read) | mkt = duckdb.connect(str(MARKET_DB), rea | ATTACH reference + sm.dim_active_a_stock → ref.dim_active_a_stock (JOI |
| bestchoice/scripts/strategy_audit.py:66 | dim_active_a_stock | in_sql_join (facts × dim cross-db, L66) + L73 FROM sm.dim_active_a_stock (list/meta read) | con = duckdb.connect(str(MARKET_DB), rea | ATTACH reference + sm.dim_active_a_stock → ref.dim_active_a_stock (L66 |
| bestchoice/scripts/strategy_effectiveness_audit.py:67 | dim_active_a_stock | in_sql_join (facts × dim cross-db, L67) + L74 FROM sm.dim_active_a_stock (list/meta read) | con = duckdb.connect(str(MARKET_DB), rea | ATTACH reference + sm.dim_active_a_stock → ref.dim_active_a_stock (L67 |
| backend/scripts/migrate_reference_db.py:63 | dim_active_a_stock, dim_all_ever_listed, dim_listing_status, dim_trading_calendar | write/ddl + count_audit (非 facts×dim JOIN): connect(reference) + ATTACH smartmoney AS sm, 然后 CREATE TABLE ... AS SELECT * FROM sm.dim_* / INSERT ... SELECT * FROM sm.dim_trading_calendar (建 reference.duckdb 4 表) + verify() 双连接 COUNT 对账 | ref = duckdb.connect(reference_path) + r | 无需改 (这是 §9 Stage A 拆库迁移工具本身, 用 ATTACH 做保真 build, 已在 check_universe_fil |
| backend/services/security_master.py:103 | dim_active_a_stock | write | 参数注入 (conn 由 caller 传入; refresh_active_a | Stage C 后此写方必须 repoint reference RW (dim_active_a_stock 是 reference 库表 |
| backend/services/security_master.py:106 | dim_active_a_stock | write | 参数注入 (同上, conn 传入) | 同 line 103 (同一函数体内的 INSERT 半边); repoint reference RW |
| backend/scripts/build_dim_listing_status.py:124 | dim_listing_status | write | duckdb.connect(SMART_DB) — build_dim_lis | Stage C 后必须 repoint reference RW: dim_listing_status 是 reference 库表 (m |
| backend/scripts/build_dim_listing_status.py:60 | dim_listing_status | ddl | duckdb.connect(SMART_DB) (同 build 函数 con | plan B2/B3: 此 CREATE TABLE IF NOT EXISTS + ALTER ADD COLUMN 的建库路径须移到 r |
| backend/services/primitives/ddl.py:129 | dim_listing_status | ddl | DDL 常量字符串 (DIM_LISTING_STATUS_DDL); 由 sc | plan B2/B3 必移 reference 建库路径: dim_listing_status 是 reference 表, 此 DDL  |
| backend/services/schema_core.py:263 | dim_active_a_stock | ddl | schema_core 建库 DDL 块 (大段 CREATE TABLE IF | plan B2/B3 必移 reference 建库路径: dim_active_a_stock 是 reference 表 (manife |
| backend/services/schema_core.py:275 | dim_trading_calendar | ddl | schema_core 建库 DDL 块 (同上), smartmoney 主库 | plan B2/B3 必移 reference 建库路径: dim_trading_calendar 是 reference 表 (mani |
| backend/services/schema_migrations.py:277 | dim_active_a_stock | ddl | schema_migrations DDL 块 (CREATE INDEX 字符 | plan B2/B3: 此索引 idx_daas_updated 随 dim_active_a_stock 一起移 reference 建库 |
| backend/scripts/migrate_reference_db.py:66 | dim_trading_calendar | ddl | duckdb.connect(reference_path) (ref = 新建 | 无需改 (这本身就是 Stage A reference-split 一次性迁移工具, 已经是 reference 建库路径; 它从 sma |
| backend/scripts/migrate_reference_db.py:69 | dim_trading_calendar | write | ref = duckdb.connect(reference_path) + A | 无需改 (一次性 Stage A 保真复制工具; 从 smartmoney sm.dim_trading_calendar 灌入 refer |

## 对抗验证抓漏 (services 之外, stage_e_safe=False 根因)

- **backend/routers/v3_selection.py:206** [dim_active_a_stock] map 只扫了 backend/services/* 与 bestchoice/*，整个 backend/routers/ 前端层没进 fan-in。此处 get_board() 用 get_conn()（services/db.py 无 reference ATTACH）跑 mart_stock_selection_summary s LEFT JOIN dim_active_a_stock d（code→name）。 | risk: Stage E 后 get_conn 连接里 dim_active_a_stock 不存在 → /v3/board API 返回 500 Catalog Error，前端 selection board 整块挂。这是面向用户的活路径，非 frozen/audit。
- **backend/scripts/build_rally_entry_pit.py:62** [dim_trading_calendar] map 未枚举 backend/scripts 下的 rally builder。此处 rconn=connect(SMARTMONEY_DB, read_only=True) 后裸 SELECT trade_date FROM dim_trading_calendar WHERE is_trading=1（GT episode PIT 标注的交易日历真相源）。 | risk: Stage E 把 dim_trading_calendar 搬出 smartmoney 后，SMARTMONEY_DB 连接裸 FROM 直接 Catalog Error → rally entry PIT 标注 builder 跑不动（episode 标注地基断）。需改 connect(REFERENCE_DB) 或 ATTACH reference。
- **backend/scripts/build_rally_negatives.py:86** [dim_trading_calendar] 同 build_rally_entry_pit，scripts 层 rally 负样本 builder 不在 map。rconn=connect(SMARTMONEY_DB) 裸 FROM dim_trading_calendar。 | risk: Stage E 后裸 FROM 在 smartmoney 连接 Catalog Error → rally 负样本生成断，episode 训练集地基断。
- **backend/scripts/data_health_snapshot.py:171** [dim_trading_calendar] scripts 层数据健康快照不在 map。open_data_health_connection 走 get_conn()/duck_connect(主库) 均无 reference ATTACH，_last_completed_market_date 裸 FROM dim_trading_calendar，且被 try/except:return None 包住。 | risk: 比硬炸更阴险：Stage E 后查询抛异常→except 吞→return None→上游回退 wall-clock 当最新日（正是 check_calendar_usage.py 防的静默绕开日历真相源反模式）。源健康 SLA 失真不报错，违 §4.4。L197 trading_lag_hours 同样裸 FROM dim_trading_calendar 同 except 吞。
- **backend/scripts/audit_panel_leakage.py:629** [dim_all_ever_listed] panel leakage 审计脚本不在 map（map 只在意 services）。connect(db, read_only=True) 单库无 reference ATTACH，audit_check_10_survivorship_bias 裸 COUNT FROM dim_all_ever_listed（L629）与 dim_active_a_stock（L635），各自 try/except 吞成 None。 | risk: Stage E 后生存者偏差检的 ever_listed/active 取数失败→None→survivorship 判定逻辑被跳过（panel_stocks < ever_listed*0.95 永不触发）= 审计自废武功假绿，泄漏防线静默失效，违 §4.4 try/except:pass。
- **backend/scripts/build_macd_state_history.py:63** [dim_trading_calendar] 间接命中：map 只扫了字面表名出现，没追经 helper 的间接访问。此处 smart_conn=get_conn() 后调 latest_completed_trade_date(smart_conn)，该 helper(calendar.py:64) 内部裸 FROM dim_trading_calendar，conn 由调用方提供且无 reference ATTACH。 | risk: Stage E 后 helper 在 get_conn 连接上裸 FROM Catalog Error → macd state history builder 起手即抛 RuntimeError（L67 已有 None 守卫但异常是 Catalog Error 不是 None），构建断。同模式 build_picture_daily.py:64 (get_conn) 与 sync_hs300_benchmark_kline.py:222 (get_business_conn) 均经同一 helper 间接炸。
- **backend/tests/realdb/test_real_data_consistency.py:49** [dim_trading_calendar] map 完全没纳入 tests/。此测试 _connect_existing(SMART_DB) 真连库后裸 FROM dim_trading_calendar 并调 latest_completed_trade_date(conn)。 | risk: Stage E 后该 realdb 测试在 SMART_DB 连接上裸 FROM → Catalog Error，CI 红。属真连库测试，会卡 commit gate；需同步改成连 reference 或 ATTACH。
## [2026-06-27 深化] 机制实测裁决 + entity 模型阻抗失配 (执行前必解的设计缺口)

### DuckDB 机制实测 (1.5.2, 像 verified plan 测进程锁)
| 测 | 结果 | 含义 |
|---|---|---|
| 裸 `FROM dim_*` (移reference+ATTACH) | FAIL Catalog Error | 中央 ATTACH **不能**让裸 FROM 解析到 reference |
| 限定 `reference.dim_*` | OK | 限定名可读 |
| cross-db JOIN `fact × reference.dim` | OK (需ATTACH) | JOIN 走 ATTACH+限定名 |
| smartmoney view→reference + 裸FROM | OK 仅ATTACH连接 | view 可行但... |
| **另开不ATTACH连接读 view** | FAIL 磁盘污染 | **架构师否决 view+ATTACH 理由实测坐实** |
| search_path 跨库 | FAIL | 不解 |

**机制裁决**: 验证者的"中央 get_conn ATTACH 根治"被实测**证伪** (裸 FROM 不解析 + view 磁盘污染)。**alias-routing/限定名读 reference 是唯一干净路, 73 点改写无捷径**; cross-db JOIN 走 ATTACH+`reference.dim_*`。

### [!] entity 模型阻抗失配 (执行前必解的真设计缺口)
架构师选"alias-routing via data_access SERVE entity", 但实测 4 dim 不符 entity 模型:
- **EntitySpec 强制 asof_col + code_col** (spec.py:20/19, PIT+code-centric); 但
- dim_active/all_ever/listing = **current 快照非 PIT** (set asof_col 会触发 PIT cutoff `<=latest_close` 错滤当前态);
- **dim_trading_calendar 无 code** (trade_date/is_trading) = 根本不符 code-centric distinct_codes/get(codes) 模型。

→ **dim 读机制设计未决** (3 选项, 影响 SERVE 不变量, 须焦点 session 定):
- (a) 扩 data_access 支持 current-dim entity 类型 (无 asof PIT + 非 code 形态) — SERVE 模型变更, 最一致;
- (b) 专用 reference-dim 读/写服务 (connect_ro/rw(reference) 薄 helper, 不进 PIT entity 模型) — 最简但开 SERVE 之外第二读路;
- (c) 全限定名 `reference.dim_*` + get_conn ATTACH 直读 — 不进 SERVE, 最少抽象但散。

### 还需的 infra (执行前定)
- **connect_rw(reference)**: resolver 只有 connect_ro (read-only 铁律); dim writer (security_master refresh / build_dim_listing_status) 写 reference 需 RW 路径 = 新 infra + 破 resolver 只读不变量, 须定写侧归属。
- **读+写耦合**: 每 dim 的读必须见其 writer 输出 → §9 须**逐 dim 整体迁** (全读+writer+DDL+drop 一致), 非逐点; 一个 dim (如 dim_active) 就 ~15 点。

### 结论
§9 不是机械执行: 有真架构子决策 (dim-读机制 a/b/c + connect_rw 写侧归属) 影响 SERVE 不变量, + 73 点逐 dim 耦合高 blast (whole-app: universe/calendar/scoring/dossier). **执行 = fresh 焦点 session: 先定 dim-读机制设计 → 逐 dim 迁 (Phase0读收口+writer repoint) → Stage C/D → Stage E物删(escalate)**。本 session 已 18+commit, 不鲁莽起。
