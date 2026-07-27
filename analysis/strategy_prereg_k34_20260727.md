# Research prereg_v1 + factor-family K3/K4 (2026-07-27)

> Lifecycle: evidence-only · Label: **FIXED** (research_prereg_v1 + K3/K4)

## Scope

Audit step 5: atomic prereg / param hash / single-touch token; factor-family
K3 (frontier projection) + K4 (STRATEGY plan inventory exit criteria).

## Delivered

| Piece | Path |
|---|---|
| Atomic prereg store | `backend/services/research_prereg_store.py` |
| ExperimentPrereg `param_hash` + `single_touch_token` | `research_runtime.py` / `research_runtime_loop.py` |
| Holdout consume wrapper | `holdout_guard.consume_holdout_single_touch` |
| Policy | `holdout_policy.yaml` `training_boundary_plus_research_prereg_v1` |
| K3 frontier projection | `factor_family_frontier_projection.py` + script |
| Live projection artifact | `data/lineage/factor_family_frontier_projection.json` |
| K4 | `analysis/STRATEGY_EXECUTION_PLAN.md` §3 inventory exit |
| Contract note | `docs/strategy_validation_contract.md` §10 |

## Tests

`test_research_prereg_store` + `test_factor_family_frontier_projection` + holdout /
research_runtime / continuity gates — green; added to CI blocking surface.

## Residual

- Not StrategyRelease; no multi-writer DB ledger claim.
- Fresh disclosure snapshot + full strategy preflight = next knife.
- `goal.md` RX schedule still owner-only (not written).
