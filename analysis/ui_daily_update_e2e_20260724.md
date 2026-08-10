# UI daily_update E2E — 2026-07-24

> Status: evidence-only

**Verdict: PASS** (pipeline + report SSOT). **UI post-run poll: PARTIAL** (API blocked on DuckDB while unrelated org trunc-repair holds the file).

## Setup

| Step | Result |
|------|--------|
| Org trunc repair SIGTERM | PID 15969 already gone; **SIGTERM PID 17146** (`org_holding_period_repair_truncated.py --max-periods 23`); exited ~2s; DuckDB free for daily |
| Backend | `cd backend && ../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000` |
| Frontend | `cd frontend && npm run dev` → `http://localhost:5173/app/#/workbench` |
| Browser | **cursor-ide-browser MCP** could not attach tab; **Playwright CLI** used for real UI click |

## UI path (verified)

1. `#/workbench` → tab「一键更新」
2. Click **「数据更新」** (`button` ref `e22` / `getByRole('button', { name: '数据更新' })`)
3. UI disabled to「更新进行中…」; API showed `running=true`, `owner=pipeline.run`, `owner_pid=17876`, phase `preflight` → `acquire` …

Same backend path as: `POST /api/v3/ops/jobs/daily_update/run` via `manual_job_wrapper.py` → `scripts/daily_update.sh`.

## Run window

- **run_date:** `20260724`
- **Started:** 2026-07-24 22:43:04 (+08)
- **Finished:** 2026-07-24 23:42:55 (+08) (~60 min)
- **Log:** `/tmp/chunkymonkey_daily_update.log` (banner + dated copy in report)
- **Report SSOT:** `data/reports/daily_20260724.json`

## run_outcome (typed)

| Field | Value |
|-------|--------|
| `run_outcome` | **`success`** |
| `run_outcome_label` | 成功 |
| `run_outcome_reason` | `clean_success` |
| `run_outcome_exit_code` | 0 |
| `degraded_total` | 0 |
| Log closing line | `[2026-07-24 22:43:04 -> 2026-07-24 23:42:55] OK job=daily_update run_outcome=success` |

Contrast (yesterday stale UI state before this run): prior idle API showed `integrity_observe` / soft banners from `daily_20260723.json` — not conflated with today's success.

## Post-fix checks

### All-due drain (before formal)

- `[drain 1/40]` … `[drain 40/40]` in acquire for run `20260724`
- Examples: `moneyflow`, `daily_basic`, `sw_daily` drained **1 day → 20260724** (`refilled_days: 1`)
- Formal after drain: `daily` + `stock_st` **accepted** partition `20260724`; `margin` bounded catchup to `20260723`

### org_holding (incremental gate, no mass refresh)

- `org_holding_gap_check`: `missing_count: 0`, `action: skip_current`, plannable `2026-03-31`
- Manifest: `incremental[].org_holding.action = skip_current` (not `repair_fetch_period` / `provider_truncated` this run — frontier complete without trunc on plannable period)
- **No mass org backfill** in this run

### Type-B same-run publish

- Log: `type_b_publish_catchup: status=completed`, **6 domains** published window `20260724` (moneyflow, moneyflow_dc, limit, index_daily, dc_member, top_inst_seat)
- Report: `delta_manifest.acquire_summary.type_b_publish` mirrors above; lag closed same run

### Phase rail

`preflight` → `acquire` (~2953s) → `clean` (pass) → `process` (pass, budget **fail** on latency only) → `store` (pass)

## 20260724 land summary (clock-eligible vs policy)

| Domain / layer | 20260724 | Notes |
|----------------|----------|--------|
| `daily` (accepted nominal OHLCV) | **yes** | formal `partition_value=20260724` |
| `stock_st` | **yes** | formal accepted |
| Raw drain (e.g. moneyflow, daily_basic, adj_factor, sw_daily) | **yes** | drain refilled 1 day |
| Type-B facts | **yes** | 6 facts published through 20260724 |
| T+1 / legacy domains (forecast, income, stk_holdernumber, …) | **20260723** | `pending_today: true`, honest `t_plus_one_legacy` |
| `org_holding` current period | skip | next `2026-06-30` unlocks **2026-08-31** |
| `holders_aif10` incremental | partial | 103 notices; **1 provider fail** in batch (59/60 at one checkpoint); run still `success` |

## UI behavior (during / after)

- **During run:** button disabled, progress via log tail + ops poll (when API responsive)
- **After run:** Playwright reload showed「未加载」/ `run_outcome=—` because **uvicorn could not get DuckDB** (busy retries in server log)
- **Cause at check time:** separate process **PID 40365** `org_holding_period_repair_truncated.py --max-periods 23` (not started by this E2E after daily DONE; blocks read-only ops API)
- **Expected UX once DuckDB free:** idle card should show `run_outcome=success`, soft banner absent, not red FAIL

## Residuals

1. **Org trunc repair:** resume when DuckDB free (do not overlap daily_update):
   ```bash
   cd /Users/dp/Documents/M/stock/chunkymonkey && PYTHONPATH=backend .venv/bin/python backend/scripts/org_holding_period_repair_truncated.py --max-periods 23 2>&1 | tee -a /tmp/org_trunc_repair_20260724.log
   ```
   Prior SIGTERM progress (from `/tmp/org_trunc_repair_20260724.log`): batch had reached ~**3/23** periods (`2019-06-30`) with **23 truncated_before**; full batch not completed.
2. **Process stage latency** `budget_status.process=fail` (612s vs 360s budget) — outcome still `success`; institution_profile + market_pulse cost.
3. **Post-run workbench refresh** — verify manually after trunc repair releases DB or restart API.

## Evidence files

- ~~`analysis/ui_daily_update_monitor_20260724.log`~~ — 30s log tail during run；**2026-08-10 删除**（308KB 进度刷屏日志，从未纳入 git；内容为 holders_aif10 103 分片的逐批进度与耗时，可由重跑复现，关键结论已摘入本文档正文）
- `data/reports/daily_20260724.json` — full delta_manifest + type_b_publish（**这份才是该次运行的结构化真相**）
- Playwright snapshots: `.playwright-cli/` 下的 `page-2026-07-24T14-43-05-468Z.yml` (trigger) / `page-2026-07-24T17-28-49-361Z.yml` (post-run API stall)；**该目录未纳入 git**，快照仅存于当时的本地工作区
