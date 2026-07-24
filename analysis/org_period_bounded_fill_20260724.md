# Org period bounded fill (2026-07-24)

> Status: evidence-only · **SHIPPED** (Knife 1) · Replaces log-not-fill for org intermediate quarters  
> Law: `plan_partition_catchup(calendar \ local_raw, P≤plannable, oldest_first, N=1)`

## Contract change

| Before (2026-07-23) | After (2026-07-24) |
|---|---|
| Intermediate quarter holes = log-not-fill | Plannable complete → fill **oldest** missing quarter **N=1/run** |
| Explicit backfill CLI only | Pipeline action `fill_older_period` (NOT `backfill()`) |
| Mass ~830k refresh banned | Unchanged — `allow_existing_refresh=False` on older fill |

## Implementation

| Module | Role |
|---|---|
| `org_holding_period_catchup.py` | Due-set + execute one oldest period |
| `org_holding_aif10.sync_org_holding_incremental` | Wire fill after plannable skip |
| `acquire._sync_org_holding` | Manifest gap: `fill_target_period`, `older_remaining` |
| `ops_manual_run._org_due_row_from_gap` | Surfaces `fill_older_period` in due_plan |

## Live expectation

~27 missing quarters (2026-07-24 evidence) should decrement by **1 per daily_update** when plannable is complete — not require mass full refresh.
