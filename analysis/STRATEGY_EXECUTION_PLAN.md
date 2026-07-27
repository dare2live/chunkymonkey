# 后续策略执行方案（Strategy Execution Plan）

> **生命周期**：evidence-only execution roadmap（analysis 层；**非** owner bible）
> Authority chain: `AGENTS.md` → `goal.md` → `docs/strategy_validation_contract.md`（立法）→ **本文件 = 策略「下一步做什么」**
> Gate: foundation exit **MET**；策略实验室 framework-only **ACTIVE**；正式 RX 仍 **BLOCKED until** `goal.md` owner 显式排期且输入契约通过
> Cleanup ledger: `analysis/DOC_CLEANUP_20260723.md`
> Label: **PARTIAL**（本地只读 framework 已开；RX 未开；禁提前寻优/远程计算）

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
| Strategy Lab ingress / compute admission | **PARTIAL (control plane installed)**；live inputs BLOCKED；`manual_only`、`claimable=false` |
| E institution_follow measured | 已归档 `measured_reject_no_gain`；**非**完成迁移 |
| F main_rally F0–F3 ladder | protocol-complete **reject** / `claimable=false`；可 checkpoint |
| BestChoice / G | **FROZEN**；未进消融 |
| H StrategyRelease / 纸面生产候选 | **未开** |
| Owner 对 RX 的显式 schedule | **MISSING** → 整轨 BLOCKED |
| Optuna / α·β 堆叠（Phase N） | **BANNED** until RX 开 + owner 再开 N |
| Modal / remote compute | **BANNED**；无 adapter、无跨节点 holdout owner、无本地 parity / budget evidence |

---

## 3. 开门条件（全部满足才从 BLOCKED → 可执行）

1. **Foundation exit**：`FOUNDATION_EXECUTION_PLAN.md` §6 已 MET；若后续回退，本门自动重关。
2. **`goal.md` 显式一句**：schedule RX / E/F remeasure，并写入与 typed config 一致的 `RX_AUTH=<id>`；Phase N / remote 分别使用 `PHASE_N_AUTH=<id>` / `REMOTE_COMPUTE_AUTH=<id>`，缺一即 fail closed。
3. **同 protocol**：development snapshot 仅含 train/validation；sealed holdout 独立冻结且 worker 不可见；同一 universe / folds / costs / execution；PIT 截断；purged WF；embargo；最终 holdout **one-touch**；T+1；名义价；停牌/涨跌停；成本；unmeasured=`unknown` 永不 0。
4. **因子族 inventory + live exit**（K3/K4）：`backend/config/factor_family_inventory.yaml` + `check_factor_family_inventory.py` + `check_factor_family_gates.py` PASS；随后 `project_factor_family_frontiers.py` 生成绑定 inventory hash 的 fresh artifact，`check_factor_family_frontier_live.py` 必须 PASS（DB missing、query error、UNVERIFIED、stale 均阻断）；B3/B4 未满足 gate 时只允许 `inconclusive`，禁 pad-0。
5. **禁令未破**（§5）。

Checklist（原 phase4 gate 折叠）：

- [ ] owner signature in `goal.md`
- [ ] foundation exit or explicit skip
- [ ] factor_family inventory + continuity gates PASS（K1–K4）
- [ ] fresh development DatasetSnapshot（nominal accepted generation 严格截止 holdout 前）+ sealed holdout opaque ref + research_prereg_v1（param_hash + stable holdout scope single-touch）
- [ ] canonical pointer/content、逐日 universe membership、availability、Tier1/Tier2/disclosure generation/hash evidence
- [ ] no Optuna / no Release / no holdout loosen in the remeasure knife
- [ ] margin stays product-trust-gated；org mass still banned
- [ ] verdict artifact path + `FIXED|reject` 诚实标签

---

## 4. 有序 TODO

