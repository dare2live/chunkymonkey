# 补丁跑步机 — 第一原理架构修复方案（2026-07-22）

> Status: evidence-only / **Phases 0–3 FIXED**; Phase 4 checklist FIXED / E-F compute BLOCKED — closeout `analysis/architecture_fix_treadmill_closeout_20260722.md`
> Authority chain: `AGENTS.md` → `goal.md` → `docs/README.md` owners → 本文件（treadmill 控制面/ops/product ceiling 的架构裁决）
> Diagnosis owner（勿重打）: `analysis/why_patch_treadmill_20260722.md`（evidence-only 判断，本文件是它的 architecture 续作）
> Skills applied: `$architect-controller`（立法→控制→执行）· `$first-principles-thinking` · `$thinking-occams-razor` · `$mio`（真金白银 / 消费者锚定 / 别为制度假绿 / 别被"做完了"锁死）
> 业主指令: **act as architect from first principles**；**该改就改，不必守现架构**；adversarial 2–3 角色论证；default NO production rewrite this turn。

---

## 0. 立法层裁决（谁定义了「对」）

**定义权**：业主把 "what-counts-as-done" 的裁决权在本轮显式委托给 architect（mio = standing proxy）。诊断文件已裁 REAL_PROBLEM = **CLOSED 后队列/ceiling 错位（主）+ 编排交互边偶发真债（次）**。本文件不重开那个裁决，只把它翻译成一部可执行的**控制面法**。

**创世层（本控制面为何存在，≤3 句，stranger-testable）**
1. 存在目的：让「地基是否闭合」「今天数据到没到」「产品有没有在被用」三个**不同时钟**各自说真话，而不是被压成一个 "done/FAIL" 标量。
2. 死线（宁死不越）：**绝不**为了让某个绿灯好看而假报 Tier0 READY / 假 accepted / 静默 cutover（真金白银护栏）；**绝不** greenfield 第二市场 DB / plugin / DAG / event-bus。
3. 单一裁决权：`foundation_done.yaml` + FND-GATE 是「地基闭合」的**唯一** acceptor；任何 analysis 文档的 PARTIAL 都不构成重开许可证。

---

## A. 用第一原理重述问题

### A.1 被违反的核心不变量

**系统只有一个词（"done" / 非零 exit = "FAIL"）来描述三条互不同步的时钟，又只有一个交付仪式（代码刀 = 一次 commit）来消费全部三条。错位即跑步机。**

三条正交时钟（three clocks）：

| 轴 | 真相源 | 靠什么推进 | 谁裁决"到位" |
|---|---|---|---|
| **① code-fitness** 代码符合契约 | 测试 / gate / 契约 | **commit** | FND-GATE / pytest tier |
| **② live-ops** 今天数据真的 land/accept 了 | accepted partition / watermark / 交易所·供应商发布窗 | **交易所时钟 + manual run**（非 commit） | ops 观测（continuity/水位） |
| **③ product-value** 已 ship 面在产出决策 | owner 的真实使用动作 | **owner 使用 / owner 排期** | owner |

跑步机机制（Occam 版，最少假设）：
- §15 交付文化只奖励**轴①的动作**（一刀=一 commit）。
- 于是每当轴②（`pending_publish` / DONE degraded / continuity 非 READY）或轴③（"产品差一点"）冒出信号，agent 的**唯一**已知消费方式就是**把它翻译成一把轴①代码刀**。
- 轴②本质是「时钟还没到」，不是代码缺陷；把它当代码债 → 制造假队列 → 修完同日 sibling 仍真空 → 又像没做完 → 永动。

这不需要「架构太复杂」这个假设就能完整解释现象 → **Occam 主因 = 词/队列错位，不是分层该废**。

### A.2 第二不变量：run outcome 把「时钟没到」和「坏了」混成同一个非零

`pipeline/run.py` 现状：`exit 1`(degraded 续跑) 与 `exit 5`(Tier0 硬挡) 对 wrapper / UI / 通知都是「非零 = FAIL」。**软等时钟（soft-waiting）与硬失败（hard-fail）在下游长得一模一样。** 一个分不清这两者的 UI/通知**必然撒谎**——而每一条假红都是新的跑步机燃料（每条红看起来都可行动）。

