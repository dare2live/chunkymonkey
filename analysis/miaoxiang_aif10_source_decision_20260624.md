# 东财妙想 aif10 作为十大流通股东数据源 — 决策 + 后续计划 (2026-06-24)

> 状态：evidence-only；保留不可替代的数据源裁决证据，当前规则以 `docs/README.md` 指向的 owner 为准。

> 当时状态：源决策已定；实施状态必须 live 重查。本文不拥有当前 pipeline 或配置。
> 关联证据：`analysis/非tushare源_双轨_holders_20260623.md`。

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

## 4. 实施状态 (2026-06-24)

**[DONE] 功能性迁移 + tdx_f10 数据层退役**:
1. [DONE] aif10 holder 接入 (services/holders_aif10.py 获取/清洗/加工/存储分层 + pipeline acquire Step2i2 + 薄CLI).
2. [DONE] 全市场 backfill (K线范围 20181231+, 非2003): 5189股/172.5万行/32.8万退出/49min; **覆盖率 99.6%** (5189/5208活跃股, 缺19=次新股 aif10 0行自愈).
3. [DONE] availability_source='page_update_date' 全覆盖 (0 NULL, PIT 可用日锚).
4. [DONE] 增量水位驱动 (MAX 披露日, 接 pipeline acquire 自动跑).
5. [DONE] fan-in 安全核: 无消费方硬过滤 source='tdx_f10' → **物删 fact_top10_holder_period 的 tdx_f10 行 (594k)**, 消费方 smoke PASS (现 miaoxiang-only 单源, 消除双源重复计数).

**[REVIEW-FOLLOWUP] 物理代码/表退役 (自主 loop 不莽撞, 须 review)**:
6. [PENDING] 退役 ingest_holders_tdxhub.py (已加 DEPRECATED 头, 勿跑) + tdx_f10_extra_client.py (tdxhub.holders client) + 物删 raw_tdx_f10_holder_research 表
   — 阻塞: raw 表 10 消费方须逐个核 (mythos §14); 控股股东/计划/交易 3 产品表 reset 后不存在(死, 无孤立风险); raw 表暂留作 raw→fact 恢复网.
7. [PENDING] tushare top10_floatholders 域处置 (sync_registry 无 enabled 字段; 移除条目 + 物删 raw_tushare_top10_floatholders[1消费方 seed_dim_data_asset] + 退 by_ann_date 代码).
