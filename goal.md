# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-19
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane PARTIAL**（calendar + 单日 K/ST canary；eligible frontier=`20260717` READY；缺连续历史覆盖/下一交易日）。**B-ext FIXED（诚实化；数值未切）**。**B-pit PARTIAL**（canary shadow 已有且 divergence；`cutover_allowed=false` — PIT 池 vs unfiltered 广度分歧为预期，禁切）。**E0 FIXED（gate）**：三域 live shadow MATCH → `cutover_allowed=true`；provider-field 读 prefer canonical；canary `DatasetSnapshot` 已冻；**E 消融仍禁**（canary scope + feature_store PARTIAL）。Fable5 **REVISE** 已吸收。

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
- **多源实况**：TuShare=正式 registry 域唯一 live adapter；东财妙想 aif10/`miaoxiang` 已是十大流通股东等披露域 live 主源。E0：三域写仍 `formal_default_legacy_mirror`（mirror **deprecated** 再留一刀）；研究 provider-field 读 prefer canonical（MATCH）；feature_store 画像仍 PARTIAL。细节见 ledger。

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
- **E0 FIXED（gate；E 硬前置）** 三域 inventory + land→accept + dual-write +
  shadow sidecar + research read policy。
  **cutover 规则**：`cutover_allowed=true` **仅当** holders/org/stk 三域在
  serving partitions 上 shadow **MATCH**（live 已 MATCH）。
  **读路径**：`disclosure_research_read` 对 MATCH 域 prefer canonical；缺失/
  分歧 fail-closed → legacy + `NONCONFORMING`/`PARTIAL`；`/api/v3/inst` 暴露
  `disclosure_read_policy`。feature_store 画像重建仍读 legacy enrichment →
  **PARTIAL**（canonical 无 holder_name_norm/share_class/shares_approx…）。
  **写路径**：legacy mirror **deprecated but active**（连续性）；naked
  `NONCONFORMING` 已从生产撤出（仅 `allow_test_escape` /
  `legacy_direct_only`）。
  **DatasetSnapshot FIXED（canary）**：
  `data/lineage/disclosure_dataset_snapshot.json` →
  holders`20260717`/org`20190430`/stk`20260706` + config/content hashes；
  `phase_e_ablation=blocked_canary_scope_only`。
  **残余（不挡 gate，挡全量消融）**：停 mirror；feature_store 切 enrichment；
  非 canary 分区 mass accept（禁本刀）。
- **E PARTIAL-unblocked（gate）** snapshot gate 绿 → 允许 **smoke / 契约接线**；
  **禁** `institution_follow` B0→B4 策略消融直至更广 snapshot +
  feature_store 非 PARTIAL。**F** main_rally。**G** 公式+BestChoice。**H** Release/名义价纸面。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。E0 canary snapshot 绿≠可跑 institution_follow 消融；feature_store PARTIAL≠ACCEPTED。
