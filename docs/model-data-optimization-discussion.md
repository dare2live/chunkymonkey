# 妙想 F10 数据源接入专题

**起始**: 2026-04-27
**作者**: 用户 + Claude
**目标**: 把妙想 F10 (`aif10` / `datacenter.eastmoney.com/securities`) 接入项目, **专门替代 akshare 中 tdxhub 不能覆盖的部分** + **补充项目空白维度**. 范围严格限定在 akshare 替代, 不与 tdxhub 重叠.

> 设计原则 (用户 2026-04-27 三次澄清):
>
> 1. **tdxhub 是最稳定的数据源, 不动** (K 线 / 财务 gpcw / 行业 / 板块 / 实时行情)
> 2. **妙想 F10 主要是为了替换 akshare**, 不是替换 tdxhub
> 3. **综合考虑的是 tdxhub 未覆盖的部分**
> 4. **加工好的数据可以直接用**, 不必从原始数据建模
> 5. **样本量足够支撑统计结论 + 影响股价**, 不堆砌
> 6. **十大"流通"股东**, 不是十大股东 (流通股口径才有交易意义)
>
> **本专题与"东财 skill"工程整体设计是两回事**:
> - 本专题: 单一数据源 (妙想 F10) 的接入选型与字段映射
> - 东财 skill: 更大的工程概念 (含 datacenter-web Phase 1 / aif10 Phase 2 / 客户端 SDK 设计) — 后续单独讨论

---

## 1. 三层数据源定位

| 层 | 数据源 | 角色 | 行动 |
|---|---|---|---|
| 1. 行情 / 板块 / 财务原始 | **tdxhub (mootdx)** | **最稳定, 主用** | 不动 |
| 2. 公司面 / 估值 / 评级 / 事件 | **妙想 F10** (本专题) | **替代 akshare** | Phase 2.5 接入 |
| 3. 临时备用 | akshare | 现存 15 个调用, 逐步退役 | 按本专题排期切换 |

### 1.1 tdxhub 当前覆盖范围 (代码扫描确认)

| 数据 | 实现位置 | 状态 |
|---|---|---|
| 日 K / 月 K | `services/kline_source.py` (mootdx.Quotes) | ✅ 主用 |
| 通达信行业 `tdxhy.cfg` | `services/tdx_industry_client.py` | ✅ 主用 |
| 财务 gpcw 季度文件 | `scripts/build_fundamental_quarterly.py` (mootdx.Affair) | ✅ 主用 |
| 板块文件 | `services/block_client.py` | ✅ 主用 |
| 除权除息 + 股本变动 | `services/xdxr_client.py` | ✅ 主用 |
| 实时行情快照 | mootdx.Quotes 实时 | ✅ 主用 |

**tdxhub 不覆盖** (是 akshare / 妙想 F10 的领域):
- 公司面: 评级 / 一致预期 / 估值分位
- 事件: 高管增减持 / 限售解禁 / 分红明细 / 股票回购 / 停复牌
- 横截面: 同行 PE/PEG/EPS rank
- 监管层: 两融每日明细
- 股东: 户数变化 / 流通股东季度差分 / 机构持仓 ORG_TYPE 分桶

### 1.2 当前 akshare 调用清单 + 替代决策

代码扫描全部 akshare 函数 (`grep "ak\."`), 共 15 个不同函数:

#### 类别 A — 行情 K 线类 (tdxhub 已覆盖, 不必动)

| akshare 函数 | 用途 | 决策 |
|---|---|---|
| `ak.stock_zh_a_hist` | A 股日 K | tdxhub 已覆盖, akshare 仅作 fallback (`kline_source.py`), 保留 |
| `ak.stock_zh_a_daily` | A 股日 K (旧) | 同上 |
| `ak.stock_zh_a_hist_tx` | A 股 K 线 (TX 源) | 同上 |
| `ak.fund_etf_spot_ths` | ETF 行情 (同花顺源) | ETF 业务模块用, 妙想 F10 不覆盖 ETF, 保留 |

#### 类别 B — 妙想 F10 替代 (tdxhub 不覆盖, 当前走 akshare)

