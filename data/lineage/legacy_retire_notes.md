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

## 2026-07-20 re-audit — residual: NONE

Re-ran the dual-track bypass sweep this session (rg across `backend/routers`,
`backend/services`, `backend/scripts`, frontend `src/api` + moth/codegraph)
looking for any new production-serving read that skips a resolver:

- `backend/routers/market_pulse.py` `_drill_leaf_rows`: still resolves once
  via `resolve_tier12_production_read` before choosing legacy SQL join vs
  accepted overlay (no dual read). Confirmed wired end-to-end (router →
  `market_pulse_tier12_read.overlay_pulse_form_from_production_read` /
  `attest_pulse_b_pit_mart_production_read`), not dead code.
- `backend/services/market_pulse_b_pit_read.py` /
  `b_pit_mart_cutover.resolve_b_pit_mart_production_read`: pulse UI B-pit
  attestation still the only path to `project_universe_pit` breadth; no
  direct mart table read found elsewhere.
- `backend/routers/institution_profile.py`: reads `cutover_allowed` from a
  typed `read_policy` resolver, not a raw table join.
- `backend/routers/paper_portfolio.py`, `ops_manual_run.py`: no
  Tier1/2/B-pit table references at all.
- `frontend/src/api/*`: HTTP client only, no direct DB access — not a
  parallel bypass surface.
- Remaining raw `accepted_partition` / `fact_stock_form_daily` references
  (`main_rally_dataset_snapshot.py`, `institution_follow_b0.py`,
  `institution_follow_b2_measure.py`, `tier12_nominal_canary.py`,
  `persist_b_pit_breadth_shadow.py`) are Tier3 research dataset-snapshot
  enumeration / GT-adjacent measure modules or the explicitly read-only,
  never-cutover-authorizing `market_pulse_shadow_reconcile.py` audit tool —
  same category already recorded above, not new production-read bypasses.

**Verdict: dual-track residual = NONE** (no bypass found to delete/retire
this pass; all previously-retired boundaries hold). Evidence command:
`rg -n "FROM fact_stock_form_daily|JOIN fact_stock_form_daily|FROM
accepted_partition|JOIN accepted_partition" --type py` plus manual read of
every matching file's call site.
