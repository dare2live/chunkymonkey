# 非 tushare 源全盘点 + 迁移/退役路线图 (2026-06-19)

> 状态: live (数据底座迁移决策依据)。owner: 本文件。
> 来源: workflow wf_673f8e25 (6 agents / 633k tokens / 5 lens + 完整性 critic), 主会话综合。
> 触发: 用户 "相关的表之类的全查一下依赖并退役旧表, 然后你再看还有哪些不是 tushare 源的"。
> 政策锚 (CLAUDE §4.3 + analysis/tushare_migration_program_20260615.md): tushare 转正主源;
> akshare 淘汰退役; tdxhub K线 build/sync 退役物删 (2026-06-15 用户决议); 其余 tdxhub/aif10 热备
> 切 tushare 后保健康直到逐表双轨核对退役。

## 0. 一句话结论

非 tushare 源共 **4 类 53 表** (akshare 22 / tdxhub 18 / aif10 13, 去重后更少; derived 7 不算源),
分布在 smartmoney/market/etf 库。tushare 侧多数接口**已注册** sync_registry (摄入已切),
但**消费侧旧表未 repoint** = 双轨期。本轮立即做 universe/stock_basic (身份真相源), 其余按 M 系列逐簇双轨退役。

## 1. universe 身份真相源切换 (立即做, 已验证低风险)

**双向 bug 实证** (lens 5): 当前 `get_active_universe` = K线90d活性 ∩ 前缀(00/30/60/68) − ST名,
不与真股清单交集 →
- 漏**入**指数 000300 (沪深300, 1075行K线, 不在 dim_active)
- 漏**掉** 2 只真股 (001393/600355 在K线却不在 stale akshare 快照)

根因: 身份真相源缺位 + dim_active 用 akshare (bare码 + `_market_from_code` 前缀猜市场)。

**修**: tushare `stock_basic` (5529股, ts_code/symbol/name/market/exchange/list_status/list_date/delist_date,
单接口 ≤6000行一次拉完, 50次/分, 2000积分) 作身份真相源:
- `universe = K线活性 ∩ 在stock_basic(真股身份) − exchange=BSE(北交所) − ST(PIT日历)`
- `dim_active_a_stock` 改从 raw_tushare_stock_basic 重建 (退役 akshare 调用)

**依赖图** (lens 1, 实测): dim_active_a_stock 26 引用 = ~19 真消费 + 7 DDL/检测/元数据。
真消费**只读 stock_code/stock_name 两列** → 保两列语义 + market 列继续输出 'SH'/'SZ' = 18/19 零改动。
**唯一 break 点** = `ingest_holders_tdxhub.py:106` (`where market in ('SH','SZ')`) → writer 把 exchange 映射回 SH/SZ 即可。
`_market_from_code` 唯一用户是 writer 自己, 删除零外部影响。

## 2. 非 tushare 源清单 (按源 × 处置)

### 2.1 akshare (22, 淘汰源 — 双轨核对后物理退役)

| 处置 | 表 |
|---|---|
| **RETIRE** (死/空壳/已迁) | price_kline(akshare K线, 已切tushare) · raw_financial_indicator_ak / fact_financial_indicator_ak(0行空壳→fina_indicator已注册) · raw_capital_dividend_summary · fact_hsgt_daily · raw_fund_flow_daily |
| **MIGRATE** (tushare 已注册接口, 消费侧待 repoint) | dim_active_a_stock(本轮做) · dim_trading_calendar(→trade_cal) · raw_profit_forecast_snapshot_daily(→forecast) · dim_financial_indicator_latest(→fina_indicator) · raw_capital_dividend_detail(→dividend) · raw_capital_repurchase(→repurchase) · raw_capital_unlock(→share_float) · fact_dzjy_event(→block_trade) · fact_executive_trade_event(→stk_holdertrade) · etf_price_kline/etf_asset_universe(→fund_daily, M2) |
| **NEEDS_REVIEW** | fact_stock_attention_snapshot · raw_capital_allotment_detail · mart_stock_fund_flow_rank_snapshot_daily |
| **live daily_update 接线** (走 akshare 非 registry) | Step2k external_attention · Step2l profit_forecast · 日历 refresh · HS300 benchmark fallback |

