# Foundation UI click verify — after drain stream + probe-first clocks (2026-07-22)

> Evidence-only. Owner「点吧」. Primary path = workbench「数据更新」
> (`http://127.0.0.1:5173/app/#/workbench` → button →
> `POST /api/v3/ops/jobs/daily_update/run`). No agent CLI sync as primary pull.
> Fixes under test: `56d5e3835` (drain stream + manual probe-first clocks);
> taxonomy `c1efecc78` (landed on HEAD; not exercised by this UI path).

Wall clock: **2026-07-22 20:41:10 → 20:48:33 CST** (~7.3 min). Post-close ≈20:37+.

## Verdict

**PARTIAL** — landed drain/clock fixes **PASS** under live UI click; full job is not green (`run_outcome=soft_waiting_clock`, rc=1, 3 ops observes). Taxonomy commit present on HEAD but **not** UI-exercised tonight.

| # | Check | Result |
|---|--------|--------|
| 1 | Drain live `[drain i/N]` / domain progress (not 40min black box) | **PASS** |
| 2 | Incremental recognition + post-close probe (daily / `ths_hot`) | **PASS** |
| 3 | `run_outcome` typed (`soft_waiting_clock` / `hard_fail` / `success`) | **PASS** (terminal); mid-run API briefly showed stale `hard_fail` while still running |
| 4 | Modular chain continues clean→process | **PASS** |
| 5 | Taxonomy `c1efecc78` | **Landed** (ancestor of HEAD); **N/A** for daily_update click |

## Click / observe method

- Vite `127.0.0.1:5173` + backend `start.command` `:8000` (were down; started for this verify).
- Cursor browser MCP flaked (tab vanish); **same frontend button** clicked via Playwright CLI (`getByRole('button', { name: '数据更新' })`) — still UI POST, not CLI sync.
- Observe: `GET /api/v3/ops/jobs/daily_update` (`current_activity`, `run_outcome`) + `/tmp/chunkymonkey_daily_update.log` + dated `/tmp/chunkymonkey_daily_update_20260722.log` + `data/reports/daily_20260722.json`.

Job accepted: wrapper/`pipeline.run` **pid=16662**; preflight PASS (policy 42 / calendar / auth).

## 1. Drain streaming (`56d5e3835`)

Live stderr markers in parent log from **20:41:25** onward, e.g.:

```text
[drain 1/42] domain=moneyflow …
…
[drain 23/42] domain=ths_hot …
…
[drain 42/42] domain=stk_holdertrade …
```

`current_activity.progress_line` tracked `[drain i/42]` while acquire ran. Monitor samples with drain progress: `log_age_s` ∈ **[0.5, 23.0]**, **`stale_log=false`** throughout drain (max age during `ths_hot` probe wait). Wall drain ≈ **20:41:25→20:44:14** (~3 min), not a silent 40min buffer.

Residual degrade after drain (expected soft, not stream bug): stdout JSON shows `ths_hot` `pending_publish` + `fina_mainbz`/`express`/`stk_holdernumber` `status=unsupported` → `DEGRADED: sync_registry drain 有残余缺口或域错误` → acquire `check_fail`, chain continued.

## 2. Probe-first clocks + incremental (post-close ~20:37)

| Domain / path | Evidence |
|---|---|
| formal **daily** | `action=skip` `reason=latest_eligible_already_accepted` `eligible_end=20260722` `eligibility_reason=manual_calendar_eligible` |
| formal **stock_st** | same skip / `manual_calendar_eligible` / `20260722` |
| **ths_hot** | **probed** (`manual_calendar_eligible`, `eligible_end=20260722`); `zero_rows` → `pending_publish` `pre_available_after_zero_rows` (soft; window 22:30 not yet) |
| same-day pulls | e.g. `moneyflow_mkt_dc` / `limit_cpt_list` / `moneyflow_hsgt` / `index_daily_benchmark` / `index_dailybasic` with `manual_calendar_eligible` + rows for `20260722` |
| incremental | `fina_indicator` skip-already-latest; org gap check `plannable=2026-03-31` local present → skip fetch (not mass) |

Manual path did **not** hard-wait clocks before probe. Matches fix intent.

## 3. `run_outcome` typing

Terminal report `data/reports/daily_20260722.json`:

- `run_outcome` = **`soft_waiting_clock`**
- `run_outcome_reason` = `soft_waiting_clock_with_ops_observe`
- `run_outcome_exit_code` = 1
- classified: drain residual → `soft`; continuity FAIL → `other`; watermark SLA → `other`

Log: `=== daily_update DONE soft_waiting_clock (3 项; exit 1) ===` + skip FAIL macOS notification (outcome-keyed).

Caveat: while job still running, job API sometimes exposed top-level `run_outcome=hard_fail` / `hard_tier0` before terminal rewrite — final idle state corrected to soft_waiting. Not a misclassification of the finished run.

## 4. Modular chain clean→process

```text
[stage_status] acquire → check_fail
=== ② 清洗 CLEAN === … clean → check_pass (gate=PASS)
=== ③ 加工 PROCESS === … process → check_pass
=== ④ 存储 STORE === … store → check_fail  (continuity + watermark SLA)
```

Acquire degrade did **not** abort clean/process. Process had a ~3.5 min quiet window after DC segments (log stale mid-process) — orthogonal to drain-stream fix.

## 5. Taxonomy `c1efecc78`

`git merge-base --is-ancestor c1efecc78 HEAD` → true. SW industry PIT one-active-L1 view is **not** invoked by workbench daily_update; no live UI proof tonight beyond commit presence.

## Artifacts

- Log: `/tmp/chunkymonkey_daily_update.log` + `/tmp/chunkymonkey_daily_update_20260722.log`
- Report: `data/reports/daily_20260722.json`
- Continuity: `data/audit/continuity_20260722.json` + `/tmp/chunkymonkey_ALERT_continuity.flag`
- Watermark SLA: `data/audit/watermark_sla_20260722.json`
- Design note for the fix: `analysis/business_clock_and_drain_rework_20260722.md`

## Label

**PARTIAL** — UI click verified drain live-stream + manual probe-first clocks + typed terminal `soft_waiting_clock` + clean→process continuation. Residual: ops continuity/SLA, drain unsupported/pending softs, mid-run outcome flicker, taxonomy not on this path.
