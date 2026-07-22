# Plan residual reconcile — 2026-07-22

> Status: evidence-only / residual clear + plan drift check
> Authority: `product_plan_execution_closeout_20260721.md` residual ledger +
> `product_plan_reeval_stock_dossier_20260721.md` + `foundation_phase_reeval_20260721.md` + `goal.md`
> Scope: clear closeout residuals → mark docs honestly → reconcile remaining vs overall optimization plan

## 1. Cleared this session

| Residual | Was | Now | Evidence |
|---|---|---|---|
| **0r.4 ths_hot** empty/window | PARTIAL (pre-22:30 zero → failed_batches) | **FIXED** mechanism — typed `pending_publish` on same-day pre-`available_after` zero_rows; **not** `known_empty` tombstone; post-window still fail-closed | `sync_runner._is_pre_publish_same_day_zero`; test `test_pre_publish_same_day_zero_is_typed_pending_not_tombstone` |
| **4D sw_industry 3-chain** | FIXED subset (DC∩概念 only) | **FIXED** — DC 行业∩概念∩申万 L1；PIT `l1_code` member rollup (`sw_l1_member_mem_sql`); 3-way as-of freshness | `decision_intersection.py` v1; tests 8 passed; UI shows 申万 |
| **5B cutover-with-F** | deferred until F flips | **FIXED** hybrid — shared `form_production_read` for F + 5B; ACCEPTED_CUTOVER overlays form_name/pos/trend/breakout; purity/vol/sub stay on fact brick (accepted payload gap, disclosed) | `services/form_production_read.py`; dossier `_load_form` + screener overlay |
| **dossier axis-label drift** | clean/mixed/light unused | **FIXED** — trending/choppy + heavy/shrink/normal (matches live + screener yaml) | `stock_dossier.py` `_AXIS_*`; dossier API test asserts 结构嘈杂/放量 |

### Live ops note (ths_hot watermark)

- Domain is **Tushare** (`source: tushare`, API `ths_hot` → `raw_tushare_ths_hot`); content is 同花顺热榜 via TuShare/tinyshare — **not** a separate THS cookie/login path.
- `raw_tushare_ths_hot` max was **`20260720`** at session start (missing `20260721` from early-window run).
- This session **could not** provider-drain (`missing_token` = TuShare auth from `.env`: `TUSHARE_TOKEN` / `TUSHARE_PRO_TOKEN` / `TS_TOKEN`) — **ops catchup** remains: with that token loaded the usual way (`chunkyctl` / `daily_update.sh` source `.env`), run normal sync (`run_domain ths_hot` or drain via `daily_update` acquire) for `20260721` (and later days post-22:30). Mechanism no longer misclassifies pre-window vacuum as hard failure.

## 2. Remaining open (owner / priority)

| Item | Owner | Priority | Notes |
|---|---|---|---|
| ths_hot **live catchup** `20260721`(+) | ops / owner **TuShare** token | P1 | Code FIXED; watermark catchup needs `TUSHARE_TOKEN` (usual `.env`), not THS cookie |
| Accept enrich full form axes (purity/vol/sub → accepted) | Tier1 publish | P2 | Unblocks pure accepted-only form read (hybrid stays honest until then) |
| Optional dossier F header intersection badge | product | P3 | Plan §3.5 "later" — still deferred by design |
| Cap E parameterized S1/S2 UI | product | P3 | Honest disabled+reason; not a silent gap |
| Trading-day-exact SLA (vs calendar-day) for 3A/4D/5B | product | P3 | Conservative calendar lag OK |
| **E/F remeasure** | owner schedule only | paused | Hard ban until explicit schedule |
| Optuna / StrategyRelease / Type-B enrichment / S7 fake COMPAT / org invent / margin thaw | — | **banned** | Unchanged |
| Foundation S7 23 ssot typed walls | owner publication/sunset | wall | Not product residual |

## 3. Drift vs plan

| Planned | Status | Drift? |
|---|---|---|
| Product spine 0r → F → A/C → D → B | Mandate **CLOSED** (closeout) | No — residuals were explicit next |
| Closeout residual ledger (4 items) | **Cleared** this session | No — executed as scheduled residual knife |
| sw_industry as 3rd chain | Done via L1 PIT rollup (not L3 fan-out) | **Positive drift vs fear** — simpler than "new aggregation"; still PIT-honest |
| Screener+F cutover | Hybrid, not full accepted-only | **Honest partial ceiling** — payload lacks purity/vol; documented, not greenwashed as full cutover |
| ths_hot | Mechanism FIXED; live day still gap | **Not greenwashed** — ops catchup separate from code |
| Foundation phase_closure / FND-GATE | Unchanged PASS | No reopen |
| Items done but never in plan | — | None material |
| Items in plan still undone that block use | — | None of the four residuals block shipped surfaces |

## 4. Recommended next order

1. **Ops:** TuShare-token (`TUSHARE_TOKEN`) `ths_hot` catchup for `20260721`+ (post-22:30); confirm continuity/group coverage ≠ 热基.
2. **P2 (optional):** Tier1 accept enrich for `axis_purity`/`axis_vol`/`form_sub` → then thin the hybrid overlay.
3. **Stay paused:** E/F remeasure, Optuna, Release, Type-B, S7 blanket COMPAT.
4. **P3 polish only if owner asks:** intersection badge, trading-day SLA, Cap E S1/S2 params UI.

## 5. Verdict

**FIXED** for the four closeout residuals (code + tests + UI + docs).  
**PARTIAL** only on ths_hot *live watermark catchup* (TuShare `missing_token` / `.env`).  
**Reconcile:** no hostile drift vs product/foundation plans; next work is ops catchup + owner-scheduled research, not reopening the product mandate.
