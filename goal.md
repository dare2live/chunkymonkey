# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-19
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane residual=BLOCKED**（无 authorized canary + 无 live calendar/K accepted）。**B-ext PARTIAL**（trust/shadow/FE 已落地，无数值 cutover）。**B-pit** 仍阻塞于 A3。Fable5 **REVISE** 已吸收（见 ledger）。禁 E0/institution_follow 生产至 A/B 足够。

已拍板：多源=契约可换 adapter（**目标态**）；首策略包=`institution_follow`；边做边测。Tier0 未闭合前禁止寻优、生产候选、cutover、自动跑批。

## 产品层级（已裁决）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 日历、身份、名义 K、公司行动、复权 | accepted canonical partition |
| Tier 0B 分类 | 版本化树/概念/成员/crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 阶段/形态/事件，不预测 | stock state + pattern event |
| Tier 2 市场感知 | 活跃度/不平衡代理/广度/价格响应 | market context snapshot |
| Tier 3 研究/策略 | B0→B5 消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略 | strategy release + decision batch |

依赖只向下。Ops 观察但不拥有业务事实。

## 架构硬决定

1. 积木=`module+data+config+contract+evidence`。
2. landing 保留供应商响应；universe 过滤在 canonical/serve 并记 reason。
3. 日历与 eligible universe 同级硬门。
4. 正式集声明 `raw_evidence`/`external_aggregate`/`project_universe_pit` + policy hash。
5. 外部汇总≠项目池；PIT 过滤明细；后来退市不改写当时合格史。
6. 名义 OHLCV=成交真相；qfq=带血缘分析视图。
7. 分类统一契约、不统一值域。
8. “资金”=活动度/代理/广度/响应，非守恒。
9. 一数据集一 writer；YAML 只存政策。
10. strangler + `manual_only`。
11. Provider=adapter；真相在 accepted/canonical；契约可换是目标，非宣称现已单源。
12. 切片红→绿；静态 PASS≠`live_readiness`。

## Live 证据与已推翻前提

- 白名单仅 `60/00/30/68`；多数域仅前缀过滤；margin v2/pulse 含 BSE 错误 scope。
- universe/Moth 曾假绿；live ST 污染 breadth/龙虎榜/SW/DC。
- margin accepted=1823 只证冻结自洽；`20260709/BSE` 出 scope。
- **多源实况**：TuShare=正式 registry 域唯一 live adapter；东财妙想 aif10/`miaoxiang` 已是十大流通股东等披露域 live 主源（`holders_aif10` 直写 fact，无 landing/accepted）= **NONCONFORMING**，待 E0 formal 化。细节见 ledger。

## 执行计划（A→H）

控制面原语/margin 冻结/all-due 前阻断/doctor `NOT_EVALUATED` FAIL/calendar 隔离原型：见 ledger。≠业务就绪。

- **A** A1–A5 **FIXED**。`live_readiness` 可评估（常见 BLOCKED=无 live accepted partition）。禁 mass fetch/切消费者。
  **A3 data-plane residual=BLOCKED（本 session 实测）**：`trade_cal`/`daily`/`stock_st`
  `execution_policy.mode=disabled`（`accepted_generation_pending` /
  `accepted_partition_pending`）；live `tushare_raw` 无 calendar/K accepted
  landing·canonical 表；仅有 legacy `raw_tushare_stock_st` + margin accepted
  （margin 仍 `scope_blocked`）。未授权 → **不跑** provider/manual canary。
  下一授权点：窄 calendar generation canary（再 K/ST），禁 mass backfill / margin 解冻。
- **B-ext（PARTIAL，代码面可收）** scope + shadow + sentiment
  `population_scope`/`shadow_reconcile`/`cutover_allowed=false` + 前端 trust 标注；
  live margin attach 尽力（失败 `margin_raw_not_attached` fail-closed）。mart 数值未改、无 cutover。
  残余仅 B-pit 数值切读（需 A3）。
- **B-pit（阻塞 A3 data-plane）** project_universe_pit 广度/resolver 消费者迁移；shadow 后切读面。未闭合 A3 data-plane residual 不得宣称 B 完成。
- **C** Tier1/2 正式 lineage。**D** `DatasetSnapshot`→`ExperimentVerdict` + PIT 截断。
- **E0（E 硬前置）** 披露域 formal 化：holders/org_holding/stk_holdertrade → adapter/landing/canonical + notice/`available_at` 契约。未完成则 **E=BLOCKED**。
- **E** `institution_follow_v1`（首包；B0→B4）。**F** main_rally。**G** 公式+BestChoice。**H** Release/名义价纸面。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。披露域 NONCONFORMING≠可进 E。
