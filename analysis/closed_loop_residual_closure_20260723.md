# Closed-loop residual closure — 2026-07-23

> Status: evidence-only  
> Parent law: `serve_derive_closed_loop_law_20260723.md`  
> Label: **FIXED**

## What closed

| Residual | Fix | Live proof |
|---|---|---|
| org canary / under_populated only observe | `repair_accept_from_local_raw` when dense raw | Q1-2026: 2 → **5524** stocks accepted; gap `skip_current`/`ok` |
| F6 existence-only org | `min_org_accepted_stocks=500` | F6 PASS `max_stocks=5524≥500` |
| surprise 164s rebuild | `seed_institution_as_of_from_holders` | as_of=`20260723`; process plan `inst_frontier_unchanged` skip |

## Tests

- `test_pipeline_closed_loop.py` — decide repair / seed as_of  
- `test_org_holding_aif10.py` — gap action = repair when canary+dense raw  
- `test_check_foundation_done.py` — F6 FAIL on canary / PASS on dense  

## Still banned (not residuals of this knife)

- daily 全市场逐公司扫公告 / org by-date invent / full-history ~830k mass refresh  
- 期内晚披露 provider re-pull without miss ledger（shareholder design）
