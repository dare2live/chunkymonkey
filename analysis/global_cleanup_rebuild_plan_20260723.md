# Global cleanup / rebuild plan — 2026-07-23

> **生命周期**：evidence-only plan (execution board; no knife code in this commit)
> Status: **EXECUTING** (knives land one-by-one; this file is the board)
> Authority: `goal.md` · `AGENTS.md` · eng_gov §15 · first-principles: orphan → **delete capability** (not tombstone patch); live domains keep **idempotent replay**
> Parents / evidence: `foundation_residual_fix_plan_20260723.md` · `db_bloat_deep_dive_20260723.md` · `db_refill_after_delete_audit_20260723.md` · `db_storage_hygiene_20260721.md` · `margin_calendar_catchup_blocker_20260723.md`
> Tooling snap (boot): moth `WARN` (dirty worktree + complexity 80 high leads); codegraph synced; agent-boot overall=warn
> Label: **IN PROGRESS** — Knife 1 **FIXED** (update-flow margin; `margin_catchup_live_20260723.md` 补跑≠正解); Knife 2 FIXED path (skip-land); Knife 3 **FIXED** (market reclaim + qfq in-module post-CTAS compact); next = #4 continuity

---

## 0. Executive order (Occam)

| # | Knife | Why | State now |
|---|---|---|---|
| **0** | Uncommitted inventory serialize | Same `sync_registry.yaml` / shared docs — moth overlap; **ban half-baked** | **CLOSED** with Knife 1 ship |
| **1** | Margin 1b ship (+ **update-flow** catchup) | Continuity lag / calendar due must not require operator one-shot | **FIXED** — acquire wires `margin_catchup_acquire`; v3 accepted frontier; ImportError unblocked; tests stale→run / current→skip; evidence `margin_catchup_live_20260723.md` (补跑≠正解) |
| **2** | Holders ACCEPTED+payload_hash → **skip-land** | ~32× landing re-land storm; largest ongoing bloat | **FIXED path** (historical multiplicity retained) |
| **3** | Market qfq free-block compact (+ recurrence in update flow) | ~0.7 GiB free after DROP+CTAS | **FIXED** — reclaim 1.439→0.719 GiB (free 2940→1); post-CTAS compact **inside** `build_price_kline_qfq_tushare` (not orchestrator补跑); evidence `market_compact_knife3_20260723.md` |
| **4** | Continuity residuals (honest) | dividend/hsgt WARN — after margin path honest | After #1 live verify / token catchup |
| **∅** | Org mass / Optuna / READY cosmetics / S7 fake COMPAT / margin product thaw | Banned | — |

Cross-link: residual margin board stays `analysis/foundation_residual_fix_plan_20260723.md` (pointer back from §8).

---

## 1. Inventory (places that need change)

Risk tiers follow `commit_tiers.yaml` / eng_gov §15 (L3 = writer/PIT/schema/config/deletion).

