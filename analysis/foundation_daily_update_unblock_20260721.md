# Foundation daily_update unblock (2026-07-21)

> 状态：evidence-only
> Follow-up to E2E PARTIAL: `analysis/foundation_e2e_frontend_update_20260721.md` (`a78708eb8`)
> Wall clock: 2026-07-21 ~21:00–21:15 Asia/Shanghai
> Overall: **FIXED** (foundation knives 1–2 + operational DC/pulse catchup); UI button residual remains

## Verdict

| Knife | Label | Evidence |
|---|---|---|
| 1 margin preflight deadlock | **FIXED** | `a84e0867e` |
| 2 formal daily/ST orchestrator | **FIXED** | `8bcc37dad` |
| 3 DC/sector pulse lag | **FIXED** (ops catchup; no code knife) | raw DC+moneyflow+limit_cpt → `20260721`; pulse APIs `20260721` |
| UI「数据更新」button | **residual** | still unwired in edge React (separate backlog) |

## Before → After

### Knife 1 — margin all-due deadlock

**Before (E2E):**
```text
POST /api/v3/ops/jobs/daily_update/run
→ DEGRADED: PREFLIGHT BLOCK: sync_execution_blocked:margin:scope_blocked (exit 4)
```

**After:**
- `margin.sync_policy=on_demand` while `execution_policy.mode=disabled` / `scope_blocked` (product stays frozen; no thaw)
- `automatic_domains()` no longer includes `margin` (42 domains)
- acquire margin drain/shadow hard-gate runs **only** when margin is enabled
- Live: `ensure_pipeline_sync_ready` → `PASS domains=42`
- Explicit `chunkyctl sync --domain margin` still hard-stops on `scope_blocked`

### Knife 2 — formal daily/ST on_demand catchup

**Before:** formal `daily`/`stock_st` are `on_demand`, so they never ride `--all-due`; UI path pulled nothing for K/ST.

**After:** `pipeline.acquire._sync_formal_on_demand_security_days` before all-due drain:
- resolves `eligible_end` (`trigger_mode=manual`)
- if `accepted_partition` missing → modular `run_domain` / land_then_accept for **that single day**
- if already accepted → skip (no history mass-fill)

Live plan @ accepted frontier:
```json
{"domain":"daily","action":"skip","reason":"latest_eligible_already_accepted","eligible_end":"20260721"}
{"domain":"stock_st","action":"skip","reason":"latest_eligible_already_accepted","eligible_end":"20260721"}
```

Preserved: org incremental-only; formal daily/ST semantics; no ~830k org mass refresh.

### Knife 3 — DC / pulse cards

**Before:** `build_dc_industry_view` failed (`industry/concept=20260720` vs `member=20260716`); `flow_board`/`strongest` stuck at `20260720`.

**After (targeted sync, same domains all-due would pull once preflight unblocked):**
- synced `dc_index` / `dc_member` / `dc_daily` / `moneyflow_ind_dc` / `moneyflow_dc` / `limit_cpt_list` / `limit_list_d` through `20260721`
- DC dims rebuilt (`idx=20260721 mem=20260721`)
- `mart_sector_pulse_daily` / `mart_market_pulse_daily` max **`20260721`**
- HTTP: `flow_board` **`20260721`**, `strongest` **`20260721`**

## Residuals

1. **UI button** — edge React still has no「数据更新」control calling `/api/v3/ops/jobs/daily_update/run` (document only; optional stub owned by product backlog agent).
2. **Full `daily_update` wall-clock acquire** — not re-run end-to-end tonight (42-domain drain); preflight + formal planner + targeted DC sync prove the former hard-stop is gone and next-due planning is sane.
3. **Alert flags** — `/tmp/chunkymonkey_ALERT_daily_update*.flag` may still be stale from the failed E2E run (hygiene, not foundation block).
4. Product moneyflow analysis UI / Optuna / StrategyRelease — out of scope (unchanged bans).

## Commits

- `a84e0867e` — fix(foundation): unblock daily_update preflight under frozen margin
- `8bcc37dad` — feat(foundation): daily_update pulls latest eligible daily/ST
