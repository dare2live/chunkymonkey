# Legacy dual-track retire notes (post cutover ON)

Generated: 2026-07-20 — after owner opt-in `b38e9ac5` (C + B-pit cutover ON).

## Deleted / retired this knife

- **Pulse drill dual-track read**: `_drill_leaf_rows` no longer always
  `LEFT JOIN fact_stock_form_daily` and then overlay accepted stock_states on
  the same request. Resolver runs once:
  - `uses_legacy` → legacy SQL form join only
  - ACCEPTED_CUTOVER → null form columns + overlay from accepted only
- Stale router/module comments claiming default yaml → LEGACY / cutover false.

## Kept (required fail-closed / still live)

| Artifact | Why kept |
|---|---|
| `fact_stock_form_daily` table + `technical_states` builders | Rich form source; LEGACY/BLOCKED fallback when accept missing (e.g. 20260720) or canary refused |
| `_load_legacy_stock_state_by_day` in `institution_follow_b1_measure` | Per-day fail-closed path after `resolve_tier12_production_read` |
| `legacy_scaffold` / `legacy_mart` statuses + `_legacy()` helpers in resolvers | Typed fail-closed outcomes; not dead shims |
| `load_accepted_partition_as_production_truth` / `load_project_universe_breadth_as_mart_truth` | Anti-bypass loaders (always call resolver); enforce tests |
| Disclosure/margin `*_LEGACY*` compatibility tables | Separate E0 domain; out of C/B-pit cutover scope |
| Rally GT / pipeline joins to `fact_stock_form_daily` | Research/process consumers; not Tier1/2 production-read bypasses |

## Discovery evidence

- `rg` legacy_scaffold / load_accepted_partition_as_production_truth: no silent
  accepted-JSON production readers outside resolver wrappers.
- `moth coupling --impact fact_stock_form_daily`: fan-in 30; do not drop table.
- `codegraph explore "tier12 production read cutover legacy callers"`: consumers
  already go through `resolve_tier12_production_read`.

## Residual non-blockers

- Accepted 20260717 stock_states remain scaffold-grade (`form_name`/`axis_pos`
  null). Cutover-ON drill/B1 therefore expose scaffold form for that day, not
  rich `fact_stock_form_daily` — intentional production-read honesty until
  accept payload is enriched.
- Days without matching accept still LEGACY (fail-closed).