| akshare 函数 | 当前位置 | 用途 | 妙想 F10 替代 reportName |
|---|---|---|---|
| `ak.stock_tfp_em` | `services/audit.py:298` | 停复牌 | `RPT_F10_REMIND_TRADESUSPEND` (待确认) |
| `ak.stock_margin_detail_sse` | `services/margin_client.py:104` | 上交所两融明细 | `RPT_MARGIN_STATISTICS_STOCKS` (含沪深) |
| `ak.stock_margin_detail_szse` | `services/margin_client.py:109` | 深交所两融明细 | 同上 |
| `ak.stock_repurchase_em` | `services/capital_client.py:268` | 股票回购 | `RPT_F10_REPURCHASE` (待确认) |
| `ak.stock_history_dividend` | `services/capital_client.py:263` | 历年分红送转汇总 | `RPT_F10_DIVIDEND_COMPRE` / `RPT_F10_DIVIDEND_3YEAR` |
| `ak.stock_history_dividend_detail` | `services/capital_client.py:278` | 分红明细 | `RPT_F10_DIVIDEND_MAIN` |
| `ak.stock_restricted_release_detail_em` | `services/capital_client.py:273` | 限售解禁 | `RPTA_APP_LIFTFUTURE` |
| `ak.stock_financial_abstract` | `services/financial_indicator_client.py:181` | 扩展财务指标 | `RPT_PCF10_FINANCEMAINFINADATA` + `RPT_F10_FINANCE_DUPONT` |
| `ak.stock_ggcg_em` | `scripts/build_executive_trade_events.py` | 高管增减持 | `RPT_EXECUTIVE_HOLD_DETAILS` / `RPT_F10_TRADE_EXCHANGEHOLD` |
| `ak.stock_info_a_code_name` | `services/security_master.py:80` | A 股代码列表 | (mootdx 优先 / aif10 兜底) |

#### 类别 C — 替代源待评估

| akshare 函数 | 用途 | 评估 |
|---|---|---|
| `ak.tool_trade_date_hist_sina` | 交易日历 | 妙想 F10 没单独 endpoint, 项目 `dim_trading_calendar` 表已存, 保留 akshare 或迁 tdxhub |

### 1.3 项目当前空白 (妙想 F10 补充, 非替代)

下列维度项目当前**完全没有**, 妙想 F10 能直接拿到加工好的数据:

| 维度 | reportName | 价值 |
|---|---|---|
| 估值分位 | `RPT_STOCKVALUATIONTANTILE` | PE/PB 在自身历史 30/50/70 分位 |
| 卖方一致预期 | `RPT_HSF10_RES_ORGRATING` + `RES_PREDICT_STATISTICS` | 综合评级 + 各档家数 + 多年度 EPS 均值 |
| 股东人数变化 | `RPT_F10_EH_HOLDERNUM` | 户数 / 集中度 / 人均流通股 |
| 同行排名 | `RPT_PCF10_INDUSTRY_CVALUE` + `INDUSTRY_GROWTH` | 行业内 PE/PEG/EPS增长 RANK |
| 机构持仓 ORG_TYPE 分桶 | `RPT_F10_MAIN_ORGHOLDDETAILS` | 基金/QFII/社保/券商/保险/信托 全维度 |
| 主营构成 | `RPT_F10_FN_MAINOP` | 业务结构变化 (行业/产品/地区) |

### 1.4 已有工程基础

- **Phase 1** (commit `e74122f1`): `backend/services/eastmoney_skill/` BaseClient + datacenter-web RPC, 替代 akshare 4 处 (调研/龙虎榜/QFII/资金流底层)
- **Phase 2** (commit `a1d473fb`): `aif10.py` 通用 RPC + 5 个 endpoint smoke test (全过). 完整 spec 见 [docs/eastmoney-aif10-spec.md](eastmoney-aif10-spec.md)

---

## 2. 接口签名 (妙想 F10)

```
GET https://datacenter.eastmoney.com/securities/api/data/v1/get
    ?reportName=<逻辑表名>
    &filter=(SECUCODE="600519.SH")(...)
    &columns=ALL
    &pageNumber=1&pageSize=N
    &sortTypes=-1&sortColumns=REPORT_DATE
    &source=HSF10&client=PC
```

