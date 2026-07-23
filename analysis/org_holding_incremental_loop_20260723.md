# org_holding incremental-check-every-run（2026-07-23）

> Status: evidence-only.
> Label: **FIXED** (loop + surface) · mass/by-date invent still **BANNED**
> Owner correction: 「默认不修 PARTIAL」不对；org 不是 eternal BLOCKED。

## 1. 之前为啥还写成 BLOCKED

历史 E0 刀把两件事压成一个词：

| 事实 | 正确语义 | 被误读成 |
|---|---|---|
| aif10 `RPT_MAIN_ORGHOLDDETAIL` = by-period ~830k，无 NOTICE_DATE | **禁** by-date provider land invent；**禁**已落地期 mass refresh | 「org 永远别碰」 |
| F7 typed wall `org_provider_land_blocked` | 守 mass/by-date 红线 | UI/roadmap 写 forever BLOCKED |
| 某次 daily_update skip（plannable 已在 raw） | 披露钟无新期 → **正确 skip** | 「增量路径坏了 / 被挡住」 |

goal.md Q3 其实早已写清：每次更新 **必须 check** latest plannable vs local；缺→拉一期；有→skip。缺口在 **表面**：due_plan / delta_manifest / dossier 文案仍说 BLOCKED，F6 还用「partitions≤8」当 thin wall，像永久忽略。

## 2. 现在每次「数据更新」如何检增量

单一计算点：`services.org_holding_aif10`

1. `org_holding_period_gap_report`：`latest_plannable_report_date`（法定披露截止已过）vs raw + `accepted_partition`
2. `action`：
   - `fetch_then_accept` — raw 缺最新可披露期 → `sync_period`（仅一期）→ `accept_org_holding_partition_from_legacy`
   - `accept_from_local_raw` — raw 有、accepted 无 → 只 accept
   - `skip_current` — 都有 → skip，并写 `next_period` / `next_period_unlock`（披露钟前进，非冻结）
3. Pipeline：`acquire._sync_org_holding` **每次**跑上述路径；结果进 `delta_manifest.acquire_summary.incremental`；gap 落 `data/reports/org_holding_period_gap_latest.json`
4. Workbench：`due_plan` 始终挂 `org_holding` period 行（live RO 或 latest artifact）

**仍禁**：全历史 / 已有期 ~830k re-pull；by-date NOTICE_DATE invent；中间历史洞 auto-fill（显式 backfill 刀另开）。

## 3. 还缺什么（诚实残差）

- 已落地但行数明显偏少的期（例：raw `2026-03-31` ≪ 全市场量级）在 no-refresh 下**不会**自动重拉 — 需显式 repair 刀，不进 daily loop
- 期内晚披露 / 「逐公司扫最新公告」：**不**进 daily（供应商无 NOTICE_DATE；禁 mass）— 裁决见 `shareholder_update_check_design_20260723.md`
- 机构档案 deep-link 已抬至 ~episode；新鲜度靠闭环 process 挂接（见 `serve_derive_closed_loop_law_20260723.md`）
- 人口：partition 存在但 thin/canary → `under_populated_accepted`（不 mass 重拉）
- F7 wall 名仍叫 `org_provider_land_blocked`（兼容 foundation 断言）；语义已改为 mass-ban + incremental-required

## 4. 验证

```bash
PYTHONPATH=backend python -m pytest \
  backend/tests/test_org_holding_aif10.py \
  backend/tests/test_ops_manual_run.py \
  backend/tests/scripts/test_check_foundation_done.py \
  -q
```
