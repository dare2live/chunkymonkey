# BestChoice (BC) — FROZEN (Track A)

> **Status**: FROZEN 2026-05-24. Track A original. Display only via UI tab.
>
> No further logic dev in this directory. See `backend/services/bc_absorbed/` (Track B) for active optimization.

## Frozen scope

- All formulas in `bestchoice/compute.py`, `bestchoice/scripts/` — **no changes**
- Existing `mart_daily_formula_candidate_bestchoice_v1` table — data refresh OK, logic frozen
- Existing UI tab `design/v3-tab-bestchoice.jsx` — read-only display continues

## What changes in Track B (active dev)

See `backend/services/bc_absorbed/` (created Phase 2 of MASTER_SYNTHESIS plan).

Phase 2 of goal.md roadmap covers:
- Wire `universe.get_active_universe()` (remove ST/退市 contamination)
- Walk-forward expanding_monthly governance
- Formula bank expansion (7 categories × ~7 = 50 formulas)
- Stage filter integration
- Phase 4 gate verification

## Why two tracks?

User instruction 2026-05-23: "现有状态保留不需要优化, 在主项目留入口, 然后主项目吸收合并其副本并优化".

- Track A keeps users access via UI without disruption
- Track B iterates aggressively without breaking originals
- Quarterly drift review (Track A vs B alignment)

## If you need to modify BC logic

**Do NOT modify files in this directory**.

Instead:
1. Modify `backend/services/bc_absorbed/` (Track B copy)
2. Test + Phase 4 gate
3. Once stable, decide whether to backport to Track A (rare — most likely NO, replace with Track B in UI eventually)
