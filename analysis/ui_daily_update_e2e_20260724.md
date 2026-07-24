# UI daily_update E2E — 20260724

> Status: evidence-only · Label: **FIXED** (UI-triggered run landed)  
> Trigger: `manual_job_wrapper.py daily_update` via ops API · uvicorn `127.0.0.1:8000`  
> Log: `/tmp/chunkymonkey_daily_update.log` · Report: `data/reports/daily_20260724.json`

## Run summary

| Field | Value |
|---|---|
| Wall | `2026-07-24 22:43:04` → `23:42:55` CST (~60 min) |
| `run_outcome` | **success** (`clean_success`, exit 0) |
| API after run | `running=false`, `run_outcome=success` |
| Prior same log file | `20260723` run ended `soft_waiting_clock` (22:53) — separate session |

## Acquire — org path

From `org_holding_gap_check` + `delta_manifest` (20260724 acquire):

- `plannable=2026-03-31` · raw+accepted present · action **`skip_current`**
- `missing_older_count=0` · `provider_truncated=false`
- `older_remaining=0` (bounded quarter fill idle; next unlock `2026-06-30` / `2026-08-31`)

## Type-B same-run publish

Log line `type_b_publish_catchup` @ `23:32:19`:

- `status=completed` · `published_domains=6` · `errors=[]` · `max_days=40`
- Domains included moneyflow, moneyflow_dc, limit, index_daily, dc_member, top_inst_seat (raw `20260724` → fact window publish)

## Formal daily

`delta_manifest.formal`: `daily=accepted`, `stock_st=accepted`, `margin=land_then_accept`

## Continuity / store

- `check_continuity_integrity.py` → audit `data/audit/continuity_20260724.json`
- `degraded_total=0`

## Residual (honest)

- **Org pagination trunc repair** (23 `provider_truncated` periods) not part of this run’s auto path — separate ops script after writer idle.
- **holders_aif10** forward batch: 14 fails in 100/103 progress counter (log); run still **success** — track in holders ops if needed.
- **RX / Optuna**: not in scope; blocked until inventory gates + owner schedule.

## Monitor artifacts

- `analysis/ui_daily_update_monitor_20260724.log`
- Playwright CLI dumps under `.playwright-cli/` (untracked)
