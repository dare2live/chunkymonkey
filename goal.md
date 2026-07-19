# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-19
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。**Phase A 代码完整**（A1–A5 FIXED）；**A3 data-plane PARTIAL**（calendar + ~40 交易日名义 K/ST accepted；eligible frontier=`20260717` READY；窗 `20260522`–`20260717`；仍禁 mass backfill / 下一交易日未到）。**B-ext FIXED（诚实化；数值未切）**。**B-pit PARTIAL**（canary shadow 已有且 divergence；`cutover_allowed=false` — PIT 池 vs unfiltered 广度分歧为预期，禁切）。**E0 FIXED（gate+mirror off）**：三域 MATCH → `cutover_allowed=true`；formal writes=`formal_only`；provider-field 读 prefer canonical；canary `DatasetSnapshot` 已冻；feature_store 画像=typed enrichment PARTIAL（非 blanket legacy）。**E PARTIAL（bounded measured B0+B1+B2+B4 paper）**：disclosure snapshot=`bounded_accepted_partitions` / `phase_e_ablation=bounded_scope_measured_b0_short_window`；40 日名义 K purged WF（3 folds）+ T+1 paper；**accept edge gates**（holdout net>0、max DD≤25%、min trades≥30、eval total_return>0）+ **holdout-lift vs B0 稳定性门**。Live：B0/B1 `reject`；B2 短窗 edge 过但 holdout=B0 → **`reject` / `holdout_lift_vs_b0_unmet` / `claimable=false`**（撤回先前短窗 accept）；B4 disclosure 事件覆盖薄 → **`inconclusive`**。均 ≠ StrategyRelease。Fable5 **REVISE** 已吸收。

已拍板：多源=契约可换 adapter（**目标态**）；首策略包=`institution_follow`；边做边测。Tier0 未闭合前禁止寻优、生产候选、cutover、自动跑批。短窗 B2 无独立 holdout lift ≠ `StrategyRelease` / 生产候选。

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
- **E PARTIAL（bounded measured B0+B1+B2+B4 paper）** E0 FIXED 前置已满足。
  `institution_follow_b0` + `_measure`：40 日 `20260522`–`20260717` purged WF
  （embargo=1、holdout=2、3 folds）+ T+1 paper。**Accept edge gates（prereg）**：
  holdout net return > 0；eval total_return > 0；max DD ≤ 0.25；
  n_trades ≥ 30；**另** `holdout_lift_vs_b0`（严格 > B0 holdout）挑战短窗
  巧合 accept。Live B0：ret≈−24.4%、max_dd≈33.5%、win≈0.45、payoff≈0.93、
  turnover≈1.45、n=145；holdout≈+5.9% → `reject` / `claimable=false`。
  **B1**：state 条件化；ret≈−39.6% → `reject`。
  **B2**：project-board breadth risk-on；ret≈+0.34%、max_dd≈13.7%、n=60；
  holdout≈+5.9%**=B0** → edge 过但 **`reject` / `holdout_lift_vs_b0_unmet`**
  （撤回短窗 accept 声称）。
  **B4**：`canonical_top10_float_holders_period` + snapshot date_set；PIT=
  `notice_date`∧`available_at` 日历日 ≤ signal day（NULL notice 排除）；
  信号=首次可用增持/新进事件日；entry=次日 open + `max_chase_days=3`。
  Live 覆盖：event_days=4/40、unique_stocks=11 ＜门槛 → **`inconclusive`** /
  `b4_disclosure_event_coverage_insufficient`（宁缺毋滥，无假 accept）。
  Tests：`_b0`+`_b1`+`_b2`+`_b4`+phase_e smoke。
  **禁** Optuna/全历史/B-pit cutover/margin thaw/multi-year backfill /
  StrategyRelease。
  **残余**：扩披露 snapshot / 更长窗再测 B4；form 缺 `20260717`；正式
  Tier1/Tier2 publish/PIT 零差契约仍薄；无跨年稳定性前禁 release。
  **F** main_rally。**G** 公式+BestChoice。**H** Release/名义价纸面。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。E0 FIXED + measured paper ≠ claimable release；B0/B1/B2 均 `claimable=false`（B2 因 holdout lift 门）；B4 覆盖薄 = inconclusive ≠ reject-with-fake-metrics；均 ≠ StrategyRelease / 生产候选 / B-pit cutover；canary `canary_scope_only` ≠ 全量消融；feature_store field-level PARTIAL ≠ ACCEPTED。