主键: `SECUCODE` = 6 位 + `.SH/.SZ/.BJ/.HK`. Referer 必须 `emweb.eastmoney.com`. 单 IP ≤ 2 QPS.

完整 16 模块 reportName 映射 + ORG_TYPE 枚举 + 各模块字段表见 [docs/eastmoney-aif10-spec.md](eastmoney-aif10-spec.md). 本文档只讨论**对项目有价值的子集**.

---

## 3. 项目准备使用的字段 + alpha 假设

按"对股价影响 × 样本量 × 加工程度"三维度排. 每个指标都附 **alpha 假设** (为什么会影响价格), 否则不入模.

### 3.1 P0 — 立即入信号层 (高频更新, 影响价格)

#### A. 估值分位 (`RPT_STOCKVALUATIONTANTILE`)

**alpha 假设**: 个股 PE/PB 在自身历史 30 分位以下 → 估值修复概率高; 70 分位以上 → 均值回归压力. 这是 ready-to-use 横截面信号.

**字段**:
| 字段 | 含义 | 用法 |
|---|---|---|
| STATISTICS_CYCLE | 周期 (1/2/3/4 = 1Y/3Y/5Y/10Y) | 用 4 (10Y) 最稳 |
| INDEX_TYPE | 指标 (1=PE / 2=PB / 3=PS / 4=PEG / ...) | 取 PE+PB |
| PERCENTILE_THIRTY / FIFTY / SEVENTY | 30/50/70 分位值 | 当前值 vs 这三档判位 |

**入模方式**:
- 衍生特征 `pe_pos_10y`: 当前 PE 在 10Y 历史中的分位 (从分位反算)
- 衍生特征 `pb_pos_10y`: 同上 PB
- 当前值需从 `RPT_PCF10_FINANCEMAINFINADATA` 取 PE/PB 实时

**频率**: 日级 (每日盘后)
**样本量**: 全市场 5500 票 × 每天一行 = 充足

#### B. 一致预期 (`RPT_HSF10_RES_ORGRATING` + `RPT_HSF10_RESPREDICT_STATISTICS`)

**alpha 假设**: 卖方一致评级是机构集体观点, 上调评级 / 上修预测 EPS 是经典短期 alpha (PEAD-like 效应).

**字段** (`RPT_HSF10_RES_ORGRATING`):
| 字段 | 含义 |
|---|---|
| RATING_RECENT_*M | 1/2/3/6/12 月窗口下评级系数 |
| RATING_FACTOR | 综合评级 (1-5, 1=买入) |
| BUY_NUM / OVERWEIGHT_NUM / NEUTRAL_NUM / UNDERWEIGHT_NUM / SELL_NUM | 各档家数 |

**字段** (`RPT_HSF10_RESPREDICT_STATISTICS`):
| 字段 | 含义 |
|---|---|
| EPS_AVG / EPS_GROWTH | 25A/26E/27E 各年度 EPS 均值 + 增速 |
| PE_AVG | 对应 PE 均值 |

**入模方式**:
- 信号 `analyst_rating_score`: 综合评级 1-5 (1 最强), 直接 z-score
- 信号 `eps_revision_30d`: 30 天内 EPS_AVG 变化率 (需累积入库才能算)
- 信号 `analyst_consensus_strength`: 买入家数 / 总家数 (覆盖度 + 强度)

**频率**: 周级 (机构发研报后才更新)
**样本量**: 主流 A 股都有覆盖 (~3000-4000 只), 小盘股可能缺

#### C. 股东人数变化 (`RPT_F10_EH_HOLDERNUM`)

**alpha 假设**: 户数减少 = 筹码集中度上升 = 主力建仓信号 (经典中长期 alpha). 散户出货被大户接盘.