| Area | Owner / file / symbol | Moth coupling (fan-in) | CodeGraph callers (blast) | Risk | Need |
|---|---|---|---|---|---|
| **Factor retirement (capability delete)** | `sync_registry.yaml` tombstone; `legacy_raw_plane` `retired`; `foundation_done.yaml` S7 22+1; lifecycle manifest; tests `test_legacy_raw_plane_s7` | `stk_factor_pro` **32** (mostly docs/tests after delete) | registry consumers via `sync_runner` / plane checks; **0** DataAccess | L3 (done) | **FIXED in `a75288129`** — live probe: table absent; `tushare_raw` ≈4.68 GiB |
| **Margin 1b (bounded catchup)** | `sync_registry` margin `execution_policy`/`contract_version=3`; `margin_catchup.py`; `margin_catchup_acquire.py`; `sync_runner`; `acquire.py`; `frozen_domain_observe.py`; margin_* acceptance/evidence/ingest/scope; tests + `ci_pytest_surface` | `margin_catchup` **6**; `sync_registry` **90** (shared hub) | `assert_margin_accepted_population_scope` → ingest/runner; `MarginFragment` → catchup | **L3** | **FIXED** — click-update acquire plans v3 gap; accepted frontier; no all-due thaw; see `margin_catchup_live_20260723.md` |
| **Holders skip-land** | `disclosure_transport.land_disclosure_partition_from_rows` (uuid `batch_id`); `land_holders_top10_batch`; planner/catchup that re-pulls ACCEPTED partitions | `land_holders_top10_batch` **6**; `disclosure_transport` **16** | land → disclosure_transport (6); contract loaders tested | **L3** | **FIXED path** — ACCEPTED+same `payload_hash` skip; historical ~32× remains until retention |
| **Holders retention (later)** | landing archive of non-latest ACCEPTED batches; then `db_compact --db smartmoney` | same + lifecycle | — | L3 + ops | **After** skip-land proven; ban bare DELETE landing |
| **QFQ derive / CTAS** | `build_price_kline_qfq_tushare.build` (`DROP`+CTAS+`CHECKPOINT`); triggers `pipeline/clean.py`, `derive_runtime`, ops `derive_qfq` | `build_price_kline_qfq_tushare` **23–24**; `derive_qfq` **9** | clean/derive_runtime entrypoints | L2/L3 (script+pipeline) | Rebuild **must** (latest-adj); **qfq module owns post-CTAS compact** (Knife 3) — update 不遗漏；orchestrator 不知 free pages |
| **db_compact / lifecycle** | `backend/scripts/db_compact.py`; `db_lifecycle_delete.py` | `db_compact` **13** | lifecycle → compact; tests `test_db_compact` | L3 ops | Compact after DROP/CTAS; **does not refill**; needs exclusive write lock |
| **Closed-loop / derive peers** | `institution_profile` rebuild_all (delta-gate); DC industry view CTAS; `market_pulse` rare rebuild_all | process path | keep delta skip | L2 | **Do not** undo existing skip; not cleanup target |
| **Continuity / SLA** | `check_continuity_integrity.py`; watermark SLA; frozen observe | continuity + registry | factor refs remain as **historical WARN examples** only | L2 | After margin verify; fix typed wrongness only — no silence-by-delete |
| **Docs / maps / board** | `goal.md` / FEATURE_MAP / PROJECT_INDEX / lineage / agent_context | projection | generated board | L1 | Follow each knife; no enforcement from BOARD |
| **Tests** | margin_* · holders acceptance · legacy_raw_plane · continuity · qfq build · db_compact | per domain | — | with knife tier | Knife-local; blocking pytest surface updates when new modules land |

### 1.1 Uncommitted / half-baked inventory (binding — name it)

Snapshot at plan authoring (`main` = `a75288129`, origin aligned):

| Bucket | Paths (representative) | Verdict |
|---|---|---|
| **Factor retirement** | Already on `main`: registry tombstone, plane `retired`, foundation_done wall, lifecycle manifest, S7 tests | **Closed** — do not re-open “disabled writer patch”; capability gone (`KeyError` on sync domain) |
| **Margin 1b WIP** | `M` registry/margin services/sync_runner/acquire/frozen_observe + tests; `??` `margin_catchup.py`, `margin_catchup_acquire.py`, `test_margin_catchup.py`, `margin_v3_bounded_catchup_1b_20260723.md`; residual/calendar/refill analysis edits | **Must ship as one L3 knife** (or abort cleanly) — **do not** leave enabled v3 registry without catchup module committed |
| **Shared hub hazard** | `sync_registry.yaml` touched by factor (done) **and** margin (WIP) | Serialize; never parallel agents on registry |
| **Noise / do not commit with knives** | `.playwright-cli/` (if reappears); generated board/lineage churn unless knife owns it | Exclude from cleanup knives unless required for gate |

Live probes (read-only): `raw_tushare_stk_factor_pro` **absent**; `market.duckdb` **0.719 GiB** free_blocks=**1** after Knife 3 (was ≈1.44 GiB / free≈2940).

---

## 2. Principles (first-principles)

