# Residual observe root causes (2026-07-25)

> Status: evidence-only
> Parent: `analysis/foundation_full_audit_20260725.md`
> Label: **FIXED** via `analysis/dual_plane_fix_plan_20260725.md` (DataAccess/health/watermark → canonical)

## 1) `fact_top10_holder_period` yellow — dual-plane lag (real)

### Measured

| plane | tip | meaning |
|---|---|---|
| `accepted_partition` holders | notice **20260725** | formal frontier |
| `canonical_top10_float_holders_period` | notice **20260725** · report max 20260723 | formal SSOT rows present |
| `fact_top10_holder_period` | notice **20260717** · report **20260715** | legacy fact frozen tip |

Canonical has **6 notice partitions / 1112 rows** after fact tip (`20260718`…`20260725`); fact has **0** rows on those notice dates.

### Mechanism

Production holders write path is **`formal_only`** (`holders_aif10._write` → `write_holders_top10_formal_then_mirror`; default **no legacy mirror** — comment: *Legacy fact lags after formal_only sync*).

Doctor `data_health` still scores **`fact_top10_holder_period`** using first date col = **`report_date`** (`DATA_DATE_COLUMN_CANDIDATES`), SLA 168h from `data_layers.yaml` → yellow. Even on `notice_date` it would yellow (tip 20260717).

`formal_holders_watermark()` already prefers canonical — code knew this.

### Why it can get serious

`data_access.yaml` **`holders_top10.table` still = `fact_top10_holder_period`**. Any DataAccess consumer of that bucket reads a plane that **stops advancing** after formal cutover. Dossier prefers canonical when present (mitigated), but the faucet contract is stale.

### Not the story

Not “no shareholder disclosures since 7/15”. Formal/canonical is live through 7/25.

### Next knife (owned)

1. Retarget DataAccess `holders_top10` → canonical (+ notice_date asof), or re-enable explicit legacy mirror with tests.
2. Point data_health SLA at canonical (or demote fact to compatibility / non-blocking).
3. Do **not** mass re-pull; fill gap is mirror policy, not provider hole.

---

## 2) `raw_tushare_daily` tip 20260716 vs accepted OHLCV 20260724 — expected after S7

### Measured

| plane | tip |
|---|---|
| `landing_tushare_daily` → `canonical_nominal_ohlcv_daily` | **2026-07-24** (full-universe days after raw tip) |
| `accepted_partition` `nominal_ohlcv_daily` | **20260724** |
| `raw_tushare_daily` | **20260716** |

Days in canonical after raw tip: 7/17,20,21,22,23,24 (~5522–5526 codes/day).

### Mechanism

`legacy_raw_plane.yaml`: `raw_tushare_daily` **role=fill**, **write=forbidden**; formal domain=`daily`; derive/clean default **from-accepted**. Compatibility table is no longer the writer tip.

Same class as holders fact: **transport strangler left a frozen compatibility plane**.

### Risk if ignored

Scripts/habits that `MAX(trade_date) FROM raw_tushare_daily` will falsely scream “K线断了”. Continuity already uses accepted — OK. Blind raw-tip automation would false-alarm or wrong backfill.

### Next

Keep raw as fill-only; any live consumer of raw tip → retarget accepted/canonical. Optional: data_health ignore / mark fill-plane.

---

## 3) Org soft `under_modern_baseline` ×19 — heuristic, not truncation

Already proven (`org_heuristic_soft_baseline_20260725.md` + canary 2019-03-31): provider_count==landed, re-fetch noop. Soft observe only; hard repair queue=0.

Risk if ignored: someone mass-repairs “19 truncated” forever. Mitigation already shipped (hard vs soft split).

---

## Priority

| # | Severity if left | Action |
|---|---|---|
| 1 holders fact vs DataAccess | **High** (silent stale faucet) | retarget DataAccess / health to canonical |
| 2 raw_daily tip folklore | Medium (false ops) | document + gate consumers off raw tip |
| 3 org soft baseline | Low (already demoted) | none |

**Verdict**: yellow was a **useful smoke** — it pointed at dual-plane debt, not missing market data.
