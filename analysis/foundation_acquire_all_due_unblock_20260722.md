# Foundation acquire `--all-due` unblock — 2026-07-22

> Status: evidence-only
> Evidence for owner follow-up from `foundation_ths_hot_ui_catchup_20260722.md` (`e56fc7aef`).
> Primary path remains workbench「数据更新」.

## Problem (measured)

UI acquire ordered holders → QFII/org → formal daily/ST → `--all-due`.
Run B: daily soft-skip WIP was incomplete on disk; **stock_st** after
`available_after=09:20` still hard-failed on same-day `zero_rows` → drain never
started → `ths_hot` max stayed `20260720`.

## Fix

| # | Change | Label |
|---|---|---|
| 1 | Formal security-day land_then_accept: same-day vendor vacuum → typed `pending_publish` (`pre_available_after_zero_rows` **or** `same_day_vendor_vacuum` after optimistic HH:MM). Non-today empty still fail-closed via accept path. | **FIXED** |
| 2 | `acquire._sync_formal_on_demand_security_days` soft-continues on `pending_publish` so `--all-due` still runs | **FIXED** |
| 3 | holders: heartbeat (`progress_every=10`) + skip rewrite when provider max UPDATE_DATE ≤ formal wm | **FIXED** (small) |
| 4 | Workbench due-plan preview from newest watermark SLA JSON (domain / wm / will-fetch≈all-due) | **FIXED** (small; not planner verdict) |

## Tests

- `test_formal_security_day_same_day_empty_after_window_is_pending`
- `test_formal_security_day_pre_publish_empty_is_pending_not_error`
- `test_formal_on_demand_catchup_soft_skips_pending_publish`
- `test_incremental_skips_when_provider_watermark_unchanged`
- `test_due_plan_preview_marks_lagged_all_due_domains`

## Live verification (UI)

Re-click「数据更新」after land. Expect:

1. formal daily and/or stock_st → `action=pending_publish` (not TIER0 BLOCK)
2. log reaches `sync_runner --all-due --drain`
3. planner may list `ths_hot` due (wm=`20260720`); fetch may still be
   `pending_publish` if before domain `available_after` (22:30) — OK if recognized

## Residual

- Raise measured `stock_st` `availability_policy.at` when real publish clock is
  known (09:20 remains optimistic eligibility hint).
- `ths_hot` live fill past `20260720` still clock/ops dependent.
- holders same-day corrections with identical UPDATE_DATE may wait until wm moves.
