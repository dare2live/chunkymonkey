# Dual-plane faucet hygiene — fix plan (2026-07-25)

> Status: evidence-only · execution plan (this knife)
> Authority: `foundation_residual_rootcause_20260725.md` · MASTER § transport vs serve
> Ban: mass re-pull · re-enable silent legacy mirror · Continuity READY cosmetics · RX

## Problem class

Transport strangler left **compatibility planes frozen** while **formal/canonical** advances.
Ops/health/DataAccess still pointed at the frozen plane → yellow/false frontier / silent stale reads.

| ID | Symptom | Root | Severity |
|---|---|---|---|
| H1 | doctor yellow `fact_top10_holder_period` | formal_only; fact tip 20260717; canonical 20260725 | **High** (DataAccess faucet) |
| H2 | `raw_tushare_daily` tip ≪ accepted OHLCV | fill/write=forbidden; land→canonical is SSOT | Medium (ops folklore) |
| H3 | org soft under_modern ×19 | heuristic vs modern baseline | Low — **already FIXED** |

## Decision (Occam)

**Do not** re-enable default legacy mirror (rewrites fact forever; banned mass dual-write).
**Do** retarget **read/health/watermark** to the formal tip plane.

```
landing → validate → accepted canonical  = truth
compatibility fact / raw_tushare_daily   = fill/rebuild residual only
```

## Knives (one logical delivery this session)

### K1 — Holders serve faucet → canonical (blocking)

1. `data_access.yaml` `holders_top10`:
   - `table: canonical_top10_float_holders_period`
   - `asof_col: notice_date` (formal availability axis)
   - columns ⊆ canonical (drop fact-only fields not on canonical)
2. `data_layers.yaml`:
   - `canonical_top10_float_holders_period`: daily SLA 168h + **date_column=notice_date**
   - `fact_top10_holder_period`: demote to `on-demand` (compat observer; not publication SLA)
3. `data_health_snapshot.py`: honor `table_health_overrides.*.date_column`
4. `update_watermark_sla.py` `holders_top10_float` probe → `MAX(notice_date)` on canonical
5. `stock_screener` name lookup: prefer canonical, fallback fact
6. Tests: access yaml points canonical; health override; watermark query string

### K2 — Fill-plane tip hygiene (same commit if small)

1. Document in plan + PROJECT_INDEX: never use `MAX(raw_tushare_daily)` as frontier
2. If `raw_tushare_daily` appears in health inventory: override `on-demand` / non-expiring
3. No writer change; no mass fill of raw

### Out of scope

- Org soft baseline (done)
- Rebuilding fact from canonical (optional later ops; not required for serve)
- RX / Optuna

## Acceptance

| Check | Pass |
|---|---|
| DataAccess holders table = canonical | yes |
| Live `formal_holders_watermark` unchanged (already canonical) | yes |
| data_health: fact not yellow-for-stale-publication; canonical green on notice tip | yes |
| Watermark probe SQL uses canonical notice | yes |
| Blocking pytest holders/access/health related | green |
| No mass provider fetch | yes |

## Rollback

Revert yaml + health override + watermark query; dossier already dual-reads.

## Label after ship

`FIXED` (H1+H2 hygiene) · residual: fact table remains freeze-lagged as intentional compat (observe-only).

### Ship evidence (2026-07-25)

- data_health dry-run: **verdict=PASS** · yellow=0 · red=0
- `canonical_top10_float_holders_period` last_data_date=**20260725** (notice) severity=green
- `fact_top10_holder_period` on-demand observer green (not publication SLA)
- DataAccess `holders_top10.table=canonical_top10_float_holders_period` · `asof_col=notice_date`
