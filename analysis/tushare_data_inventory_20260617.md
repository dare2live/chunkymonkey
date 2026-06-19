# Tushare 数据资产盘点 (10000 积分档) — 已拉 / 已用 / 未拉可能有价值

> 2026-06-17 · owner: 主会话 · 真相源: tushare_raw.duckdb 实查 + backend/config/tushare_api_catalog.json(241接口) + Explore 消费面扫描
> 账户: tinyshare 代理 10000 积分档 (单接口 120次/分 / 多接口 200/分 / 并发 2)。catalog 241 接口中 ≤10000 积分可拉。

## 一、已拉取 (32 表 / 实查周期 / 消费状态)

消费状态: **A**=live serving 在用 · **B**=曾探索脚本用 (污染期 rally/yushen/episode/altdata 探索 2026-06-17 已清; 判别力=unknown 待结构型 GT 重验) · **C**=拉了无任何消费(死数据)

| 域 | 表 (raw_tushare_) | 周期 | 行数 | 用? | 消费者 |
|---|---|---|---|---|---|
| **K线复权** | daily + adj_factor | 2019-01~2026-06 | 856万+874万 | **A** | market.price_kline_qfq_tushare(回测主源) |
| 筹码 | cyq_perf | 2018-01~2026-06 | 930万 | B | 探索(已清); 结构型 GT 下重验 |
| 资金流-主 | moneyflow | 2020-01~2026-06 | 739万 | **A** | feature_panel.mf_trend_20 + 实验 |
| 资金流-东财个股 | moneyflow_dc | 2023-09~2026-06 | 384万 | B | 探索(已清); 结构型 GT 下重验 |
| 资金流-东财行业 | moneyflow_ind_dc | 2024-01~2026-06 | 28万 | **C** | 无 |
| 资金流-东财市场 | moneyflow_mkt_dc | 2023-04~2026-06 | 764 | **C** | 无 |
| 资金流-沪深港通 | moneyflow_hsgt | 2014-11~2026-06 | 2723 | **C** | 无 |
| 换手/市值/PE | daily_basic | 2020-01~2026-06 | 762万 | B | 10处实验(circ_mv/turnover) |
| 指数行情 | index_daily | 2005-01~2026-06 | 3.5万 | B | HS300/中证500/1000基准(yushen实验) |
| 指数基本面 | index_dailybasic | 2014-02~2026-06 | 1.5万 | **C** | 无 |
| 申万成分PIT | index_member_all | (in/out_date) | 7787 | **A** | v_sw_industry_pit + dim_stock_sw_industry |
| 龙虎榜机构席位 | top_inst | 2018-01~2026-06 | 187万 | B | altdata_factors |
| 龙虎榜每日 | top_list | 2018-01~2026-06 | 15万 | B | altdata_factors |
| 券商研报 | report_rc | 2018-01~2026-06 | 50万 | B | altdata_factors |
| 机构调研 | stk_surv | 2021-08~2026-06 | 36.6万 | B | altdata_factors(tinyshare新解封) |
| 财报-质量 | fina_indicator | 2023-01~2026-04 | 9.6万 | **A** | feature_panel.roe_dt_asof |
| 财报-利润表 | income | 2023-01~2026-04 | 7.6万 | **C** | 无 |
| 财报-业绩预告 | forecast | 2023-01~2026-06 | 1.7万 | **C** | 无 |
| 财报-业绩快报 | express | 2023-04~2026-05 | 4465 | **C** | 无 |
| 财报-主营 | fina_mainbz | 仅2025 | 2.6万 | **C** | 无(且周期残缺) |
| 财报-预收款 | balancesheet_advrecv | 2017~2021 | 2.6万 | **C** | 无(且停在2021) |
| 分红 | dividend | 2022-08~2026-05 | 1.6万 | B | c0/cyq 事件研究 |
| 东财概念指数 | dc_index | 2025-01~2026-06 | 21.7万 | **C** | 无 |
| 东财概念成分 | dc_member | 2025-01~2026-06 | 2329万 | **C** | 无(仅2025+) |
| 涨跌停日线 | limit_list_d | 2023-01~2026-06 | 10.5万 | **C** | 无 |
| 涨跌停题材 | limit_cpt_list | 2024-01~2026-06 | 1.2万 | **C** | 无 |
| 涨跌停限价 | stk_limit | 2022-01~2026-06 | 723万 | **C** | 无(R2 execution 该用!) |
| ST标记 | stock_st | 2022-01~2026-06 | 16万 | **C** | 无(宇宙过滤该用) |
| 停牌 | suspend_d | 2022-01~2026-06 | 4万 | **C** | 无(R2 该用) |
| 同花顺热榜 | ths_hot | 2024-01~2026-06 | 40万 | **C** | 无 |
| 指数基本面 | index_dailybasic | 见上 | — | C | — |
| 交易日历 | trade_cal | 1990~2026 | 1.3万 | **A** | 全局日历真相源 |

