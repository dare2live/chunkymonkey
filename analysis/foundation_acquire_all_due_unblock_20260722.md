# Foundation acquire `--all-due` unblock — 2026-07-22

> Status: evidence-only
> Follow-up from `foundation_ths_hot_ui_catchup_20260722.md` (`e56fc7aef`).
> Primary path = workbench「数据更新」.
> Adversarial: [FOR rebuild](408a0846-2920-4080-bac3-e91c36f0233c) vs
> [AGAINST / patch](6b02b058-20f3-43d0-b4c0-c81126113fd8).

## Root cause（白话）

**不是「stock_st 的 09:20 写错了」这么简单。**

真正根因是 **acquire 编排形状**：

1. Knife（`foundation_daily_update_unblock_20260721`）为了补 S3「on_demand 的 daily/ST 不进 `--all-due`」，在编排器里加了
   `_sync_formal_on_demand_security_days`，并放在 `--all-due` **前面**。
2. 该步把单域 `land_then_accept` 失败写成 **`raise Tier0AcquireError`**。
3. `pipeline.run` 对 `Tier0AcquireError` 直接 **exit 5**，标注「后续阶段未启动」——
   整段 acquire（含 registry drain）和 clean/process/store 全停。

于是「今日 K/ST vendor 真空」被误当成「整条 Tier0 地基不可用」，
**已发布域的增量（ths_hot wm=`20260720`）被绑架，planner 从未启动。**

这与 S3 / strangler「编排器依次调用模块、每步可单独重跑、禁止在 sync 里再焊新龙」意图相反：
模块化 land/accept 已拆开，但编排器仍用 fused 硬门把兄弟域串死。

触发条件（症状）：`stock_st` `available_after=09:20` 过乐观 → 09:23 仍 `zero_rows` → 硬失败。
根因（机制）：编排器把单域失败升级为全链 abort。

## Rebuild vs patch — 裁决

| Option | Claim | Verdict |
|---|---|---|
| Narrow `if stock_st: pending_publish` only | Unblocks morning ST | **Paper** if ordering/raise 不变 — 下一域时钟漂移复现 |
| Typed `pending_publish` + soft-continue only (`911786247`) | Domain contract for vacuum | **必要但不够** — 仍依赖「别 raise」；硬失败仍可绑架 |
| **Structural rebuild（本刀）** | Drain 与 formal 解耦；formal 域内 fail-closed | **ADOPT** |

**Adopted shape（Occam structural，非新 DAG/plugin）：**

1. **`--all-due` drain 先跑**（published automatic domains 不依赖今日 formal 空窗）。
2. **formal on_demand catchup 后跑**；`pending_publish` = soft；硬失败 = `ctx.degraded` + 继续 sibling，**不再 `raise Tier0AcquireError`**（wiring 错仍 raise）。
3. 保留 sync_runner 侧 typed `pending_publish`（含 stock_st same-day vacuum）作为**域契约表达**，不是唯一解阻手段；编排器不再依赖「猜对每个域的 HH:MM」。
4. **不做**通用 stage framework / event-bus；due-plan UI 与 holders skip 保留为 observability，非平行真相。

为何不是「只 patch」：owner 明确要求找根因并重建错误形状；对抗 FOR 指出 raise-before-drain 未动则必复发。
为何不是「大重建」：对抗 AGAINST 正确 — 新 stage 框架有 dual-writer / margin 门 / 假依赖风险；最小结构改动即可。

## Changes

| # | Change | Label |
|---|---|---|
| R1 | `run_acquire`: drain **before** formal on_demand | **FIXED** structural |
| R2 | formal hard-fail → degrade + continue（不 raise / 不 exit 5） | **FIXED** structural |
| R3 | typed `pending_publish` for same-day vacuum（daily pre-window；stock_st same-day） | **FIXED** contract（retained） |
| R4 | holders skip+heartbeat；workbench due-plan preview | **FIXED** small |
| T | Regression: drain before formal；formal hard does not raise；sibling continues | **FIXED** |

## Tests

- `test_acquire_runs_registry_drain_before_formal_and_despite_formal_hard`
- `test_formal_hard_fail_degrades_not_raises_and_continues_sibling`
- `test_formal_on_demand_catchup_soft_skips_pending_publish`
- `test_formal_security_day_same_day_empty_after_window_is_pending`
- `test_formal_daily_after_window_empty_still_fail_closed`
- `test_run_acquire_wires_active_stock_refresh_step`（order: drain < formal）

## Live verification (UI)

Re-click「数据更新」after rebuild lands. Expect:

1. Log shows `--all-due --drain` **before** formal daily/ST catchup lines
2. Formal may print `pending_publish` / `failed`+degraded — **not** `TIER0 BLOCK … 后续阶段未启动` from formal alone before drain
3. Planner can list/attempt `ths_hot`（wm=`20260720`）；fetch may still be pending if pre-22:30 — OK if recognized

## Residual

- Measure real `stock_st` publish clock → raise `availability_policy.at` when known
- `ths_hot` live fill past `20260720` still clock/ops
- holders probe empty-filter returned 0 → `provider_max=None` rewrite amp; **FIXED** bounded `UPDATE_DATE>=` probe (measured)
- Formal hard-fail no longer aborts clean/process — intentional； continuity/SLA still fail-closed on truth
