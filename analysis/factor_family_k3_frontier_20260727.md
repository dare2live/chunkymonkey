# Factor-family continuity gate matrix — 2026-07-27 update (K3)

> Status: evidence-only · Label: **FIXED（K3 frontier projection）**  
> Authority: `analysis/factor_family_governance_toplevel_20260724.md`  
> Owner: `backend/scripts/project_factor_family_frontiers.py`

## What shipped (K3)

Read-only live frontier projection for defer/blocked families:

- inventory `defer_reason` required (wired into `check_factor_family_gates`)
- artifact: `data/lineage/factor_family_frontier_projection.json`
- org tip + moneyflow raw/fact tips + margin external_aggregate note

## K4

`analysis/STRATEGY_EXECUTION_PLAN.md` §3 now lists factor-family inventory exit
criteria as an RX door.

## Residual

RX still BLOCKED without owner `goal.md` schedule; projection ≠ readiness green.