### 2.2 tdxhub (18, K线类退役物删 / 其余热备)

| 处置 | 表 |
|---|---|
| **RETIRE** (K线 build/sync, 2026-06-15 用户决议物删, M3) | price_kline_tdxhub · price_kline_tdxhub_adjustment_event |
| **KEEP_BACKUP** (热备, 切tushare后保健康) | price_xdxr · dim_stock_tdx_industry(+history) · raw_tdx_industry_file_snapshot · dim_stock_tdx_block · dim_tdx_block_catalog · dim_tdx_gpcw_field(+semantic) · fact_shareholder_plan_tdx_f10 |
| **MIGRATE** (tushare 有等价) | raw_tdx_gpcw_wide(→fina_indicator) · raw_tdx_f10_holder_research(→stk_surv) · raw_tdx_f10_holder_count_history(→stk_holdernumber) · fact_top10_holder_period(→top10_floatholders) · fact_holder_count_period(→stk_holdernumber) |

### 2.3 aif10 (13, 妙想东财F10 适配器, task#37 待迁退役; 已断流 2026-05-07)

| 处置 | 表 |
|---|---|
| **MIGRATE** (tushare 有等价) | raw_aif10_valuation_quantile(→daily_basic PE/PB分位) · raw_aif10_forecast_consensus(→report_rc一致预期) · raw_aif10_financial_history(→财报) · raw_aif10_holder_count(→stk_holdernumber) · raw_aif10_peer_valuation(→需评估) |
| **KEEP_BACKUP** (非aif10独有, 借道) | raw_lhb_daily(龙虎榜) · raw_qfii_holding_quarterly · raw_institution_surveys(→stk_surv) |
| **RETIRE** (孤儿, strategy_ensemble 退役后无消费) | 部分 raw_aif10_*(已断流 + 0 live 消费, 逐表核) |

### 2.4 critic 抓出的盲区 (5 lens 全漏, 必补)

- **tdxhub 财务簇** (整簇漏): raw_gpcw_detail(66736) · raw_gpcw_financial(22769) · dim_financial_latest(5204) · fact_financial_derived(23691) · fact_fundamental_quarterly(60528) — 迁移地图 W-B 首目标 → income/balancesheet/cashflow/fina_indicator (M4)
- **机构+龙虎榜派生链**: inst_holdings(34994, **源未明**) · inst_institutions(240) · fact_institution_event(35602) · fact_common_major_holder_stock(76300, tdxhub holder派生) · fact_lhb_event(48369, ←raw_lhb_daily)
- **源未明 (定源后才能判)**: fact_orderbook_snapshot(market, 100行, 无writer=疑污染残留→RETIRE) · inst_holdings(源待定) · dim_listing_status(5210, ←dim_all_ever_listed派生, 与退市判定耦合) · dim_stock_sw_industry(5530, registry未登记=漂移, 应=tushare index_member_all申万)
- **地图 drift (已物删, 从清单划掉)**: fact_fund_holding_tdx_f10 · fact_jgdy_event · raw_executive_trade · raw_financial_indicator_ak(raw_版不存在, 实物是fact_版0行)

## 3. 路线图 (按 M 系列, 逐簇双轨退役)

| 阶段 | 范围 | 状态 |
|---|---|---|
| **本轮** | universe/stock_basic 身份真相源 + 退役 akshare dim_active 路径 | 进行中 |
| M2 | ETF → tushare fund_daily + 删 akshare etf_price_kline | pending |
| M3 | 物删 tdxhub/akshare K线表 (price_kline_tdxhub/akshare) | pending |
| M4 | 其余簇逐表双轨: akshare 资金/财指/分红 + aif10 5表 + tdxhub 财务簇 + holder簇 | pending |
| 定源 | inst_holdings 源 / dim_stock_sw_industry registry / dim_listing_status 真相源链 / fact_orderbook_snapshot | pending |

