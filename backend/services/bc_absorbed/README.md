# bc_absorbed — Track B BC 副本 + 优化

> Created 2026-05-24 per MASTER_SYNTHESIS Phase 2.1.
> Original Track A frozen at `chunkymonkey/bestchoice/` (FROZEN.md).
> This Track B copy receives optimizations:
> - universe.get_active_universe() wiring (Phase 2.2)
> - Walk-forward expanding_monthly governance (Phase 2.3)
> - Formula bank 7 categories × 7 = 50 formulas (Phase 2.4)
> - Stage filter integration (Phase 2.5)
> - Phase 4 gate verification (Phase 2.6)

## Architecture differences from Track A

| Aspect | Track A (bestchoice/) | Track B (bc_absorbed/) |
|---|---|---|
| Universe | dim_active_a_stock direct (current snapshot, 15.7% contamination) | universe.get_active_universe() PIT clean |
| Walk-forward | 30% validation holdout (Phase 5 MILD bias) | expanding_monthly governance-enforced |
| Formula count | 5 | 50 (7 categories) |
| Stage filter | none | Wyckoff Stage {1.5, 2, 3} positive IC |
| Phase 4 gate | not run | strict --require-true-train-log mandatory |
| Output table | mart_daily_formula_candidate_bestchoice_v1 | mart_bc_absorbed_candidate_v1 |

## What's preserved

- formula_engine.py compute_formula_signals API (caller compatibility)
- compute.py architecture
- scripts/ pattern

## Status

- Phase 2.1: cp done 2026-05-24 (this README)
- Phase 2.2: wire universe — IN PROGRESS
- Phase 2.3-2.6: pending
