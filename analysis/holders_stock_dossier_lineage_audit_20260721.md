# Holders × stock-dossier lineage audit (2026-07-21)

> Status: evidence-only / association + lineage attestation for stock-dossier peers  
> Scope: broader than 2-stock samples — full `canonical_top10_float_holders_period` + legacy `fact_top10_holder_period` + land→accept→serve joins  
> Live DB: `data/smartmoney.duckdb` (+ `reference` / `feature_store` attach), read-only probes 2026-07-21 night  
> Related: `foundation_daily_update_degraded_rca_20260721.md` §B (sample PASS); `product_plan_reeval_stock_dossier_20260721.md`  
> Label: **PARTIAL** (parse integrity PASS; association/serve readiness not full PASS)

---

## Owner Q — 样本正常是否代表全量正常？

**No.** Sample rows looking sane ≠ population association / serve readiness.

| Claim surface | Sample (RCA §B, ~6 rows / 2–4 codes) | Full population (this audit) | Verdict |
|---|---|---|---|
| Parse integrity (code/name/rank/ratio/dates) | PASS | **218,444** canonical rows: null/bad_code/empty_name/grain_dup = **0** | **PASS** |
| Incremental “76 codes / ~987k rows” = clean new associations | implied | 76 = stocks with `UPDATE_DATE≥wm−7d`; 987k = **per-stock full-history rewrite** sum (`rows_written`), not net new notice rows | **PARTIAL** (counter ≠ coverage) |
| New notices since legacy wm `20260717` | 2–4 stocks shown | notice `20260718/21/22` → **30** codes / **380** rows (113+136+131) | **PASS** for filled window; **not** 76 |
| Stock↔holder join for dossier MVP | works on samples | dim∩canonical∩form∩industry = **5,117 / 5,200** (98.4%) | **PASS** (MVP bricks) |
| Holder↔institution join | not measured | unique `holder_name_norm` → episode **99.3%**; → `mart_inst_profile` **54.2%** | **PARTIAL** |
| Serve period streak / Δ | assumed OK | dossier read period streak from **stale fact** while rows from canonical (30 stocks notice-ahead) — **bug FIXED** this knife | was **BLOCKED** → **FIXED** |
| Legacy fact as truth / watermark | ignored | fact `MAX(page_update_date)=20260717`; grain-dup groups **13,131**; formal_only skips mirror | **PARTIAL** (observability lag) |

**One-line answer:** 样本 PASS 只证明 formal 解析路径没把列洗坏；**不**证明增量 76/987k 是净新增、不证明股东档案可链机构档案、不证明展示层周期/Δ 正确。

---

## 1. Holders_top10 integrity (full plane)

### 1.1 Volumes

| Plane | Rows | Distinct codes | Notice span | Exits |
|---|---:|---:|---|---:|
| `landing_miaoxiang_holders_top10` | 1,942,843 | (via 1,786 batches) | batches `20190201`→`20260722` | — |
| `canonical_top10_float_holders_period` | **218,444** | **5,191** | `20190201`→`20260722` | 44,148 |
| `fact_top10_holder_period` (legacy) | 1,726,573 | 5,190 | notice≤`20260717`; `fetched_at` max `2026-07-16` | 328,084 |
| `accepted_partition` (`tier0.disclosure.top10_float_holders_period`) | 554 parts / row_sum **218,444** | — | `20190201`→`20260722` | — |

Tonight click window (`21:50–22:08` CST): **451** partitions accepted / **209,152** canonical rows rebuilt (`built_at` in window). That is mostly **historical formal re-accept** triggered by per-stock full-history incremental for 76 symbols — not 209k brand-new disclosures.

### 1.2 Association integrity rates (canonical FULL)

