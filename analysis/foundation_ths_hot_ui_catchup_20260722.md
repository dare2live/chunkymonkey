# Foundation ths_hot UI catchup — 2026-07-22

> Evidence-only. Primary path = workbench「数据更新」
> (`POST /api/v3/ops/jobs/daily_update/run`). No agent CLI `chunkyctl sync ths_hot`
> / direct API catchup.
> Related: `plan_residual_reconcile_20260722.md`,
> `foundation_daily_update_ui_click_20260721.md`,
> `foundation_daily_update_degraded_rca_20260721.md`.

## Verdict (gates)

| Gate | Label | Note |
|---|---|---|
| (a) UI path | **PASS** | FE+API user-start; `#/workbench` →「数据更新」×2 accepted |
| (b) Incremental recognition | **PARTIAL** | Preflight sees `sync:ths_hot` wm=`20260720`; **neither run reached `--all-due`** so planner never emitted ths_hot due/fetch |
| (c) ths_hot filled `20260721+` | **FAIL** | Live max still **`20260720`** |

Overall: **FAIL** for catchup objective. Soft-skip WIP unblocked **daily** morning vacuum in Run B, then **stock_st** hard-blocked the same way.

## Pre-click baseline

| Item | Value |
|---|---|
| Wall clock start | 2026-07-22 ~08:53 CST (open day morning) |
| `raw_tushare_ths_hot` max | `20260720` |
| Preflight SLA | `watermark_sla_before_20260722.json`: `sync:ths_hot` wm=`20260720`, `days_ago=2`, `sla_days=2`, `status=NO_PROBE_RULE`, `alert=false` |

## Run A (08:53–09:05) — FAIL rc=5

| Time | Event |
|---|---|
| 08:53:45 | Click; preflight PASS |
| 08:53:50–09:05 | Quiet ~11m: `holders_aif10` → eastmoney + `smartmoney` WAL |
| ~09:05 | holders `wm=20260722 affected=59 rows=742158`; QFII/org skip |
| ~09:05 | Formal daily `eligible_end=20260722` `manual_calendar_eligible` → `zero_rows` |
| 09:05:20 | **TIER0 BLOCK** formal daily hard-fail; **no `--all-due`** |

```text
error='daily capture rejects empty provider rows trade_date=20260722'
(后续阶段未启动; exit 5)
```

## Run B (09:09–09:23) — FAIL rc=5 after daily soft-skip

Disk WIP (`sync_runner` early `pending_publish` + `acquire.py` soft-continue) was loaded by a new `pipeline.run`.

| Time | Event |
|---|---|
| 09:09:47 | Re-click; preflight PASS |
| 09:09:54–09:22 | Quiet ~12m holders again (same `affected=59 rows=742158` — rework) |
| ~09:22 | Formal **daily** `zero_rows` → **`action=pending_publish`** (`pre_available_after_zero_rows`) — soft-continue **PASS** |
| ~09:23 | Formal **stock_st** `eligible_end=20260722` → `zero_rows` |
| 09:23:13 | **TIER0 BLOCK** stock_st hard-fail; **still no `--all-due`** |

```text
{"domain":"daily","action":"pending_publish",...,"pending_publish_reason":"pre_available_after_zero_rows"}
...
error='stock_st capture rejects empty provider rows trade_date=20260722'
(后续阶段未启动; exit 5)
```

Why daily soft-skipped but stock_st did not: `_is_pre_publish_same_day_zero` only fires before registry `available_after` HH:MM.  
- `daily` `available_after=18:00` → 09:xx vacuum = pending.  
- `stock_st` `available_after=09:20` + `same_day_at 09:20` → at **09:23** clock is past window, so empty is **hard-fail**, even though vendor still returns 0.

Post both runs: ths_hot max **`20260720`**.

## 卡点 (measured)

1. **Formal on_demand hard-block before registry drain**  
   Ordering: holders → QFII/org → **daily/ST** → `--all-due`. Any Tier0 fail in formal pair aborts ths_hot catchup of already-published `20260721`.

2. **stock_st `available_after=09:20` too optimistic vs vendor**  
   Run B proved daily soft-skip works; ST still empty after 09:20 → hard rc=5. Mirror of last night’s ths_hot pre-22:30 issue, different domain/clock.

3. **holders_aif10 long opaque window (~11–12m) + duplicate pull**  
   Run B repeated identical 742k-row holders work with no mid-progress logs → UI “日志已 Ns 无新行”.

4. **SLA preflight ≠ planner due signal**  
   ths_hot appears as watermark/`NO_PROBE_RULE`, not as actionable “will pull” plan before click.

5. **Lock contention during holders**  
   Read-only `smartmoney` opens conflict with writer pid — observability/status starved.

6. **Partial soft-skip incomplete**  
   WIP covers daily; stock_st still kill-switches the chain → ths_hot still unreachable via morning UI.

## Optimizations (ranked)

| # | Change | Why |
|---|---|---|
| 1 | **stock_st same-day empty → typed `pending_publish`** when vendor vacuum (even after nominal 09:20 if still zero), or raise `available_after` to measured publish time | Unblocks morning `--all-due` / ths_hot without inventing CLI catchup |
| 2 | Soft-continue acquire for **all** `FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS` pending_publish (daily done; ST missing) | Run B already proved the pattern |
| 3 | Reorder: `--all-due` drain **before** or independent of same-day formal on_demand when formal is pending | Yesterday’s published domains must not wait on today’s empty K/ST |
| 4 | holders heartbeat + skip-if-wm-unchanged / cheaper incremental | Cuts ~11m silent wall + avoids duplicate 742k rewrite |
| 5 | Preflight/UI due-plan panel (domain, wm, eligible, will-pull?) | Makes incremental recognition visible before click |
| 6 | Cap E registry-drain-only when formal pending_publish | UI escape hatch without mass CLI |

## ths_hot — crisp answers

| Question | Answer |
|---|---|
| 前端路径成立? | **是**（两次点击均受理） |
| 是否识别增量? | **水位侧有**（preflight wm=`20260720`）；**本轮 acquire 未跑到 planner**（`--all-due` 未启动） |
| 是否拉到 `20260721`? | **否** |
| filled? | **否** — max=`20260720` |

## Residual

Live catchup residual **remains open**. Next UI re-click only useful after stock_st morning soft-skip (or post real ST publish / after `available_after` aligned). Do not greenwash; do not invent CLI primary catchup.
