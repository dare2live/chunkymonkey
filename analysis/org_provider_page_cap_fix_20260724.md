# org_holding East Money 100-page cap fix (2026-07-24)

> Status: evidence-only · Label: **PARTIAL** (code shipped; canary repair in flight)

## Root cause (live repro)

| Probe | Result |
|-------|--------|
| `RPT_MAIN_ORGHOLDDETAIL` + `REPORT_DATE='2025-12-31'` page 1 | `count=832906`, `pages=417`, `page_size=2000` |
| Same filter page 101 | `pages=0`, `data=[]` |
| Local before canary `2025-12-31` | `180449` rows, `1185` stocks (canonical same) |

East Money v1 returns at most **100 pages per filter query**. With `PAGE_SIZE=2000` the silent ceiling is **~200k rows** while page-1 `count` stays at full market mass (~640k–830k). `fetch_all_pages` treated `pages=0` on page>1 as completion.

Population gate `min_accepted_stocks=500` did **not** catch this (1185 stocks > 500).

## Fix (Knife A)

**miaoxiang** (`aif10_scraper`):

- `pagination.py`: truncate-aware loop, `plan_security_code_shards` (bisect `SECURITY_CODE` numeric range until probe `pages≤100`).
- `batch.fetch_all_pages`: delegate to truncate-aware fetch; log truncation.
- `batch.fetch_all_pages_sharded`: shard + merge; metrics `provider_count`, `fetched_rows`, `truncated`, `shard_count`.

Filter syntax (live): `(SECURITY_CODE>=600000)(SECURITY_CODE<=699999)` — **no quotes** on numeric bounds.

**chunkymonkey**:

- `org_holding_aif10._fetch_period` → `fetch_all_pages_sharded` (`EASTMONEY_MAX_PAGES=100`).
- `sync_period` fail-closed on `provider_truncated` (no partial accept).
- `pagination_integrity.py` typed land verdict + gap heuristic (`≈200k` cap signature, stocks ≪ baseline).
- `org_holding_population`: `provider_truncated` → `repair_fetch_period` (single-period sharded refresh only).

Tests: `miaoxiang/tests/test_pagination.py`, `backend/tests/services/test_pagination_integrity.py`, org gap/truncation tests.

## Canary (single period)

Period: **`2025-12-31`** (`allow_existing_refresh=True` only — not full history).

| | Rows | Stocks | API `count` |
|---|-----:|-------:|------------:|
| Before (pre-fix local) | 180449 | 1185 | 832906 |
| After aborted canary (2026-07-24) | 495209 | 3589 | 832906 |
| After fetch-only validation | _(see `org_fetch_validation_20260724.json`)_ | — | 832906 |
| After full single-period repair | _(pending when fetch validates)_ | — | 832906 |

Logs: `analysis/org_canary_repair_20260724.log` (aborted), `analysis/org_fetch_validation_20260724.log`

DoD: stocks ≥ ~0.95×max baseline (~5520), rows within ~0.2% of API count.

## Knife B — daily_update anti-truncation (general)

Contract: `services/data_sources/pagination_integrity.py`

- Inputs: page-1 `expected_count`, `landed_rows`, `page_size`, `max_pages_per_query`.
- Output: `PaginationIntegrityVerdict.truncated` + reasons.
- **org**: enforced on fetch + gap (`provider_truncated` → bounded `repair_fetch_period`).
- **Other paginated surfaces** (detection contract / follow-up wiring):

| Surface | Mechanism | Status |
|---------|-----------|--------|
| `org_holding_aif10` | aif10 sharded + integrity | **enforced** |
| `holders_aif10` / `qfii_client` | `fetch_all_pages` (truncate-aware loop) | observe → extend sharding if count>100×page_size |
| `sync_registry` paged domains | `page_limit` in registry | existing; add integrity compare where page-1 total known |
| `moneyflow` / top_inst pages | registry offset loops | soft: compare landed vs probe total where cheap |

Policy (goal.md): **truncated land ≠ complete** — next action = **bounded repair** (one period / one partition), not skip-as-ok.

Config notes: `backend/config/serve_derive_closed_loop.yaml` (`org_holding_formal`, integrity patterns).

## Residuals

- holders/qfii: truncate-aware `fetch_all_pages` only; no SECURITY_CODE sharding until a live count proves cap breach.
- Full canary runtime: multi-shard ~830k row pull is long; ops use explicit single-period repair only.

## SHAs

_(filled after push)_
