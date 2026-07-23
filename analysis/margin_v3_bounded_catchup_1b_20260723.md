# Margin Knife 1b — contract v3 bounded calendar catchup (2026-07-23)

> Status: evidence-only  
> Parent: `foundation_residual_fix_plan_20260723.md` · Knife 1a `e6b3e44c5`  
> Label: **FIXED** (path shipped) / Continuity + rzrqye product trust still evidence-gated

---

## What shipped

| Item | Change |
|---|---|
| `contract_version` | **3** — transport `split_by` / `required_groups` = **SSE+SZSE only** (no BSE) |
| Accepted claim | Unchanged from 1a: `external_aggregate` SSE+SZSE |
| Evidence filter | Accepted-state reads filter `contract_version` + `contract_hash` — v2 BSE-in-canonical stays read-only orphan evidence |
| `coverage_start` | `20260717` (day after last v2 canonical `local_max=20260716`) |
| Execution | `mode=enabled` / `reason=bounded_calendar_catchup` / `sync_policy=on_demand` |
| Runtime | `margin_catchup.land_then_accept_margin_day` + sync_runner explicit `--start/--end` (cap 10 trading days); drain inapplicable; **not** `--all-due` |
| Acquire | Bounded catchup step plans `[local_max+1 .. eligible_end]`; frozen observe only if disabled |
| Product gate | `product_blocking` absent → hard-gate **off**; pulse `rzrqye` stays UNTRUSTED |

Hard bans kept: no Optuna/holdout, no org mass, no invent by_date, no READY cosmetics, no mass history replay.

---

## Live evidence (pre-catchup smoke)

```text
contract_version=3 coverage_start=20260717
split_by=[SSE, SZSE] required_groups_since={}
v3 accepted count=0 (filter excludes 1823 v2 pointers)
canonical/raw local_max=20260716
eligible_end=20260722 (next_trading_session_published)
catchup window=[20260717, 20260720, 20260721, 20260722]
```

Live catchup attempt this knife: `authorization_blocked / missing_token` in agent
env — path refuses closed (no fake write). Owner/CI with TuShare token:

```bash
PYTHONPATH=backend python -m services.data_sources.sync_runner \
  --domain margin --start 20260717 --end 20260722
```

then re-read `MAX(trade_date)` on `canonical_margin_exchange_daily` and Continuity.

---

## Continuity / product honesty

- Enabling catchup removes `observe_frozen_stale` wash for disabled mode; lag while catchup incomplete may **FAIL** honestly until `local_max` advances — not READY-by-silence.
- rzrqye product thaw = Knife 1c / shadow evidence only.