**铁律** (每表退役前): 双轨核对 (新tushare vs 旧, 差异定位到具体code看谁对) → repoint 消费侧 → 验0残留 → 物删。
**不 bulk-drop on agent label** (mythos §14): 每簇按 ensemble 退役标准 (多源核 + 0 live 消费证伪) 才删。

## 3.5 退役执行日志 (逐表对抗验证后, 不bulk-drop)

验证 workflow wf_39200ec2 (11 表逐表对抗验证): SAFE_TO_DROP 6 / KEEP_MIGRATE_FIRST 5 / 0 KEEP_LIVE。
关键: aif10 valuation_quantile(3消费者 v3_picture serving)/peer_valuation/price_kline(4消费者 regime/return) 是 **LIVE**, 按 label bulk-drop 会断服务 → KEEP_MIGRATE_FIRST。

| 日期 | 表 | 行 | 处置 | 验证 |
|---|---|---|---|---|
| 2026-06-19 | **fact_orderbook_snapshot** (market) | 100 | RETIRED (DROP + 清 pyc/test_tool_registry; writer 639e0dfb 已删, 无源=污染残留) | 0消费者 |
| 2026-06-19 | **raw_fund_flow_daily** (smartmoney) | 86117 | RETIRED (DROP + 清 data_layers/data_deprecation/INDEX; writer 491072d1 已删) | 0消费者 (被 tushare moneyflow 替代) |
| 2026-06-19 | **raw_aif10_holder_count** (smartmoney) | 742291 | RETIRED (DROP + aif10_capability_client 删 capability + updater DAG 5文件接线 + clients_registry/data_layers/watermark_sla/data_routes; 删2留3) | 0消费者, 转 tushare stk_holdernumber |
| 2026-06-19 | **raw_aif10_financial_history** (smartmoney) | 5713 | RETIRED (DROP + aif10_capability_client 删 sync_financial_history_200q + updater DAG 6文件 + storage_retention/data_routes) | 0消费者 (50股探针孤儿) |
| 2026-06-19 | **fact_hsgt_daily** (smartmoney) | 2767 | RETIRED (DROP + build_akshare_panel 删 build_hsgt_daily 留其余5表 + clients_registry/schema_versions/data_layers/data_deprecation/panel_manifest/institution_alpha northbound块/test) | 0消费者 (akshare HSGT停2024-08, NorthboundAlpha消费者已删) |

**已退役 5/6 SAFE_TO_DROP** (749k+ 行)。剩 1: **fact_financial_indicator_ak** (0行空壳, dedicated writer financial_indicator_client + dim_financial_indicator_latest/scoring/audit 纠缠, 需先确认 scoring 死路径)。

**KEEP_MIGRATE_FIRST (有 live 消费者, 先迁消费侧再删, 勿现删)**: raw_capital_dividend_summary(→dim→scoring) · raw_aif10_valuation_quantile/peer_valuation(→v3_picture serving) · raw_aif10_forecast_consensus(→report_rc) · price_kline(→index_daily, regime/return engine; M3)。
**待 writer 手术 RETIRE (Batch B, shared writer 精细删)**: fact_financial_indicator_ak(dedicated financial_indicator_client) · fact_hsgt_daily(build_akshare_panel shared) · raw_aif10_holder_count + raw_aif10_financial_history(aif10_capability_client shared, 删2留3)。

## 4. 完整审计原始数据

workflow 完整返回 (5 lens 表级 + critic): `tasks/we2vsfoba.output` (会话级, 临时)。
本文件是其综合落档; 逐表证据/依赖行见原始 output 或重跑 wf_673f8e25。
