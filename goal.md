# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-19
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane PARTIAL**（calendar + ~40 交易日名义 K/ST accepted；eligible frontier=`20260717` READY；窗 `20260522`–`20260717`；仍禁 mass backfill / 下一交易日未到）。**B-ext FIXED（诚实化；数值未切）**。**B-pit PARTIAL**（canary shadow 已有且 divergence；`cutover_allowed=false` — PIT 池 vs unfiltered 广度分歧为预期，禁切）。**E0 FIXED（gate+mirror off）**：三域 MATCH → `cutover_allowed=true`；formal writes=`formal_only`；provider-field 读 prefer canonical；canary `DatasetSnapshot` 已冻；feature_store 画像=typed enrichment PARTIAL（非 blanket legacy）。**E PARTIAL（bounded measured B0 paper）**：disclosure snapshot=`bounded_accepted_partitions` / `phase_e_ablation=bounded_scope_measured_b0_short_window`；40 日名义 K 上已跑 purged WF（3 folds）+ T+1 paper fills → protocol power ready（`measured.claimable=true`）但 accept edge gates 未接线 → verdict `inconclusive` / `measured_protocol_ready_edge_gates_unmet` / `claimable=false`（禁假 accept）；**B1 scaffold 已立**（独立切片；仍禁假 accept）。Fable5 **REVISE** 已吸收。

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
- **多源实况**：TuShare=正式 registry 域唯一 live adapter；东财妙想 aif10/`miaoxiang` 已是十大流通股东等披露域 live 主源。E0：三域写=`formal_only`（默认停 legacy mirror；test/escape only）；研究 provider-field 读 prefer canonical（MATCH）；feature_store 画像 typed enrichment PARTIAL。细节见 ledger。

## 执行计划（A→H）

控制面原语/margin 冻结/all-due 前阻断/doctor `NOT_EVALUATED` FAIL/calendar 隔离原型：见 ledger。≠业务就绪。

- **A** A1–A5 **FIXED**。`live_readiness` 可评估。禁 mass fetch/切消费者。
  **A3 data-plane PARTIAL**：`trade_cal`/`daily`/`stock_st` =
  `authorized_manual_generation` + `sync_policy=on_demand`（禁 --all-due）。
  Live accepted：SSE calendar generation + 名义 K/ST
  `20260522`–`20260717`（40 交易日；含 frontier；缺省日自 legacy raw 经 formal land→accept）。
  例：`daily:20260717:…` 5522 行 / `stock_st:20260717:…` 211 行。
  Eligible frontier（日历+availability）=`20260717`。formal daily/stock_st CLI
  现支持同日或 ≤40 交易日短窗（逐日 accept；禁 `--backfill`/无界/多年）。Default
  `evaluate_observation_population_readiness` → `READY`；doctor
  `population_readiness=PASS`。margin 仍冻结。下一刀：下一交易日 eligible
  单日（仍禁 mass backfill / margin 解冻 / pulse cutover）。
- **B-ext FIXED（诚实化）** scope + shadow + sentiment sidecar + 前端 UNTRUSTED 标注；
  mart 数值未改、`cutover_allowed=false`。残余=B-pit 数值切读。
- **B-pit PARTIAL** 广度/shadow 已有；**未**接 pulse mart / 未 cutover。Canary 日
  `20260717` shadow：project adv/dec=386/4571 ratio≈0.0844 vs unfiltered
  proxy≈0.0964，`ratios_match=false`，`cutover_allowed=false`（默认禁切）。
- **C** Tier1/2 正式 lineage。**D** `DatasetSnapshot`→`ExperimentVerdict` + PIT 截断。
- **E0 FIXED（gate+mirror off；E 硬前置）** 三域 inventory + land→accept +
  shadow sidecar + research read policy + formal_only writes。
  **cutover 规则**：`cutover_allowed=true` **仅当** holders/org/stk 三域在
  serving partitions 上 shadow **MATCH**（live 已 MATCH）。
  **读路径**：`disclosure_research_read` 对 MATCH 域 prefer canonical；缺失/
  分歧 fail-closed → legacy + `NONCONFORMING`/`PARTIAL`；`/api/v3/inst` 暴露
  `disclosure_read_policy` + `feature_store_field_status`。
  **画像**：`disclosure_enrichment_projection` = canonical spine + typed
  legacy join；holders enrichment 列已进 canonical schema v2；历史 canary
  缺口仍 field-level **PARTIAL**（非 blanket legacy-only）。
  **写路径**：`formal_only`（默认**停** legacy mirror）；mirror /
  naked `NONCONFORMING` 仅 `allow_test_escape` / env /
  `legacy_direct_only` / `enable_legacy_mirror`。
  **DatasetSnapshot FIXED（bounded）**：
  `data/lineage/disclosure_dataset_snapshot.json` →
  `scope=bounded_accepted_partitions`，
  `phase_e_ablation=bounded_scope_measured_b0_short_window`；
  holders`20260619/20260713/20260714/20260717`；
  org`20190430`+`20260430`(stock subset 600519,000001)；
  stk`20260518/20260608/20260706/20260713` + hashes。
  Serving cutover shadow 仍 MATCH on canary 三域。
  **残余（不挡 smoke，挡全量消融）**：org 全市场 recent mass accept（禁）；
  enrichment 历史行仍依赖 legacy join。
- **E PARTIAL（bounded measured B0 paper）** E0 FIXED 前置已满足。
  `institution_follow_b0` + `institution_follow_b0_measure`：coverage ready（40 日
  `20260522`–`20260717`）后跑 **purged WF**（embargo=1、one-touch
  holdout=2、3 expanding folds）+ paper fills（T+1 名义 open→T+2 open、佣金/
  印花税/滑点 stub、涨停买/跌停卖/停牌 stub、capacity=`unknown`）。Live 例：
  total_return≈-24.4%、max_dd≈33.5%、win_rate≈0.45、payoff≈0.93、
  turnover≈1.45、n_trades=145；holdout n=5 / ret≈+5.9%。Protocol power
  `measured.claimable=true` / `measured_b0_paper_short_window`；verdict 仍
  `inconclusive` / `measured_protocol_ready_edge_gates_unmet` /
  `claimable=false`（禁假 accept；edge 阈值未接线）。
  `test_phase_e_smoke` + `test_institution_follow_b0` 绿。
  **禁** Optuna/全历史/付费搜索、B-pit mart cutover、mass disclosure
  backfill、margin thaw。**B1 scaffold**（独立切片）：
  `institution_follow_b1` 声明 stock-state FeatureBlock，仍
  `inconclusive`/`claimable=false`（不可借 B0 protocol power 假 accept）。
  **残余**：接线 accept edge gates；B1 Tier1 publish + 条件化 paper 实测。
  **F** main_rally。**G** 公式+BestChoice。**H** Release/名义价纸面。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。E0 FIXED + measured paper ≠ claimable B0/accept；protocol power ready ≠ accept（edge gates unmet）；canary `canary_scope_only` ≠ 全量消融；feature_store field-level PARTIAL ≠ ACCEPTED。