**字段**:
| 字段 | 含义 |
|---|---|
| HOLDER_NUM | 当前户数 |
| HOLDER_NUM_RATIO | 期间变化率 |
| AVG_FREESHARES | 人均流通股 |
| AVG_HOLD_AMT | 人均持金 |
| TOP10_HOLD_RATIO / TOP10_FREE_HOLD_RATIO | 前 10 大 / 前 10 流通合计占比 (集中度) |

**入模方式**:
- 信号 `holder_num_change_pct`: 季度环比变化率, 负数越大 = 越集中
- 信号 `top10_concentration`: TOP10 合计 %, 直接当横截面 rank
- 衍生 `holder_concentration_trend`: 4 季度滚动 holder_num_ratio 平均

**频率**: 季度 (披露驱动)
**样本量**: 全市场 5500 票 × 每季度一行 ≈ 22000 季观测

#### D. 十大流通股东 (`RPT_F10_EH_FREEHOLDERS`)

**alpha 假设**: 流通股东变动 (新进/增持) 是机构持仓变化的强信号. 与项目当前 `market_raw_holdings` 互补 (datacenter-web 已经在用).

**用户明确**: 项目要的是**流通股东**, 不是全部股东. (流通股口径才有交易意义).

**字段**:
| 字段 | 含义 |
|---|---|
| HOLDER_RANK | 排名 1-10 |
| HOLDER_NAME | 股东名称 |
| HOLDER_NEWTYPE | 类型 (基金/QFII/社保/...) |
| HOLD_NUM | 持股数 |
| HOLD_NUM_CHANGE | 变化数 |
| HOLDNUM_CHANGE_NAME | 变化原因 (新进/增加/减少/不变) |
| HOLDER_MARKET_CAP | 市值 |
| FREE_HOLD_RATIO | 占流通比 |

**入模方式**:
- 已有 (Phase 1): `market_raw_holdings.report_date` 季度更新, `fact_institution_event` 派生事件
- 妙想补充: `RPT_F10_SHAREHOLDER_CHANGE` 直接给 **季度差分**, 比项目自己用 SQL 算更准

**频率**: 季度
**样本量**: 充足

#### E. 同行估值排名 (`RPT_PCF10_INDUSTRY_CVALUE` + `RPT_PCF10_INDUSTRY_GROWTH`)

**alpha 假设**: 行业内 PE/PEG 低分位 + EPS 增速高分位 = 经典 GARP 选股. 已经是 ready-to-use 排序.

**字段** (CVALUE 估值):
| 字段 | 含义 |
|---|---|
| PEG / PE_25A / PE_TTM / PE_26E~28E / PS | 多年度 PE / PEG / PS |
| INDUSTRY_AVG / INDUSTRY_MEDIAN | 行业平均 / 中值 |
| RANK | 行业内排名 |

**字段** (GROWTH):
| 字段 | 含义 |
|---|---|
| EPS_GROWTH_3Y / EPS_GROWTH_25A / EPS_GROWTH_TTM | EPS 多年增速 |
| OR_GROWTH_3Y / OR_GROWTH_25A / OR_GROWTH_TTM | 营收多年增速 |
| INDUSTRY_AVG / RANK | 同行对比 |

**入模方式**:
- 信号 `peer_pe_rank_pct`: PE 在行业内分位 (越低越好)
- 信号 `peer_eps_growth_rank_pct`: EPS 增速分位 (越高越好)
- 综合信号 `peer_garp_score = peer_pe_rank_pct - peer_eps_growth_rank_pct` (低 PE 高增长)

**频率**: 季度
**样本量**: 5500 票 × 每季 = 充足

### 3.2 P1 — 替代/补充现有数据

#### F. 机构持仓概览 (`RPT_F10_MAIN_ORGHOLDDETAILS`)

**alpha 假设**: 不只跟 QFII (项目当前), 基金/社保/券商/保险全维度对比 → 形成"机构抱团度"指标.

**关键字段**:
- `ORG_TYPE`: 01 基金 / 02 QFII / 03 社保 / 04 券商 / 05 保险 / 06 信托
- 每个 type 下的: `ORG_NUM` (家数), `TOTAL_HOLD` (合计股数), `FREE_HOLD_RATIO` (占流通%)

