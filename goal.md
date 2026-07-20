# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane PARTIAL**（calendar + **120** 交易日名义 K accepted `20260116`–`20260717`；ST 同窗 + 额外 `20260720`；**manual sync 已拆时钟门**——`trigger_mode=manual` 开市可拉今日，consumer/`available_at` 仍 `same_day_at 18:00`；live `20260720` manual 已越过 window，provider `zero_rows` fail-closed，尚未 accepted；仍禁 mass backfill）。**B-ext FIXED（诚实化；数值未切）**。**B-pit PARTIAL**（120d shadow：MATCH baseline=membership_restricted_proxy **120/120 MATCH**；unfiltered 仅作 semantic delta；`cutover_allowed=false`）。**E0 FIXED（gate+mirror off）**。**E = measured reject / no-gain（120d checkpointed）**：窗 `20260116`–`20260717` purged WF（3 folds）B0−38%/B1−51%/B2−2.2% 全 `reject`；B4 `inconclusive`（event_days=11 但 fraction≈9%<25%）；均 `claimable=false`；artifacts=`data/lineage/phase_e_experiment_verdicts/`；**无 StrategyRelease**。**Phase C PARTIAL（writer+PIT+full-universe accept；未 cutover / 未 complete）**：
  `tier12_publish_contract` + `tier12_publish_writer` +
  `tier12_publish_accept` + `tier12_project_universe` + typed
  `config/tier12_publish.yaml` + `tier12_nominal_canary`。Writer：PIT 截断 →
  `WRITTEN_UNPUBLISHED` / `published=false`。Accept：`publish_scope=
  canary|project_universe`；后者需 membership/coverage 证 + parity；
  成功后才 `published=true`；accept-side `cutover_allowed=false` 硬门。
  Live full-universe `20260717`：membership=`traded_on_observation_date`
  **4989** → written/accepted **4989** / excluded **0**；
  `publish_scope=project_universe`；content_hash=
  `5c4673893b854a467d0156f39e5b20fad61e106fbbfe413237e7eb4f68c03c8f`；
  artifacts=`{batch,accepted,coverage_full_universe,full_universe_accept}_20260717.json`
  （smoke canary 仍保留作对照）。  **Consumer cutover 显式门 FIXED（默认 false）**：
  `tier12_consumer_cutover` + 生产读边界
  `resolve_tier12_production_read`；default → LEGACY；已接线 B1
  `load_stock_state_by_day` + pulse/UI（`market_pulse_tier12_read`：
  sentiment `tier12_production_read` 旁路 + drill form overlay；仍走
  `fact_stock_form_daily`/mart）。`expected_config_hash` 已填 live
  `20260717` stock hash（opt-in 就绪）；**未** `cutover_allowed=true` /
  **未** StrategyRelease / **未** claim Phase C complete。
  **D PARTIAL（scaffold）**：`research_runtime` DatasetSnapshot→PIT→
  ExperimentVerdict；E 复用；**未** D complete / Release / Optuna。
  下一刀= `20260720` provider 有行后 sync+accept **或** 显式 opt-in
  C consumer cutover（强证据）**或** B-pit mart 切读（MATCH 已证 + 独立
  gate，现仍 false）**或** stop；禁 Optuna/松门/擅翻 cutover/margin thaw/
  mass backfill。

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
- **多源实况**：TuShare=正式 registry 域唯一 live adapter；东财妙想 aif10/`miaoxiang` 已是十大流通股东等披露域 live 主源。E0：三域写=`formal_only`；研究读 prefer canonical（MATCH）；feature_store 画像 typed enrichment PARTIAL。细节见 ledger。

## 执行计划（A→H）

控制面原语/margin 冻结/all-due 前阻断/doctor `NOT_EVALUATED` FAIL/calendar 隔离原型：见 ledger。≠业务就绪。

- **A** A1–A5 **FIXED**。`live_readiness` 可评估。禁 mass fetch/切消费者。
  **A3 data-plane PARTIAL**：`trade_cal`/`daily`/`stock_st` =
  `authorized_manual_generation` + `sync_policy=on_demand`（禁 --all-due）。
  Live accepted：SSE calendar + 名义 K `20260116`–`20260717`（**120** 交易日）；
  ST 同窗 + `20260720`。**Eligibility 拆分 FIXED（~14:37 CST）**：`manual_only`；
  `trigger_mode=manual`（chunkyctl 默认）开市日历日可拉今日，不再被
  `same_day_at 18:00` → `pending_publish` 挡；`automatic` 与 consumer
  frontier 仍时钟门（live auto 仍挡 `20260720`）。Live manual
  `20260720` → 越过 window 后 provider `zero_rows` fail-closed（非
  `pending_publish`）。细节见 ledger。form/qfq max=`20260716`。
  population READY；margin 冻结。禁 mass backfill/解冻/擅自 cutover。
- **B-ext FIXED（诚实化）** scope + shadow + sentiment sidecar + 前端 UNTRUSTED；
  mart 数值未改、`cutover_allowed=false`。残余=B-pit 数值切读。
- **B-pit PARTIAL** 120d shadow contract tightened（K∩ST）：
  MATCH=project ≡ membership_restricted_proxy → **120/120 MATCH**；
  frontier `20260717` project=proxy≈0.08445；unfiltered≈0.09638 为预期
  semantic delta（ST+board 外 533 行）；`cutover_allowed=false`（MATCH
  alone 亦不放行）。Artifacts=`data/lineage/b_pit_breadth_shadow/`。
  **未**接 mart。
- **C PARTIAL** full-universe accept `20260717`（4989=4989）+ consumer gate
  + B1/pulse read wire；`cutover_allowed=false`；opt-in hash 已填未翻。
  **未** claim complete / Release。细节见 ledger。
- **D PARTIAL（scaffold）** `research_runtime`；E 已消费；**未** complete。
- **E0 FIXED** 三域 MATCH/`formal_only`；research prefer canonical。残余：
  org mass 禁；enrichment field-level PARTIAL。
- **E checkpointed = measured reject / no-gain（120d）** B0/B1/B2 `reject`；
  B4 `inconclusive`；均 `claimable=false`；artifacts=
  `data/lineage/phase_e_experiment_verdicts/`。form/qfq max=`20260716`。
  **Next**：`20260720` provider 有行后 sync+accept **或** opt-in C cutover
  （强证据）**或** B-pit mart 切读（需 MATCH+独立 gate，现仍 false）**或** stop。
  **禁** Optuna/松门/擅翻 cutover/margin thaw/mass backfill/Release。
  **F–H** 见 MASTER。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。E measured reject ≠ claimable release；B0/B1/B2/B4 均 `claimable=false`；均 ≠ StrategyRelease / 生产候选；松门禁；canary ≠ 全量消融；feature_store field-level PARTIAL ≠ ACCEPTED。
