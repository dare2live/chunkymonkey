# Continuity Knife4（2026-07-23）

> evidence-only；owner backlog = `FOUNDATION_EXECUTION_PLAN.md` F1

## Before → after

| | overall | warn | fail |
|---|---|---|---|
| before | WARN | 6 | 0 |
| after | WARN | 2 | 0 |

残留 2 = `moneyflow_hsgt` / `dividend` `warn_interior_gaps`（`gap_tolerance: annotate`）。**故意不洗绿**。

## Fixes

1. **typed**: accepted margin/security_day `coverage_start` vs 表 MIN pre-coverage retention ≠ `declared_drift`
2. **registry**: `moneyflow_ind_dc` / `dividend` `data_start_reviewed`; dividend `row_dip_tolerance`（vendor grain 对齐）
3. **ops**: `moneyflow_hsgt` backfill `20260708`–`20260710`; vendor-0 `known_empty_days`

## Tests

`backend/tests/scripts/test_check_continuity_integrity.py` — 42 passed（含 pre-coverage retention 新测）
