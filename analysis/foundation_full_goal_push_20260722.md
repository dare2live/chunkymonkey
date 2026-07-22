# Foundation full-goal push — 2026-07-22

> Status: evidence-only closeout  
> Authority: `goal.md`, `plan_residual_reconcile_20260722.md`, assessment PARTIAL
> ([Assess foundation goal](0ced2e66-d285-40d8-8a1f-a19f5f8262f9)), `$mio`  
> Peer ops: `foundation_ths_hot_ui_catchup_20260722.md`  
> Label vs「一直讨论想要的」完整地基目标: **PARTIAL** (stronger than scheme-exit YES; not full YES)

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

## 2. Moved this session (PARTIAL → FIXED / stronger)

| Item | Was | Now | Evidence |
|---|---|---|---|
| Formal daily/ST morning empty hard-blocks acquire | **BLOCKED** UI catchup (Run A rc=5) | **FIXED** — pre-`available_after` zero_rows → typed `pending_publish`; acquire soft-continues to `--all-due` | `sync_runner._publish_security_day_accepted_partition`; `acquire._sync_formal_on_demand_security_days`; tests `test_formal_security_day_pre_publish_empty_is_pending_not_error`, `test_formal_on_demand_catchup_soft_skips_pre_publish_pending` |
| Cap E S1/S2 parameterized UI | disabled+reason | **FIXED** subset — whitelist UI/API `POST /api/v3/ops/pipeline/land-accept/run` (daily/stock_st, ≤40d) | `ops_manual_run.py` + Workbench form; `test_ops_manual_run` |
| Form hybrid | prose note only | **FIXED honesty** — typed `field_sources` + `hybrid_residual_fields` + dossier UX; **not** pure accepted | `form_production_read.py`; Occam fork → `THIN_TYPED_RESIDUAL_ONLY` (enrich+re-accept deferred) |
| Institution ~54% | deep-link gate only | **FIXED honesty** — `institution_link_status` (`profile` / `profile_low_sample` / `episode_only_no_profile` / `none`); no fake links | `stock_dossier.py` + StockDossierPage |
| ths_hot mechanism | FIXED | unchanged | `pending_publish` for registry drain |
| Peer UI catchup evidence | — | **documented FAIL Run A** + 卡点 | `foundation_ths_hot_ui_catchup_20260722.md` |

Adversarial fork (form): Occam counter-argue → **do not** bump `stock_state_stage_pattern_v1` / re-accept for purity/vol/sub without scheduled contract knife. Typed residual is the honest ceiling until then.

---

## 3. Remaining true BLOCKED / open

| Item | Label | Owner |
|---|---|---|
| `ths_hot` live watermark still `20260720` | **ops PARTIAL** | UI re-click after pending-publish lands (or post-18:00); not missing token |
| Pure accepted-only form (purity/vol/sub in accepted) | **BLOCKED** until versioned enrich + re-accept scheduled | Tier1 publish |
| Institution profile population ~54% | **PARTIAL** — honesty FIXED; coverage needs mart rebuild / norm (not Type-B invent) | ops rebuild_all when free |
| Continuity / live readiness READY | **not claimed** — code dual-path FIXED ≠ Continuity READY | ops/clock |
| S7 23 ssot / org provider / Type-B enrichment | typed walls / BLOCKED / DEFER | unchanged |
| E/F remeasure / Optuna / Release | **paused / banned** | owner schedule only |
| RCA #2 BJ in accepted nominal | by-design landing; serve whitelist FIXED; audit still FAIL on qfq extras | publication decision or accept filter (not loosened) |
| Watermark `NO_QUERY_MAPPING` lhb/qfii | cosmetic/config debt | accept-as-known |

---

## 4. Continuity / live readiness (honest)

| Surface | Status |
|---|---|
| FND-GATE / phase_closure_ready | scheme exit still PASS (when DB unlocked) |
| Continuity overall READY | **do not claim** from this commit |
| Morning UI daily_update | Run A **FAIL** (pre-fix); post-fix expects soft-skip → drain can attempt ths_hot |
| Strategy schedulable | still **paused** — checklist only if owner asks |

---

## 5. Verdict vs full foundation goal

**PARTIAL** — scheme-exit YES remains; discussion-level full goal **not YES**.

Moved: morning acquire false Tier0 block, Cap E S1/S2 UI, form/institution honesty surfaces.  
Still open: ths_hot live day, pure accepted form, profile coverage lift, Continuity READY, strategy schedule.

Next verification: UI「数据更新」re-click after this lands → confirm acquire soft-skips `20260722` daily and `raw_tushare_ths_hot` advances past `20260720` (or typed pending if still pre-window for that domain).
