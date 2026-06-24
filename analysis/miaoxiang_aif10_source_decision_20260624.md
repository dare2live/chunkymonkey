# 东财妙想 aif10 作为十大流通股东数据源 — 决策 + 后续计划 (2026-06-24)

> owner: 主会话 (控制面). 状态: 决策已定, 实施待做.
> 关联: [[非tushare源_双轨_holders_20260623]] · CLAUDE §4.3 · feedback-delete-source-not-data

## 1. 源决策 (用户拍板 2026-06-24)

**十大流通股东及其变化相关数据 → 主源 = 东方财富妙想 aif10** (本地 `../miaoxiang/aif10_scraper`).
tdxhub holder 源**退役**. 这是 §4.3 "tushare唯一" 的一个**正式例外** (理由: tushare 该类数据落后).

### 为什么破 §4.3 例 (实测证据)
| 源 | 600388 最新报告期 | 6/8 季中权益变动 | 历史深度 |
|---|---|---|---|
| tushare top10_floatholders | 20260331 (Q1) | **无** (财报季驱动, 季中不收) | — |
| 同花顺 iFinD MCP | 20260331 (Q1) | **无** (季末对齐) | — |
| tdxhub 通达信 F10 | 2026-06-08 | 有 | **仅~4期 (1年)** F10限制 |
| **东财妙想 aif10** | **2026-06-08** | **有** | **2003+ (23年)** 一次拉满 |

tushare 该类数据**季中滞后最多 ~4个月** (季报披露才更新, 像紫金入主龙净 6/8 这种平时权益变动不收).
东财妙想 aif10 全市场全历史 + 季中事件 + 结构化, 三维全胜. 详见会话 2026-06-24 实测.

## 2. 抓取工具选型 (实测, 非估计)

**用现成 aif10_scraper (requests 直连 JSON API), 不用 crawl4ai.**

东财妙想 datacenter (`datacenter.eastmoney.com/securities/api/data/v1/get`) 是**干净公开 JSON API**:
无登录 / 无 JS 渲染 / 标准 UA+Referer 头即可 / 返回结构化 `{result:{pages,data,count}}`.

实测头对头 (600388 RPT_F10_EH_FREEHOLDERS):
- aif10_scraper (requests): **0.43s**, 直接 list[dict] 950行可入库.
- crawl4ai (浏览器): **3.33s (慢7.7x)**, JSON 被包进 574KB DOM 还要再解析, 需 Chromium 重依赖.

裁决: JSON API 上 crawl4ai 的渲染/LLM抽取看家本领全用不上 = 纯降级. **requests 是对的工具.**
(注: 当时做 aif10_scraper 时还没发现 crawl4ai; 对比后确认对 JSON API 无需切换.)

**crawl4ai 的真正用武之地 (future, 非 holder)**: 资讯/公告/研报全文在独立子域
(`np-anotice-pc`/`np-cnotice-pc`/`np-creport-pc`), aif10_scraper README 标"暂未实现", 那些是 JS 网页, 将来抓全文用 crawl4ai.

**东财妙想 Skills/MCP (官方 skill api, 用户有 em_ key)**: 是交互式 AI 投研 skill (NL查询, 免费50次/天配额),
**不适合批量 ETL**, 已弃用于 holder 抓取. key 存 .env EM_MIAOXIANG_TOKEN 备用 (将来做交互式分析工具可用).

## 3. 后续计划 (不急, 数据底座做完后做)

### P-later: 东财妙想 aif10 vs tushare 数据覆盖 gap → alpha 评估
**触发**: 数据底座基础设施完成后.
**做什么**:
1. 枚举 aif10_scraper 全 72 个 reportName (16 模块: trading/shareholder/business/themes/events/
   profile/peer/forecast/financial/dividend/share_capital/executives/capital_ops/related), 见
   `../miaoxiang/aif10_scraper/registry.py`.
2. 逐个映射到 tushare 等价接口, 列出 **tushare 没有 / 东财妙想独有** 的数据项.
3. 对每个 gap 项评估 **alpha 潜力**: 谁消费 (consumer 锚定) + 含成本可交易 edge 假设 (非裸IC) + 获取成本.
4. 高 alpha 潜力的 gap 项 → 走转正门接入 (像 holder 这次一样, 破 §4.3 例需评估).
**产出**: 排序的 gap-alpha 菜单 (类似 `analysis/tushare_alpha_potential_menu_*`, 但东财妙想视角).
**预判已知 gap (待核证)**: 资讯/公告/研报全文 (子域, tushare 无) · 估值分位 RPT_STOCKVALUATIONTANTILE ·
机构调研/评级/预测 · 更细的股东/机构持股结构.

## 4. 实施待做 (holder 迁移, 本会话后续或下会话)
1. aif10 holder 抓取接入 chunkymonkey (薄 fetch adapter 或 sync_registry 域, 引擎=aif10_scraper).
2. backfill 2003+ 全市场 holder 历史 → holder 表.
3. fan-in 审计 tdxhub holder 表 (`fact_top10_holder_period`) 全消费方 → repoint 到 aif10 源 (铁律11).
4. tushare top10_floatholders 域处置 (aif10 主源后, tushare 该域降备/删 — 待定; 现有未提交 by_ann_date 改动相应处理).
5. tdxhub holder client + 表退役物删 (consumer repoint 后).
