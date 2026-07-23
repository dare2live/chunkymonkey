# ChunkyMonkey — 系统顶层重评 + 整体优化方案合一（2026-07-22）

> **生命周期**：evidence-only **living roadmap authority**（analysis 层唯一）。
> **授权**：`AGENTS.md` → `goal.md` → `docs/README.md` owners（MASTER / strategy_validation /
> engineering_governance）→ **本文件**（analysis 层 roadmap 合一）→ 下列 sub-authority。
> **不替代**：`docs/MASTER_TOPLEVEL_DESIGN.md` 的业务 Tier / transport **立法**；本文件是执行 roadmap
> 与验收面，不改 north star（`goal.md` 产品目标一字未动）。
> **合并对象**：`~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md`（「ChunkyMonkey 整体优化方案」，
> 2026-07-19 讨论收敛稿，Cursor plan artifact，**不在 repo**）→ 本文件把它带入 repo 成为 living 版本，
> §12 给出 **1:1 映射 + live 状态**。
> **Supersedes（作为 roadmap 权威）**：`plan_reeval_first_principles_20260720`、`plan_reeval_evidence_pack_20260720`、
> `product_plan_reeval_stock_dossier_20260721`、`product_plan_execution_closeout_20260721`、
> `plan_residual_reconcile_20260722`、`foundation_full_goal_push_20260722`、`why_patch_treadmill_20260722`、
> `architecture_fix_treadmill_closeout_20260722`、`forward_program_efgh_20260720`（保留为 point-in-time evidence；
> 索引见 `analysis/DOC_AUTHORITY_20260722.md`）。
> **Sub-authorities（仍 living，subordinate）**：`data_brick_architecture_20260721`（L0–L4 变量分层）、
> `db_layering_toplevel_design_20260721`（物理 DuckDB）、`architecture_fix_treadmill_first_principles_20260722`
> （三时钟控制面）、`foundation_phase_reeval_20260721`（FND-GATE F1–F10 spec）、
> `hs_a_whitelist_includes_st_20260722`（universe 护栏）、`workbench_incremental_orchestrator_ux_20260722`
> （acquire UX P0.1–P3 活计划）、`product_decision_assist_backlog_20260721`（Cap A–F 定义）。
> **Skills applied**：`$architect-controller`（立法→控制→执行）· `$mio`（真金白银 / 消费者锚定 / 地基优先 /
> 别把 ops 残差翻成代码刀 / 别为制度假绿）· `$thinking-occams-razor` · `$first-principles-thinking`。

---

## 0. 立法层裁决（谁定义了「对」）

**定义权**：owner 把 "what-counts-as-done" 显式委托给 architect（mio = standing proxy），本轮三条
指令收敛为一条：**先把数据底座做成「能力完整 + 高性能 + 数据连续完整 + acquire 简单高效」，用分阶段
验收 gatekeep；策略（Optuna / α·β 因子堆叠）是底座之上的纯计算，最后才轮到。** 本文件把这条翻译成
可执行的 roadmap + 验收法。

**创世层（本 roadmap 为何存在，≤3 句，stranger-testable）**
1. 存在目的：让「产品要什么」始终由「底座能不能诚实支撑」来 gatekeep，而不是反过来用前端花活拉着底座跑。
2. 死线（宁死不越）：**绝不**在底座验收门未过前跑 Optuna / 付费寻优 / 出正式候选；**绝不**假报 Tier0
   READY / 假 accepted / 静默 cutover / qfq 当成交价（真金白银护栏）；**绝不** greenfield 第二 DB /
   plugin / DAG / 第五产品。
3. 单一裁决权：`foundation_done.yaml` + FND-GATE 是「地基闭合」的唯一 acceptor；本文件新增的**能力/性能/
   质量/UX 验收门**是它的**扩展 checklist**，不另立平行 acceptor。

**为何合一而非再造第三本圣经**：sprawl（63 篇 analysis）已经是病；再写一本竞争 bible 会加重病。本文件
= 整体优化方案（产品法 + A–H + 边做边测）× 顶层重评（三时钟 + 分层真相 + 能力缺口）× owner 新框架
（底座关键路径 + 分阶段验收），三者**同一部**，其余降为 evidence（§13 + DOC_AUTHORITY）。

---

## 1. 问题 / Jobs-to-be-done（第一性原理）

