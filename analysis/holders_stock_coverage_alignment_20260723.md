# Holders coverage alignment audit (2026-07-23)

> Status: evidence-only  
> Scope: `holders_top10` / `holders_aif10` accepted canonical vs provider notice frontier  
> Live DB: `data/smartmoney.duckdb` (+ `reference.dim_active_a_stock`), read-only probes + sparse repair  
> Related: `holders_stock_dossier_lineage_audit_20260721.md` · `shareholder_update_check_design_20260723.md` · `foundation_holders_wm_ops_counters_20260721.md`  
> Label: **BUG FIXED** (`provider_max==wm` same-day sparse) · **HS_A ALIGNED** (16-code repair); BSE residual OUT_OF_SCOPE

---

## Verdict

| Plane | Before | After sparse repair | Notes |
|---|---|---|---|
| Frontier date `MAX(notice_date)` vs provider `UPDATE_DATE` | **ALIGN** `20260723`=`20260723` | **ALIGN** | Date alone ≠ population complete |
| HS A / STAR / ChiNext in recent window (`UPDATE_DATE≥2026-07-16`) | **GAPS: 16** code×notice | **0 miss** | All 16 ∈ `dim_active_a_stock` |
| BSE / 新三板-ish provider codes | **12** code×notice absent | **12 remain** | ∉ dim; never in canonical; serve universe 沪深A |
| Grain / null integrity (canonical) | PASS | PASS | dups w/ `row_seq`=0; null code/notice/name=0 |
| Accepted ↔ canonical partition row sums | PASS (0 mismatch) | PASS | merge path preserved |
| Overall | **GAPS** | **ALIGNED** (沪深A) | BSE = documented residual, not mass target |

**One-line:** 根因 `provider_max≤wm → skip` 漏同日晚披露 — **planner FIXED**（`==wm` 改 same-day sparse miss probe）；数据侧稀疏修 16 只沪深A 后近期窗口 HS_A **无遗漏**；北交所 12 对不进 dim、不修。

---

## 1. Inventory (pre-repair snapshot)

| Plane | Rows | Codes | Notice span / max | Watermark |
|---|---:|---:|---|---|
| `canonical_top10_float_holders_period` | 219,831 | 5,191 | `20190201`→`20260723` | `holders_top10_float` last_data_date=`20260723` |
| `accepted_partition` (`tier0.disclosure.top10_float_holders_period`) | 580 parts / Σ=219,831 | — | `20190201`→`20260723` | — |
| `fact_top10_holder_period` (legacy observer) | 1,726,573 | — | page/notice max `20260717` | tier2 observer |
| Provider 1-row probe | — | — | `UPDATE_DATE=2026-07-23` (code `603659`) | — |

Integrity (canonical full): null/bad code/notice/name/rank/available_at/config_hash = **0**; grain-dup with `row_seq` = **0**; multi-name same rank slots = **1,344** (typed GRAIN allow; same as 2026-07-21 lineage audit).

Orphans vs `ref.dim_active_a_stock` (5,201): canonical orphan **14** (含 B股 `900921`/`900938` + 退市等); dim missing holders **24** (多为近上市) — 与 07-21 审计同量级，**非**本次 notice 缺口主因。

---

## 2. Canary: provider vs local (window `UPDATE_DATE≥2026-07-16`)

API: `../miaoxiang/aif10_scraper` `RPT_F10_EH_FREEHOLDERS` — **available** (fail-closed not triggered).

| notice_date | prov codes | local codes (pre) | HS_A only_prov (pre) | BSE only_prov |
|---|---:|---:|---|---|
| 20260716 | 8 | 7 | — | `838234` |
| 20260717 | 12 | 9 | — | `430685`,`835686`,`920685` |
| 20260718 | 9 | 9 | — | — |
| 20260721 | 14 | 11 | — | `832995`,`833362`,`836412` |
| 20260722 | 18 | 12 | `300192`,`300982` | 4 BSE |
| 20260723 | 17 | 2 | **14 HS_A** | `834391` |

**HS_A miss list (16 pairs)** — all `in_dim=True`, local had older notices only (max ~`202604xx`):

```
20260723: 002318 002458 002879 301360 600233 600288 603659 603683 603861 605020 605228 688005 688078 688208
20260722: 300192 300982
```

Root cause (matches design residual in `shareholder_update_check_design_20260723.md`):  
`sync_holders_aif10_incremental` skipped when `provider_max ≤ formal wm`. Early filers on `20260723` advanced wm to that date; **same-day late filers** never entered `_affected_stocks_since` path until wm moved again. Partition `20260723` had been accepted with only **22** rows / **2** codes (`600346`,`688116`) while provider later showed **170** rows / **17** codes.

**Planner fix (FIXED):** skip only when `provider_max < wm` (`watermark_unchanged`). When `provider_max == wm`, run `_affected_stocks_since(wm)` and sync **only codes missing that notice_date locally**; empty miss → `same_day_coverage_complete` (not permanent equal-wm skip). `provider_max > wm` keeps safety-window incremental. Fail-closed, sparse, no mass.

---

## 3. Sparse repair (executed)

```bash
PYTHONPATH=backend python backend/scripts/ingest_holders_aif10.py \
  --symbols 002318,002458,002879,301360,600233,600288,603659,603683,\
603861,605020,605228,688005,688078,688208,300192,300982
```

| Result | Value |
|---|---|
| ok / fail | **16 / 0** |
| `rows_written` | 235,988 (= per-stock full-history **rewrite amp**, not net new) |
| elapsed | ~145s |
| Mass backfill | **No** |
| Org / Optuna / north star | untouched |

Post-repair:

| Metric | Value |
|---|---|
| canonical rows / codes / max notice | **224,973** / 5,191 / `20260723` |
| accepted `20260722` / `20260723` row_count | **184** / **194** |
| HS_A miss in window | **0** |
| BSE miss | **12** (unchanged; out of scope) |
| accepted↔canonical mismatches | **0** |
| grain dup / nulls | **0** |
| `mart_data_source_watermark.holders_top10_float` | refreshed → `last_data_date=20260723`, `row_count=224973` |

---

## 4. vs prior lineage audit (2026-07-21)

| Item | 2026-07-21 | 2026-07-23 |
|---|---|---|
| Canonical rows | 218,444 | 224,973 (post-repair) |
| Parse / grain integrity | PASS | PASS |
| Post-wm notice codes | 30 (`>20260717`) | 34 then filled; frontier still `20260723` |
| Watermark plane | migrating to canonical | canonical frontier **in use**; skip-on-equal hides same-day lag |
| Serve period streak bug | FIXED | n/a this knife |

---

## 5. Residual / next (no mass)

1. **Same-day late-filer hole** — **FIXED** in `sync_holders_aif10_incremental` (equal-wm sparse miss probe; see §2). Tests: `test_incremental_same_day_*` / `test_incremental_skips_when_provider_strictly_behind_wm`.
2. **BSE 12 pairs** — landing contract allows preserve; incremental uses active universe. Leave unless owner wants BJ in Tier0 holders.
3. **Legacy fact** still max `20260717` (observer only) — expected under formal_only.
4. **dim missing holders ~24** — IPO/coverage hygiene; separate from notice frontier.

---

## 6. Label

**FIXED** (planner skip + 沪深A recent-window coverage) · residual owner = optional BSE land / dim IPO hygiene · next verification = re-run provider window canary after next trading-day notices (expect equal-wm late filers to sparse-sync without `--symbols` repair).
