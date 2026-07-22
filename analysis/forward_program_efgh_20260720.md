# Forward program — E/F rejects → G/H gates (2026-07-20)

> **SUPERSEDED as roadmap authority** by `analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7 RX（+ `DOC_AUTHORITY_20260722.md`）。本文件保留为旧 A→H 研究轨附录 evidence。

> 生命周期：历史证据（evidence-only）。owner-facing program draft，非产品 KPI 宣称；
> 不拥有执法（以 `goal.md` 为准）；不发明 claimable/Release。
> **Superseded near-term ordering (2026-07-21)**：近端排序见 `goal.md` +
> `analysis/plan_reeval_first_principles_20260720.md`（transport S1–S7 → E0 → E/F
> remeasure）。本文 = E/F/G/H **后置研究地图**，非 agent 启动菜单。
> 依据：`~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md` §3/§5.3；
> `goal.md`；`data/lineage/phase_{e,f}_experiment_verdicts/`；
> ledger 2026-07-19→20；strangler commit `cba8063fd`；
> `docs/strategy_validation_contract.md` §8–9；`bestchoice/FROZEN.md`。

## 0. 两层完成定义（必须先分清）

| 层 | 含义 | 当前态 |
|---|---|---|
| **Campaign protocol-complete** | A→H 各 Phase 按契约跑完可证伪切片：snapshot/runtime/消融/verdict 落盘；reject/inconclusive 算交付 | E/F **已达**（诚实 reject）；G/H **未开** |
| **Product A→H complete** | 至少一条 `ExperimentVerdict(accept, claimable=true)` → `StrategyRelease` → Tier4 候选/纸面 | **未达**；H 机械封锁 |

协议成功 ≠ 边缘成功。`claimable_protocol=true` 只证明门与测量链闭合；
`claimable=false` / `verdict=reject` 是边缘失败，**不是「差一点」**。

## 1. 因果链（为何「G/H 未开，E/F 全是诚实 reject，无 Release」）

### 1.1 Plan 出口门（摘录）

- **E**：`ExperimentVerdict` accept/reject/inconclusive；无 `StrategyRelease` 不得出正式候选  
- **F/G**：同 D/E 门 + 包特定 GT/公式契约  
- **H**：仅 `StrategyRelease` 后出候选；paper = 名义价 + T+1 + 涨跌停

### 1.2 实测（artifacts）

- E manifest `overall.status=measured_reject_no_gain`；`any_claimable=false`；
  `strategy_release=false`  
  - B0/B1/B2 = `reject` / `measured_protocol_ready_edge_gates_unmet`  
  - B4 = `inconclusive` / `b4_disclosure_event_coverage_insufficient`  
  - 120d 窗 `20260116`–`20260717`（ledger 2026-07-20）
- F manifest `overall.status=measured_reject_or_inconclusive_setup_entry`；
  `any_claimable=false`；`strategy_release=false`；`slices_complete=[F0..F3]`  
  - B0/B1/B2 = `reject` / `measured_protocol_ready_edge_gates_unmet`  
  - 121d 窗 `20260116`–`20260720`；full-episode **未尝试**

### 1.3 链

```text
measured edge gates unmet / holdout lift unmet / coverage insufficient
  → ExperimentVerdict = reject|inconclusive
  → claimable = false
  → StrategyRelease 禁止（strategy_validation_contract §9 要 accept）
  → Phase H 出口条件不可满足 → H BLOCKED
