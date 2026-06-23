# 数据底座架构 v2 — 两模块解耦 + 统一采集规划器 (2026-06-23 用户愿景定稿)

> 用户原话: "数据更新模块只负责核对哪些数据应该抓取然后抓取、清洗并正确存储;数据加工模块负责把
> 获取的数据(含存量)按各处需求加工用于展示,前端从这个模块的 api 获取。" + "获取所有数据前先用
> 交易日历判断该获取到哪天,与存量对比看抓哪些天;非日更(十大股东/财报)扫上一公告日→获取日的增量。"
> + "你应该帮我设计" → 本文 = 架构师设计 (legislator pass), 不是选择题。owner=本文。

## Legislator 层 (为何存在 / 死亡条款 / 判断法典)

- **为何存在**: 给策略与展示提供"该有的(完整)、干净的(归一+PIT)、按需加工的"数据。
- **死亡条款**: (感知死) 采集不核交易日历/存量 → 漏抓或重抓无人知; (判断死) 加工逻辑 hardcode 不随消费需求变;
  (谄媚死) 报"已更新"但实际漏期/截断 (现 by_ts_code --resume 跳整股 = 存量股新季永不补 = 典型谄媚死)。
- **判断法典 (人话 / 机器话)**:
  - J1 采集 = 日历定目标 + 存量 diff 定增量 / `AcquisitionPlanner.plan(domain)` 返增量 batch。
  - J2 每节点抓完核 日历+排除列表+数量 / `assert_node_clean(domain, fetched)` 不过 raise (像非交易日不下单)。
  - J3 两模块解耦: 更新不喂前端, 加工不抓网络 / 独立 entry, 加工只经 SERVE 读存量。
  - J4 加工逻辑随需求 = config 驱动, 不 hardcode。

## 模块边界 (各司其职)

| 模块 | 职责 | **不做** | 入口 |
|---|---|---|---|
| **① 数据更新 DataUpdate** | 对**全部域**: 日历定目标 → diff存量定增量 → 抓 → 清洗(归一/复权/PIT) → 正确存储(L0/L1) + 每节点RULE ZERO | 不加工/不算派生因子/不喂前端 | `update [--domain X｜--all]` |
| **② 数据加工 DataProcess** | 读存量(经SERVE) → 按消费需求加工(行业dim/risk_factors/macd/feature panel/档案切片) → 物化 + 出API | **不抓网络/不写raw** | `process [--stage X｜--all]` |
| 前端 | 从 DataProcess 的 API 取 (dossier/read_model) | 不抓/不算语义值(仅展示计算) | API |

> 映射现状: 我的 pipeline `acquire+clean+store(存储)` = 模块①; `process` + dossier/read_model = 模块②。
> `store` 的 watermark/retention/report = 模块①的运维治理。**解耦 = ① 和 ② 独立 entry, daily 先①后②但可分开调度。**

## 核心 1: 统一采集规划器 (对所有数据, 不止 K线) — calendar→target→diff→increment

**当前实测 (grounding)**:
| batch_mode | 域数 | 增量逻辑现状 |
|---|---|---|
| by_trade_date | 26 | [OK] watermark → 目标交易日, 抓新日 (drain gap 扫补漏) |
| by_period | 2 | [OK] watermark → 新报告期 |
| by_date_range | 1 | [OK] watermark → range |
| **by_ts_code (事件数据)** | **4 (十大股东/财报by股)** | **[NO] 缺增量** — 全量重拉 或 `--resume` 跳整股(=存量股新公告永不补) |

**设计**: `AcquisitionPlanner.plan(domain) -> list[batch]` 统一:
- by_trade_date/by_period/by_date_range: 沿用 watermark 增量 (已对)。
- **by_ts_code 新建 `plan_ts_code_increments`**: 每股取该股 target 表 `MAX(ann_date)` → 扫 (max_ann_date, 日历目标] 的新公告/新期 → 只抓增量 (= 用户的"十大股东从上一公告日到获取日扫增量")。无存量 = 全史首拉。
  - 实现: by_ts_code 接口 (top10_floatholders 等) 按 ts_code 全量返回该股所有期 → 客户端按 ann_date>max 过滤增量写入 (MERGE on grain 幂等); 或接口支持 period 参数则按缺失 period 拉。

## 核心 2: 每节点 RULE ZERO 硬门 (全部数据, 你的核心要求)

每域抓后 `assert_node_clean(domain, fetched_df)` (像非交易日不能下单的硬真相源):
- **日历核**: fetched 最新日/期 == 交易日历目标? (缺口 → degraded/补)
- **排除列表核**: 写入股集 ⊆ universe (前缀排北交所/三板 + 排退市; ST 按域 include_st)? (越界 → raise)
- **数量核**: 行/股/期数 vs 预期下限 (截断/空批/间歇空响应检测, mythos§8)?
- 三核任一不过 = degraded 送达 (链不静默吞)。owner = 新 `services/data_update/node_audit.py`。

## 核心 3: 解耦 (执行层)

- 模块① `python -m services.data_update.run [--domain｜--all]`: 只采集+清洗+存储+RULE ZERO。
- 模块② `python -m services.data_process.run [--stage｜--all]`: 只读存量加工 (0 网络/0 raw写)。
- daily_update.sh = 先跑①再跑②, 但两者独立 entry 可分别触发/分别排程 (你要的"分着")。

## 迁移 (phased, 复用现有 pipeline, 不 big-bang)

- **P1 [greenlit, 双轨99.62%已过] holders 删源收尾**: 删 dossier `_top10_tdx` fallback → 物删 fact_top10_holder_period → 退役 tdx holder client/sync。
- **P2 [核心] 统一采集规划器**: by_ts_code 增量 (plan_ts_code_increments) + 每节点 RULE ZERO 门 (node_audit)。这是你愿景的实质缺口。
- **P3 解耦**: pipeline 拆成 data_update (①) + data_process (②) 独立 entry; daily_update.sh 调两者。
- **P4 加工模块 API 收口**: dossier/read_model 统一成 process 模块的展示 API; 前端只接它。
- **P5 删源续**: 其余非tushare源 (akshare external_attention 无等价→丢 / aif10 retire) 同双轨纪律。

## 风险

| 风险 | 缓解 |
|---|---|
| by_ts_code 增量误判漏期 | MERGE on grain 幂等 + 链尾 gap 扫 (应有期 vs 实有期) 兜底 |
| RULE ZERO 门太严卡正常空批 | 区分"真空批(节假日/未披露)"vs"截断/throttle"(min_rows + 日历对齐) |
| 解耦后 daily 漏跑某模块 | daily_update.sh 显式串 ①→②; 各自 watermark+SLA 监控 |
| big-bang 风险 | 复用现有 pipeline 四阶段, 逐 P 迁移, 每步可跑可回退 |