**入模方式**:
- 信号 `inst_holding_breadth`: 持有该股的机构家数总和 (跨类型)
- 信号 `inst_diversity`: 多少种类型机构持有 (1-6 取值)
- 项目当前只跟 QFII, 这里补充 5 种新维度

**频率**: 季度
**注**: 项目当前 `inst_holdings` 表是个股级别的"具体持仓 holder", 这里 `RPT_F10_MAIN_ORGHOLDDETAILS` 是"按机构类型聚合", 互补.

#### G. 主营构成 (`RPT_F10_FN_MAINOP`)

**alpha 假设**: 业务结构变化 (新业务比例上升 / 传统业务下降) 是中长期价值重估信号.

**字段**: `MAINOP_TYPE` (1=行业 / 2=产品 / 3=地区), `ITEM_NAME`, `MAIN_BUSINESS_INCOME`, `RATIO`, `MAIN_BUSINESS_RATIO`, `GROSS_RATE`.

**入模方式**:
- 衍生特征 `main_business_concentration`: 最大主营业务收入占比 (高度集中 vs 多元化)
- 衍生特征 `business_diversification_change_yoy`: 主营结构 HHI 同比变化
- 这两个特征在小盘成长股切换时强 (扩品类 / 进入新赛道)

**频率**: 半年报 + 年报
**样本量**: 季度披露, 但只在年报/中报详细

#### H. 限售解禁 (`RPTA_APP_LIFTFUTURE`)

**alpha 假设**: 解禁日临近 → 短期股价压力 (供给冲击). 项目 `capital_client` 已有部分但口径需对齐.

**字段**: `LIFT_DATE`, `LIFT_AMT`, `RATIO_PCT_TOTAL`, `RATIO_PCT_FREE`, `LIFT_TYPE` (首发/定增/股权激励).

**入模方式**:
- 衍生特征 `days_to_unlock`: 距下次解禁天数 (越近压力越大)
- 衍生特征 `unlock_pct_total_30d`: 未来 30 天累计解禁占总股本%

**频率**: 月级 (公告驱动)

### 3.3 P2 — 事件驱动 (低频但精准)

#### I. 高管持股变动 (`RPT_EXECUTIVE_HOLD_DETAILS` + `RPT_F10_TRADE_EXCHANGEHOLD`)

**alpha 假设**: 高管 / 董事会成员增持 = 强买入信号 (insider conviction); 减持 = 卖出信号. 项目 `build_executive_trade_events` 有部分覆盖.

**字段**: `CHANGE_DATE`, `CHANGE_PERSON`, `RELATION`, `CHANGE_NUM`, `AVG_PRICE`, `CHANGE_REASON`, `POSITION` (董事/独董/总经理/...).

**入模方式**:
- 事件信号 `insider_buy_30d`: 30 天内高管净买入金额
- 事件信号 `insider_buy_breadth`: 30 天内净买入的高管人数
- 比较项目当前 `executive_trade_events` 字段口径, 决定是否替换 / 补充

**频率**: 公告驱动 (3 天内披露)

#### J. 大事提醒 (`RPT_F10_REMIND_RELATIONSHIP`)

**alpha 假设**: 公告事件聚合 (重组 / 业绩预告 / 业绩快报) 触发短期波动.

**入模方式**: 不直接入模, 作为审计字段标记股票"近期事件密度", 在排序后给用户/模型看.

**注**: 已被项目 `event_engine` 部分覆盖 (机构事件), 这里是"全公司事件" 互补.

### 3.4 不接入 (用户原话: 不堆砌)

| 模块 | 不入原因 |
|---|---|
| 财务三大表全字段 | mootdx gpcw 已结构化, 重复 |
| 公告全文 / 经营评述 | NLP, 当前阶段不入主轨 |
| 公司基本资料 / 高管简历 | 缓变, 不影响股价 |
| 大宗交易个股纵向 | 已有横向 lhb_client |
| 资本运作 / 募集资金 | 低频, 不形成 alpha |
| 同概念 / 同地域排名 | 项目已有行业关联表 |
| 核心题材 (概念) | NLP, 不可量化 |
| 研究报告全文 | NLP |

---

