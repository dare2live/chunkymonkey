# 东方财富 妙想 F10 数据接口完整 Spec

**调研日期**: 2026-04-27
**调研对象**: https://aif10.eastmoney.com/pc_extendf10/choicef10.html?code=600519
**调研方式**: Chrome DevTools Network 抓包, 14 个一级模块全覆盖
**作者**: 用户 + Claude (整理与对接)

---

## 1. 整体架构

外层 SPA 嵌套跨域 iframe:
- 外壳: `aif10.eastmoney.com` (顶部 4 个 F10 菜单组: A股/三板/港股/美股)
- 内层渲染: `emweb.eastmoney.com/PC_HSF10/`
- **数据后端统一收口**: `datacenter.eastmoney.com/securities/api/data/v1/get`
  (少数 v0 接口走 `data/get`)

工程意义: **不需要解析 HTML**, 只需按 `reportName` 把每张"逻辑表"固定下来, 就能稳定取结构化 JSON.

## 2. 统一接口签名

```
GET https://datacenter.eastmoney.com/securities/api/data/v1/get
    ?reportName=<逻辑表名>
    &columns=<字段列表 或 ALL>
    &filter=(SECUCODE="600519.SH")(...其他过滤)
    &pageNumber=1&pageSize=N
    &sortTypes=...&sortColumns=...
    &source=HSF10&client=PC
```

**关键约定**:
- `SECUCODE` = 6 位代码 + 交易所后缀 (`.SH`/`.SZ`/`.BJ`/`.HK`)
- `source=HSF10&client=PC` 是 spider 兼容必须
- 加 `Referer: https://emweb.eastmoney.com/` + 正常 UA 防反爬
- 单 IP 限速 ≤ 2 QPS, 夜间批量

## 3. 16 个模块 → reportName 完整映射

> 接口前缀均为 `datacenter.eastmoney.com/securities/api/data/v1/get?reportName=` (v0 标 `data/get`).

### 3.1 操盘必读 (Trading Essentials)

| 子栏目 | reportName |
|---|---|
| 最新指标 | `RPT_PCF10_FINANCEMAINFINADATA` / `RPT_DMSK_NEWINDICATOR` |
| 大事提醒 | `RPT_F10_REMIND_RELATIONSHIP` |
| 估值分析 | `RPT_STOCKVALUATIONTANTILE` |
| 主要指标趋势 | `RPTA_DATA_IF_LINECHART` / `RPTA_DATA_IF_INDICATOR` |
| 股东户数趋势 | `RPT_F10_EH_HOLDERNUM` / `RPT_CUSTOM_DMSK_TREND` |
| 龙虎榜单 | `RPT_BILLBOARD_DAILYDETAILS` / `RPT_OPERATEDEPT_TRADE` |
| 大宗交易 | `RPT_DATA_BLOCKTRADE` |
| 融资融券 | `RPT_MARGIN_STATISTICS_STOCKS` / `RPT_STOCK_MARGINTRENDEXPLAIN` |
| 实时行情 | `push2.eastmoney.com/api/qt/stock/get` (不在 datacenter) |

### 3.2 股东研究 (Shareholder Research)

| 子栏目 | reportName |
|---|---|
| 股东人数 | `RPT_F10_EH_HOLDERNUM` |
| 实控人 | `RPT_F10_EH_RELATION` |
| 报告期列表 | `RPT_F10_EH_HOLDERSDATE` / `RPT_F10_EH_FREEHOLDERSDATE` |
| 十大股东 | `RPT_F10_EH_HOLDERS` |
| 十大流通股东 | `RPT_F10_EH_FREEHOLDERS` |
| 十大股东季度差分 | `RPT_F10_SHAREHOLDER_CHANGE` |
| 流通股东合计 | `RPT_F10_FREE_TOTALHOLDNUM` |
| 机构持仓概览 | `RPT_F10_MAIN_ORGHOLDDETAILS` (按 ORG_TYPE 分桶: 01基金/02 QFII/03社保/04券商/05保险/06信托) |
| 基金持仓明细 | `RPT_MAIN_ORGHOLDDETAIL` |
| 沪深港通持股 | `RPT_MUTUAL_STOCK_HOLDRANKN_NEW` / `RPT_NORTH_ORG_HOLDDETAIL_NEW` |
| 限售解禁 | `RPTA_APP_LIFTFUTURE` / `RPTA_APP_ACCUMDETAILS` |

