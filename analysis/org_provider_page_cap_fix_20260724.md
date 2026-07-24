# org_holding East Money 100-page cap fix (2026-07-24)

> Status: evidence-only · Label: see **Verdict** below

## Continuity audit context (MIXED — do not re-run)

| Domain | Verdict | Facts |
|--------|---------|-------|
| org_holding | **MIXED** | 30/30 periods present, `missing_older=0`, plannable OK; **14 periods ~200k-row truncated** (H1/annual); **6/30** density OK |
| holders (季末 fact) | OK | Quarter-end fact continuity OK |
| QFII | **residual** | 22-period gaps — **next knife**; no mass QFII backfill in this change |
| TuShare fina | OK | Windows OK |

Local truncation scan (heuristic `provider_truncated`, 2026-07-24): **24** periods flagged for ops repair (includes Q1/Q3 below audit’s “14 H1/annual” threshold when stocks ≪ baseline).

## Root cause (live repro)

| Probe | Result |
|-------|--------|
| `RPT_MAIN_ORGHOLDDETAIL` + `REPORT_DATE='2025-12-31'` page 1 | `count=832906`, `pages=417`, `page_size=2000` |
| Same filter page 101 | `pages=0`, `data=[]` |
| Pre-fix local `2025-12-31` | `180449` rows, `1185` stocks |

East Money v1 hard cap: **100 pages / filter query** → silent stop ~200k rows @ `page_size=2000` while page-1 `count` stays ~640k–830k.

## Fix shipped

**miaoxiang** `e162d7b`: `pagination.py`, truncate-aware `fetch_all_pages`, `fetch_all_pages_sharded` (16 shards for 2025-12-31).

**chunkymonkey** `888bfde75` (+ follow-up): `pagination_integrity.py`, `org_holding_fetch.py`, gap `provider_truncated` → `repair_fetch_period`, `sync_period` fail-closed on truncated fetch.

**Ops**: `backend/scripts/org_holding_period_repair_truncated.py` — oldest truncated first, `--max-periods` ≤40/session, explicit refresh only.

## Fetch validation (no DB write)

`analysis/org_fetch_validation_20260724.json`:

| provider_count | fetched_rows | truncated | shard_count | elapsed_s |
|---------------:|-------------:|:---------:|------------:|----------:|
| 832906 | 832906 | false | 16 | 405.2 |

## Canary `2025-12-31`

| | Rows | Stocks | Notes |
|---|-----:|-------:|-------|
| Original pre-fix | 180449 | 1185 | audit baseline |
| Before sharded repair | 495209 | 3589 | partial prior pull |
| **After sharded repair** | **832907** | **5523** | `provider_count=832906`, `truncated=false`, 16 shards |

Log: `analysis/org_canary_repair_20260724.log` · report `data/reports/org_holding_truncation_repair_latest.json`

**Repaired this session:** 1 period (`2025-12-31`). **Remaining truncated (heuristic):** 23 → ops `org_holding_period_repair_truncated.py --max-periods N`.

## daily_update anti-truncation

Every run: `org_holding_period_gap_report` + `population_for_period` → if `provider_truncated`, action **`repair_fetch_period`** (one plannable period, sharded fetch), **not** `skip_current`. `sync_org_holding_incremental` raises on `provider_truncated` fetch result. Shared contract: `pagination_integrity.assess_paginated_land`.

## Residuals

- **QFII**: 22-period gaps — document only; separate bounded knife.
- **org**: remaining truncated periods → ops `org_holding_period_repair_truncated.py` (oldest-first, ≤40/session).
- holders/qfii: truncate-aware loop only until live count proves cap breach.

## SHAs

| Repo | SHA | Message |
|------|-----|---------|
| miaoxiang | `e162d7b` | fix(aif10): shard paginated fetches past East Money 100-page cap |
| chunkymonkey | `888bfde75` | fix(org): sharded aif10 fetch and pagination integrity gates |
| chunkymonkey | _(follow-up SHA)_ | ops truncation repair script + evidence update |

## Verdict

**FIXED** (code + sharded fetch proof + canary `2025-12-31` land). **PARTIAL** live corpus: **23** truncated periods remain — use ops script (oldest-first, ≤40/session); **QFII** gaps = next knife.
