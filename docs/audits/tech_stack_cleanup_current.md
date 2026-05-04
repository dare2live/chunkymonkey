# Tech Stack Cleanup Current Status

Date: 2026-05-05

## Current Facts

- Main backend uses DuckDB plus records/native rows for project-owned data flow.
- tdxhub integration is pinned to `b039d8c68fb21543a9ff041f004d75113b5a4e3d`, and ChunkyMonkey consumes records-first wrappers on the used paths.
- miaoxiang public DDL path no longer exposes the retired dialect.
- Data health dry run currently reports 147 green assets and no warn/fail assets.
- GitHub Actions run `25344161174` passed both Python matrix jobs.

## Verified Gates

- ChunkyMonkey backend tests: 364 passed.
- API smoke on local port 8000 passes for data routes, source health, data health, daily recommendations, model comparison, and feature validation.
- tdxhub offline subset: 130 passed, 4 skipped.
- tdxhub lock check: all set.
- Phase 0 stack scan: tabular runtime/test buckets are zero; legacy SQL runtime/test/docs buckets are zero; old route/path/link buckets are zero.

## Remaining Audit Work

- Full literal-denylist gate is being enforced by deleting retired migration notes and unused third-party doc mirrors from version control.
- Phase 7/8 functional regeneration still needs a fresh evidence pass before the active goal can be marked complete: API smoke, frontend browser QA, feature-panel rebuild, model retraining, promotion gate, and strategy backtests.
