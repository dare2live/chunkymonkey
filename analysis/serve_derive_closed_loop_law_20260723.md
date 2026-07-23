# Serve→Derive 闭环立法（2026-07-23）

> Status: evidence-only / **live law**（分析层；不改 `docs/MASTER` north star）  
> Authority: owner「开始闭环立法」+ `$mio` / `$architect-controller`  
> Evidence parents: adversarial A/B 20260723 · episode lag A+C · treadmill three-clocks  
> Machine inventory: `backend/config/serve_derive_closed_loop.yaml`

---

## 0. 创世层（≤3 句）

1. **存在目的**：凡产品 serve 面依赖的派生（wipeable L1/L2），必须与 accepted 源在同一日更闭环内保持新鲜度，或诚实标 BLOCKED/manual——禁止用「partition 存在 / soft 绿灯」冒充完成。  
2. **死亡条款**：感知死 = 门禁只查 existence；判断死 = 又把 integrity 叙事成「等时钟」；谄媚死 = 为绿而降人口/新鲜度门槛。  
3. **第一目标**：机构档案挂 process + org 人口门 + `integrity_observe` 分桶 — 停住「land 了但档案/人口假绿」这类复发。

---

## 1. 判断法典（人话 + 机话）

| # | 人话 | 机话 |
|---|---|---|
| L1 | 运输完成 ≠ 产品新鲜 | `accepted_partition` 存在 **不蕴含** `process_plan` 派生已追上 |
| L2 | 存在 ≠ 人口 | `skip_current` 仅当 population gate PASS；canary → `under_populated_accepted` |
| L3 | 时钟 ≠ 完整 | `run_outcome ∈ {success, soft_waiting_clock, integrity_observe, hard_fail}` |
| L4 | 未接线不许称 FIXED-fresh | inventory `status` ∈ wired\* \| population_gated \| blocked_manual |
| L5 | 禁 mass 仍须诚实 | org 人口洞 → repair 刀 / 观测；**不** daily 830k 重拉 |

---

## 2. Serve→derive 清单（摘要）

完整表见 YAML。本刀强制变更：

| Surface | 旧 | 新 |
|---|---|---|
| `institution_profile_dossier` | manual `rebuild_all` | **daily process delta-gated** |
| `org_holding_formal` | existence skip = ok | **population_gated**（薄接受 ≠ ok） |
| outcome 叙事 | continuity → soft_waiting | **→ integrity_observe** |

已接线保持：pulse / segments / form / DC delta / qfq via clean。

---

## 3. 本刀落地

| 构件 | 路径 |
|---|---|
| Config | `backend/config/serve_derive_closed_loop.yaml` |
| Decide helpers | `backend/services/pipeline/closed_loop.py` |
| Process wiring | `delta_manifest.plan_process_steps` + `process.py` |
| Org population | `org_holding_period_gap_report` status |
| Outcome | `run_outcome.py` + workbench/ops types |
| Tests | `test_pipeline_closed_loop.py` + org/run_outcome 增补 |

---

## 4. 验收

1. `plan_process_steps` 含 `institution_profile`；holders frontier 前进 → `action=run`；不变 → `skip`  
2. org gap：2-stock canary + dense raw → `under_populated_accepted`（仍 `skip_current`，无 mass）  
3. `continuity/integrity FAIL` → `run_outcome=integrity_observe`（label 非「等时钟」）  
4. 单元测试绿；不重开 Optuna / Continuity READY / org invent

---

## 5. Residual（诚实）

- org **期内晚披露 repair** 仍另刀（禁 daily 全市场扫）  
- F6 未因 canary 直接 FAIL（避免假拆 phase_closure）；人口诚实进 gap/acquire degrade  
- `by_trade_date` equal-day 仍 evidence-gated  
- inventory 其它 surface 漂移 → 扩 YAML + check，不另起平行法

---

## 6. Label

**FIXED（立法 + 首批强制）** — 闭环不变量已成文并接线；下一刀只修 inventory 增项或 org repair，不再「发现滞后再手工 rebuild」。