```

### 1.4 为何 G 未开（非单一原因）

1. **测序**：plan `E → F → G → H`；F0–F3 今日才 checkpoint（ledger F3）。  
2. **产品偏序**：无 claimable 包时开 G，只会再堆一条研究包，不推进 Product A→H。  
3. **BestChoice 冻结**：G = 公式包 + BestChoice 对决；`bestchoice/FROZEN.md` =
   `FROZEN_CHALLENGER`；禁直接并入主策略（MASTER §9 / validation §8.3）。  
4. **goal 现行下一刀**：更长窗 remeasure（**BLOCKED**：窗=accepted 前沿，
   不能加速）或新 package/ablation（B3+ 须先证必要性）——**不是**默认开 G。  
5. **禁令**：Optuna / E 松门 / StrategyRelease 仍硬禁。

## 2. Strangler 裁决何时写入

| 项 | 值 |
|---|---|
| SHA | `cba8063fdb2e87aea570492d5090fa9a01dbcc0a`（短 `cba8063fd`） |
| 日期 | 2026-07-20 22:22:49 +0800 |
| 编码处 | `goal.md`「已裁决」+「禁令」；ledger 同日条目；BOARD 再生 |
| 内容 | Product + Agent-OS = **strangler + 聚焦**，非 greenfield；三杠杆（resolver SSOT / pytest=CI SSOT / god-seam）；禁第二 DB、plugin bus、dual-write、残破感当重写许可证 |

## 3. 一体化前向程序（单一路径，非菜单）

程序名：**「先养窗与币值 → 再选唯一研究下注 → 仅 accept 后开 Release/H；G 为第三研究包且受 BestChoice 冻结约束；WP6 与数据面并行但互不洗绿」**。

### Phase P0 — 数据币值护栏（持续，与研究串行写点）

**做什么**（不与并行 dual-track agent 抢同一文件集则并行观察即可）：

- 每个自然交易日收盘后 eligible：`chunkyctl sync --domain daily|stock_st` +
  Tier1/2 accept；frontier 跟墙钟，**禁止**为扩窗 mass backfill。  
- dual-track：residual 已 NONE（`legacy_retire_notes.md` 2026-07-20）；
  仅在新增 router/service 时复扫，不作持续 blocker。

**退出 / 停机**：

- P0 永不「完成产品」；仅提供 E/F remeasure 的输入币值。  
- sync `operation_window_blocked` = 诚实停，不是失败伪装。

### Phase P1 — E/F 处置（唯一合法研究动作，直到决策点 D1）

**默认动作**（已写入 goal）：在 **自然扩大的 accepted nominal 窗** 上，
**不改阈值**，按同一 protocol 复测 E ladder（B0/B1/B2/B4）与 F ladder
（B0/B1/B2）。

**禁止**：松 edge gate；Optuna；把 reject 改写成 partial-success；
full-episode 250d 宣称（需数月日历，见 goal BLOCKED）。

**决策点 D1（owner，程序内置，非「你再选」）** — 首次出现以下之一即触发：

| 触发 | 裁决（绑定） |
|---|---|
| 任一块 `accept` + `claimable=true` 且 holdout lift 过门 | 进入 **P3 Release 预备**（该包） |
| 窗再扩 ≥40 交易日仍全员 reject/inconclusive | 进入 **P2 换假设**（关闭本窗下注） |
| owner 书面授权例外 backfill 扩历史窗 | 仅允许授权范围内 remeasure；仍禁松门 |

在 D1 之前：**不开 G，不开 H，不 StrategyRelease**。

### Phase P2 — 换假设（仅 D1「全员仍 reject」路径）

一次性、单一下注（不是菜单并行）：

1. 书面假说：缺的是哪一块（B3 资金活动 / 新 signal 定义 / 披露覆盖 /
   setup≠episode）——**只选一条**。  
2. 新 `DatasetSnapshot` + 单 FeatureBlock 消融；共享 E/F runtime。  
3. 同门测量 → 新 verdict artifact。

**G 允许开的条件（硬）** — 全部满足：

- F0–F3 已 checkpoint（已满足）；  
- D1 已走过且选择「第三策略包 = formulas」为唯一下注（不是顺手加戏）；  
- BestChoice 仅 namespaced 重放 + lineage/PIT/本项目 paper；  
  **禁止**解冻并入主表、禁止旧 Optuna KPI 当 edge；  
- 仍无 StrategyRelease，除非该公式包自身 `accept`+`claimable=true`。

**停机**：假说证伪 → 再走 D1 同类闸；禁止无绿叠加 B3+B4+B5。

### Phase P3 — Release / H（仅 claimable accept 之后）

前置：`strategy_validation_contract` §9 九条全过 + 明确 `accept`。

然后且仅然后：

- `StrategyRelease(<package>_v1)`  
- Phase H：DecisionBatch / 名义价纸面 / 观察账本隔离  
- UI 从 `research_evidence_only` 升到 released 面（带 surface_status）

**无 accept → H 永不开**（机械，非优先级问题）。

### Phase P4 — Agent-OS WP6（旁路轨道）

- 位置：与 P0–P3 **并行旁路**；**不**阻塞、**不**洗绿 A→H / claimable。  
- 政策：`engineering_governance` §13 — 影子期 10 session 或 14 天先到；
  flip = owner-gated + 门 parity 空 diff。  
- 退出：影子检查单全绿 → 删旧 boot 仪式；回归则重置影子期。

## 4. 硬禁（全程有效）

静默 cutover / 无证据回翻 cutover；Optuna；E/F 松门；无 accept 的
StrategyRelease；margin thaw；mass backfill；plugin bus；第二 DB；
dual-write 迁移窗；greenfield 重写（strangler 裁决）；agent 自降 commit tier；
用 continuity/工具绿洗 live readiness。

## 5. 一句话状态

E/F = **协议已闭合的边缘失败**（可 checkpoint）；G = **测序上可开但产品上未授权**
（BestChoice 冻 + 无 claimable 下注）；H = **被 claimable=false 机械封锁**；
下一步程序 = **养窗复测 → D1 →（accept→Release/H）或（换单一假说；G 仅当假说选定 formulas）**；
数据币值与 WP6 旁路并行。