## 4. 字段标准化与建模思路

### 4.1 三类数据的入库与建模模式

**(a) 时序快照型** — 估值分位 / 一致预期 / 户数 / 主营构成

模式:
- 表: `mart_stock_<dim>_snapshot(stock_code, snapshot_date, ...)`
- 主键: `(stock_code, snapshot_date)`
- 入库: 每周/每季快照, 不覆盖
- 建模: 取 `snapshot_date <= signal_date` 的最新值, lag 1 天避未来函数

**(b) 横截面排名型** — 同行 PE rank / 同行 EPS 增长 rank / 集中度

模式:
- 表: `mart_stock_peer_rank(stock_code, snapshot_date, dim, value, rank_pct)`
- 入库: 每月计算一次 (慢变)
- 建模: 直接当 feature, 不需衍生

**(c) 事件型** — 解禁 / 高管增持 / 公告大事

模式:
- 表: `raw_<event>_events(stock_code, event_date, event_type, value)`
- 主键: `(stock_code, event_date, event_type)`
- 入库: 公告日写入, 不覆盖
- 建模: rolling 窗口聚合 (近 30/60 日累计)

### 4.2 与项目现有 fact_feature_panel 集成

`fact_feature_panel` 已是横截面特征矩阵, 新增列名约定:
- `em_pe_pos_10y` / `em_pb_pos_10y` (估值分位)
- `em_analyst_score` / `em_eps_revision_30d` (一致预期)
- `em_holder_num_chg_q` / `em_top10_concentration` (集中度)
- `em_peer_pe_rank` / `em_peer_eps_growth_rank` (同行)
- `em_inst_breadth` / `em_inst_diversity` (机构持仓)
- `em_main_biz_concentration` (主营构成)
- `em_days_to_unlock` / `em_unlock_pct_30d` (解禁)
- `em_insider_buy_30d` / `em_insider_buy_breadth_30d` (高管)

`em_` 前缀统一标识来自 eastmoney_skill, 跟 mootdx 字段区分.

### 4.3 入模红线 (与之前 §4.21 一致)

- Coverage profile: 每个 snapshot_date 票覆盖率 ≥ 95%
- Quality profile: 极端值 winsorize, 异常率 < 1%
- 单特征 RankIC 提升 ≥ 0.005 (vs base_43)
- 分层收益稳定改善 (5 fold walk-forward), 不只一两个交易日贡献
- 通不过 → 留作审计字段, 不入主轨

---

## 5. 接入路径 (在项目内, 不做独立 SDK 项目)

> 注: "做成 GitHub 项目 / 类 tdxhub SDK" 是更大的话题, 涉及 datacenter-web + aif10 + push2 多个东财子域统一封装. **本专题不展开**, 等 Phase 2.5/2.6 跑通再单独讨论.

本专题的妙想 F10 接入仅在 `backend/services/eastmoney_skill/aif10.py` 内扩展 (Phase 2 已就位), 加 `reports/` 目录承载业务封装:

```
backend/services/eastmoney_skill/
├── client.py                       # ✅ Phase 1 BaseClient
├── datacenter.py                   # ✅ Phase 1 datacenter-web RPC
├── quote.py                        # ✅ Phase 1 push2his/push2delay
├── aif10.py                        # ✅ Phase 2 aif10 通用 RPC + 5 个 smoke endpoint
└── reports/                        # ⬜ Phase 2.5 待加: 业务封装
    ├── valuation.py                # P0 估值分位 → em_pe_pos_10y/pb_pos_10y
    ├── analyst.py                  # P0 一致预期 → em_analyst_score/eps_revision_30d
    ├── holders.py                  # P0 户数 + 流通股东季度差分
    ├── peer.py                     # P0 同行 PE/EPS 增长 rank
    ├── unlock.py                   # P1 替代 ak.stock_restricted_release_detail_em
    ├── insider.py                  # P1 替代 ak.stock_ggcg_em
    ├── dividend.py                 # P1 替代 ak.stock_history_dividend*
    ├── repurchase.py               # P1 替代 ak.stock_repurchase_em
    ├── margin.py                   # P1 替代 ak.stock_margin_detail_sse/szse
    ├── halt.py                     # P1 替代 ak.stock_tfp_em
    ├── institution.py              # P2 ORG_TYPE 分桶机构持仓
    └── business.py                 # P2 主营构成
```

