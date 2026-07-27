# Research prereg_v1 + factor-family K3/K4 (2026-07-27)

> Lifecycle: evidence-only · Label: **SUPERSEDED/PARTIAL**
> Current evidence:
> `analysis/strategy_runtime_holdout_snapshot_fix_20260727.md` and
> `analysis/factor_family_k3_live_gate_20260727.md`

## Scope

Audit step 5: atomic prereg / param hash / single-touch scope; factor-family
K3 (frontier projection) + K4 (STRATEGY plan inventory exit criteria).

## Delivered

| Piece | Path |
|---|---|
| Atomic prereg store | `backend/services/research_prereg_store.py` |
| ExperimentPrereg `param_hash` + stable `holdout_scope_id`（snapshot+strategy+universe+protocol+policy；fold/date 不重置） | `research_runtime.py` / `research_runtime_loop.py` |
| Holdout consume wrapper | `holdout_guard.consume_holdout_single_touch` |
| Policy | `holdout_policy.yaml` `training_boundary_plus_research_prereg_v1` |
| K3 frontier projection + live fail-closed gate | `factor_family_frontier_projection.py` + `project_factor_family_frontiers.py` + `check_factor_family_frontier_live.py` |
| Live projection artifact | `data/lineage/factor_family_frontier_projection.json` |
| K4 | `analysis/STRATEGY_EXECUTION_PLAN.md` §3 inventory exit |
| Contract note | `docs/strategy_validation_contract.md` §10 |

## Tests

`test_research_prereg_store` + `test_factor_family_frontier_projection` + holdout /
research_runtime / continuity gates — green; added to CI blocking surface.

## Residual

- Not StrategyRelease；文件 ledger 仅为单节点 evidence，无跨节点 CAS/唯一约束 claim。
- Fresh disclosure snapshot + full strategy preflight = next knife.
- `goal.md` RX schedule still owner-only (not written).
