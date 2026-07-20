# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane PARTIAL**（calendar + **120** 交易日名义 K accepted `20260116`–`20260717`；ST 同窗 + 额外 `20260720`；daily eligible frontier 仍=`20260717` READY/`pending_publish` 挡 `20260720`；仍禁 mass backfill）。**B-ext FIXED（诚实化；数值未切）**。**B-pit PARTIAL**（canary shadow 已有且 divergence；`cutover_allowed=false`）。**E0 FIXED（gate+mirror off）**。**E = measured reject / no-gain（120d checkpointed）**：窗 `20260116`–`20260717` purged WF（3 folds）B0−38%/B1−51%/B2−2.2% 全 `reject`；B4 `inconclusive`（event_days=11 但 fraction≈9%<25%）；均 `claimable=false`；artifacts=`data/lineage/phase_e_experiment_verdicts/`；**无 StrategyRelease**。**Phase C PARTIAL（writer+PIT+accepted publish canary；未 cutover / 未全市场）**：
  `tier12_publish_contract` + `tier12_publish_writer` +
  `tier12_publish_accept` + typed `config/tier12_publish.yaml` +
  `tier12_nominal_canary`。Writer：PIT 截断 → `WRITTEN_UNPUBLISHED` /
  `published=false`。Accept：要求 `WRITTEN_UNPUBLISHED` +
  `PUBLISHABLE_SCAFFOLD`；原子写 accepted-partition 等价 attestation；
  成功后才 `published=true`；`cutover_allowed=false` 硬门（禁 smoke 静默升级）。
  Live canary `20260717`（20 codes）：
  `{batch,smoke,accepted}_20260717.json`（stock_row_count=20；
  content_hash 已落）。**未** consumer cutover / **未** full-universe /
  **未** StrategyRelease。下一刀=consumer cutover 显式门（仍默认 false）
  **或** full-universe accept **或** daily 下一 eligible 单日；
  禁 Optuna / gate-loosening / B-pit cutover / margin thaw / mass backfill。

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
  Live accepted：SSE calendar + 名义 K `20260116`–`20260717`（**120** 交易日；
  两段 ≤40d 短窗同步，禁 years backfill）；ST 同窗 + `20260720`。
  Daily eligible frontier=`20260717`（`20260720` = `operation_window_blocked` /
  `pending_publish`）。form/qfq 仍 max=`20260716`（builder 无法超 raw/adj）。
  `evaluate_observation_population_readiness` → `READY`；doctor
  `population_readiness=PASS`。margin 仍冻结。下一刀：daily 下一 eligible
  单日（仍禁 mass backfill / margin 解冻 / pulse cutover）。
- **B-ext FIXED（诚实化）** scope + shadow + sentiment sidecar + 前端 UNTRUSTED；
  mart 数值未改、`cutover_allowed=false`。残余=B-pit 数值切读。
- **B-pit PARTIAL** 广度/shadow 已有；**未**接 pulse mart / 未 cutover。Canary 日
  `20260717`：PIT vs unfiltered 分歧 → `cutover_allowed=false`。
- **C PARTIAL（accepted publish canary）** contract/writer/accept/yaml +
  canary + `persist_tier12_{writer_smoke,accepted_publish}.py`：
  live `20260717` → `accepted_20260717.json`（`ACCEPTED`/`published=true`/
  `cutover_allowed=false`；canary scale）。**未** consumer cutover /
  **未** full-universe publish-complete。
  **D** `DatasetSnapshot`→`ExperimentVerdict` + PIT 截断（E 已部分消费）。
- **E0 FIXED（gate+mirror off；E 硬前置）** 三域 MATCH → `cutover_allowed=true`；
  formal writes=`formal_only`；research read prefer canonical；
  `data/lineage/disclosure_dataset_snapshot.json` →
  `scope=bounded_accepted_partitions` /
  `phase_e_ablation=bounded_scope_measured_b0_short_window`；
  holders date_set=11；org/stk canary+small sets。Serving shadow MATCH。
  残余：org 全市场 mass（禁）；enrichment 历史仍 field-level PARTIAL。
- **E checkpointed = measured reject / no-gain（120d；非松门理由）**
  Protocol：120 日 `20260116`–`20260717` purged WF（3 folds, claimable_protocol）
  + T+1 paper；accept edge gates + `holdout_lift_vs_b0`。Live ladder
  （全 `claimable=false`）：B0 ret≈−38.2% → `reject`；B1≈−51.1% → `reject`；
  B2≈−2.2% → `reject`/`accept_edge_gates_unmet`（短窗 B2 微正已不复现）；
  B4 event_days=11 但 fraction≈9%<25% → `inconclusive`（不松 coverage 门、
  不假 accept）。Artifacts：
  `data/lineage/phase_e_experiment_verdicts/{manifest,b0,b1,b2,b4}.json`
  （`persist_phase_e_experiment_verdicts.py` 幂等 regenerate；window 从实测
  trading_days 派生）。form/qfq `20260717` **still blocked**（max=`20260716`）。
  **Next**：C consumer cutover 显式门（默认 false）**或** full-universe
  Tier1/2 accept **或** daily 下一 eligible 单日（frontier 仍=`20260717`；
  `20260720` 午前仍 `pending_publish`/operation_window_blocked）**或** stop。
  **禁** Optuna / gate-loosening / B-pit cutover / margin thaw / mass backfill /
  StrategyRelease。**F** main_rally。**G** 公式+BestChoice。**H** Release。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。E measured reject ≠ claimable release；B0/B1/B2/B4 均 `claimable=false`；均 ≠ StrategyRelease / 生产候选；松门禁；canary ≠ 全量消融；feature_store field-level PARTIAL ≠ ACCEPTED。
