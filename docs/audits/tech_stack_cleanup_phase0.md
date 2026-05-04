# Tech Stack Cleanup Phase 0 Baseline

Date: 2026-05-04

Plan: `/Users/dp/Documents/M/stock/chunkymonkey_ui_design_goal_plan.md`

## Workspace State

Checked before Phase 0 changes:

- `chunky-monkey-v2`: `main...origin/main`
- `tdxhub`: `master...origin/master`
- `miaoxiang`: `main...origin/main`

No uncommitted changes were present in the three repositories before updating the audit tooling.

## Audit Tool

Updated:

- `backend/scripts/audit_stale_references.py`

The script now includes the plan-required cross-project categories:

- `pandas_runtime`
- `pandas_test`
- `pandas_docs`
- `legacy_sql_runtime`
- `legacy_sql_test`
- `legacy_sql_docs`
- `old_db_path`
- `old_source_route`
- `old_external_link`
- `duckdb_allowed`

Baseline report:

- generated during Phase 0 and intentionally removed from the repo during Phase 2 because raw hit text contains retired tokens that the Phase 2 gate must reject.

Command:

```bash
python3 backend/scripts/audit_stale_references.py --no-fail --output docs/audits/tech_stack_cleanup_baseline_2026-05-04.json
```

## Baseline Summary

The Phase 0 scan covered 781 files across `chunky-monkey-v2`, `tdxhub`, and `miaoxiang`.

```text
duckdb_allowed: 248
old_db_path: 12
old_external_link: 2
old_source_route: 33
pandas_docs: 84
pandas_runtime: 1211
pandas_test: 89
legacy_sql_docs: 4
legacy_sql_runtime: 52
legacy_sql_test: 16
```

The legacy stale-reference tiers found no critical live references for the existing retired registry:

- `mootdx`: clean
- legacy test DB import: clean for active import references
- `market_raw_holdings`: comments/retirement actions only
- `top_free_holders`: clean in the existing retired registry scan
- `dim_stock`: comments only
- `dim_stock_industry`: comments/retirement actions only
- `fact_institution_event_industry_snapshot`: comments/retirement actions only

## Next Queues

Phase 1 should inspect whether these baseline buckets are live runtime dependencies, false-positive documentation hits, or current facts that need an allowlist:

- `pandas_runtime`: data source adapters, model scripts, lock files, and tdxhub runtime modules.
- `legacy_sql_runtime`: DuckDB adapter polyfills, transaction calls, schema introspection, and docs-adjacent comments in runtime files.
- `old_db_path`: retired test fixture file names.
- `old_source_route`: source route displays and migration comments involving `datacenter-web` and holder capabilities.

No data, DuckDB files, logs, mlruns, caches, pkl files, or temporary runtime artifacts are included in this baseline.
