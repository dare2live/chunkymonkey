# tushare vs 东财妙想 aif10 — 逐域字段级对比 (2026-06-24)

> 证据来源 (measured not estimated):
> - aif10 字段全集 = `/Users/dp/Documents/M/stock/miaoxiang/schema/*.sql` (72 CREATE TABLE) + registry.py 74 ReportSpec;
>   字段样本值 = 实弹调 datacenter.eastmoney.com v1 API (600388.SH/600519.SH/000001.SZ/无secucode 多股回退), 71/74 报表抓到真值。
> - tushare = `backend/config/tushare_api_catalog.json` 240 unique 接口 (241 含 pro_bar 一码两条), 读 catalog 未调 API。
> - 详细字段清单见 `aif10_field_inventory_20260624.json` (74 报表) / `tushare_field_inventory_20260624.json` (240 接口) / `tushare_vs_aif10_comparison_20260624.json` (18 域 + 3 清单)。

## 0. 一句话裁决

**妙想替代 tushare 的可能性 = 分层结论, 校正并确认主会话判断:**

- **基石层 (K线/复权/交易日历/涨跌停/集合竞价/逐日指标/资金流/筹码) -> 替代率 ≈ 0 [NO]**。aif10 是 F10 静态/低频基本面数据库, 全无逐日 OHLCV、无复权因子、无交易日历。这是回测与特征工程的物理地基, **不可替代**。
- **F10 基本面层 (财务三表/指标/股东/估值分位/同行对比/分红/机构预测/资本运作) -> 覆盖高且多处更优 [OK]**。妙想在此层是 tushare 的强力补充乃至局部主源 (holder 已 promote、估值分位/同行估值已沙化为正式源)。
- **净结论**: 妙想是 **F10 基本面的强力补充 + 几个域的更优主源, 不是全局替代**。主会话判断成立。

## 1. 逐域覆盖对比表

| 域 | tushare 代表接口 | 妙想代表报表 | 判定 |
|---|---|---|---|
| 01 行情K线/复权/交易日历 | daily, pro_bar, adj_factor, trade_cal, stk_limit, daily_basic, moneyflow, cyq_chips | (仅 MARKETPER 区间涨跌幅 / NEWINDICATOR 实时快照) | **tushare 独有·基石 [NO]** |
| 02 财务三表 | balancesheet, income, cashflow, express, disclosure_date | GBALANCE(320)/GINCOME/GCASHFLOW/GRATIO/单季QC | **双方都有** (aif10 字段更细) |
| 03 财务指标 | fina_indicator | MAINFINADATA(166)/QTR/DUPONT(76) | **双方都有** (aif10 分层更细) |
| 04 股东·十大流通/户数/增减持 | top10_floatholders, stk_holdernumber, stk_holdertrade | FREEHOLDERS/HOLDERNUM/SHAREHOLDER_CHANGE/RELATION | **双方都有·妙想更及时 [OK]** |
| 05 股东·质押/回购/解禁 | pledge_stat, pledge_detail, repurchase, share_float | LIFTFUTURE/ACCUMDETAILS(仅解禁) | **tushare 多** (质押/回购独有) |
| 06 估值分位/同行估值 | daily_basic(仅点值) | STOCKVALUATIONTANTILE/INDUSTRY_CVALUE | **妙想更优 [OK]** (预算分位) |
| 07 分红送转 | dividend | DIVIDEND_MAIN/LITY(派现概率)/SEO/CURVE | **双方都有·妙想更细** |
| 08 机构持股/预测/评级/调研 | fund_portfolio, report_rc, broker_recommend, stk_surv | ORGHOLDDETAILS(分桶)/DMSK_HOLDERS(QFII)/PREDICTDETAIL/ORG_SURVEYNEW | **妙想更优 [OK]** |
| 09 行业对比/分类 | index_classify, index_member_all(申万PIT), sw_daily | RELATE_GN(snapshot)/INDUSTRY_*(同行对比) | **双方都有·分工** |
| 10 龙虎榜 | top_list, top_inst, hm_detail | DAILYBILLBOARD/OPERATEDEPT_TRADE | **双方都有** |
| 11 大宗交易 | block_trade | DATA_BLOCKTRADE | **双方都有·等价** |
| 12 融资融券 | margin, margin_detail, margin_secs, slb_* | MARGIN_STATISTICS/趋势解读 | **双方都有** (tushare 多转融券) |
| 13 北向/陆股通 | hk_hold, hsgt_top10, moneyflow_hsgt | MUTUAL_STOCK_HOLDRANKN | **双方都有·都受停披露限制** |
| 14 股本结构 | stk_premarket(快照) | EH_EQUITY(70列时序) | **双方都有·妙想时序更细** |
| 15 高管 | stk_managers, stk_rewards | MANAINTRO/EXECUTIVE_HOLD_DETAILS | **双方都有** (tushare 多薪酬) |
| 16 资本运作/重组/募资 | (无结构化, 靠 anns_d 全文) | RECAPITALIZE/CAPITAL_RAISE/CAPITAL_ITEM | **妙想独有 [OK]** |
| 17 概念题材 | ths_member/dc_member(成分), moneyflow_cnt(概念流) | CORETHEME_BOARDTYPE/CONTENT(文本) | **双方都有·性质不同** |
| 18 资讯公告研报 | anns_d, irm_qa, news, report_rc | BUSINESSANALYSIS(NLP)/REMIND/BASIC_ORGINFO | **双方都有·性质不同** |

