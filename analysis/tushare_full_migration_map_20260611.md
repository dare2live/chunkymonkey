# 全量迁移地图: 存量 44 表 → tushare 默认主源

> **[状态校正 2026-06-26 doc治理]** 两处已偏离现行政策: (1) 文中"消费侧**双轨核对**一致后旧路径退役" = **2026-06-23 用户简化推翻** — 现行删源规则 = tushare 有就用+删旧源/没有就删数据, **不做 tushare-vs-旧源值比对** (CLAUDE §4.3); (2) "tushare 唯一" 开了**正式例外 = 东财 aif10** (2026-06-24, 仅限 holder 主源/估值分位/QFII/机构持仓明细, 实测 tushare 滞后)。迁移范围表仍可参考, 删源纪律以 CLAUDE §4.3 现行版为准。

> 2026-06-11 用户三次递进决策终态: "项目里现有的和将来准备接入的所有数据都默认改接 tushare"。
> 政策权威: CLAUDE.md §4.3 + docs/data_product_contract.md (已改写 "no global primary" 旧条款)。
> 输入: 存量摄入面盘点 (44 表 / tdxhub 12 + aif10 9 + akshare 22 + 停用 1) + catalog 239 接口核证。
> 纪律: 每域注册前必须单日实弹核证字段/grain/单页上限 (top_inst 1000 整、ths_hot 多榜截断、
> rank_time 撞键 = 当日实测抓住的三连反例); 消费侧双轨核对一致后旧路径物理退役。

## 波次总览