| # | 项 | Exit criteria |
|---|---|---|
| **S0** | Strategy Lab 本地框架 | **PARTIAL**：development-only bundle + local-smoke control plane 已落；当前两份 live freeze 均被拒绝；formal validators / evaluator / artifacts 未实现 |
| **S1** | **RX-E** institution_follow 同 protocol remeasure | `ExperimentVerdict`；诚实 reject 亦算交付；≠ Release |
| **S2** | **RX-F** main_rally 同 protocol remeasure（相对 B0 holdout lift 门不变） | 同上；可与 S1 分刀，禁松门求绿 |
| **S3** | **G** 公式 / BestChoice 挑战（仅 RX 后） | B5 消融块；仍禁无 Release 出候选 |
| **S4** | **H** StrategyRelease + 纸面执行 | 仅 released 出生产候选；名义+成本硬约束 |
| **S5** | **Phase N** Optuna / 因子堆叠 | **另需** owner 显式开 N；搜索空间非空；后果穿透；底座验收未回退 |

默认序：S1 → S2 → S3 → S4；S5 最后且双签字（RX + N）。

---

## 4.1 Strategy Lab 边界与计算准入

唯一数据入口：

```text
accepted canonical
  → frozen DatasetSnapshot + accepted/hash evidence
  → read-only ResearchInputBundle
  → prereg / folds / cost / execution
  → pure local trial evaluator
  → immutable per-trial artifact
  → single-owner reducer
  → ExperimentVerdict
```

`ResearchInputBundle` 只接受 development snapshot，并在构造时拒绝任何 sealed-holdout partition 或 Tier3 label input；对象本身只含 train/validation/immutable refs，因此 worker 无第二条序列化路径可偷看 sealed data。Tier3 标签以后只能由独立 PIT evaluator 读取，不能进入 candidate generator。正式 reader 还必须补齐 canonical pointer/content、逐日 universe、availability、Tier1/Tier2 与 disclosure generation/hash；只写一个 `universe_id` 字符串不算冻结。

计算优先级：

1. **现在**：local、manual、read-only smoke；`claimable=false`。
2. **RX**：`goal.md` 排期 + fresh development freeze + sealed holdout ref + 全部证据 + purged WF 后才开。
3. **Optuna**：RX 之外另开 Phase N；非空 search space 必须逐项改变 behavior hash；objective 只读 development validation；先用 5–10 个本地 trial 测量成本/收益。
4. **Modal**：最后才做薄 compute adapter。前提是同 bundle/spec 本地与远端结果 hash 0 diff、只读 bundle、无项目 DB/provider/holdout ledger 权限、明确 timeout/budget/cancel/retry；单一 controller reduce。没有这些证据时不增加 Modal 依赖或配置。

机器门：`PYTHONPATH=backend python3 backend/scripts/check_strategy_lab.py --framework --json`。control plane 已安装但任一 live input 不合格时返回非零；不得把 installed 当 ready。

当前 residual（按优先级）：

- **P0**：纠正 development validation vs sealed holdout 语义；main-rally freeze 增加 cutoff；重建两类合格 freeze。
- **P1**：泛化 nominal binder 到 universe、Tier1/Tier2、disclosure；正式模式禁 B1 legacy fallback，B2 接 accepted MarketContext。
- **P2**：实现纯 `evaluate_trial(bundle, spec)` + 独立 trial artifact/reducer，并做便宜 B0/B1。
- **P3**：RX 过门后才评估 Optuna；本地 benchmark 证明值得并行后才评估 Modal。
- **P4**：跨节点 CAS holdout owner 完成后，才允许远程 worker 与最终 single-touch evaluator 共存。

---

## 5. 硬禁令（策略侧，始终有效）

| 禁 | 为何 |
|---|---|
| Optuna / 付费寻优 / 全期 fit | Phase N；底座未 exit / RX 未开 = 死线 |
| Modal 读取项目 DuckDB、共享 JSON 或 holdout ledger | 远程多 writer / single-touch 假闭合 |
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
