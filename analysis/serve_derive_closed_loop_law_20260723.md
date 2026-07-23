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

## 5. Residual closure（2026-07-23 follow-up）

| 项 | 状态 |
|---|---|
| org under_populated repair | **FIXED** — dense raw → `repair_accept_from_local_raw`；thin raw → `repair_fetch_period`（单期） |
| F6 org population floor | **FIXED** — `min_org_accepted_stocks`（默认 500）；canary FAIL |
| institution as_of surprise rebuild | **FIXED** — `seed_institution_as_of_from_holders` |
| 期内晚披露 / by-date invent | 仍 **BANNED**（非本闭环残差；见 shareholder_update_check） |
| `by_trade_date` equal-day | 仍 evidence-gated（frontier 刀残差，非本闭环） |

---

## 6. Label

**FIXED（立法 + 强制 + 残差收口）** — serve→derive 闭环可验收；org canary 不可再假绿；档案 as_of 已可种子化。