| 波 | 状态 | 内容 |
|---|---|---|
| W-A | **回填中/排队** (chain1-5) | 资金流系/概念域/fina_mainbz/龙虎榜 top_list/盈预 report_rc/热榜 ths_hot/北向 moneyflow_hsgt/分红 dividend/复权 adj_factor/**K线 daily+daily_basic** |
| W-B | 接口已核证, 待逐域实弹注册 | 下表 16 接口, 积分全部 <= 10000 |
| W-C | 等 runner offset 分页 | stk_surv(调研,100/页) / top_inst(1000/页) / dc_hot(参数未核证) |
| 例外 | 保留旧源/本地, 记录理由 | 见末节 |

## W-B 映射表 (catalog 核证: 接口在录 + 积分够; grain/截断注册前实弹)

| 替代对象 (现状) | tushare 接口 | 积分 | 备注 |
|---|---|---|---|
| raw_gpcw_detail/raw_tdx_gpcw_wide (tdxhub 财务三表 8 期) | `income`+`balancesheet`+`cashflow` | 2000×3 | 全史可取 (替 8 期限制); 参数口径 (by ts_code vs period) 注册前实测 |
| raw_financial_indicator_ak (akshare 财指) | `fina_indicator` | 2000 | ROE/毛利等派生指标 |
| raw_tdx_f10_holder_research 十大股东部分 | `top10_holders`+`top10_floatholders` | 2000×2 | F10 十大股东有等价 (盘点 agent 误判"无") |
| fact_holder_count_period (F10 股东人数) | `stk_holdernumber` | 600 | 单次 3000 总量不限 |
| raw_capital_unlock (akshare 解禁) | `share_float` | 5000 | 单次 6000 |
| fact_dzjy_event (akshare 大宗) | `block_trade` | 300 | 单次 1000 — 分页风险注册前实测 |
| raw_executive_trade (akshare 高管增减持) | `stk_holdertrade` | 2000 | 单次 3000 |
| fact_hsgt_daily (akshare 陆股通持股) | `hk_hold` | 120 | 单次 3800 |
| raw_capital_repurchase (akshare 回购) | `repurchase` | 600 | — |
| 指数行情 (HS300 benchmark 等) | `index_daily` | 2000 | — |
| etf.duckdb (akshare ETF 行情) | `fund_daily` | 5000 | 单次 5000 |
| fact_fund_holding_tdx_f10 (F10 基金持股) | `fund_portfolio` | 5000 | 季频 |
| dim_stock_tdx_industry (行业) | `index_member_all` 申万分级 | 2000 | 既有决策 (申万 L2 主口径, 含 in/out date 可 PIT) |
| dim_trading_calendar (akshare 日历) | `trade_cal` | — | **已注册** (full_refresh) |
| raw_capital_dividend_* (akshare 分红) | `dividend` | 2000 | **已注册** (chain4) |
| price_kline (tdxhub+akshare K线) | `daily`+`adj_factor`+`daily_basic` | 2000×3 | **已注册** (chain4/5); 消费链 qfq 切换 = 独立大手术须 review |

## 不需要外源的 (更优替代)

| 现状 | 替代方式 |
|---|---|
| raw_aif10_valuation_quantile (妙想估值分位) | `daily_basic` pe/pb 全史落库后**自算分位** — PIT 更干净, 少一个外依赖 |
| raw_aif10_financial_history (妙想 200 期财务) | `income` 等全史直接覆盖 |
| raw_aif10_forecast_consensus (妙想一致预期) | `report_rc` (已注册) 自聚合共识 |
| CYQ 筹码 | 保持本地计算, 输入切 `daily`+`daily_basic.float_share` |

## W-B 追加批 (2026-06-11 深挖 catalog, 用户纠偏 "认真挖一下 tushare" — 实弹核证 ✓ 标注)

| 替代对象 / 新能力 | tushare 接口 | 积分 | 实弹 |
|---|---|---|---|
| 股权质押 (Serenity 减持质押 veto 件原料, 原盘点缺失) | `pledge_stat`+`pledge_detail` | 1000×2 | ✓ 749 行全史/股 |
| F10 管理层 | `stk_managers` (含简历) + `stk_rewards` (薪酬持股) | 2000×2 | ✓ 193 行 |
| F10 公司概况 + **文字主营** (产业链 L1 文本源, 补 fina_mainbz 结构化收入) | `stock_company` (main_business/introduction/business_scope/employees) | 120 | ✓ |
| 曾用名/ST 史 (universe 清洗) | `namechange` (含 change_reason) | — | ✓ 6 行 |
| 审计意见 (基本面风控) | `fina_audit` | 2000 | 待核 |
| 每日股本盘前 (CYQ 股本输入候选 #2) | `stk_premarket` | — | 代理超时待重核 |
| 北水持股穿透 | `ccass_hold_detail` | 8000 | 待核 |
| fact_common_major_holder_stock (同大股东个股) | **不需要接口** — `top10_holders` 全市场自 JOIN 派生 | — | — |
| QFII 持仓 (妙想 RPT_DMSK_HOLDERS) | `top10_floatholders` 按 holder 类型筛选派生 | — | — |

## 真例外 (深挖后仅剩)

| 域 | 理由 | 处置 |
|---|---|---|
| 股东增减持**计划** (fact_shareholder_plan, 计划类非已发生) | tushare 无结构化接口; 已发生变动归 `stk_holdertrade` | 半替代: `anns_d` 全量公告 + 关键词过滤 (**注意: anns_d 曾触发网关 357s 风控, 重新接触只许单次谨慎探测**); 过渡期保留 tdxhub 热备 |
| fact_jgdy_event (调研事件, akshare) | tushare 有 `stk_surv` 但 100/页需分页 | W-C, 分页支持后切 |
| raw_fund_flow_daily (停用) | 已由 `moneyflow` (chain1 已回填) 替代 | 直接退役, 不恢复 |

## 执行纪律 (每域 checklist)

1. 单日实弹: 字段/grain/行数 vs 单页上限 (差 <15% 即标截断年检)
2. 注册 registry (pit_anchor/available_after/data_start/sla/min_rows 全填)
3. 回填排 chain 队列 (gateway 并发 2 串行纪律)
4. 消费侧双轨: 新表 vs 旧表核对窗口 >= 20 交易日关键字段一致率
5. 旧路径按角色处置 (用户补充决策: 备用源也要修, fallback 也可能会用到):
   - **akshare 等淘汰源**: 双轨核对一致后物理退役 (脚本删除 + daily_update step 删除)
   - **tdxhub/miaoxiang 热备源**: 切换后保持健康运行 — 故障照修、SLA 照测 (阈值放宽到
     备源档如 3-5 交易日, 但不许静音/停更); fallback 链顺序同步更新为 tushare 主 → 热备
6. INDEX/FEATURE_MAP 同步