## 2. 清单 A — tushare 独有且妙想给不了 (基石不可替代)

| 项 | tushare | 妙想等价 | 标签 |
|---|---|---|---|
| K线 OHLCV (日/周/月/分钟) | daily, pro_bar, weekly, monthly, stk_mins | **无** | **基石不可替代** |
| 复权因子 | adj_factor, pro_bar(qfq/hfq) | **无** | **基石不可替代** |
| 交易日历 | trade_cal | **无** | **基石不可替代** |
| 每日指标 (PE/PB/换手/量比/市值 逐日序列) | daily_basic | 部分(分位非时序) | 基石近不可替代 |
| 涨跌停价/集合竞价/停复牌 | stk_limit, stk_auction_o/c, suspend_d | **无** | **基石不可替代** (execution-aware 回测必需) |
| 筹码分布/胜率 | cyq_chips, cyq_perf | **无** | tushare 独有 |
| 个股资金流向 | moneyflow, moneyflow_dc | **无** | tushare 独有 |
| 股权质押 (统计+明细) | pledge_stat, pledge_detail | **无** | tushare 独有 |
| 股票回购 | repurchase | **无** | tushare 独有 |
| 申万行业 PIT 成员 | index_classify, index_member_all | RELATE_GN (snapshot 无PIT历史) | 基石近不可替代 |
| 互动易 Q&A | irm_qa_sh/sz | **无** | tushare 独有 |
| 技术因子库 | stk_factor, stk_nineturn | **无** (项目可自算) | tushare 独有 |

> 关键: **妙想 RELATE_GN 行业归属是 latest snapshot, 无 out_date 历史区间** = 项目已知的 latest-snapshot leakage 红线变体。PIT 行业成员必须继续走 tushare 申万 index_member_all (含 is_new='N' 历史剔除区间)。

## 3. 清单 B — 妙想独有 gap 菜单 (按 alpha 潜力初判排序)

