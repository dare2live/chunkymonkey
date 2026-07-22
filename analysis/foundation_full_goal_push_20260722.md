# Foundation full-goal push — 2026-07-22

> Status: evidence-only closeout  
> Authority: `goal.md`, `plan_residual_reconcile_20260722.md`, assessment PARTIAL
> ([Assess foundation goal](0ced2e66-d285-40d8-8a1f-a19f5f8262f9)), `$mio`  
> Peer ops: `foundation_ths_hot_ui_catchup_20260722.md`  
> Label vs「一直讨论想要的」完整地基目标: **PARTIAL** (scheme-exit YES; full goal not YES)

---

## 1. What “完整目标” meant (reaffirmed)

Not “FND-GATE green / scheme ~94% exit” alone. Discussion-level goal also wants:

- live incremental日更 that can catch published peers without morning false Tier0 blocks
- form/serve honesty toward accepted (hybrid disclosed, not greenwashed as pure)
- institution deep-link honesty (~54% coverage not faked)
- Cap E operable including parameterized S1/S2
- Continuity/live readiness **never** claimed READY from commit alone
- Strategy remains paused unless owner schedules

---

## 2. Coordination (acquire vs product honesty)

| Lane | Owner | Status |
|---|---|---|
| Formal on_demand → must not kidnap `--all-due` / ths_hot | **peer acquire knife** | Run A: daily hard-block. Run B: daily `pending_publish` soft-skip (disk WIP) then **stock_st** empty after `available_after=09:20` hard-blocks; `--all-due` never runs; ths_hot max still `20260720`. **Do not duplicate / do not revert peer lane.** |
| Cap E S1/S2 parameterized UI | this knife | **FIXED** (committed with honesty surfaces) |
| Form hybrid typed residual | this knife | **FIXED honesty** (not pure accepted) |
| Institution link honesty | this knife | **FIXED honesty** (~54% coverage unchanged) |

Adversarial fork (form): Occam → `THIN_TYPED_RESIDUAL_ONLY`. Do **not** bump `stock_state_stage_pattern_v1` / re-accept for purity/vol/sub without a scheduled contract knife.

---

## 3. Moved this session (product / honesty)

| Item | Was | Now | Evidence |
|---|---|---|---|
| Cap E S1/S2 parameterized UI | disabled+reason | **FIXED** — `POST /api/v3/ops/pipeline/land-accept/run` + workbench form (daily/stock_st, ≤40d) | `ops_manual_run.py`, WorkbenchPage, `test_ops_manual_run` |
| Form hybrid | prose note only | **FIXED honesty** — `field_sources` + `hybrid_residual_fields` + dossier UX | `form_production_read.py` |
| Institution ~54% | deep-link gate only | **FIXED honesty** — `institution_link_status` (`profile` / `profile_low_sample` / `episode_only_no_profile` / `none`); no fake links | `stock_dossier.py`, StockDossierPage |
| Peer UI ths_hot catchup | — | **FAIL documented** (×2 runs; drain never reached) | `foundation_ths_hot_ui_catchup_20260722.md` |

---

## 4. Remaining true BLOCKED / open

| Item | Label | Owner |
|---|---|---|
| Formal on_demand empty (esp. stock_st post-09:20) blocks `--all-due` | **open / peer knife** | acquire reorder or broader soft-skip — **not this commit** |
| `ths_hot` live watermark still `20260720` | **ops PARTIAL** | after peer acquire fix + UI re-click |
| Pure accepted-only form (purity/vol/sub in accepted) | **BLOCKED** until versioned enrich + re-accept | Tier1 publish |
| Institution profile population ~54% | **PARTIAL** — honesty FIXED; coverage = rebuild/norm (not Type-B invent) | ops |
| Continuity / live readiness READY | **not claimed** from commit | ops/clock |
| S7 23 ssot / org provider / Type-B enrichment | typed walls / BLOCKED / DEFER | unchanged |
| E/F remeasure / Optuna / Release | **paused / banned** | owner schedule only |
| RCA #2 BJ in accepted nominal | serve whitelist FIXED; audit may still FAIL | publication decision |
| Watermark `NO_QUERY_MAPPING` lhb/qfii | cosmetic/config | accept-as-known |

---

## 5. Continuity / live readiness (honest)

| Surface | Status |
|---|---|
| FND-GATE / phase_closure_ready | scheme exit still PASS (when DB unlocked) |
| Continuity overall READY | **do not claim** from this commit |
| Morning UI daily_update | Run A/B **FAIL rc=5**; ths_hot not filled |
| Strategy schedulable | still **paused** |

---

## 6. Verdict vs full foundation goal

**PARTIAL** — scheme-exit YES remains; discussion-level full goal **not YES**.

This knife ships Cap E S1/S2 UI + form/institution honesty. Acquire/drain unblock for ths_hot remains with the peer lane. Pure accepted form, profile coverage lift, Continuity READY, and strategy schedule stay open/paused.