| Check | Count | Rate | Verdict |
|---|---:|---:|---|
| null / empty `stock_code` | 0 | 0% | PASS |
| non-`\d{6}` code | 0 | 0% | PASS |
| empty / numeric / code-eq `holder_name` | 0 | 0% | PASS |
| null `holder_name_norm` | 0 | 0% | PASS |
| bad `notice_date` / `report_date` shape | 0 | 0% | PASS |
| `notice_date` < `report_date` | 0 | 0% | PASS |
| bad rank (∉1..50) / ratio ∉[0,100] | 0 | 0% | PASS |
| full-grain duplicates | 0 | 0% | PASS |
| same rank, multiple names (`row_seq`>1) | 1,344 rank-slots | 0.78% of non-exit rank slots | PASS (typed; GRAIN allows) |
| null `available_at` / `config_hash` / batch | 0 | 0% | PASS |
| distinct `config_hash` / `contract_version` | 1 / 1 | — | PASS |

Board prefixes (non-exit): 60/00/30/68 dominate; **2** B-share codes `900921`/`900938` in canonical (venue edge). Dossier serve rejects non-沪深A via `classify_exclusion`.

**Legacy fact:** same parse-null metrics ≈0, but **13,131** grain-dup groups (legacy `row_seq=1` collisions; e.g. 15 distinct names at same rank). Formal `assign_unique_holders_row_seq` fixed this on accept path — fact remains NONCONFORMING strangler.

### 1.3 Coverage vs watermark / expected notices

| Item | Value |
|---|---|
| Legacy watermark probe | `MAX(fact.page_update_date)=20260717` (still) |
| Incremental safety since | ~`2026-07-10` (`wm − 7d`) |
| Log claim | `affected=76 rows=987036 exits=3941 errors=[]` |
| Codes with `notice_date ≥ 20260710` in canonical | **67** |
| Codes with `notice_date > 20260717` | **30** (`20260718`=9, `20260721`=11, `20260722`=10) |
| Orphan vs `dim_active_a_stock` | canonical orphan **14** (incl. 2 B-shares + delisted); dim missing holders **23** (mostly recent IPOs) |

**Interpretation of 76 / 987k:** `_affected_stocks_since` returns stocks with any provider `UPDATE_DATE≥since`; `sync_holders_aif10` then rebuilds **each stock’s full history** from `start_period` and sums `canonical_rows` written. So 987k is **write amplification**, not “987k new notice associations.” Net new post-wm notice population ≈ **380 rows / 30 codes**.

### 1.4 Land → accept → serve lineage

| Check | Evidence | Verdict |
|---|---|---|
| Landing → accepted coverage | partitions with landing but no accept = **[]** | PASS |
| Accepted ↔ ACCEPTED ingest batch | mismatch = **0** | PASS |
| `config_hash` | `6e0c721f…4df3a9` (matches live `HoldersTop10Contract`) on all 554 parts | PASS |
| `contract_hash` / version | single `33422fa9…` / `2` | PASS |
| `available_at` on accepted + canonical | 0 null | PASS |
| Historical REJECTED `DUPLICATE_GRAIN` | 4 batches (pre-row_seq fix); **all 4 partitions later ACCEPTED** | PASS (recovered) |
| Stuck `LANDED` batch | 1 orphan batch id on `20260715`; partition later ACCEPTED | PARTIAL (hygiene) |
| Watermark / SLA vs formal frontier | still largely legacy-fact probed | PARTIAL |
| Serve (`stock_dossier._load_holders`) | prefers canonical; period streak **was** fact-lagged | FIXED this knife |

---

## 2. Stock dossier ↔ holders ↔ institutions readiness

### 2.1 Can we join today?

| Join | Metric | Verdict |
|---|---|---|
| dim ∩ canonical holders | 5,177 / 5,200 | PASS |
| dim ∩ form (`trade_date=20260721`) | 5,117 / 5,200 | PASS |
| **dossier MVP bricks** dim∩can∩form∩industry | **5,117 / 5,200 (98.4%)** | **PASS** |
| Latest-report holder rows → `fact_inst_episode` (holder×stock) | 51,315 / 51,971 (**98.7%**) | PASS |
| Distinct holders → episode | 43,743 / 44,069 (**99.3%**) | PASS |
| Distinct holders → `mart_inst_profile` | 23,879 / 44,069 (**54.2%**) | **PARTIAL** |
| Episode stocks orphan vs canonical | 0 | PASS |
| Freshness: canonical notice ahead of fact | **32** stocks (30 post-wm-only-in-can) | PARTIAL (formal_only by design) |