### 3.3 经营分析 (Business Analysis)

| 子栏目 | reportName |
|---|---|
| 主营范围 | `RPT_HSF9_BASIC_ORGINFO` (字段 BUSINESS_SCOPE) |
| 主营构成 | `RPT_F10_FN_MAINOP` (按行业/产品/地区) |
| 经营评述 | `RPT_F10_OP_BUSINESSANALYSIS` |

### 3.4 核心题材 (Core Themes)

| 子栏目 | reportName |
|---|---|
| 概念题材 | `RPT_F10_CORETHEME_BOARDTYPE` (IS_PRECISE=1 精选) |
| 题材亮点 | `RPT_F10_CORETHEME_CONTENT` (IS_POINT=1) |
| 题材详情 | `RPT_F10_CORETHEME_CONTENT` (KEY_CLASSIF_CODE 全集, v0 data/get) |
| 人气龙头 | `RTP_F10_POPULAR_LEADING` |

### 3.5 资讯公告 (News & Announcements)

| 子栏目 | 接口 |
|---|---|
| 相关资讯 | `emdcnewsapp.eastmoney.com/infoService` (POST) |
| 相关公告列表 | `np-anotice-pc.eastmoney.com/api/security/ann?market_stock_list=1.600519` |
| 公告全文 | `np-cnotice-pc.eastmoney.com/api/content/ann?art_code=...` |
| 资讯/研报全文 | `np-creport-pc.eastmoney.com/api/content/rep?art_code=...` |

### 3.6 公司大事 (Major Events)

| 子栏目 | reportName |
|---|---|
| 大事字典 | `RPT_F10_REMIND_RELATIONSHIP` |
| 高管持股变动 | `RPT_EXECUTIVE_HOLD_DETAILS` |
| 同类事件 | `RTP_F10_ADVANCE_DETAIL_NEW` |

### 3.7 公司概况 (Company Profile)

| 子栏目 | reportName |
|---|---|
| 基本资料 | `RPT_F10_BASIC_ORGINFO` |
| 发行信息 | `RPT_PCF10_ORG_ISSUEINFO` |
| 发展历程 | `RPT_ORG_COURSECHANGE` |
| 资本运作 (重大重组) | `RPT_ORG_RECAPITALIZE` |
| 参股控股 | `RPT_F10_PUBLIC_OP_HOLDINGORG` |

### 3.8 同行比较 (Peer Comparison)

| 子栏目 | reportName |
|---|---|
| 行业归属 | `RPT_F10_RELATE_GN` (BOARD_TYPE_NEW=2) |
| 成长性比较 | `RPT_PCF10_INDUSTRY_GROWTH` |
| 估值比较 | `RPT_PCF10_INDUSTRY_CVALUE` |
| 杜邦分析比较 | `RPT_PCF10_INDUSTRY_DBFX` |
| 市场表现 | `RPT_PCF10_MARKETPER` |
| 公司规模 | `RPT_PCF10_INDUSTRY_MARKET` |

### 3.9 盈利预测 (Earnings Forecast)

| 子栏目 | reportName |
|---|---|
| 评级统计 | `RPT_HSF10_RES_ORGRATING` |
| 机构预测 (按机构) | `RPT_HSF10_RES_ORGPREDICT` |
| 预测均值 | `RPT_HSF10_RESPREDICT_STATISTICS` |
| 预测统计扩展 | `RPT_HSF10_RESPREDICT_COUNTSTATISTICS` |
| 预测明细 | `RPT_HSF10_RES_PREDICTDETAIL` |

### 3.10 研究报告 (Research Reports)

| 子栏目 | 接口 |
|---|---|
| 研报全文 | `np-creport-pc.eastmoney.com/api/content/rep?art_code=...` |

### 3.11 财务分析 (Financial Analysis)