| # | 项 | 妙想报表 | alpha 假设 | consumer | PIT 锚 | 状态 |
|---|---|---|---|---|---|---|
| 1 | 十大流通股东季中ad-hoc变动 | FREEHOLDERS, DMSK_HOLDERS | 聪明钱季中权益变动早于财报 = 提前信号 | holder 因子层 | [OK] FREEHOLDERS=UPDATE_DATE 可用日锚; DMSK=NOTICE_DATE(含临时公告) | **已 promote 主源** |
| 2 | 估值历史分位 PE/PB/PS/PEG @多窗 | STOCKVALUATIONTANTILE | 估值分位均值回归/低估+催化 | 估值因子层 | [WARN] 无 date_field = 快照, 须自存每日才有PIT时序 | 已沙化正式源 |
| 3 | 同行估值/成长/杜邦排名 | INDUSTRY_CVALUE/GROWTH/DBFX | 行业内相对排名 = sector-relative alpha | sector-relative 特征 | [WARN] REPORT_DATE 报告期锚, 须外接披露日 | 已沙化正式源 |
| 4 | 机构持仓 ORG_TYPE 分桶 | ORGHOLDDETAILS, DMSK_HOLDERS | 各类机构(社保/QFII/险资)持仓方向 = 资金属性 alpha | 机构持股因子 | [WARN] END_DATE, 部分带 NOTICE_DATE | gap (QFII 已接) |
| 5 | 分红派现概率提示 | DIVIDENDNEW_LITY/PROFILE | 高股息+派现确定性 = 红利增强 | 红利因子 | [WARN] 无明确披露日, 当快照 | gap |
| 6 | 盈利预测明细 (机构-分析师-发布日) | PREDICTDETAIL, ORGRATING | 预期上调/评级变动 = 预期差 alpha | 预期因子/主升浪候选 | [OK] RESEARCHER_DATE/PUBLISH_DATE = 发布日真 PIT | **gap (高潜·PIT干净)** |
| 7 | 资本运作 重组/募资/募投 | RECAPITALIZE, CAPITAL_RAISE/ITEM | 重组/再融资事件驱动 | 事件驱动层 | [OK] NOTICE_DATE 公告日 | gap (tushare 无结构化对应) |
| 8 | 限售解禁 持有人维度 | LIFTFUTURE, ACCUMDETAILS | 解禁供给冲击空头信号; 含 holder_name | 事件/风险层 | [OK] LIFT_DATE = 未来已知日 | gap |
| 9 | 经营评述 NLP 全文 | OP_BUSINESSANALYSIS | 管理层讨论文本情绪/主题 | NLP 文本因子(未来) | [WARN] REPORT_DATE, 须外接披露日 | gap (探索性) |
| 10 | 题材亮点/详情文本 | CORETHEME_CONTENT/BOARDTYPE | 概念标签/题材热度 | 概念标签层 | [NO] 无 date_field=静态snapshot, 单用会 leakage | gap (PIT不可得, 慎用) |

## 4. 清单 C — 双方都有但妙想更优/更及时

| 域 | 妙想 | tushare | 为何妙想更优 | 证据 |
|---|---|---|---|---|
| 十大流通股东 | FREEHOLDERS | top10_floatholders | tushare 季报驱动滞后~4月, 不收季中变动 | 600388 紫金入主龙净: tushare 只到 3/31, aif10 收到 6/8 (已 promote 主源, 物删 tdx_f10, backfill 99.6%) |
| QFII持仓 | DMSK_HOLDERS | fund_portfolio(仅公募) | tushare 无独立 QFII; aif10 含季中临时公告 | END_DATE=2026-06-08 NOTICE_DATE=2026-06-13 临时公告 |
| 估值分位 | STOCKVALUATIONTANTILE | daily_basic(点值自算) | aif10 预算 1Y/3Y/5Y/10Y 多窗分位 | 7字段直给 PE/PB/PS/PEG 30/50/70 分位 |
| 同行财务对比 | INDUSTRY_CVALUE/GROWTH/DBFX/MARKET | 需自 JOIN 算 | aif10 直给行业平均/中值/排名 | INDUSTRY_CVALUE 27字段含多年度+行业排名 |
| 股本结构时序 | EH_EQUITY | stk_premarket(盘前快照) | aif10 含历年股本变动时序+限售/流通拆分 | EH_EQUITY 70字段 END_DATE 锚历年变动 |

