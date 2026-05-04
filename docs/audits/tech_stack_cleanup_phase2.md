# Tech Stack Cleanup Phase 2

Date: 2026-05-04

Plan: `/Users/dp/Documents/M/stock/chunkymonkey_ui_design_goal_plan.md`

## Scope

Phase 2 removed legacy SQL-engine wording and compatibility behavior from the main repository. The goal was to make the denylist scan in the plan return no matches across code, tests, comments, and audit docs.

## Changes

- Removed the SQL dialect rewrite layer from `backend/services/duck_adapter.py`; statements now execute against DuckDB directly.
- Removed the unused DB-engine compatibility setter from `DuckConn`.
- Sanitized test guidance and audit docs so historical raw hit text does not keep retired terms alive in the repo.
- Removed the raw Phase 0 baseline JSON from version control because it intentionally contained old hit text; current evidence is captured through reproducible commands and summarized audit reports.
- Kept Row dict-style access in the adapter because many services still rely on `row["col"]` reads.

## Verification

Commands run:

```bash
python3 -m py_compile backend/services/duck_adapter.py backend/scripts/audit_stale_references.py
cd backend && python3 -m pytest tests/test_db.py tests/test_etf_db.py tests/test_data_consistency.py tests/test_institution_contract.py tests/test_audit_financial.py tests/test_block_client.py tests/test_xdxr_client.py -q
cd backend && python3 -m pytest tests/test_utils.py tests/test_kline_sources.py tests/test_tdx_source.py tests/test_audit_financial.py tests/test_scoring_engine.py tests/test_scoring_composite.py -p no:cacheprovider --tb=short -q
python3 backend/scripts/audit_stale_references.py --phase0-only --output /tmp/phase2_clean_scan.json
```

Results:

- Phase 2 denylist scan from the plan: no matches.
- Required Phase 2 test set: `18 passed`.
- CI offline subset: `57 passed`.
- Cross-project audit summary now reports legacy SQL docs/runtime/test buckets as `0`.

## Remaining Queue

- Phase 3: add records-first tdxhub APIs and move ChunkyMonkey integration paths off DataFrame contracts.
- Phase 4: retire pandas usage from ChunkyMonkey services, scripts, and tests.
