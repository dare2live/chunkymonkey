# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-19
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane PARTIAL**（calendar + 单日 K/ST canary；eligible frontier=`20260717` READY；缺连续历史覆盖/下一交易日）。**B-ext FIXED（诚实化；数值未切）**。**B-pit PARTIAL**（canary shadow 已有且 divergence；`cutover_allowed=false` — PIT 池 vs unfiltered 广度分歧为预期，禁切）。**E0 PARTIAL（三域 formal→legacy-mirror 写；`/api/v3/inst` 有 `disclosure_shadow` sidecar；研究数值仍读 legacy；`cutover_allowed=false`）**。Fable5 **REVISE** 已吸收。禁 institution_follow 生产至 E0 闭合。

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
- **多源实况**：TuShare=正式 registry 域唯一 live adapter；东财妙想 aif10/`miaoxiang` 已是十大流通股东等披露域 live 主源。E0：三域生产写默认 `formal_default_legacy_mirror`（formal land→accept 后 mirror legacy）；研究仍读 legacy。细节见 ledger。

## 执行计划（A→H）

控制面原语/margin 冻结/all-due 前阻断/doctor `NOT_EVALUATED` FAIL/calendar 隔离原型：见 ledger。≠业务就绪。

- **A** A1–A5 **FIXED**。`live_readiness` 可评估。禁 mass fetch/切消费者。
  **A3 data-plane PARTIAL**：`trade_cal`/`daily`/`stock_st` =
  `authorized_manual_generation` + `sync_policy=on_demand`（禁 --all-due）。
  Live accepted：SSE calendar generation（13162 行）+ `20260717` nominal OHLCV
  （5522 行，`daily:20260717:20260719T132225Z`）+ ST（211 行，
  `stock_st:20260717:20260719T132222Z`）。Eligible frontier（日历+availability）=
  `20260717`（周末无新缺口；未做 mass backfill）。Default
  `evaluate_observation_population_readiness` 现解析该 frontier（非 calendar-today）
  → `READY`，population=4989；doctor `population_readiness=PASS`。margin 仍冻结。
  下一刀：下一交易日 eligible 单日（仍禁 mass backfill / margin 解冻 /
  pulse cutover）。
- **B-ext FIXED（诚实化）** scope + shadow + sentiment sidecar + 前端 UNTRUSTED 标注；
  mart 数值未改、`cutover_allowed=false`。残余=B-pit 数值切读。
- **B-pit PARTIAL** 广度/shadow 已有；**未**接 pulse mart / 未 cutover。Canary 日
  `20260717` shadow：project adv/dec=386/4571 ratio≈0.0844 vs unfiltered
  proxy≈0.0964，`ratios_match=false`，`cutover_allowed=false`（默认禁切）。
- **C** Tier1/2 正式 lineage。**D** `DatasetSnapshot`→`ExperimentVerdict` + PIT 截断。
- **E0 PARTIAL（E 硬前置）** `disclosure_boundaries`：三域 inventory
  （holders_top10/org_holding/stk_holdertrade）+ `raw_evidence` + notice/
  available/ann 轴；`/api/v3/inst/*` 带 `disclosure_conformity` +
  `disclosure_shadow` sidecar，**不改**研究数值。
  **三域 land→accept + dual-write FIXED**：`formal_default_legacy_mirror`。
  **读侧 shadow FIXED**：`disclosure_shadow_compare`；`cutover_allowed` 恒
  false。**live holders canary FIXED**：`data/smartmoney.duckdb`
  `notice_date=20260717` → accepted
  `holders_top10:20260717:3cbe897f7736`（73 行）+ shadow **MATCH**；
  research overall **PARTIAL**（org/stk 仍 `canonical_table_unavailable` /
  `both_tables_unavailable`）。根因=dual-write 已接线但合并后未跑生产 sync，
  非错 DB。**仍阻 E0 闭合 / DatasetSnapshot 冻结**：
  1) org_holding + stk_holdertrade 各至少一 accepted partition + live MATCH；
  2) 研究读路径切 canonical（`/api/v3/inst` + institution_profile 上游）；
  3) 停 legacy mirror + 撤 NONCONFORMING escape hatch；
  4) 再冻 DatasetSnapshot。未闭合则 **E=BLOCKED**。
- **E** `institution_follow_v1`（首包；B0→B4）。**F** main_rally。**G** 公式+BestChoice。**H** Release/名义价纸面。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。披露域 NONCONFORMING≠可进 E。