## 5. PIT 视角 (alpha 判定关键, 诚实分级)

aif10 74 报表按"date_field 是真披露日还是仅报告期"分级 (含 payload 内 NOTICE_DATE 字段修正):

| PIT 分级 | 报表数 | 含义 | alpha 可用性 |
|---|---|---|---|
| disclosure_or_trade_date (真PIT) | 21 | date_field = NOTICE_DATE/TRADE_DATE/CHANGE_DATE/LIFT_DATE/RESEARCHER_DATE | [OK] 直接可用 |
| report_period **但 payload 带 NOTICE_DATE** | 12 | 锚 END_DATE 但有真披露日字段, **改锚 NOTICE_DATE 即 PIT-recoverable** | [OK] 改锚后可用 |
| report_period (无披露日) | 16 | 仅 END_DATE/REPORT_DATE, 报告期 != 可用日 | [WARN] 单用会 leakage, 须外接财报披露日 (tushare disclosure_date) |
| 无 date_field (静态/快照) | 25 | 当日 snapshot 无时序 | [WARN/NO] 历史回溯须自行每日落库; 否则只有当下值 |

> **重点警示**: 估值分位 (TANTILE)、题材 (CORETHEME)、行业归属 (RELATE_GN)、同行对比当日值 = 无 date_field 快照。回测要 PIT 时序必须**自己每日落库存档**, 否则只有 latest snapshot = leakage 风险 (项目 §4.5 latest-snapshot 红线)。这是把妙想当 alpha 源的最大 PIT 工程负担。

## 6. 诚实标注 (未核证 / 抓空 / 局限)

- **抓空 3 报表 (fetch_status=empty, 无 schema 文件, 0 字段)**: `RPT_DMSK_NEWINDICATOR`(实时市值快照)、`RTP_F10_POPULAR_LEADING`(人气龙头日榜)、`RTP_F10_ADVANCE_DETAIL_NEW`(同类事件扩展)。这 3 个走 v1/v0 均返回空, 推测需特殊参数或属实时/弹窗端点; 均非核心 F10 基本面, 不影响主裁决。
- **财务三表 v0 路径返回空, 字段来自 schema**: GBALANCE/GINCOME/GCASHFLOW 等 6 个 api=v0 报表, 用 get_v0 返回 0 行 (legacy v0 endpoint 似已废), 但用 v1+reportName 能抓到真值 (本次已用 v1 补抓成功); 字段全集以 schema SQL 为准 (320~373 列), 权威。
- **PIT 锚语义仅核证到字段存在层**: 我核证了 12 个报表 payload 含 NOTICE_DATE/UPDATE_DATE (改锚即 PIT-recoverable), 但**未逐报表验证 NOTICE_DATE 是否 = 真实首次披露日**(可能是更新日)。FREEHOLDERS 的 UPDATE_DATE 当可用日锚是项目已实现并验收的 (availability_source), 其余报表的披露日语义需用前实弹核证。
- **gap 的真实 alpha 未实测**: 清单 B 的 alpha 假设是基于字段语义的**初判**, 非回测验证。任何 gap 转正必走项目 alpha 验证程序 (含成本 backtest 绝对收益 + R1/R2 门), IC 高不等于能赚钱 (项目坑库铁律)。
- **域归类是人工判断**: 18 域映射由我按字段语义手工归类, 个别接口可跨域 (如 daily_basic 既属行情又属估值); 以 comparison JSON 内逐接口清单为准。
- **未覆盖 tushare 非 A股域**: tushare 含港股/美股/期货/基金/可转债/债券/宏观/电影票房等大量非 A股个股接口 (us_*/hk_*/fut_*/cb_*/fund_*/bo_* 等约 80+ 个), 本对比聚焦 A股个股域, 这些非 A股接口未逐一列入域表 (妙想全无对应, 全属 tushare 独有但与本项目 A股选股主线无关)。