| 子栏目 | reportName |
|---|---|
| **公司类型** ⭐ | `RPT_F10_PUBLIC_COMPANYTPYE` (决定财报模板: 一般/银行/保险/券商) |
| 主要指标 (按报告期) | `RPT_F10_FINANCE_MAINFINADATA` (sty=APP_F10_MAINFINADATA, v0 data/get) |
| 主要指标 (按单季) | `RPT_F10_QTR_MAINFINADATA` |
| 主要指标 (仅年报) | `RPT_F10_FINANCE_MAINFINADATA` + filter |
| 杜邦分析 | `RPT_F10_FINANCE_DUPONT` |
| 资产负债表 | `RPT_F10_FINANCE_GBALANCE` (sty=F10_FINANCE_GBALANCE) |
| 利润表 (累计) | `RPT_F10_FINANCE_GINCOME` (sty=APP_F10_GINCOME) |
| 利润表 (单季) | `RPT_F10_FINANCE_GINCOMEQC` |
| 现金流量表 (累计) | `RPT_F10_FINANCE_GCASHFLOW` |
| 现金流量表 (单季) | `RPT_F10_FINANCE_GCASHFLOWQC` |
| 百分比/同比报表 | `RPT_F10_FINANCE_GRATIO` |
| 原始财报披露 | `RPT_PCF10_ORIG_REPORT` |

⚠️ **关键工程注意**: 入库前必须先查 `RPT_F10_PUBLIC_COMPANYTPYE` 决定字段集, 否则跨行业聚合会大量字段为空.

### 3.12 分红融资 (Dividend & Financing)

| 子栏目 | reportName |
|---|---|
| 分红融资概览 | `RPT_F10_DIVIDENDNEW_PROFILE` |
| 分红提示 | `RPT_F10_DIVIDENDNEW_LITY` |
| 分红排名 | `RPT_PCF10_DIVIDENDNEW_RANK` |
| 分红明细 | `RPT_F10_DIVIDEND_MAIN` |
| 分红汇总 (按年) | `RPT_F10_DIVIDEND_COMPRE` / `RPT_F10_DIVIDEND_3YEAR` |
| 融资明细 | `RPT_F10_DIVIDEND_SEO` |
| 分红影响 (除权曲线) | `RPT_F10_DIVIDEND_CURVE` / `RPT_F10_DIVIDEND_EFFECT` |

### 3.13 股本结构 (Share Capital Structure)

| 子栏目 | reportName |
|---|---|
| 股本结构 (最新) | `RPT_F10_EH_EQUITY` (pageSize=1) |
| 历年股本变动 | `RPT_F10_EH_EQUITY` (全量) |
| 限售解禁 | `RPTA_APP_LIFTFUTURE` |

### 3.14 公司高管 (Executives)

| 子栏目 | reportName |
|---|---|
| 高管列表 | `RPT_F10_ORGINFO_MANAINTRO` |
| 高管持股变动 | `RPT_F10_TRADE_EXCHANGEHOLD` |

### 3.15 资本运作 (Capital Operations)

| 子栏目 | reportName |
|---|---|
| 募集资金来源 | `RPT_F10_CAPITAL_RAISE` |
| 项目进度 | `RPT_F10_CAPITAL_ITEM` |

### 3.16 关联个股 (Related Stocks)

| 子栏目 | reportName |
|---|---|
| 行业归属 | `RPT_F10_RELATE_GN` (BOARD_TYPE_NEW=2) |
| 同行业排名 | `RPT_F10_RELATE_RANK` |
| 区间涨跌幅榜 | `RPT_F10_RELATE_RANK` (排序键 Change3/6/12) |
| 同概念排名 | `RPT_F10_RELATE_RANK` (BOARD_TYPE_NEW=3) |
| 同地域排名 | `RPT_F10_RELATE_RANK` (BOARD_TYPE_NEW=4) |

## 4. 数据更新频率分桶

| 频率桶 | 模块 | 调度建议 |
|---|---|---|
| 实时 (分钟级) | 行情、最新指标市值/涨跌、概念板块涨跌、同行排名 | 交易日 9:25–15:05 每 1–5 分钟 |
| 日级 | 龙虎榜、大宗交易、融资融券、沪深港通、研报/资讯/公告增量、大事提醒、估值分位、人气龙头 | 每日盘后 17:00 一次 |
| 周级 | 机构评级统计、研报评级聚合、十大股东季度差分 | 每周一次 |
| 季度 (强事件驱动) | 财务三大表、主要指标、杜邦、主营构成、十大股东、十大流通、机构持仓、股东人数、单季 EPS/营收 | 业绩披露窗口前后每日轮询; 常规期每周校对 |
| 半年/年 | 分红方案、融资明细、限售解禁计划、高管列表、管理层简介 | 每月一次扫描 |
| 缓变 | 公司基本资料、发展历程、参股控股、经营范围、所属行业 | 每月或每季度刷新 |
| 一次性 | IPO 信息、首发募资项目进度 | 入库一次后增量监控 |