证据：`daily_update_notification_spam_triage_20260722.md` — 单轮软降级被 3 通道各报一次 × 多次点击；旧 wrapper 把 `exit 1` 一律标 "job FAIL" = **假失败告警**。

### A.3 第三不变量（次因）：fused-dragon 编排

sibling 域经**共享硬门**耦合：formal catchup `raise Tier0` 放在 `--all-due` 前 → 绑架已发布 sibling。这是**真轴①结构债**，合法产刀（已 drain-first 重建）。它是「次因」，非主因；勿用它论证「架构该推倒」。

### A.4 bedrock（去掉全部约定后剩下的真）
1. A 股「可审计决策辅助」系统**必然**付 ops 税（发布窗 / PIT / T+1 / 停牌涨跌停 / manual 日更）——税是域固有的，不是本仓工程失败。
2. 「代码对不对」可被 commit 证明；「今天数据到没到」只能被时钟+运行证明；「产品有没有用」只能被 owner 使用证明。三者**物理上不共享一个 acceptor**。
3. 因此：**给每条时钟一个自己的 typed 词 + 一个自己的队列**，就足以停跑步机。不需要新框架。

---

## B. 物理 vs 习惯

### B.1 MUST STAY（物理 / 真金白银护栏 —— 勿砍）

- **PIT / `available_at` / fail-closed Tier0**（宁 degraded 不假 READY）
- **A 股时钟**：盘前 `zero_rows`、`available_after`、T+1、停牌/涨跌停、周末/节假日历硬约束
- **沪深A 白名单含 ST/*ST**；`stock_st` = membership 证据，**非** denylist / acquire 排除名单
- **typed soft `pending_publish`**（发布窗真空是合法域状态，非缺陷）——这是解药的一部分，不是病
- **DuckDB single-writer**（序列化写；audits 默认 read-only）
- **landing→validate→accept canonical；一数据集一 writer**
- **manual_only**（无 cron/launchd/隐藏重启）
- **commit-green ≠ Continuity READY**（宪法写死）
- **strategy paused / 禁 Optuna·Release·松 holdout；margin frozen；org BLOCKED；禁 mass backfill；禁第二 DB / plugin / DAG**
- **§15 knife-merge**（对**真代码刀**不变）

### B.2 MAY BURN（习惯 / 可误读交互边 —— 该改就改）

| 现状（习惯） | 病灶 | 处置 |
|---|---|---|
| `daily_update` exit code 把 soft-wait 与 hard-fail 都映射成 "FAIL" | A.2 load-bearing lie | **REPLACE** → typed RunOutcome（§C2） |
| Script Editor / `osascript` 三通道各弹一次 | 通知刷屏 | **REPLACE** → outcome-keyed 单渲染（§C2）；现 `--skip-macos`+rc==1 heuristic = 过渡桩 |
| Continuity READY 当 agent 北极星 backlog | 轴②被误当轴① | **DEMOTE** → ops 观测，非地基重开许可（§C3） |
| 多权威文档各挂 PARTIAL（对「讨论级 ceiling」） | PARTIAL fog | **RESCOPE**（非删）→ 归类为轴②观测 或 轴③野心注记，均非代码队列（§C3） |
| §15 一刀一 commit **套在 ops 残差上** | 逼 agent 把 ops 翻成代码刀 | **CARVE-OUT**（窄）→ ops 残差默认**不是刀**；§15 对真代码不动 |
| all-due/formal ordering 遗留 | fused-dragon | 已结构修；保留 shape 教训 + 回归断言（§C1） |
| docs 里「清 residual」措辞 | 变刀燃料 | 改为「观测/等时钟/owner 排期」措辞 |

---

## C. 目标架构（把 *控制面/ops/product ceiling* 当 greenfield —— **不是**第二市场 DB greenfield）

> Controller substrate 裁定：真相源不是 `exit int`，而是运行已经在写的 **`data/reports/daily_*.json`**（已含 `phase_status.chain`、`degraded_total`、`degraded_msgs`、`alert_flags`）。所有下游读**这个对象**，不读 rc。

### C1. 编排 = sibling 域并列，禁绑架

- 每个 acquire 域（daily / stock_st / ths_hot / drain-backlog / formal-catchup / org-period）是**独立 sibling**，各带自己的 outcome。
- `--all-due` sweep：**drain 先于 formal**；逐域收集 outcome；**任一域的硬状态不得 raise 越过 sweep 边界**去 abort 其它 sibling。
- 已结构落地（`foundation_acquire_all_due_unblock_20260722.md`）。目标 = 把「no cross-sibling hard-raise」**固化为不变量 + 一条回归断言**，不是重新架构。契约：`module+data+config+contract+evidence`，边界=域，不引入 DAG。

### C2. Typed run outcome（让 UI + 通知**无法**撒谎）—— 载重改动

把标量 exit→FAIL 映射换成**每域 + 每轮**的 typed outcome：

```text
RunOutcome ∈ {
  success            # 目标 partition 已 accepted
  soft_waiting_clock # pending_publish / pre_available_after_zero_rows /
                     #   drain 同日真空 —— 时钟未到, 非缺陷
  hard_fail          # writer/auth/preflight/真 Tier0 block —— 现在可行动
}
```

- **Rollup**：一轮 headline = worst({任一 hard_fail→HARD；否则任一 soft_waiting→SOFT_WAITING；否则 SUCCESS})。
- **来源**：从已存在的 `degraded_msgs` 分类**派生**（单一计算点）：`pending_publish/pre_available_after_zero_rows/still_failed=[今日]` → `soft_waiting_clock`；`AUTH/PREFLIGHT/TIER0/WRITER BLOCK` → `hard_fail`。写进报告 JSON 的 `run_outcome` 字段。
- **exit code 保留**但**派生自** outcome（shell 语义），不再是真相源。
- **UI**：三态三色；`soft_waiting_clock` = 「等时钟」（琥珀/观测），**永不**红色 "FAIL"。
- **通知策略**（outcome-keyed，构造上杜绝刷屏，取代当前 rc==1 heuristic + `--skip-macos` 特判）：
  - `success` → 静默
  - `soft_waiting_clock` → 至多 1 条合并「观测」横幅（可配置为 0）
  - `hard_fail` → 1 条 FAIL 告警
- 第一原理动作：**运行发一个 machine-readable outcome 对象；exit code / wrapper flag / 通知 / UI badge 全是它的 renderer。** 一真相源，多渲染。

> **取代关系明确**：`scripts/manual_job_wrapper.py` 的 `rc==1 && degraded_flag` 特判 与 `dispatcher --skip-macos` 是**过渡桩**；Phase 1 后它们成为 typed-outcome 的实现，不再是启发式。

### C3. Agents 停跑步机 = 单一权威 + ops-residual-≠-knife

- **单一「地基闭合」权威**：`foundation_done.yaml` + FND-GATE = 唯一 acceptor。`phase_closure_ready=true` 即闭合。**其它文档的 PARTIAL 不重开它。** 多 PARTIAL 重归类为：(a) 轴②ops 观测 或 (b) 轴③野心 ceiling 注记——**均非**重开许可。
- **工作队列法（人话 + 机器话）**：
  - 人话：ops 残差（软等时钟 / continuity 非 READY / DONE degraded）**默认不是刀**；只有 {owner 新 block ∨ 可指名 consumer ∨ 轴①失败 gate} 才允许开 foundation/product 代码刀。
  - 机器话：agent 开 foundation/product knife 前须 cite 三触发之一；否则 boot checklist 判为 fake-progress（对齐 `goal.md`「默认禁止再开清 PARTIAL 代码刀」）。
- **队列死亡条款（判断死 guard）**：若「下一动作」不是 {用产品 / 观测 ops 时钟 / owner 排期研究}，默认视为**假推进**（= 诊断文件 verdict）。

### C4. 产品路径 = 下一档齿轮（轴③）

- 真正的轴③杠杆 = **使用已 ship 面** + viz MVP（`frontend_complex_viz_plan_20260722.md` Phase 1 潜伏象限图，只读 Tier3，无新 backend）。
- **推进的度量换轴**：从「又 FIXED 一个 residual」→「本周用产品面做出一个可复核决策辅助动作」或「owner 签字开研究窗」。
- product ceiling 只经 owner 排期推进；viz Phase 1 是已就绪的齿轮。

### C5. 一张图

```text
        run (manual_only)
             │ writes ONE truth object
             ▼
   data/reports/daily_*.json  ──►  run_outcome ∈ {success, soft_waiting_clock, hard_fail}
             │                              │ (derived, single compute point)
   ┌─────────┼──────────┬─────────────┬─────┴───────┐
   ▼         ▼          ▼             ▼             ▼
 exit code  wrapper   dispatcher     UI badge     doctor
 (derived)  flag      (outcome-keyed notify)   (observes axis②)

 axis①code-fitness → FND-GATE (single acceptor of "foundation closed")
 axis②live-ops     → ops observation (NOT a knife by default)
 axis③product      → owner usage + viz MVP (next gear)
```

---

## D. 迁移 / strangler 阶段（每阶段显式 kill criteria）

| Phase | 内容 | 授权 | 完成长什么样 | Kill criteria | Status |
|---|---|---|---|---|---|
| **0 冻结跑步机**（本轮） | 本文件 + `goal.md` 三时钟指针；宣示 ops-residual-≠-knife；**零代码** | 本轮已授权 | 三时钟 + 单一权威 + 队列法写死 | 若 owner 读后仍感「地基没做完」→ 权威还不够单一，回 §C3 收紧 | **FIXED** |
| **1 outcome model**（小代码轮） | 报告 JSON 派生 typed `run_outcome`；wrapper+dispatcher+UI 读它；取代 rc==1 heuristic / `--skip-macos` | **owner 开做 2026-07-22 → FIXED** | 报告+API+UI 三态；soft 不弹 job FAIL；证据 `phase1_run_outcome_20260722.md` | 若仍三连炸 / 仍显 FAIL → 回退到过渡桩，重设计派生规则 | **FIXED** |
| **2 orchestrator**（多半已完） | 仅当**新的** cross-sibling hard-raise 复现时；加「不越 sweep 边界」回归断言 | 触发式 → 断言固化 | 断言绿；无 sibling 绑架 | 只有**第二次**真 kidnap（不同域）才泛化；否则**不预建** DAG（架构死#1） | **FIXED** (`phase2_orchestrator_assert_20260722.md`；无第二 kidnap → 无 DAG) |
| **3 product viz MVP** | viz plan Phase 1 潜伏象限（真 Cap A API，只读 Tier3） | owner schedule → shipped | owner 用它产出一个决策辅助观测 | 若沦为装饰 → 回退 header band（viz plan §6） | **FIXED** (`phase3_latent_quadrant_mvp_20260722.md`；地形 Enrich 延后) |
| **4 owner-scheduled E/F remeasure** | 仅 owner 签字后；同 protocol | owner signature | remeasure 完成（诚实 reject 亦算交付） | 禁 Optuna/Release/margin-thaw；未签字保持 paused | checklist **FIXED** / compute **BLOCKED** (`phase4_ef_schedule_gate_honesty_20260722.md`) |

**全局 kill criterion**：任一 phase 一旦开始「造代码刀清 ops 状态」= abort，跑步机回归。

---

## E. 明确**不修**的（防假绿 / 防重开）

- **margin frozen** → continuity 会持续产 degraded：这是**诚实**，不是待 code-fix 的 bug。（future 可给冻结域一个 typed continuity-exemption，但**不在本轮**，且不得假绿。）
- **org_holding BLOCKED** — 维持；period 域 manual = incremental-only（缺拉一期/有则 skip），禁 mass re-pull。
- **Continuity READY 当永久 agent backlog** — 故意 demote；它不是北极星。
- **pure accepted / form hybrid 双读残** — 诚实披露保留，不追「最后一刀」。
- **多权威 PARTIAL fog** — 重归类不物删（git history 留档）；除 `goal.md` 指针外**不**再花刀合并文档。
- **Type-B enrichment / S7 假 COMPAT / mass backfill / 第二 DB / plugin·DAG** — 禁。

---

## F. Adversarial 决策日志（3 角色）

> 按 `$mio` #8：对**方向性/架构选型**开正反论证，不在琐碎上仪式化。inline 三角色（plan-only + 需完整 brick 语境，未派并行子 agent，同 viz plan §6 做法）。

**R1 保守 strangler**：诊断已裁主因=队列错位。别碰生产。最多留现有 rc==1 + `--skip-macos` 过渡桩 + 文档纠偏。任何代码改都可能再生刀。
**R2 大胆重建 ops/product ceiling**：exit-code→FAIL 的语义是 load-bearing lie，补丁桩治标；应 typed outcome 重写；顺手松 §15 让 ops 不必产刀；把 continuity 门整个换掉。
**R3 architect 综合**：取 R2 的「outcome 是谎必须换」，取 R1 的「DB/编排不重建 + 桩先留」，拒 R2 的「松 §15」。

| # | 硬call | 裁决 | 理由 |
|---|---|---|---|
| 1 | greenfield DB/编排？ | **NO** | owner 禁 + 物理不可约 + Occam；R2 让步 |
| 2 | 重建 run-outcome + 通知策略？ | **YES（replace 非 patch）** | A.2 load-bearing lie；桩=过渡，typed outcome=SSOT 目标 |
| 3 | Continuity READY 是北极星？ | **NO（demote 为 ops 观测）** | 最大 habit-burn；轴②≠轴① |
| 4 | 废 §15 knife 文化（对 ops）？ | **NO，仅窄 carve-out** | §15 是真护栏；只声明 ops 残差默认非刀，代码刀不动；驳 R2 松门 |
| 5 | 本轮实现？ | **NO 生产重写** | 遵 "default NO production rewrite"；Phase 0 = doc+指针；Phase 1 owner 排期 |
| 6 | 用 DAG/event-bus 泛化编排？ | **NO（触发式，第二次真 kidnap 才泛化）** | architect skill §6「不为想象负载预建」= 多 agent 系统头号死因 |

**判断法典 seeds（沉淀，两语言）**
1. 人话：三条时钟各说各的真话 / 机器话：`run_outcome` typed 三态 + FND-GATE 单 acceptor + ops 残差非刀触发。
2. 人话：状态对象是唯一真相，exit/通知/UI 都是渲染 / 机器话：下游读 `daily_*.json.run_outcome`，禁读 rc 判 FAIL。
3. 人话：ops 残差默认不是代码刀 / 机器话：开 foundation/product knife 须 cite {owner block ∨ named consumer ∨ 轴①失败 gate}。

**死亡条款（本控制面何时算失败）**
- 感知死：run_outcome 与真实 accepted 状态脱钩（软等被标 success 或反之）→ 立即失败。
- 判断死：agent 仍把 continuity 非 READY 当地基重开许可 → 法典未生效。
- 谄媚死：为让某绿灯好看而假报 READY/accepted → 触真金白银红线，abort。

---

## G. 一句话

**改的是「词」和「队列」，不是「地基」**：给 code / data-clock / usage 三条时钟各自 typed 的真话（`run_outcome` 三态 + 单一 FND-GATE 权威 + ops 残差默认非刀），本轮只落文档+指针，代码分 owner 排期的 strangler phase 推进；做成 = **下次日更软等显 SOFT_WAITING 不再假 FAIL/刷屏，且 agent 不再把时钟/诚实翻译成代码刀**。

Label: **Phases 0–3 FIXED**; Phase 4 checklist FIXED / E-F compute BLOCKED. Closeout: `analysis/architecture_fix_treadmill_closeout_20260722.md`. Residual owner: owner usage of viz + optional E/F schedule signature.
