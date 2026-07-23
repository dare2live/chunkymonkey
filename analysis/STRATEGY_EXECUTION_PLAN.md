# 后续策略执行方案（Strategy Execution Plan）

> **生命周期**：evidence-only execution roadmap（analysis 层；**非** owner bible）
> Authority chain: `AGENTS.md` → `goal.md` → `docs/strategy_validation_contract.md`（立法）→ **本文件 = 策略「下一步做什么」**
> Gate: **BLOCKED until** `analysis/FOUNDATION_EXECUTION_PLAN.md` §6 foundation exit（或 owner 显式签字跳过并承担后果）
> Cleanup ledger: `analysis/DOC_CLEANUP_20260723.md`
> Label: **BLOCKED backlog**（RX 未开；禁提前寻优）

---

## 0. 定位

| 是 | 不是 |
|---|---|
| RX（E/F remeasure）及之后 G/H / Phase N 的**唯一执行 backlog** | 不替代 `docs/strategy_validation_contract.md` |
| 底座之上的研究/发布轨 | 不回头改 Tier0 真相；不与 FOUNDATION 抢 cleanup 刀 |
| 明确 bans + 开门条件 | 不是「主方案」；无支线分类 |

研究立法仍只认：`docs/strategy_validation_contract.md`（B0→B5、PIT、purged WF、holdout、T+1、名义价、成本）。

---

## 1. Supersession

| 旧文件 / 外部 | 角色曾是 | 处置 |
|---|---|---|
| `~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md` §E–H / 策略阶梯 | 原整体方案研究轨 | → 本文件 |
| `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7 RX / Phase N | living roadmap 策略段 | **superseded** |
| `forward_program_efgh_20260720.md` | 旧 A→H 附录 | **deleted** |
| `cx_closeout_rx_honesty_20260723.md` | CX 完 + RX honesty | **folded** |
| `phase4_ef_schedule_gate_honesty_20260722.md` | E/F schedule gate | **folded** §3 |
| `overall_plan_completion_audit_20260723.md` R5+ | 审计残留 | **folded** |

---

## 2. 现状（诚实）

| 项 | 状态 |
|---|---|
| CX-1…CX-4 | **PASS**（能力门闭合 ≠ Continuity READY ≠ 开 Phase N） |
| Phase D research_runtime | FIXED |
| E institution_follow measured | 已归档 `measured_reject_no_gain`；**非**完成迁移 |
| F main_rally F0–F3 ladder | protocol-complete **reject** / `claimable=false`；可 checkpoint |
| BestChoice / G | **FROZEN**；未进消融 |
| H StrategyRelease / 纸面生产候选 | **未开** |
| Owner 对 RX 的显式 schedule | **MISSING** → 整轨 BLOCKED |
| Optuna / α·β 堆叠（Phase N） | **BANNED** until RX 开 + owner 再开 N |

---

## 3. 开门条件（全部满足才从 BLOCKED → 可执行）

1. **Foundation exit**：`FOUNDATION_EXECUTION_PLAN.md` §6，或 owner 书面 skip。
2. **`goal.md` 显式一句**：schedule RX / E/F remeasure（日期或「现在开」）。
3. **同 protocol**：冻结同一 `DatasetSnapshot` / universe / folds / costs / execution；PIT 截断；purged WF；embargo；holdout **one-touch**；T+1；名义价；停牌/涨跌停；成本；unmeasured=`unknown` 永不 0。
4. **禁令未破**（§5）。

Checklist（原 phase4 gate 折叠）：

- [ ] owner signature in `goal.md`
- [ ] foundation exit or explicit skip
- [ ] no Optuna / no Release / no holdout loosen in the remeasure knife
- [ ] margin stays product-trust-gated；org mass still banned
- [ ] verdict artifact path + `FIXED|reject` 诚实标签

---

## 4. 有序 TODO（开门后）

| # | 项 | Exit criteria |
|---|---|---|
| **S1** | **RX-E** institution_follow 同 protocol remeasure | `ExperimentVerdict`；诚实 reject 亦算交付；≠ Release |
| **S2** | **RX-F** main_rally 同 protocol remeasure（相对 B0 holdout lift 门不变） | 同上；可与 S1 分刀，禁松门求绿 |
| **S3** | **G** 公式 / BestChoice 挑战（仅 RX 后） | B5 消融块；仍禁无 Release 出候选 |
| **S4** | **H** StrategyRelease + 纸面执行 | 仅 released 出生产候选；名义+成本硬约束 |
| **S5** | **Phase N** Optuna / 因子堆叠 | **另需** owner 显式开 N；搜索空间非空；后果穿透；底座验收未回退 |

默认序：S1 → S2 → S3 → S4；S5 最后且双签字（RX + N）。

---

## 5. 硬禁令（策略侧，始终有效）

| 禁 | 为何 |
|---|---|
| Optuna / 付费寻优 / 全期 fit | Phase N；底座未 exit / RX 未开 = 死线 |
| 松 holdout / 多次触碰 holdout | 数字游戏 |
| StrategyRelease 前出生产候选 | Tier4 纪律 |
| margin thaw / Continuity 洗绿当研究许可 | 真金白银 |
| org mass / by-date invent | Tier0 |
| qfq 当成交价 | 执行真相 |
| 把策略结论回写 Tier0 accepted | 依赖只向下 |
| 无 foundation exit 擅自开 RX | 地基优先 |

---

## 6. 交付纪律

同 §15 / Rule10 / `safe_commit`；研究刀另守 `strategy_validation_contract.md`。完成标签：`FIXED|PARTIAL|BLOCKED` + residual owner。

**一句话**：策略何时可开 = **foundation exit + `goal.md` 显式 schedule RX**；在此之前本文件只作禁令与清单，不开刀。