**Answer:** Stock dossier can join **stock → holders (canonical) → form/industry** for ~98% of active A-shares today. Linking a holder chip to **institution archive profile** is only ~54% (episodes exist for almost all; profile mart is thinner / low-sample filtered). Do not claim “股东档案 ↔ 机构档案” as full PASS.

### 2.2 Brick L0–L3 vs process vs display

| Layer | Intent (`data_brick_architecture`) | Holders/dossier today | Gap |
|---|---|---|---|
| L0 Evidence | landing payload preserved | `landing_miaoxiang_holders_top10` 1.94M | OK |
| L1 Accepted | canonical + `accepted_partition` | formal path PASS; legacy fact dual-truth | watermark still on fact |
| L2 Primitive | form / identity / typed holder rows | form + canonical holders | stock_name still from fact |
| L3 Composite | inst episode / profile | episodes strong; profile half | profile rebuild / filter honesty |
| Display (R1) | dossier API / `#/stock/:code` | MVP layers; observation label | PnL/cycle unknown; period streak FIXED |
| Process | incremental formal_only | 76-stock full-history rewrite | counter/ops semantics confusing |

### 2.3 Coordination note (peer dossier agent)

- Do **not** treat RCA sample tables as readiness.
- Consume this file via API `lineage.audit` + `lineage.status=attested_partial`.
- UI may keep showing `gaps[]` / unknown PnL; do not invent join rates in the page.
- Avoid editing workbench Capability E files from this knife (non-overlap).

---

## 3. Fail-closed fix shipped this knife

**Bug:** `_load_holders` selected rows from `canonical_*` but computed `prev_report_date` / `approx_periods_present` from `fact_top10_holder_period`. After formal_only sync, fact lags → latest report missing from streak (e.g. `002161` can report `20260714` vs fact max `20260331`).

**Fix:** period/presence queries use the **same source plane** as rows. Regression: `test_dossier_canonical_period_streak_not_fact_lag`.

No gate loosen. No mass backfill. No watermark policy change in this knife.

---

## 4. Overall metrics scorecard

| Area | Label |
|---|---|
| Formal parse / grain integrity (canonical full) | **PASS** |
| Land→accept lineage (config/partition/available_at) | **PASS** |
| Incremental coverage since wm (notice filled) | **PASS** (30 codes; not 76) |
| Sync counter semantics (76/987k) | **PARTIAL** |
| Stock↔holder↔form dossier MVP join | **PASS** (~98%) |
| Holder↔institution profile join | **PARTIAL** (~54%) |
| Serve period/Δ honesty | **FIXED** (was BLOCKED) |
| Legacy fact / watermark dual-path | **PARTIAL** |
| Holder return / true cycle engine | **BLOCKED** (product unknown by design) |

**Overall: PARTIAL**

---

## 5. Next knives (ordered)

1. **Watermark/SLA probe → formal accepted notice frontier** (stop advertising legacy `page_update_date` as holders truth).  
2. **Ops counter clarity** — log `affected_stocks`, `notice_partitions_touched`, `net_new_notice_rows` separately from `rows_written` amplification.  
3. **Institution profile coverage** — explain 54% (low_sample filter vs rebuild lag); only then claim dossier↔机构 deep link.  
4. **Optional:** drain orphan `LANDED` ingest row hygiene; B-share rows policy in canonical vs serve reject.  
5. **Do not:** mass re-mirror to fact; loosen DUPLICATE_GRAIN; fake profile joins; claim sample PASS = full PASS.

---

## 6. Evidence commands (reproducible)

```bash
# volumes + integrity (smartmoney read-only)
PYTHONPATH=backend python - <<'PY'
import duckdb
con = duckdb.connect("data/smartmoney.duckdb", read_only=True)
print(con.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM canonical_top10_float_holders_period").fetchone())
print(con.execute("""
SELECT COUNT(*) FROM accepted_partition
WHERE dataset_id='tier0.disclosure.top10_float_holders_period'
""").fetchone())
PY

# targeted dossier regression
pytest backend/tests/test_stock_dossier_api.py -q
```