### 5.1 调度桶 (与 §1.3 项目空白维度对齐)

| 桶 | 模块 | 调度 |
|---|---|---|
| 日级 | 估值分位 | 17:00 一次 |
| 周级 | 一致预期 / 评级 | 周一 18:00 |
| 季度 (披露驱动) | 户数 / 流通股东 / 同行 / 机构持仓 / 主营构成 | 业绩窗口轮询 |
| 月级 | 解禁 / 分红 / 高管增减持 | 月初一次 |

### 5.2 入 fact_feature_panel 列名约定 (em_ 前缀)

新增列统一加 `em_` 前缀, 跟 mootdx 字段区分:

| 特征 | 来源 reportName |
|---|---|
| `em_pe_pos_10y` / `em_pb_pos_10y` | RPT_STOCKVALUATIONTANTILE |
| `em_analyst_score` / `em_buy_share` / `em_eps_revision_30d` | RPT_HSF10_RES_ORGRATING + RES_PREDICT_STATISTICS |
| `em_holder_num_chg_q` / `em_top10_free_concentration` | RPT_F10_EH_HOLDERNUM |
| `em_peer_pe_ttm_rank_pct` / `em_peer_eps_growth_rank_pct` / `em_garp_score` | RPT_PCF10_INDUSTRY_CVALUE/GROWTH |
| `em_inst_breadth` / `em_inst_diversity` | RPT_F10_MAIN_ORGHOLDDETAILS |
| `em_main_biz_concentration` | RPT_F10_FN_MAINOP |
| `em_days_to_unlock` / `em_unlock_pct_30d` | RPTA_APP_LIFTFUTURE |
| `em_insider_buy_30d` / `em_insider_buy_breadth_30d` | RPT_EXECUTIVE_HOLD_DETAILS |

---

## 6. 落地路线 (按 ROI 排)

### Phase 2.5 (本周 2-3 天) — 实现 P0 4 个 endpoint + 入 fact_feature_panel

按 §3.1 P0 顺序:

1. **估值分位** (1 小时): `eastmoney_skill.aif10.fetch_valuation_quantile` 已有, 加 derive `pe_pos_10y/pb_pos_10y` 到 `fact_feature_panel`
2. **一致预期** (4 小时): 实现 `RPT_HSF10_RES_ORGRATING` + `RES_PREDICT_STATISTICS`, 累积入库到 `mart_stock_consensus`, 衍生 `analyst_score / eps_revision_30d`
3. **股东人数变化** (4 小时): 实现 `RPT_F10_EH_HOLDERNUM`, 入库 `mart_stock_holder_num`, 衍生 `holder_num_change_pct / top10_concentration`
4. **同行估值** (4 小时): 实现 `RPT_PCF10_INDUSTRY_CVALUE/GROWTH`, 入库 `mart_stock_peer_rank`, 衍生 `peer_pe_rank / peer_eps_growth_rank`

每个完成后跑 RankIC 评估, 通过红线才进 base_43.

### Phase 2.6 (后续 1 周) — P1 替代 akshare (按 §1.2 类别 B 顺序)

| # | reports/ 模块 | 替代 akshare | 当前位置 |
|---|---|---|---|
| 1 | `unlock.py` | `ak.stock_restricted_release_detail_em` | capital_client |
| 2 | `insider.py` | `ak.stock_ggcg_em` | build_executive_trade_events |
| 3 | `dividend.py` | `ak.stock_history_dividend*` | capital_client |
| 4 | `repurchase.py` | `ak.stock_repurchase_em` | capital_client |
| 5 | `margin.py` | `ak.stock_margin_detail_sse/szse` | margin_client |
| 6 | `halt.py` | `ak.stock_tfp_em` | audit |

每个替换:
- 字段对齐验证 (老 akshare 返回 vs 新 aif10 返回, 关键字段非空率不下降)
- 跑一次智能更新, 同 step 数据条数不变
- 切换后保留 1 周 akshare 兜底, 验稳后删掉