## 5. 字段标准化建议

### 5.1 主键约定

- 所有接口都用 `SECUCODE="600519.SH"` 当主键过滤
- 数据库标的主表保留两列: `secucode` (带后缀, 如 `600519.SH`) + `security_code` (不带, 如 `600519`)
- 所有事实表用 `secucode` 做外键

### 5.2 报告期型数据

模式: `(SECUCODE, REPORT_DATE/END_DATE)` 联合唯一键
- 服务端保留约 8 期历史
- 必须每期采集后**累积入库**, 不是覆盖式快照
- 涉及表: 财务、股东、机构持仓、主营构成

### 5.3 事件型数据

模式: `(SECUCODE, NOTICE_DATE/TRADE_DATE, [事件子类型])` 唯一键
- 涉及表: 公告、龙虎榜、大宗、融券、限售解禁、分红除权

### 5.4 财报模板差异 (关键)

`RPT_F10_PUBLIC_COMPANYTPYE` 返回公司归属财报模板:
- 一般工商企业 / 银行 / 保险 / 券商
- 不同模板下字段名差异很大
- **入库前必须先查模板, 再选字段集**

### 5.5 ORG_TYPE 枚举 (机构持仓)

`RPT_F10_MAIN_ORGHOLDDETAILS` 的 `ORG_TYPE`:
- `00` = 汇总
- `01` = 基金
- `02` = QFII
- `03` = 社保
- `04` = 券商
- `05` = 保险
- `06` = 信托

### 5.6 BOARD_TYPE_NEW 枚举 (板块切换)

- `1` = 指数
- `2` = 行业 (茅台对应板块 1277 = 白酒Ⅲ)
- `3` = 概念
- `4` = 地域

## 6. 调用示例

最新指标:
```bash
curl 'https://datacenter.eastmoney.com/securities/api/data/v1/get
?reportName=RPT_PCF10_FINANCEMAINFINADATA
&columns=ALL
&filter=(SECUCODE="600519.SH")
&pageNumber=1&pageSize=1
&sortTypes=-1&sortColumns=REPORT_DATE
&source=HSF10&client=PC' \
  -H 'Referer: https://emweb.eastmoney.com/'
```

财务主指标历史 (v0 接口):
```bash
curl 'https://datacenter.eastmoney.com/securities/api/data/get
?type=RPT_F10_FINANCE_MAINFINADATA
&sty=APP_F10_MAINFINADATA
&filter=(SECUCODE="600519.SH")
&p=1&ps=200&sr=-1&st=REPORT_DATE
&source=HSF10&client=PC' \
  -H 'Referer: https://emweb.eastmoney.com/'
```

## 7. 风险与限制

1. **不公开 SLA**: 这是东财对内 F10 的内部接口, 反爬规则可能变
2. **限流**: 单 IP ≤ 2 QPS, 夜间批量取数
3. **退避重试**: 响应 `success/code/data` 包裹结构, 非 200 / 空数组退避重试
4. **schema 漂移**: v0 (`data/get`) / v1 (`data/v1/get`) 共存; reportName 偶有迭代; 每张逻辑表配 schema 校验, 字段缺失/新增时告警

## 8. 与 datacenter-web 的差异 (Phase 1 vs Phase 2)

| 维度 | `datacenter-web.eastmoney.com/api/data/v1/get` (Phase 1) | `datacenter.eastmoney.com/securities/api/data/v1/get` (Phase 2 妙想) |
|---|---|---|
| 子域 | `datacenter-web` | `datacenter` |
| Path 前缀 | `/api/data/v1/get` | `/securities/api/data/v1/get` |
| source 标记 | `WEB` | `HSF10` |
| client 标记 | `WEB` | `PC` |
| 主键过滤 | `SECURITY_CODE` (6 位) | `SECUCODE` (带后缀 `.SH`) |
| Referer | `data.eastmoney.com` | `emweb.eastmoney.com` |
| 适用场景 | 通用列表 (调研/QFII/龙虎榜横向数据) | 单股 F10 (财务/股东/估值/事件个股纵向数据) |

两条线**互补**, 不冲突. Phase 1 已落地 datacenter-web (调研/QFII/龙虎榜), Phase 2 加 datacenter (HSF10) 单股 F10.