**小结**: 4 域 live(K线/主资金/质量财报/申万分类+日历) · 8 域仅探索 · **16 域死数据**(拉了没用)。

## 二、未拉但可能有价值 (我的判断, 按对"主升浪/动量/板块/风控"相关性排序)

| 优先 | 接口 | 积分 | 价值判断 |
|---|---|---|---|
| **P0** | **weekly / monthly / stk_week_month_adj** | 2000 | 你要的**周线主升浪**原生周/月线(现在我从日线重采样, 原生更干净) |
| **P0** | **ths_index / dc_daily / dc_concept** | 6000 | **板块/概念指数行情** = 直接的板块同期热度/资金轮动(板块维度=最可能藏判别力处, 比聚合个股干净; 判别力待结构型 GT 重验, 不引污染期数字) |
| **P0** | **moneyflow_ths / moneyflow_ind_ths / moneyflow_cnt_ths** | 6000 | 同花顺**个股/行业/概念资金流** = 板块资金轮动的直接源(口径需与membership自洽) |
| P1 | **stk_factor_pro / stk_factor** | 5000 | 预算好的**技术因子(量化因子)** = Qlib Alpha158 等价, 省自建; 横截面可比 |
| P1 | **stk_holdertrade** | 2000 | **股东增减持** = 内部人信号(信息维度不同于价量, 你点过) |
| P1 | **margin / margin_detail / margin_secs** | 2000 | **融资融券** = 杠杆情绪/标的(两融余额变化是真信号) |
| P1 | **hm_list / hm_detail** | 5000/10000 | **游资名录+每日明细** = 比 top_inst 更细的游资席位(你要的龙虎榜深化) |
| P1 | **kpl_list / kpl_concept_cons** | 5000 | **开盘啦榜单+题材成分** = 题材热度/连板(主升浪常题材驱动) |
| P2 | **index_weight** | 2000 | 指数成分权重(基准构建/对标更精确) |
| P2 | **top10_holders / top10_floatholders** | 2000 | 前十大(流通)股东(机构持仓集中度) |
| P2 | **fund_daily / fund_portfolio** | 5000 | ETF日线 + 公募持仓(ETF策略 / 公募抱团信号) |
| P2 | **stk_nineturn** | 6000 | 神奇九转(一个现成的反转择时技术信号) |
| P2 | **pledge_stat** | 2000 | 股权质押(爆仓风险过滤) |
| P3 | dc_hot/limit_list_ths/limit_step | 8000 | DC热榜/同花顺涨停榜/连板天梯(题材情绪, 与已拉ths_hot/limit_list_d重叠) |

> 期货/外汇/债券/期权/港股(fut_*/fx_*/bond_*/opt_*/hk_*) 不在 A 股主升浪范围内, 暂不取。

## 三、死数据处置建议 (拉了无消费的 16 域)

- **R2 execution 该立刻用 (不是死数据, 是漏接)**: `stk_limit`(涨跌停价→涨停剔买不进) / `suspend_d`(停牌冻结) / `stock_st`(ST宇宙过滤) — 这三个是 execution-aware 回测的硬料, 当前回测假设无摩擦正缺它们。**建议接进回测引擎**。
- **财报线接了没用 (income/forecast/express/fina_mainbz/advrecv)**: 质量/成长/PEAD 因子料; 当前优先监督式 episode-first 结构型主升浪方向 (D2 因子判别力待跑), 基本面线暂不急。advrecv 停在 2021 + mainbz 仅 2025 = 周期残缺, 用前须回填。
- **概念线 dc_index/dc_member 仅 2025+**: 东财概念历史短, 价值有限; 板块热度优先用 ths_index/dc_daily(2020起)。
- **真死数据 (可退役)**: moneyflow_ind_dc/mkt_dc/hsgt(资金流冗余, 同花顺版更好) / index_dailybasic / ths_hot(题材情绪未用)。

## 四、结论
1. **已拉 32 域但仅 4 域进 live**, 16 域死数据 = 数据拉取领先于消费(探索期正常, 但 stk_limit/suspend_d/stock_st 是 R2 漏接该补)。
2. **最该补的未拉数据**: 周/月线(主升浪周线) + 板块概念指数行情&资金流(板块热度真信号) + 技术因子专业版(省自建)。
3. 拉数据不是瓶颈(信息维度够); 新数据优先级服从监督式 episode-first 方向 — 在结构型主升浪 GT (universe 硬门 clean) 上验各数据源对起涨/持仓/出场的判别力 (各维判别力=unknown 待逐数据验证), 不凭污染期结论定优先级。
