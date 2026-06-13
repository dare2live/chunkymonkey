# 数据 Access Surface 菜单 (2026-06-13)

> 用户纠偏产物: 探索不限"已入库", 先全面看"能拿到什么", 据此定该入库/更新什么。
> 证据: workflow wf_be48d7bc (tushare 全目录映射 + iFinD MCP 小规模实测)。
> 真相源: backend/config/tushare_api_catalog.json (239 接口, doc-scrape, probed_* 全空=无实测回写)。

## 核心发现: 真正的缺口不是 stale 表, 是基本面 factor 族几乎零底座

tushare 239 接口 / 已接入库 25 / 可调未接 ~214 (扣 off-strategy 后 ~136 个 A 股相关未挖)。
账户 10000 积分 = catalog points_num<=10000 理论可调; gateway `fetch_raw` 通用直调 = 接新域纯
sync_registry 配置零代码。

## 用户点名两盲区 — 定论

| 盲区 | 真实状态 |
|---|---|
| **盈利预测** | `report_rc`(卖方分析师预测)已接但仅 ~5 月数据(回填 gap, registry 称 2010 起); 公司自家 **`forecast`(业绩预告)/`express`(业绩快报) 全未接** = 真缺口。**财务三大表 income/balancesheet/cashflow + fina_indicator 也全未接**, 现仅 `fina_mainbz`(主营构成) = 基本面 factor 族无底座 |
| **筹码胜率** | `cyq_perf.winner_rate` 已落库 4.57M 行全非空(2023起)但被 **C0 审计冻结**(疑未复权坐标口径, J1-J3 FAIL), 无 live feature 消费; 完整价位分布 `cyq_chips`(2018起)**未接** — 可用本地 qfq 自算重建并独立验证被冻的 winner_rate |

## tushare high-value-unmined (可调没接, 像有用)

| 接口 | 域 | 积分 | alpha 假设 |
|---|---|---|---|
| forecast / express | 盈利预测 | 2000 | 业绩预告/快报 = PEAD 预期差事件因子, ann_date 早于正式财报, PIT 干净 |
| income / fina_indicator | 财务报表 | 2000 | 质量/成长/价值因子底座 (ROE/毛利/周转/负债率数十项), 现完全没有 |
| cyq_chips | 筹码 | 5000 | 完整价位分布 → 自算获利盘/套牢盘/集中度/主力成本; 可救被冻 winner_rate |
| kpl_list / limit_step | 打板/连板天梯 | 5000/8000 | 连板天梯+情绪周期 = **主升浪猎手(北极星)直接相关**, 强势股梯队刻画 |
| stk_holdertrade / repurchase | 内部人事件 | 2000/600 | 增减持/回购 = 经典正向/负向事件因子, "公司+内部人"行为信号 |
| stk_holdernumber | 股东户数 | 600 | 筹码集中/分散代理 (户数降=吸筹假设), 与 cyq 口径独立互补 |
| index_classify / index_weight / sw_daily | 申万行业树 | 2000/5000 | 行业中性化底座; 直指 §4.5 "行业 fallback 99.978% leakage" 反例根因 |
| disclosure_date | PIT 工具 | 500 | 财报预约披露日 = 财务因子防 look-ahead 的配套基础设施 |

needs-validation: stk_factor_pro(技术因子, 疑与 alpha158 重叠 + 分页截断)、stk_surv(机构调研, 实测 0 行待核)、research_report、hsgt_top10(北向个股, 披露规则变更)、pledge(质押风险)、stk_auction(集合竞价)。
low-value: moneyflow_dc/ths(与已接 moneyflow 重叠, 资金流 base-edge 已知弱)。

## iFinD = 研究探查工具, 不是批量入库 API

8/8 能力实测成功, 但形态 = NL 查询返回 markdown 表格 (实体解析有歧义: 三花智控默认解析成 HK 码;
茅台首查空响应需补显式码)。**定位 = 交互式调研/定向核证, 批量入库走 tushare 等价结构化接口**。

| iFinD 能力 | DB 有? | 用法 |
|---|---|---|
| get_risk_indicators (alpha/beta/夏普/VaR) | 无 | DB 零金融工程风险因子; 但返回全区间单值=必须自己按时点 rolling 重算防 PIT。可探查+自算入库 |
| search_notice / search_news (语义公告/新闻) | 无 | DB 无文本/NLP 域; 业绩预告预喜预亏催化探查。批量结构化仍走 tushare forecast |
| get_stock_events (解禁/增减持/诉讼) | 部分 | 与 tushare stk_holdertrade/share_float 重叠, iFinD 适合定向核证 |

## 据此的小规模验证优先级 (探索→定价值→再定入库/更新)

1. **基本面四件套** (forecast+express+income+fina_indicator+disclosure_date): 最大未挖矿, 经典 alpha (盈利惊喜/PEAD/质量), PIT 干净。小规模: 拉样本测 earnings-surprise IC。
2. **cyq_chips**: 救活被冻筹码维度, 本地 qfq 自算独立验证。
3. **kpl_list/limit_step**: 喂主升浪猎手北极星 (连板生态它从没有过)。
4. **iFinD risk_indicators**: regime_gate / 组合风险预算的缺口, 自算 rolling 入库。

> 注: 验证 = 测 alpha 价值, 非建管道。验出有增量的才谈入库+更新 (更新任务的定义在此之后)。