**要成立一个中国沪深A「可审计研究 + ops」栈，必须为真的最小集：**

| JTBD | 必须为真 | 违反后果 |
|---|---|---|
| J1 正确获取 | 全市场按 `trade_date` landing 保原样；universe 读时过滤（ST∈白名单）；换源只换 adapter | exclude-then-fetch → 换源即崩；vendor dump 冒充池 |
| J2 项目接受 | `stage→validate→publish→accepted_partition` 原子；PIT `available_at`；fail-closed | 「数据已变、验收未变」→ 静默污染 |
| J3 分层派生 | L0 证据→L1 接受→L2 原语→L3 组合砖（≤2 hop）→L4 研究产物；每层 lineage + config hash | 无限 DAG 藏 future → 假 alpha |
| J4 状态描述 | Tier1 决策时点可见的阶段/形态/事件；无未来收益/信号 | 未来泄漏进 Tier1 → 泄漏 |
| J5 市场感知 | Tier2 activity / imbalance_proxy / participation / price_response 带 method/unit | 跨口径求和「资金」→ 伪守恒 |
| J6 研究裁决 | 冻结 `DatasetSnapshot` → B0–B5 单块消融 → `ExperimentVerdict`；PIT 截断 0-diff；purged WF；holdout 一次 | 全期 fit / 多次触碰 holdout → 数字游戏 |
| J7 发布纸面 | 仅 `StrategyRelease` 出候选；名义价 + T+1 + 停牌/涨跌停 + costs | qfq 成交 / 无 Release 出候选 → 假生产 |
| J8 产品消费 | 展示只读 serve；research/unknown/stale/blocked/released 显式；结论进 Tier3/产品，不回写 Tier0 | 「看起来完整」≠ 决策可用 |

**Owner 的关键洞见（本轮立法）**：J1–J5（底座）**决定** J6–J7（策略）的难度。底座若能力完整、
高性能、连续完整、acquire 简单，则策略退化为「在干净快照上跑纯计算」——**Optuna / 因子堆叠不是难点，
底座能力/性能/质量才是**。因此 **J1–J5 是关键路径，J6–J8 排在它们的验收门之后**。

---

## 2. 物理 vs 习惯（keep vs burn）