### Phase 2.7 (远期) — P2 业务结构

- `institution.py` 机构持仓 ORG_TYPE 分桶 → `em_inst_breadth` / `em_inst_diversity`
- `business.py` 主营构成 → `em_main_biz_concentration`

### 待用户确认

- [ ] **6.1** 同意按 P0 1→2→3→4 顺序做? 或先做某一个 (如先估值分位最快)?
- [ ] **6.2** 入模红线 RankIC ≥ 0.005 是否合适? (base_43 已 +22pp/fold, 这个门槛低)
- [ ] **6.3** mart 表 schema 写代码前要不要 SQL 草案先讨论?
- [ ] **6.4** Phase 2.6 的 6 个 akshare 替代是否一起做? 还是先验 P0 再说?

---

## 7. 风险与边界

### 7.1 反爬

- `datacenter.eastmoney.com/securities/` 子域跟 `datacenter-web.eastmoney.com` 一样, 用户 IP 全通 (实测).
- 但跟 `push2his` / `push2.eastmoney.com` 不同, 后者用户 IP 在反爬黑名单.
- 单 IP ≤ 2 QPS, 夜间批量是底线.

### 7.2 接口稳定性

- 妙想 F10 接口是东财对内 F10, 没有公开 SLA.
- 历史看 reportName 偶有迭代 (v0/v1 共存就是证据).
- **必须做 schema 校验**: 每张逻辑表配 expected_fields, 缺失/新增告警.

### 7.3 字段语义

- 财报模板 `RPT_F10_PUBLIC_COMPANYTPYE` 必查, 一般/银行/保险/券商字段集差异大.
- 单位混乱: 主营收入"万元", 持股"股", 市值"元". 必须 `unit_norm.py` 统一.
- 时间字段格式: REPORT_DATE='2026-03-31 00:00:00' 含时间, NOTICE_DATE='2026-04-27' 不含 — 这跟 sync_raw 之前的 bug 同源.

### 7.4 与 mootdx 重叠时优先 tdxhub

- K 线 / 通达信行业 / 财务主指标 (gpcw): mootdx 已稳定, 不动.
- eastmoney_skill 只填**项目当前缺**的字段, 不替换稳定数据源.

---

## 8. 决策记录

### 8.1 (2026-04-27 用户三次澄清)

1. tdxhub 是最稳定数据源, 不动
2. 妙想 F10 主要为了替换 akshare, 不是替换 tdxhub
3. 综合考虑 tdxhub 未覆盖的部分
4. 加工好的数据可以直接用, 不必从原始数据建模
5. 样本量足够支撑结论 + 影响股价, 不堆砌
6. 十大流通股东, 不是十大股东
7. 妙想 F10 接入与"东财 skill 整体设计"是两回事, 后者后续单独讨论

### 8.2 (2026-04-27 Claude 提议, 待用户审阅)

**项目空白 (P0 优先)**:
- 估值分位 / 一致预期 / 股东人数 / 同行 PE-EPS 排名 — 4 个全是项目当前没有的维度

**akshare 替代 (P1, tdxhub 不能替代的)**:
- 限售解禁 / 高管增减持 / 分红明细 / 股票回购 / 两融日明细 / 停复牌 — 6 个

**业务结构 (P2)**:
- 机构持仓 ORG_TYPE 分桶 / 主营构成 — 2 个

**不入** (tdxhub 已覆盖 / NLP / 缓变 / 已有横向):
- K 线 / 通达信行业 / 财报全字段 / 公告全文 / 高管简历 / 大宗交易个股纵向 / 龙虎榜 (Phase 1) / 调研 (Phase 1) / QFII (Phase 1) / 同概念地域 / 资本运作 / 核心题材

**接入路径**: 选项 C — 现在内嵌 `eastmoney_skill/reports/`, 不做 GitHub 独立项目. 后者属于"东财 skill"专题, 等本专题跑通再开.

等用户审阅 §6 决策点 6.1/6.2/6.3/6.4.
