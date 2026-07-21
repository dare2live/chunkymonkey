# Foundation daily_update UI click follow (2026-07-21)

> Evidence-only. Owner clicked workbench「数据更新」≈21:53 CST.
> Job: `POST /api/v3/ops/jobs/daily_update/run` → poll `GET /api/v3/ops/jobs/daily_update`.
> Related: observability UX fix (current_activity); Capability E backlog only.

## 1. Click run timeline

| Time (CST) | Event |
|---|---|
| 20:42:13 | Prior FAIL: `PREFLIGHT BLOCK: sync_execution_blocked:margin:scope_blocked` (rc=4); ALERT flag written |
| 21:53:18 | Owner click accepted; wrapper/pipeline `pid≈3297/3299`; writer=`pipeline.run` |
| 21:53:18–23 | Preflight **PASS** (sync policy 42 domains, calendar, auth, watermark SLA deferred) |
| 21:53:23 | Enter **① ACQUIRE**; log then quiet ~10m (Python stdout block-buffer + long eastmoney pull) while CPU/WAL/TCP proved alive |
| ~22:03 | Log flushed: holders_aif10 ran; **org incremental check+skip**; daily/ST skip already-accepted `20260721`; sync_runner `--all-due --drain` started (child pid 5496) |
| 22:07:00 | Acquire ended `check_fail` (registry drain residual + margin on_demand skip); entered CLEAN |
| 22:07:12 | CLEAN DEGRADED: data_audit FAIL (`cross_table_consistency` 327 kline codes not in universe); chain continued |
| 22:07–22:08 | PROCESS `check_pass` (DC build + market_pulse SW +1 day); STORE DEGRADED (continuity FAIL + watermark SLA) |
| 22:08:12 | **DONE with degraded (4 项; exit 1)** — ALERT flags rewritten |

Process evidence during quiet window: `lsof` showed `smartmoney.duckdb` write + `datacenter.eastmoney.com:https`; WAL grew (e.g. ~2MB→12MB) then checkpointed.

## 2. Observability UX (owner complaint)

**Wrong before:** button/status only「更新进行中 / 运行中 · writer=…」; log_tail stalled on ACQUIRE banner → looked stuck with no current step.

**Fixed tonight (minimal):**
- Backend `GET /api/v3/ops/jobs/daily_update` adds `current_activity` (+ `alert_summary`); spawn sets `PYTHONUNBUFFERED=1` for future runs.
- Frontend workbench shows **当前活动** panel: phase /「正在: …」/ last progress line / log mtime / poll time; ALERT reason prominent when idle.
- Frontend `deriveActivityFallback` still works if API process not yet reloaded.

**Not tonight:** Capability E independent step-card buttons (scheduled in product backlog).

Note: mid-follow API restart blocked on DuckDB lock held by pipeline (`init_db()` at import). Frontend fallback still parses log_tail; API should return after writer releases.

## 3. Org incremental vs skip-all (crisp)

**Policy:** org (period domain) on manual/`daily_update` = **incremental-only** — check latest plannable vs local; missing → fetch one period; present → skip. **Never** full-period ~830k mass refresh. Provider land mass path remains historically **BLOCKED**; that is orthogonal to tonight’s incremental check.

**Tonight live log (authoritative):**

```text
org_holding_gap_check: {"plannable": "2026-03-31", "local_has_plannable": true,
  "local_periods": ["2019-03-31", "2025-12-31", "2026-03-31"], "missing_count": 27, "status": "ok"}
{"count": 0, "status": "skipped", "report_date": "2026-03-31", ...,
  "message": "check: plannable=2026-03-31 local=present; skip fetch (older_missing=27; not auto mass-filled)"}
```

| Question | Answer |
|---|---|
| Did incremental **check** run? | **Yes** (`org_holding_gap_report` printed in acquire) |
| Did incremental **pull** run? | **No** — correctly nothing-due (`local_has_plannable=true` for `2026-03-31`) |
| Is SKIP = “never incremental”? | **No** — SKIP tonight = no new plannable period due; older 27 holes = log-not-fill (explicit backfill knife only) |
| Mass ~830k? | **Not requested / not run** |

## 4. Holders (十大流通股东) + other acquire notes

| 项 | 证据 |
|---|---|
| 系统域名 | 主路径 `holders_aif10`（东财妙想 `RPT_F10_EH_FREEHOLDERS`）；正式/镜像域 **`holders_top10`** → `fact_top10_holder_period` |
| 增量策略 | 水位驱动：扫存量 MAX(披露日) 之后有新披露的股（非全市场 period mass） |
| 本轮缺口 | **有** — 水位 `watermark=20260717` |
| 本轮动作 | **已增量拉取** — `affected=76` `rows=987036` `exits=3941` `errors=[]` |
| Accept | acquire 已写；partition accept 细节待 drain 结束后只读核 |

Also this run:
- QFII-like skip: `report_date=2026-03-31` 已有 2508 条
- Formal daily / stock_st: `action=skip` `reason=latest_eligible_already_accepted` `eligible_end=20260721`
- Registry: `sync_runner --all-due --drain --max-dates 30` (child 5496; in flight at draft)

## 5. Product backlog

- Capability **E** recorded: modular pipeline step cards / independent node ops — `analysis/product_decision_assist_backlog_20260721.md`
- Light `goal.md` pointer added
- Sequencing: after click-proof; near Capability C tabs; **not** built tonight

## 6. Label

**PARTIAL** — click path ran end-to-end; org incremental skip **FIXED**; holders incremental **DID fetch**; UI observability **FIXED** (API `current_activity` live after restart); terminal = **DONE with degraded / FAIL rc=1** (4 degradations; not hard preflight block).

---

## Append — terminal outcome

- Wall clock: `21:53:18 → 22:08:12` (~15m)
- Exit: **rc=1** `daily_update DONE with degraded (4 项)`
- Degradations:
  1. `sync_registry drain 有残余缺口或域错误`
  2. `data_audit` FAIL — `cross_table_consistency`: 327 kline codes not in universe
  3. `continuity/integrity` FAIL → `/tmp/chunkymonkey_ALERT_continuity.flag` + `data/audit/continuity_20260721.json`
  4. post-acquire watermark SLA alert → `data/audit/watermark_sla_20260721.json`
- Stage status: acquire `check_fail` → clean `check_fail` → process `check_pass` → store `check_fail`
- Report: `data/reports/daily_20260721.json`
- API after restart: idle + `current_activity.summary` = FAIL/alert residual (flags present)