| Principle | Meaning | Apply |
|---|---|---|
| **Owned vs retired** | Owned = live writer + contract + consumer; retired = **no registry domain / no refill path** | Factor = retired capability. Margin = owned under v3 bounded policy. Holders/qfq = owned |
| **Delete capability ≠ compact space ≠ skip-land** | Three different tools | **Delete capability** removes writer (orphan). **Compact** reclaim file holes after DROP/CTAS. **Skip-land** stops accidental append storms without deleting evidence semantics |
| **Orphan → delete capability** | Not “keep sync + patch anti-refill” | Factor P0 pattern; future orphans same |
| **Live domains keep idempotent replay** | `replace_partition` / grain DELETE→INSERT / accepted rebuild from landing = **must-have** | Do not “optimize” by merging landing+canonical or banning replay |
| **Landing append is evidence, not bug** | New content → new batch OK | Same ACCEPTED partition + same `payload_hash` → skip is the bugfix |
| **Derive full rebuild is semantic; uncompacted CTAS is ops debt** | latest-adj needs rebase | Compact after CTAS or change write shape later — don’t pretend CHECKPOINT shrinks files |
| **Continuity READY ≠ code green** | Observe can stay non-READY | Ban cosmetics / deleting checks |

---

## 3. Ordered knives

### Knife 0 — Serialize dirty tree (meta, before code knives)

- **Exit**: WT either (a) only unrelated noise, or (b) exactly one logical knife staged; moth non-overlap for any parallel agent
- **Ban**: mixing factor leftovers with margin; `git add .`; committing `.playwright-cli`
- **Parallel**: **none** (registry hub)
- **Blast**: process only

### Knife 1 — Margin v3 bounded catchup **in update flow** (L3) — **FIXED**

- **Scope**: acquire orchestrator bridge + transport import fix; evidence `margin_catchup_live_20260723.md` (补跑≠正解)
- **Exit**:
  1. Rule10 + `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh` + push async
  2. Tests: stale local_max → schedule once; current → skip; `run_acquire` wires planner
  3. Click-update / `daily_update` discover calendar gap without hardcoded dates
  4. New generation canonical **no BSE**; v2 read-only; frontier = accepted `contract_version=3`
  5. Continuity not “READY” by deleting observe; rzrqye stays UNTRUSTED
- **Ban**: framing one-shot CLI as the fix; `--all-due` margin; mass history; product thaw; org mass; coupling into dossier/holders
- **Parallel**: only non-overlapping docs; **not** holders/qfq/registry peers
- **Blast**: `margin_catchup_acquire` + `sync_runner` publish path; acquire Step 2.955 only

### Knife 2 — Holders skip-land (L3) — **SHIPPED** (path)

- **Scope**: `disclosure_transport` / `holders_top10_acceptance` / `disclosure_dual_write`; tests for ACCEPTED+same hash → no new landing rows
- **Exit**:
  1. Rule10 + `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh` + push async
  2. Re-land identical ACCEPTED partition does not grow landing; canonical unchanged
  3. Historical multiplicity (~32×) **not** deleted here — retention/archive = later knife
  4. No org mass; no bare DELETE landing
- **Ban**: DROP/DELETE landing as “dedupe”; changing PK to `row_hash`; touching org mass refresh
- **Parallel**: OK vs market compact **if** moth shows non-overlap (different DB files) **and** no shared disclosure files
- **Blast**: disclosure land path (fan-in 6–16); smartmoney write lock during tests/live

### Knife 3 — Market compact + recurrence in update flow (L3) — **FIXED**

- **Scope**: (1) one-shot reclaim OK; (2) **must** fix recurrence inside qfq/market module after DROP+CTAS — not orchestrator补跑 / free-page coupling
- **Exit** (measured 2026-07-23 ~14:51 CST + hook):
  1. Before: **1.439 GiB**, free_blocks=**2940**; After: **0.719 GiB**, free_blocks=**1**; qfq rows **8,412,670** preserved; bak removed
  2. Recurrence: `build_price_kline_qfq_tushare.main` default `compact_market_after_ctas` after successful rebuild (prod path only); escape `--no-compact` / `CHUNKY_QFQ_SKIP_COMPACT=1`; compact fail → rc=3
  3. Boundaries: `pipeline/clean` + `derive_runtime` still only invoke qfq builder; **no** DuckDB free-page logic in daily_update orchestrator
  4. Evidence: `analysis/market_compact_knife3_20260723.md` + pytest `test_main_compacts_market_after_successful_rebuild`
- **Ban**: compact-as-only-fix; orchestrator knowing free pages; VACUUM myths; holders landing DELETE; margin thaw; Continuity READY cosmetics
- **Parallel**: not with smartmoney holders land / tushare_raw writers (peer margin other DB OK)
- **Blast**: `market.duckdb` exclusive during reclaim; derive/clean callers via existing qfq entry
- **Residual for 100% foundation bar**: market free-block **cleared + update-flow recurrence FIXED**; holders ~32× historical **KEEP** (retention later); Continuity WARN honest; incremental-qfq product knife **deferred**

