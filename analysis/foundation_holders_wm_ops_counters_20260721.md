# 0r.5b — formal holders WM/SLA + split ops counters + 机构档案 honesty (2026-07-21)

> Status: evidence-only / foundation knife 0r.5b
> Label: **FIXED** subset (three follow-ons from holders lineage audit §5 landed)
> Authority: `analysis/holders_stock_dossier_lineage_audit_20260721.md` (0r.5 PARTIAL),
> `analysis/product_plan_reeval_stock_dossier_20260721.md` §2 (0r.5b next knife), goal.md Tier0.
> Predecessor: `6afea30fc` (0r.1–0r.4 serve whitelist + formal continuity).

## Scope (audit §5 next-knife #1/#2/#3)

| # | Follow-on | Before | After |
|---|---|---|---|
| 1 | Holders freshness watermark | legacy `fact_top10_holder_period.page_update_date` (lags after formal_only sync) | **formal canonical notice frontier** `MAX(canonical.notice_date)`; legacy fact demoted to strangler observer |
| 2 | Sync ops counter clarity | single `rows_written` (= per-stock full-history rewrite amplification, read as "987k new") | split: `affected_stocks` / `net_new_notice_rows` / `notice_partitions_touched` / `rewrite_amplification_rows` + `watermark_source` |
| 3 | 机构档案 deep-link honesty | dossier holder chip linked to `#/institutions/:holder` **unconditionally** (fake link at ~54% profile coverage) | per-row `has_institution_profile` (join `mart_inst_profile`); UI links only when true; coverage stated |

## 1. Formal holders watermark (WM/SLA)

`services/holders_aif10.py::formal_holders_watermark(conn)`:
- Prefers `MAX(notice_date)` on `canonical_top10_float_holders_period` (land→accept plane).
- Falls back to legacy fact `page_update_date` only when canonical absent (pre-migration DB).
- Returns `(watermark, watermark_source)` where source ∈ `{canonical_notice_frontier,
  legacy_fact_page_update_date, empty}`.

`sync_holders_aif10_incremental` now drives the incremental scan window off this formal
frontier (was legacy fact). Because canonical is ahead of the lagging fact after
formal_only sync (audit: canonical notice `20260722` vs fact `20260717`), the scan window
is **narrower and more accurate**, not wider — no mass re-pull, period-domain hard lock
(goal 禁令) untouched.

`services/source_watermarks.py` DOMAIN_SPECS: `holders_top10_float` repointed to canonical
`notice_date`; new `holders_top10_float_legacy_observer` (tier 2) keeps legacy fact
freshness visible for dual-path diagnosis but is **not** holders publication truth.

## 2. Split ops counters (kill the "76 / 987k" ambiguity)

`sync_holders_aif10_incremental` result now reports, distinctly:

| Field | Meaning |
|---|---|
| `affected_stocks` | # stocks in the UPDATE_DATE≥wm−safety window (provider changed) |
| `net_new_notice_rows` | canonical rows with `notice_date > pre-sync watermark` (honest net-new) |
| `notice_partitions_touched` | distinct new notice_date partitions |
| `rewrite_amplification_rows` | = legacy `rows_written`; per-stock full-history rewrite sum (NOT net-new) |
| `watermark` / `watermark_source` | formal frontier + which plane it came from |

Audit fact reproduced: net-new post-wm ≈ 380 rows / 30 codes, while `rows_written` ≈ 987k is
rewrite amplification. Counters now name the difference instead of conflating them.

## 3. 机构档案 honesty (no fake deep-link at ~54%)

`routers/stock_dossier.py`:
- Attaches `feature_store` read-only; `_institution_profile_holders` joins holder norms →
  `mart_inst_profile`; per holder row sets `has_institution_profile`.
- `holders.institution_profile = {holders_total, holders_with_profile, coverage, note}`.
- gap `institution_profile_partial_no_deep_link_when_absent` when partial.
- Fail-open to unknown (all false) if feature_store absent — never invents a profile.

Frontend `StockDossierPage`: holder chip is a `<Link>` only when `has_institution_profile`;
otherwise plain text with tooltip "无机构档案（覆盖~54%）— 不做假链接"; coverage badge in header row.

## 4. Tests (red-first behavior locked)

| Test | Locks |
|---|---|
| `test_formal_watermark_prefers_canonical_notice_frontier` | canonical ahead → frontier used |
| `test_formal_watermark_falls_back_to_legacy_when_no_canonical` | pre-migration fallback |
| `test_net_new_notice_since_splits_amplification_from_new` | net-new ≠ rewrite amplification |
| `test_dossier_institution_profile_honesty` | per-row link gate + coverage + gap |

Blocking tier: **956 passed** (`run_ci_pytest.py --tier blocking`). Frontend `tsc + vite build` clean.

## 5. Non-goals / guardrails held

- No mass org/holders re-pull; period-domain incremental hard lock unchanged.
- No DUPLICATE_GRAIN loosen; no legacy fact re-mirror; no watermark policy that back-fills holes.
- No fake profile joins; absent profile ≠ "no institution" — stated in note.
- Holder PnL / true holding-cycle engine still **BLOCKED** (product unknown by design).

## 6. Label

**FIXED** (0r.5b follow-ons 1–3). Residual (unchanged, own elsewhere): holder return/cycle
engine BLOCKED; institution profile *population* coverage improvement (54%→) is a separate
Tier3 rebuild knife, not a serve-layer honesty gap.
