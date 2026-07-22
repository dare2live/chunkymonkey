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

### Live ops note (ths_hot watermark) — corrected 2026-07-22

- Domain is **Tushare** (`source: tushare`, API `ths_hot` → `raw_tushare_ths_hot`); content is 同花顺热榜 via TuShare/tinyshare — **not** a separate THS cookie/login path. Same provider as the rest of `sync_registry` drain.
- **UI click run 2026-07-21 (~21:53–22:08)** already had a working TuShare token: many Tushare domains drained OK (`margin_detail`/`kpl_list`/`cyq_perf`/`moneyflow`/…). Same job `ths_hot` itself wrote **2214 rows / 6 batches** and only failed `trade_date=20260721` with **`err=zero_rows`** at **22:04** — before registry `available_after: "22:30"`. That is **pre-publish empty**, later typed as `pending_publish` — **not** `missing_token`.
- Holders incremental that night was **东财妙想** (`holders_aif10`), not TuShare — do not cite holders as TuShare proof; use the drained Tushare domains above.
- `raw_tushare_ths_hot` max stayed **`20260720`** (missing `20260721`) because the early-window zero was mis-classed as hard fail at the time; mechanism is now FIXED.
- A later agent shell reported `missing_token` only because that shell **did not** `source .env` (agent env ≠ `daily_update.sh` / workbench / `chunkyctl`, which all load `.env`). **Do not** treat that as the owner-run blocker. Residual wording that led with “缺 token” **overstated** the blocker.
- **Ops catchup** remains: post-22:30 (or next open day) normal sync/`daily_update` acquire for `ths_hot` `20260721`+ — same token path as every other Tushare domain.

## 2. Remaining open (owner / priority)

| Item | Owner | Priority | Notes |
|---|---|---|---|
| ths_hot **live catchup** `20260721`(+) | ops / owner (UI `daily_update`) | P1 | Still gap (`raw` max=`20260720`). Run A: daily hard-block; Run B: daily `pending_publish` OK then **stock_st** empty after 09:20 hard-block — `--all-due` never ran. Evidence `foundation_ths_hot_ui_catchup_20260722.md` |
| Accept enrich full form axes (purity/vol/sub → accepted) | Tier1 publish | P2 | **Deferred** (Occam): typed hybrid residual FIXED; pure accepted needs versioned contract + re-accept |
| Optional dossier F header intersection badge | product | P3 | Plan §3.5 "later" — still deferred by design |
| Cap E parameterized S1/S2 UI | product | — | **FIXED** 2026-07-22 (land-accept parameterized endpoint + workbench form) |
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

1. **Ops:** UI「数据更新」catchup for `ths_hot` `20260721`+ after **stock_st** same-day empty also soft-skips as `pending_publish` (daily already does); or wait until ST vendor publishes. Confirm continuity/group coverage ≠ 热基. See `foundation_ths_hot_ui_catchup_20260722.md`.
2. **P2 (optional):** Tier1 accept enrich for `axis_purity`/`axis_vol`/`form_sub` → then thin the hybrid overlay.
3. **Stay paused:** E/F remeasure, Optuna, Release, Type-B, S7 blanket COMPAT.
4. **P3 polish only if owner asks:** intersection badge, trading-day SLA, Cap E S1/S2 params UI.

## 5. Verdict

**FIXED** for the four closeout residuals (code + tests + UI + docs).  
**PARTIAL** only on ths_hot *live watermark catchup* (gap day from pre-22:30 `zero_rows` / `pending_publish`; **not** owner-run missing token).  
**Reconcile:** no hostile drift vs product/foundation plans; next work is ops catchup + owner-scheduled research, not reopening the product mandate.
