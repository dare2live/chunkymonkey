# Workbench UI truthfulness fix — 2026-07-22

> Evidence-only. Owner screenshots 20:48 IDLE≠正在运行 + soft_waiting vs due-plan.
> Knife: status binding / copy / due-plan snapshot. Not a new sync run.

## Verdict

**FIXED** (UI truthfulness) · prior click verify remains **PARTIAL** (job
`soft_waiting_clock` + continuity/SLA ops observe — unrelated to this knife).

## True bugs (not hand-waving)

| # | Symptom | Root cause | Fix |
|---|---------|------------|-----|
| 1 | 分步节点 badge=`IDLE` but body=`正在：运行中` + same `pid=16662` on all cards | `_derive_current_activity` treated **global writer flock** as this job running; every step job inherited `daily_update`'s lock owner/pid in `current_activity` while badge used only `process_hint` | Activity「正在…」only when **this job** owns the run (`process_hint` or daily_update+flock). Idle + foreign lock → `空闲 · 全局 writer 占用中…本 job 未跑`. Frontend `nodeActivityView` strips「正在…」/pid when node idle |
| 2 | Yellow「等待时钟」+ due-plan `will-fetch=all-due` looked like stuck forever | (a) soft_waiting copy sounded **present continuous**; run had **finished** 20:48:33 with typed `soft_waiting_clock`. (b) due-plan only read `watermark_sla_before_*` (preflight 20:41 UTC=`12:41Z`≈20:41 CST — not 8h stale) so post-run still showed pre-fetch intent | Copy → `最近一次已结束 · 结果=soft_waiting_clock`. Due-plan prefers newest **post_acquire** `watermark_sla_YYYYMMDD.json`; label `kind=` + local+UTC as_of. Live mid-run **hides** prior report `run_outcome` (kills hard_fail flicker) |
| 3 | ALERT flags still on | **Not stale prior fails** — written by **this** soft_waiting/degraded completed run (`20:41→20:48`). Soft outcome keeps doctor flags by design (wrapper test). | Explain in UI; do **not** auto-rm (contract). Owner may `rm /tmp/chunkymonkey_ALERT_daily_update*.flag` for clean doctor; success run clears |

## Tests

`pytest backend/tests/test_ops_manual_run.py` → **14 passed** (incl. step-idle-under-global-lock, hide prior outcome while live, prefer post_acquire due-plan).

## Live API after restart (20:5x)

- `daily_update`: `running=false`, `owner_pid=null`, summary starts `最近一次已结束`, `due_plan.snapshot_kind=post_acquire` src=`watermark_sla_20260722.json` as_of=`12:48:31Z`
- pipeline nodes: all `idle` / `空闲 · 尚无日志` / `owner_pid=null` — no shared dead pid

## Click-verify carry-forward

Prior UI click (`foundation_ui_click_verify_after_drain_fix_20260722.md`): drain stream + probe-first **PASS**; terminal **PARTIAL** soft_waiting. No second pull tonight — job already finished; this knife is UI honesty only.