### 2.1 MUST STAY（物理 / 真金白银护栏 —— 勿砍）
- **PIT / `available_at` / fail-closed Tier0**（宁 degraded 不假 READY）
- **A 股时钟**：盘前 `zero_rows`、`available_after`、T+1、停牌/涨跌停、周末/节假日历硬约束
- **沪深A 白名单含 ST/*ST**；`stock_st` = membership 证据，非 denylist / acquire 排除名单
- **typed soft `pending_publish`**（发布窗真空是合法域状态，非缺陷）
- **DuckDB single-writer**（序列化写；audits 默认 read-only）
- **landing→validate→accept canonical；一数据集一 writer；一处计算多处只读**
- **manual_only**（无 cron/launchd/隐藏重启）
- **commit-green ≠ Continuity READY**（宪法写死）
- **strategy paused / 禁 Optuna·Release·松 holdout；margin frozen；org mass by-date invent
  banned + **每次更新检增量**（缺最新可披露期→拉一期+accept；已有→skip）；禁 mass backfill；
  禁第二 DB / plugin / DAG / 第五产品**
- **§15 knife-merge**（对**真代码刀**不变）
- **积木五件套**：`module + data + config + contract + evidence`

### 2.2 MAY BURN（习惯 / 已烧或该烧）
| 习惯 | 病灶 | 处置 | 状态 |
|---|---|---|---|
| exit-code 把 soft-wait 与 hard-fail 压成一个非零 | load-bearing lie | typed `run_outcome` 三态 | **已烧（Phase 1 FIXED）** |
| Continuity READY 当 agent 北极星 backlog | 轴②误当轴① | demote 为 ops 观测 | **已烧** |
| §15 一刀一 commit 套在 ops 残差上 | 逼 agent 把时钟翻成代码刀 | ops 残差默认非刀（窄 carve-out） | **已烧** |
| 63 篇 analysis 各挂 PARTIAL / 各像 plan | PARTIAL fog + 多 bible | 合一本文件 + DOC_AUTHORITY 索引；余降 evidence | **本轮烧** |
| 「清 residual」措辞当推进 | 变刀燃料 | 换轴为「能力验收 / 用产品 / owner 排期」 | **本轮烧** |
| 空增量仍全量重算 dc_view | 真冗余 | delta manifest 选择性加工（named consumer=acquire UX） | **PASS（CX-1）** |

**不是残差追绿，是能力缺口**：owner 本轮明确「底座必须**已有或去补**支撑未来产品的能力」。因此
delta manifest / 状态传感器 / briefing 砖 / facet serve 砖，**有 named product consumer**，属**正当
能力刀**（treadmill 法典允许：`{owner block ∨ named consumer ∨ 轴①失败 gate}`），与「清 Continuity
PARTIAL」这种无 consumer 的残差追绿**性质不同**。

---

## 3. 分层真相模型（一张图：transport × 业务 tier × 前端 L1/L2/L3 × facet 图）

```text
 传输轴 (每数据集生命周期)          业务依赖轴 (只向下)         前端渐进披露        facet explore 图
 ───────────────────────          ───────────────────       ─────────────       ────────────────
 Provider ─ adapter                                                              股票档案 ──chip──► /explore
   │                                                                                 ▲   │            │ (L2 universe)
   ▼                                                                                 │   │row         ▼
 [land]  raw_evidence  ───────────►  L0 证据 ────────────►  L3 明细 (on demand)      │   └───────►  股票/机构档案
   │                                  │                     全表/gaps/episode dump   │              │
   ▼                                  ▼                                             holder          │ deep-link
 [validate]                          L1 接受事实 (canonical) ► L3 明细 tables         │              ▼
   │                                  │  daily/ST/qfq/taxonomy  <details>            │        /market?tab&filters
   ▼                                  ▼                                             机构档案         ▲
 [accept] canonical (partition) ───► Tier1 stock_state ─────► L2 展开               (episode→code)   │
   │                                  │  form/axis/holder ep    chips+axis grids ───────────────────┘
   ▼                                  ▼                         +horizon strips
 [derive] D1 primary (qfq/form)     Tier2 market_sensing ────► context tabs
   │        D2 variable (L2/L3)       │  flow_regime/pulse      (evidence for L1 labels)
   ▼                                  ▼
 [process] pulse/brick recompute    Tier3 decision surfaces ─► L1 极简 (first screen)
   │                                  │  behavior(潜伏/抢筹/出货) observation 句 / 潜伏象限
   ▼                                  │  intersection why       / chip labels (sparse)
 [serve] resolver read models ─────► │  screener why
                                      ▼
                                     Tier4 release/paper/product (仅 released 出候选)

 Ops/Governance ⟂ 三时钟 (§4) 观察全轴, 不拥有业务事实, 不是超级层.
```

**读法**：三条链**正交**且各有真相源——传输轴回答「怎么进来 / 被接受 / 服务读」；业务轴回答「上层能否
依赖下层」；前端 L1→L3 **镜像** process 层深度（不在浏览器重算）；facet 图把「任何展示的计算 facet 都
可点进它的 universe，再进 dossier」。**pretty exploration 无诚实砖 = 装饰（mio：异常漂亮=警报）**——
这就是为什么 owner 说底座能力/质量 > 前端花活。

---

## 4. 控制面：三时钟 + 单一 acceptor + ops≠刀

> 权威：`architecture_fix_treadmill_first_principles_20260722.md`（本文件不重打，只固化。Phases 0–3 FIXED）。

| 轴 | 真相源 | 靠什么推进 | 谁裁决「到位」 |
|---|---|---|---|
| **① code-fitness** 代码符合契约 | 测试 / gate / 契约 | **commit** | FND-GATE / pytest tier |
| **② live-ops** 今天数据真的 land/accept | accepted partition / watermark / 发布窗 | **交易所时钟 + manual run** | ops 观测（continuity/水位） |
| **③ product-value** 已 ship 面在产出决策 | owner 真实使用动作 | **owner 使用 / owner 排期** | owner |

- **单一真相对象**：运行写 `data/reports/daily_*.json`，派生 typed
  `run_outcome ∈ {success, soft_waiting_clock, hard_fail}`（单一计算点
  `services/pipeline/run_outcome.py`）；exit code / wrapper / 通知 / UI badge / doctor 全是它的
  **renderer**，禁读 rc 判 FAIL。
- **FND-GATE = 唯一 foundation 闭合 acceptor**；任何 analysis PARTIAL **不**重开它。
- **ops 残差默认非刀**：只有 `{owner 新 block ∨ 可指名 consumer ∨ 轴①失败 gate}` 才允许开
  foundation/product 代码刀。
- **死亡条款**：感知死（run_outcome 与真实 accepted 脱钩）/ 判断死（把 continuity 非 READY 当地基重开
  许可）/ 谄媚死（为绿灯好看假报 READY）→ 立即 abort。

**本轮扩展**：owner 要「分阶段验收 gatekeep」→ 把三时钟接进 §7 的**能力/性能/质量/UX 四类验收门**，
每类给机器可检信号，让「验收」也是 typed 真话，不是手挥。

---

## 5. 产品面（current goal 之下，非新 north star）

| 面 | 定位 | Tier | 现状 |
|---|---|---|---|
| 工作台 workbench | ops 可操作（一键更新 / 分步节点） | 观测/控制 | shipped；P0–P3 + **P2 progress UX FIXED**（瀑布日志 / 全链+节点进度 / delta_manifest 面） |
| 市场 market（资金决策辅助 default / 交集 / 选股 / 感知） | L1 极简 → L2 → L3 | Tier2 evidence + Tier3 consumer | Enrich：**地形 2.5D FIXED** + Cap D 桑基/平行坐标 FIXED；感知 L1 稀疏 |
| 股票档案 / 机构档案 dossier | per-stock / per-holder 决策辅助 | Tier3 consumer | Cap F **FIXED 100% usable**（`dossier_100_usable_20260723.md`）；org = **incremental-check-every-run**（mass re-pull banned；非 forever ignore） |
| `#/explore` facet 图 | 计算 facet → universe → dossier | Tier3 navigation | **HAVE（CX-3）** sector_membership + flow_streak live |
| **候选每日简报 daily briefing** | 把 conclusion/why/observation 聚成叙事 | **Tier3 narrative consumer（optional）** | **HAVE（CX-3）** `daily_briefing` serve + UI |

**原则**：产品面**只读 serve / 只消费已发布砖**；behavior/结论进 Tier3/产品，**永不**回写 Tier0；
`unknown` 永不画成 0；stale 保持诚实。briefing 是「已就绪砖的叙事消费者」，**不是**新数据真相层——
它验证「底座能力是否足以支撑叙事」这条 owner 主张。

---

## 6. Acquire 之后的计算：delta-manifest 选择性加工 + 状态传感器（分期）

> 权威：`workbench_incremental_orchestrator_ux_20260722.md`（P0–P3 FIXED；P2 progress UX shipped 2026-07-23）。

- **裁决（Occam）**：空增量仍加工 = **半设计半冗余**。`segments`/`technical_states` 无缺日秒退（良好）；
  `market_pulse` 迟到窗回补**必须跑**（t+1 迟到列自愈，owner「derive lag 不许跳」）；`dc_view` 全量
  重建 = **真冗余**（可加 frontier guard）。**一刀切短路不安全**（会杀迟到列自愈 = 真金白银红线）。
- **P1 delta manifest**：acquire 产 typed delta（advanced_partitions / late_window_changed /
  state_changes）→ process 只重算真受影响 + **恒定**重算 pulse 迟到窗。
- **P3 状态传感器**：ST 戴帽/摘帽、holder 比例变化（排名不变也算）、退市——「非增量但状态变」的 typed
  探测纳入 delta manifest 触发选择性更新；**不得**把状态变化融进 Tier0 真相；停牌/涨跌停/T+1 硬约束不破。
- **决策法**：默认 delta；全量仅当 (a) 配置/口径变 (b) 全局影响（日历/universe 身份）(c) owner「保险
  准确」模式；每次跳过/全量 cite typed reason，不静默。**不做** DAG/event-bus（触发式，不预建）。

---

## 7. 分阶段验收法（owner 新框架核心：design → implement → 验收 → next）

> 每阶段先 **serious design**，再 **steady implement**，再 **phased 验收**（机器可检优先），验收过才进
> 下一阶段；**验收不过 → kill criteria 触发，回退不硬闯**。四类验收面各给 typed 信号。

### 7.1 四类验收面 + 机器可检信号

| 类 | 关注 | 机器可检信号（示例，*=待建 checker） | 反面（不算数） |
|---|---|---|---|
| **A 能力 Capability** | 分层砖能否支撑 briefing/facet/delta consumers | `check_brick_registry.py`（orphans=0, hops≤2）；每个 FEATURE_BLOCK_ID 在 registry；*briefing 输入砖 resolver 全绿；*facet serve 砖非 stub | 砖存在但 serve stub / stock 名 text 冒充 universe |
| **B 性能 Performance** | acquire/process 延迟预算 + DuckDB 写纪律 | *`daily_*.json` 记 per-stage 墙钟 + 落 budget（如空增量 process ≤ Xs；qfq rebuild 已测 6.45s）；single-writer 无并发写锁冲突（`parallel-grid-runner` 教训） | 用旧计时洗绿；忽略锁争用 |
| **C 数据质量 Data quality** | continuity / coverage / PIT / grain 唯一 / SLA 诚实 | `check_continuity_integrity.py`；`check_foundation_done.py`（F1–F10）；PIT 截断 0-diff；grain 唯一性断言；`run_outcome` 软态**只**含真等时钟（无假 soft_waiting） | continuity READY 靠 commit 洗绿；SLA unknown/墓碑当 stale（**P0.1 FIXED / CX-4 PASS**） |
| **D Acquire UX** | 流式日志 / 增量识别 / 无 sibling 绑架 | Phase 2 断言（drain-first / no-cross-sibling-kidnap，`6f74d2919`）；*delta manifest typed 字段；*per-node + overall 进度事件 | IDLE 冒充运行；共享死 pid；空点刷屏（P0 已修） |

### 7.2 阶段路线 + 每阶段 kill criteria

| 阶段 | 内容 | 授权 | 验收（过才进下一阶段） | Kill criteria | 状态 |
|---|---|---|---|---|---|
| **F0 底座闭合基线** | transport S1–S6 / S7 墙 / E0 / brick L0–L4 / qfq lineage / FND-GATE | 已完成 | FND-GATE `phase_closure_ready=true`（F1–F10 PASS） | 任一 F* 回退红 → 停一切上层 | **FIXED** |
| **F1 控制面真话** | typed `run_outcome` + drain-first + ops≠刀 | 已完成 | Phase 1/2 断言绿；软态不画 FAIL/不刷屏 | 三连炸/仍 FAIL → 回退桩 | **FIXED** |
| **CX-1 acquire 高效** | delta manifest（P1）+ acquire UX 流式/增量识别（P2）+ 性能预算落 `daily_*.json` | **owner 排期** | §7.1 D 全绿 + B 预算达标；迟到列仍自愈 | 跳过致迟到列 stale / ST 戴帽漏 → abort | **PASS**（证据 `cx1_acquire_efficiency_acceptance_20260722.md`；live process≤90s 下次空增量 OBSERVE） |
| **CX-2 状态传感器** | ST/holder/退市 state-change → delta manifest（P3） | **owner 排期** | 状态变即使无新行也触发对应域重算；PIT 安全 | 融进 Tier0 / 破 T+1 → abort | **PASS**（证据 `cx2_state_sensors_acceptance_20260722.md`） |
| **CX-3 能力补砖** | briefing 输入砖 + facet serve 砖（sector membership / stock-level flow streak universe） | **owner 排期** | §7.1 A 全绿：briefing/facet consumer 读真砖，无 stub | 砖 stale/UNTRUSTED 仍出叙事 → fail-closed | **PASS**（证据 `cx3_capability_bricks_acceptance_20260722.md`） |
| **CX-4 SLA/质量收口** | P0.1 SLA 去误报（墓碑清 / unknown≠stale）+ coverage/continuity 诚实提升 | **owner 排期** | §7.1 C：`run_outcome` 软态只含真等时钟 | 误删活源 watermark / 静音真 stale → 回退 | **PASS**（证据 `cx4_sla_quality_acceptance_20260723.md`） |
| **RX 研究窗** | E/F remeasure（同 protocol）→ 再 G 公式 → H 发布纸面 | **owner 签字后** | `ExperimentVerdict`（诚实 reject 亦算交付）；PIT/purged-WF/holdout 门 | Optuna/Release/松 holdout/margin thaw → abort | **BLOCKED（paused）** |
| **Phase N 寻优** | Optuna / α·β 因子堆叠 = 底座之上纯计算 | **仅 CX-* 验收全过 + owner 显式开研究窗后** | 冻结 snapshot + 搜索空间非空 + 穿透真实后果期望 | 底座验收未过即开 → 触死线，abort | **BANNED（未到）** |

**全局 kill criterion**：任一阶段一旦开始「造代码刀清 ops 状态 / 无 named consumer 补砖」= abort，
跑步机回归。**Phase N（Optuna）在 CX-* 验收门全过 + owner 开窗前，一律 BANNED**——这正是 owner「策略
是底座之上纯计算，最后才轮到」的机器化。

---

## 8. 底座能力缺口矩阵（have / partial / missing —— owner 硬约束的核心交付）

> 判据：产品方向所需的**底座 serve/砖能力**是否已诚实就绪（不是前端有没有画）。证据=live routers/services + brick registry + analysis。

| 产品方向 | 需要的底座能力 | have / partial / missing | 证据 / 缺口 |
|---|---|---|---|
| 分层变量（raw→basic→advanced） | L0–L4 brick + lineage + config hash | **HAVE** | `brick_registry.yaml`；qfq lineage FIXED（8.4M 行 missing_lineage=0）；L3 enrichment PARTIAL |
| 资金多档位 + 行为 regime（潜伏/抢筹/出货） | Tier2 `flow_regime` + 7 档 horizon + 相对分母 | **HAVE** | `moneyflow_assist.py`（horizons[1,3,5,10,20,30,60]；`behavior_from_regime` versioned unknown-allowed；sector_mv 分母） |
| 交集最强 | DC∩概念∩申万 membership + 强度 | **HAVE** | `decision_assist.py /intersection/strongest`；Cap D FIXED |
| 形态/阶段选股 | Tier1 form/axis serve | **HAVE（subset）** | `stock_screener.py /form_stage`；Cap B FIXED subset |
| facet explore 跳转 | 每个计算 facet → universe serve 砖 | **HAVE（CX-3）** | behavior/form/axis/breakout/intersection/holder + sector_membership + stock flow_streak live；证据 `cx3_capability_bricks_acceptance_20260722.md` |
| 股票档案 dossier | stock↔holders↔form↔收益 lineage | **FIXED** | Cap F `stock_dossier_cap_f_usable`（`dossier_100_usable_20260723.md`）；tabs ok/empty/delegated；episode cycle/return；机构 deep-link closed-loop |
| 机构档案 | org/holders episode + 披露时点 | **FIXED**（serve） / ops incremental | deep-link ≈ episode（闭环 process）；**`org_holding` = period-gap + population gate**（mass/by-date invent banned）；holders F6 PASS；见 `shareholder_update_check_design_20260723.md` |
| **候选每日简报 briefing** | conclusion + why + observation 聚合叙事砖 | **HAVE（CX-3）** | `daily_briefing` serve + `#/briefing` / Market assist panel；stale/UNTRUSTED → narrative=null |
| delta 选择性加工 | acquire typed delta manifest | **HAVE（CX-1 PASS）** | `delta_manifest` → DC frontier skip；pulse late window always；证据 `cx1_acquire_efficiency_acceptance_20260722.md` |
| ST/holder 状态变更传感 | 非增量状态变探测 | **HAVE（CX-2 PASS）** | `state_sensors` → `delta.state_changes`；证据 `cx2_state_sensors_acceptance_20260722.md` |
| serve 新鲜度 / continuity | typed soft + 域水位对齐 | **PARTIAL**（continuity READY 仍 ops；SLA 误报 **FIXED**；margin frozen **observe**） | `run_outcome` 三态 FIXED；P0.1/CX-4 PASS；margin `observe_frozen_stale`（`margin_calendar_catchup_blocker_20260723.md`）— 非假 READY |
| 性能预算可观测 | per-stage 墙钟 + budget | **HAVE（CX-1）** | `daily_*.json` `stage_timing_s` + `budget_status`；live empty-increment OBSERVE |

**一句话**：**资金 regime / 交集 / 选股 / 分层砖 / briefing / facet serve = HAVE（含 CX-3）**；**dossier Cap F / 机构 serve = FIXED 100% usable**（`dossier_100_usable_20260723.md`；org 每日增量检）；**delta / 状态传感器 / 性能预算 / SLA 去误报 = HAVE（CX-1…CX-4）**——CX-* 能力门闭合；RX 仍要 owner 签字；Optuna=Phase N BANNED。

---

## 9. 什么 DONE vs 什么 NEXT（live evidence，诚实）

**DONE（勿回滚）**：
- 底座闭合 F0：transport S1–S6 FIXED / S7 23 typed 墙 / E0-HIST F6 PASS / brick L0–L4 + qfq lineage /
  FND-GATE `phase_closure_ready=true`。
- 控制面 F1：typed `run_outcome`（`122896464`）/ drain-first no-kidnap（`6f74d2919`）/ 潜伏象限 MVP
  （`b3a9fd4e7`）/ 通知合并。
- 产品 consumer subset：Cap A/B/D/E FIXED；Cap F dossier **FIXED 100% usable**；前端 L1/L2/L3 + facet skeleton
  （`b29a134f2`）。

**NEXT（owner 排期驱动，按 §7 顺序）**：~~CX-1~~ **PASS** → ~~CX-2~~ **PASS** → ~~CX-3~~ **PASS** →
~~CX-4（SLA/质量收口）~~ **PASS**（`cx4_sla_quality_acceptance_20260723.md`）→ **RX**（E/F 研究窗，**owner 签字后**）→
Phase N（Optuna，仍 BANNED 直到 RX 开 + 底座验收过）。

**NOT NEXT（禁）**：默认清 PARTIAL 代码刀 / Continuity READY 追绿 / mass org re-pull / S7 假 COMPAT /
margin thaw / 擅自 E/F remeasure / Optuna / StrategyRelease / 松 holdout / 第二 DB·DAG·plugin·第五产品。

---

## 10. Adversarial 决策日志（3 模型 → 综合）

> `$mio` #8：对方向性/架构选型开正反论证。inline 三角色（plan-only）。

- **R1 保守（不动底座）**：底座已 CLOSED，别再开刀；只用产品 + 等时钟 + owner 排期研究。风险：owner
  明说「底座必须**去补**支撑未来产品的能力」——纯 use 会让 briefing/delta/传感器永远缺，产品叙事无根。
- **R2 大胆（先冲产品 + 早开 Optuna）**：把 briefing/facet 前端先做漂亮，Optuna 早点试因子。风险：违反
  owner 死线（底座验收未过跑寻优）+ mio 地基优先 + 「异常漂亮=警报」；前端花活无诚实砖 = 装饰。
- **R3 architect 综合（采纳）**：**底座能力是关键路径**——把 delta/传感器/briefing 砖/性能预算/SLA 当
  **有 named consumer 的能力刀**（非残差追绿），走 §7 分阶段验收；产品前端只作为「验收这些能力是否够用」
  的消费者；Optuna 严格排到 Phase N，CX-* 验收 + owner 开窗前 BANNED。

| # | 硬 call | 裁决 | 理由 |
|---|---|---|---|
| 1 | 底座是关键路径还是前端？ | **底座** | owner 立法 + mio 地基优先 + 策略=底座上纯计算 |
| 2 | delta/传感器/briefing 是残差还是能力刀？ | **能力刀（named consumer）** | treadmill 法典允许；有产品消费者 |
| 3 | Optuna 何时？ | **Phase N，CX-* 验收 + owner 开窗后** | 死线：底座验收未过禁寻优 |
| 4 | 再写一本 bible？ | **NO，合一本文件** | sprawl 是病；DOC_AUTHORITY 索引降其余为 evidence |
| 5 | 改 goal.md north star？ | **NO** | owner 明令；只加 design-notes 指针 |
| 6 | 分阶段验收怎么保证不是手挥？ | **四类 typed 机器信号 + kill criteria** | 每阶段 design→impl→验收→next |

**判断法典 seeds（两语言）**
1. 人话：底座能力/性能/质量 gatekeep 产品，不反过来 / 机器话：产品面只读 serve；CX-* 验收门未过不进 RX；RX 未开不进 Phase N。
2. 人话：能力刀要有 named consumer，残差不追绿 / 机器话：开 foundation/product knife 须 cite `{owner block ∨ named consumer ∨ 轴①失败 gate}`。
3. 人话：验收是 typed 真话不是手挥 / 机器话：每阶段过 §7.1 A/B/C/D 机器信号 + kill criteria，否则回退。

---

## 11. 明确不做（防假出口 / 防重开）
- 不 greenfield 第二 DB / plugin / DAG / event-bus / 第五产品 / 按加工阶段拆库
- 不把 Continuity 非 READY / DONE degraded / 晨间 `pending_publish` 当 foundation 重开许可
- 不擅自 E/F remeasure / Optuna / StrategyRelease / 松 holdout / margin thaw / mass org re-pull / S7 假 COMPAT
- 不把状态变化/结论融进 Tier0 accepted 真相；不 qfq 当成交价
- 不为「配置化」把事实/运行状态写进 active config；不写 YAML DSL
- 不物删历史 analysis（降 evidence + 加 superseded 指针）；不建 archive-of-archive
- 不改 `goal.md` north star

---

## 12. 与「整体优化方案」1:1 映射（合一证明）

| 整体优化方案 §（plan artifact） | 本文件对应 § | live 状态（2026-07-22） |
|---|---|---|
| §1 产品法（死亡线 + 人话/机器话） | §0 立法 + §1 JTBD + §2 physics | 保留；死线扩「底座验收未过禁 Optuna」 |
| §2 目标架构（积木 + 两轴 + 多源 + 分责） | §3 分层真相模型 + §2.1 积木五件套 | S1–S6 FIXED；多源 adapter=TuShare live；miaoxiang NONCONFORMING→E0 |
| §3 修订迁移总序 A–H（机构提前） | §7 阶段路线 + §9 DONE/NEXT | A–E0+D 底座 = **F0 CLOSED**；E/F=RX paused；G/H 排 RX 后 |
| §4 Phase A 细化 | §7 F0（已闭合） | Tier0 硬门 / calendar / resolver / landing 纯度 / adapter 边界 = FIXED |
| §5 边做边测（红→绿→窄回归） | §7 每阶段 design→impl→验收 + §7.1 验收面 | 纪律不变；扩四类机器验收信号 |
| §6 策略包消融阶梯 B0–B5 | §7 RX + Phase N | E/F reject 已归档；paused；Optuna=Phase N BANNED |
| §7 展示与产品面（先诚实后转正） | §5 产品面 | Cap A–F consumer subset shipped；research/unknown/stale 显式 |
| §8 与现状差距（审计） | §8 能力缺口矩阵 | 更新为 have/partial/missing 产品能力视角 |
| §9 文档落地动作 | §13 + DOC_AUTHORITY | **本轮执行**：合一 + 索引 + superseded 指针 |
| §10 明确不做 | §11 明确不做 | 承接 + 扩 treadmill/Optuna 死线 |
| 附录 A 差异审计 G1–G6… | §8 + `project_state_ledger.md` | G1–G6 Tier0 门 = F0 已闭合；余进 CX-* |

**结论**：整体优化方案的产品法 + 两轴 + A–H + 边做边测 **全部保留并推进到 live 状态**；本文件 = 它的
**living 续版 + owner 新框架（底座关键路径 + 分阶段验收 + Optuna=Phase N）**，无第三本竞争 bible。

---

## 13. 文档权威落地（cleanup 动作）

- **living roadmap authority** = 本文件（analysis 层唯一）。
- **索引** = `analysis/DOC_AUTHORITY_20260722.md`（读什么 / 什么是历史）。
- **sub-authorities**（§ 顶部列出）仍 living，subordinate。
- **superseded 为 evidence**（§ 顶部列出）：加一行 superseded 指针，不物删；历史查 `project_state_ledger.md` +
  git。
- **goal.md**：仅在指针块加一行 design-notes 链接本文件与索引，**north star 一字未改**。

---

## 14. Verdict

| 标签 | 内容 |
|---|---|
| **MERGE** | 整体优化方案 + 顶层重评 + owner 底座框架 = **合一本文件**（§12 1:1 映射） |
| **CRITICAL_PATH** | **底座能力 / 性能 / 质量**（J1–J5）；策略（J6–J8 + Optuna）= 底座之上纯计算，排 Phase N |
| **ACCEPTANCE** | 四类 typed 门（能力/性能/质量/UX）+ 每阶段 design→impl→验收→next + kill criteria |
| **NEXT** | CX-1…CX-4 **PASS** → RX（owner 签字）→ Phase N（BANNED 直到 RX 开） |
| **NORTH_STAR** | **未改**（goal.md 产品目标一字未动） |
| **SPRAWL** | 63 篇 → 少数 living + 索引 + evidence（DOC_AUTHORITY） |

**APPROVED** — 作为 analysis 层 living roadmap 合一权威；implementation 仍 strangler + 分阶段验收，不触发
greenfield；Optuna 排 Phase N，底座验收 + owner 开窗前 BANNED。