### Knife 4 — Continuity residuals (L2/L3 as needed)

- **Scope**: measure dividend/hsgt/other WARN; fix only typed gate wrongness
- **Exit**: each residual `FIXED|PARTIAL|BLOCKED` with owner; no READY cosmetics
- **Ban**: deleting continuity checks to green
- **Parallel**: after Knife 1 verify; moth-gated vs Knife 2/3
- **Blast**: continuity scripts/tests

### Optional later (not near-end)

- Holders landing retention archive + smartmoney compact
- Margin 1c pulse consumer cutover (shadow)
- Qfq incremental/partitioned write (product; larger than compact)

---

## 4. Complexity / ops notes

Scanner: `analyze_complexity.py` on `data_sources/` → many **nested-loop / sort-in-loop / io-in-loop** leads (calendar/accepted_schema dominated). Hot cleanup paths:

| Path | Scanner | Real cost signal (ops) |
|---|---|---|
| `sync_runner.py` | no heuristic hits | Fan-out hub; registry load + drain; **not** O(n²) lead — config correctness dominates |
| `holders_top10_acceptance` / `disclosure_transport` | no heuristic hits | **Append storm** ~7.2M landing / ~225k distinct hash (~32×) — I/O + storage, not CPU nested loops |
| `build_price_kline_qfq_tushare` | no heuristic hits | **Full CTAS** ~8.4M rows; DROP leaves ~0.7 GiB free; wall-clock + disk peak on compact |
| `db_compact` | no heuristic hits | ATTACH-copy ≈ **old+new** peak disk (factor compact failed VACUUM-style path; EXPORT/IMPORT used) |
| `margin_catchup` | n/a (new) | Bounded ≤10d; keep first-error stop — avoid accidental full calendar scan |
| calendar_* under data_sources | many O(n²) leads | **Out of scope** unless a cleanup knife touches calendar (do not drive cleanup order) |

**Ops rules**: serialize DuckDB writers; never compact under concurrent derive; measure before/after sizes; prefer skip-land over scanning landing for dedupe deletes.

---

## 5. What NOT to touch

- Org mass / by-date invent / unbounded page crawl
- Optuna / holdout loosen / StrategyRelease / E–F remeasure without owner
- S7 blanket pre-accept / fake COMPAT
- Margin product thaw of `rzrqye` / pulse trusted cutover without shadow knife
- Greenfield rewrite of sync_runner / disclosure stack
- Merging landing+canonical; bare DELETE landing “to save space”
- Reviving `stk_factor_pro` writer without new consumer + owner
- Plugin bus / second DB / dual-write migration window
- Continuity READY cosmetics
- Peer WIP unrelated files; agent self-downgrade of commit tier

---

## 6. Recommended next knife (after Knife 3)

**Knife 4 — Continuity residuals** (honest WARN: dividend/hsgt/…; Continuity margin=WARN typed drift). Holders retention/archive after skip-land proves no new storm.

---

## 7. Delivery binding

- One logical knife = one Rule10 + one `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh`; L3 `chunkyctl pre-knife <name>`; async CI (no sync `gh watch`)
- Parallel agents **only** when `moth coupling --repo . --impact <name>` proves non-overlap
- Post data/PIT/schema: `$post-fix-audit`
- Done labels: `FIXED|PARTIAL|BLOCKED` + residual owner + next verification

---

## 8. Pointers

| Doc | Role |
|---|---|
| This file | Global cleanup/rebuild **execution plan** |
| `foundation_residual_fix_plan_20260723.md` | Margin-focused residual board (1a/1b); add reciprocal link when editing that knife |
| `db_bloat_deep_dive_20260723.md` | Size / owner verdicts / DELETE outcome |
| `db_refill_after_delete_audit_20260723.md` | Why delete-then-write exists; skip vs delete capability |
| `margin_v3_bounded_catchup_1b_20260723.md` | 1b path ship evidence |
| `margin_catchup_live_20260723.md` | Knife 1 **update-flow** fix (补跑≠正解; acquire planner; Continuity WARN) |
