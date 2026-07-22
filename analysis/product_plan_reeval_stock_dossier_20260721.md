# Product plan re-eval — full schedule (foundation + Cap A–F) 2026-07-21

> 状态：evidence-only / **near-term product+ops schedule authority**
> Supersedes prior thin dossier-only draft of this path **and** §7 near-term order in
> `analysis/product_decision_assist_backlog_20260721.md` (capability *definitions* A–E there still valid; E status = DONE subset).
> Layer authority: brick L0–L4 + MASTER Tier0–4. Conclusions = Tier3/product — **never** fused into Tier0.
> Cap E shipped evidence: `analysis/capability_e_pipeline_step_cards_20260721.md` (`799b7412d`).
> Holders lineage audit: `analysis/holders_stock_dossier_lineage_audit_20260721.md` (`624d50c87`) — **PARTIAL**; do not re-audit.
> Foundation 0r.1–0r.4: `analysis/foundation_bj_dualpath_ashare_whitelist_20260721.md` (`6afea30fc`) —
> whitelist/BJ sensing/continuity/share_float **FIXED**; `ths_hot` **FIXED** mechanism
> (2026-07-22 typed `pending_publish`; live watermark catchup = ops).
> Residual reconcile: `analysis/plan_residual_reconcile_20260722.md`.
> Foundation click/unblock/RCA: `foundation_daily_update_unblock_20260721.md`,
> `foundation_daily_update_ui_click_20260721.md`, `foundation_daily_update_degraded_rca_20260721.md`.

## 0. Verdict

**YES — overall re-adjust the plan.**

After foundation E2E → unblock → UI click (PARTIAL/degraded) and Cap E step cards **DONE**, the old backlog order
(`foundation → moneyflow API/taxonomy → A+C UI → D → E → B`) is obsolete.

New spine: **foundation truth knives (whitelist / BJ / continuity / holders lineage)** → **sensing serve only 沪深A** →
**股票档案 F MVP** → **A moneyflow assist + C tabs** → **D 交集最强** → **B 形态/阶段选股面**.
Cap E stays shipped; do not rebuild workbench into dossier files.
**Execution closeout:** mandate 0r.5b→5B **FIXED** subsets — see
`analysis/product_plan_execution_closeout_20260721.md`. Closeout residuals **cleared**
2026-07-22 (`plan_residual_reconcile_20260722.md`). **Next** = ops ths_hot catchup
(post-22:30 drain; not missing token) + owner-scheduled E/F remeasure only (still paused otherwise).

| Question | Answer |
|---|---|
| Re-adjust after click-proof + Cap E + degradations? | **Yes** |
| Cap E still a near-term knife? | **No** — DONE subset; residual = parameterized S1/S2 UI only |
| 0r.1–0r.4 (whitelist / BJ / continuity / drain)? | **FIXED** (`6afea30fc` + 2026-07-22 ths_hot `pending_publish`); live ths_hot day catchup = ops |
| Accept BJ in accepted raw_evidence as project pool? | **No** — landing/raw may keep BJ by design; **serve** = 沪深A whitelist |
| Holders audit / next foundation product knife? | **0r.5 PARTIAL**; **0r.5b FIXED** (`387eb79b5`); **2F FIXED** subset (`50817db0f`) |
| Optuna / StrategyRelease / E/F remeasure? | **No** unless owner schedules |
| Fuse product labels into Tier0? | **No** |
| Sample holders PASS ⇒ full dossier readiness? | **No** — see §1.2 |
| 0r.5b→5B product mandate closed? | **Yes** — closeout `9b1a0eb90` (+ status-sync knife) |

---

## 1. What changed (evidence pack)

### 1.1 Foundation / ops (current)

| Item | State | Evidence / note |
|---|---|---|
| E2E UI path | PARTIAL → **unblock FIXED** | margin on_demand+frozen; formal daily/ST orchestrator; DC/pulse `20260721` |
| UI「数据更新」+ progress「正在:…」 | **FIXED** | workbench one-click + `current_activity` |
| Cap **E** step cards (一键 + 分步) | **DONE** | `799b7412d`; residual S1/S2 parameterized disabled+reason |
| Click wall-clock | **PARTIAL** / degraded rc=1 | 4 degradations RCA’d — not greenwashed |
| BJ / B-share sensing leak (qfq / pulse) | **FIXED** | root cause: `apply_universe_serve_filter` never wired after A4; wired at serve (`6afea30fc`) |
| BJ in accepted `raw_evidence` | **by design** | landing may keep BJ; project **serve** filters — not universe invent |
| Continuity dual-path (raw lag vs accepted) | **FIXED** | continuity/SLA judge **formal accepted frontier** (`6afea30fc`) |
| Holders formal land→accept parse | **FIXED** (full plane) | canonical **218,444** rows null/bad=0 |
| Holders **lineage / association / process·serve** | **PARTIAL** audit DONE | see §1.2 — consume, don't re-audit |
| Dossier period streak / Δ plane | **FIXED** | canonical-only streak (was fact-lag bug) |
| Drain `share_float` bare BJ normalize | **FIXED** | e.g. `874075` → `874075.BJ`; shape gate not loosened |
| Drain `ths_hot` same-day empty | **FIXED** mechanism / ops catchup open | typed `pending_publish` pre-22:30; post-window fail-closed; live `20260721` needs post-22:30 drain (UI run had token; failed `zero_rows`) |

### 1.2 Holders audit fold-in (peer DONE — facts only)

Source: `analysis/holders_stock_dossier_lineage_audit_20260721.md`.

| Claim | Verdict | Number / note |
|---|---|---|
| Sample PASS ⇒ population OK | **No** | sample ≠ full |
| Canonical parse integrity | **PASS** | 218,444 rows; bad_code/empty_name/grain_dup=0 |
| Ops “76 / ~987k” = net new notices | **PARTIAL** | 76 = UPDATE_DATE window stocks; 987k = **full-history rewrite amp**; net new notices ≈ **30 codes / 380 rows** |
| Stock↔holders↔form(+industry) for F | **PASS** | ~**98.4%** (5,117/5,200) |
| Holder→institution **profile** deep link | **PARTIAL** | episodes ~99.3%; `mart_inst_profile` ~**54.2%** — honesty before deep UX |
| Dossier streak/Δ from fact while rows canonical | was BLOCKED → **FIXED** | same-plane canonical |
| Formal watermark/SLA still on legacy fact | **PARTIAL** | next knife |

**Follow-on knives (from audit § next):** (1) formal watermark/SLA on canonical/accepted notice frontier; (2) **split ops counters** (window stocks vs rewrite rows vs net new notices); (3) honesty gate on **机构档案** coverage before claiming dossier↔机构 deep UX.

### 1.3 Product frontend capabilities (ALL — from prior owner chats)

| ID | Name | Intent (north star = **辅助买卖决策**, not raw dump) |
|---|---|---|
| **A** | 资金流决策辅助 | Horizons **1/3/5/10/20/30/60**; relative ratios (not abs ¥ alone); behavior **潜伏/抢筹/出货**; industry/sector/concept **stated conclusions**; cut manual 复盘 |
| **B** | 形态+阶段选股面 | Form + stage as **选股策略展示** (Tier1 bricks as dependency; no Optuna/Release) |
| **C** | 页内多标签 | Tabs-within-page; **forbid** one long flatten scroll for decision surfaces |
| **D** | 交集最强股 | Strongest at intersection of factors / sectors / concepts; fail-closed on stale/UNTRUSTED |
| **E** | 分步操作台 | **Shipped** — one-click primary + independent stage/derive buttons; S1/S2 param UI residual |
| **F** | 股票档案 (+股东) | Basic; **阶段**; **形态**; holders; 持仓周期; 变化; 收益; clever IA; **verify associations + lineage + process/serve** (not sample-only) |

Also binding: **市场感知只沪深A** (whitelist serve path **FIXED** at `6afea30fc`; BJ may still land in accepted raw by design).

---

## 2. Revised phase order (binding)

| Phase | Work | Depends on | Status |
|---:|---|---|---|
| **0r.1** | **沪深A whitelist** at correct serve/sensing layer (+ exclusion RCA) | live evidence B shares leaked | **FIXED** (`6afea30fc`) — wired `apply_universe_serve_filter` |
| **0r.2** | **BJ sensing leak** stop at serve (+ audit wording) | degraded RCA #2 | **FIXED** (`6afea30fc`) — raw BJ land OK by design |
| **0r.3** | **Continuity dual-path** + daily/ST watermark honesty | degraded RCA #3/#4 | **FIXED** (`6afea30fc`) — formal accepted frontier |
| **0r.4** | Drain knives: `share_float` normalize; `ths_hot` empty | degraded RCA #1 | **FIXED** — share_float **FIXED**; ths_hot `pending_publish` **FIXED** (live catchup ops) |
| **0r.5** | **Holders lineage audit** (association + process + serve) | formal parse ≠ coverage | **PARTIAL DONE** — audit landed; follow-ons §1.2 |
| **0r.5b** | Formal holders WM/SLA + split ops counters + 机构档案 honesty | 0r.5 audit | **FIXED** (`387eb79b5`) |
| **1E** | Cap E step cards | click-proof + activity | **DONE** (`799b7412d`) |
| **2F** | **股票档案 MVP** `#/stock/:code` | 0r.1 code-gate; 0r.5 join PASS; streak FIXED | **FIXED** subset (`50817db0f`) — episode cycle/returns + C-light tabs; 机构 deep-link still honesty-gated |
| **3A** | Moneyflow decision assist (API + relative denom + taxonomy) | 0r enough that moneyflow bricks/serve honest; sensing A-only | **FIXED** subset (`4f70adc08`) |
| **3C** | Tabbed UX | rides **2F** + early **3A** | **FIXED** subset (`4f70adc08` + dossier tabs) |
| **4D** | 交集最强股 | after A conclusions **or** parallel once intersection inputs + freshness owned | **FIXED** (`a959baf06` + 2026-07-22) — DC∩概念∩申万三链 |
| **5B** | 形态/阶段 **选股策略** surface | after F proves display; Tier1 publish stable | **FIXED** (`8fb0192f9` + 2026-07-22) — filter + shared F cutover hybrid |
| — | E/F remeasure / Optuna / StrategyRelease | owner schedule only | **paused / banned** |

**Next:** ops ths_hot catchup + owner-scheduled E/F remeasure only. Cap E stays shipped (do not revert). Mandate closeout: `product_plan_execution_closeout_20260721.md`. Residual reconcile: `plan_residual_reconcile_20260722.md`.
**Dropped default:** old A-first moneyflow stack before dossier/whitelist.

---

## 3. Frontend IA sketches (tabs, not dashboard soup)

North star per surface: **one job, one observation sentence, supporting evidence** — no KPI soup, no fake signals.

### 3.1 Workbench `#/workbench` (Cap E — done)

| Tab | Job |
|---|---|
| 一键更新 | Primary `daily_update`; show「正在:…」/ alerts |
| 分步节点 | Independent acquire/clean/process/store + derive_qfq; disabled nodes state reason |

NON-goal: second orchestrator DAG; fake-runnable S1/S2 without params.

### 3.2 市场感知 `#/market`

| Rule | Detail |
|---|---|
| Universe | **沪深A only** (60/00/30/68) after 0r.1 |
| Layout | Keep widget cards; leaf drill must not surface B/BJ as project pool |
| Later | Link leaf → `#/stock/:code` (F) |

### 3.3 股票档案 F `#/stock/:code` (MVP + later)

| Tab | Job | MVP | Later |
|---|---|---|---|
| **概况** | Identity + one observation (阶段·形态 compose) + freshness/gaps | yes | moneyflow one-liner → A |
| **形态·阶段** | Tier1 axes + form_name/sub + weekly/monthly | yes | history strip; B hooks |
| **股东** | Top holders + 变化; cycle/PnL honest | list + Δ; streak **canonical**; **收益=未知**; link→机构 only if profile exists | episode×price; deep 机构 UX after 0r.5b |
| **资金** | A assist | no | after 3A |
| **交集** | D context for this name | no | after 4D |

**Field → brick (summary):**

| Field | Source | Layer | PIT note |
|---|---|---|---|
| code/name/industry | holders name / `dim_stock_dc_industry` | identity / taxonomy snap | show source |
| 阶段 / 形态 | `fact_stock_form_daily` → later accepted stock_states resolver | Tier1 | observation `trade_date`; no forward return |
| holders + 变化 | canonical top10 prefer; else fact | E0 disclosure | `available_at` / notice |
| 持仓周期 / 收益 | episode reverse × price | Tier3 product | unknown until measured |
| Lineage attestation | `holders_stock_dossier_lineage_audit_20260721.md` + DataAccess/serve | process/serve | parse PASS; profile join PARTIAL; fail-closed on unknown |

**Clever UX:** observation `stock_dossier_obs_v0` product label only; link holders → `#/institutions/:holder`; reject non-沪深A at API.

### 3.4 Moneyflow assist A (with C)

| Piece | IA |
|---|---|
| Horizons | 1/3/5/10/20/30/60 as explicit chips/tabs — not seven charts at once |
| Relative | contract denominator first; unknown > fake ratio |
| Behavior | versioned 潜伏/抢筹/出货; unknown allowed |
| Conclusions | auto-stated sentences at industry/sector/concept; evidence expandable |
| Host | market tab **or** dossier 资金 tab — C tabs, not long scroll |

### 3.5 交集最强 D

| Piece | IA |
|---|---|
| Input honesty | membership + strength with serve as-of; UNTRUSTED → unknown |
| Output | decision list + why-intersection sentence; not raw rank dump |
| Entry | market surface; optional badge on F header later |

### 3.6 选股 B

| Piece | IA |
|---|---|
| Filters | 形态 / 阶段 as strategy surface consuming same Tier1 bricks as F |
| Gate | after F display proven; still no Optuna/Release |

---

## 4. MVP vs later (F focus)

### Shipped / shipping (2F PARTIAL)

- `#/stock/:code` + `GET /api/v3/stock/{code}/dossier`
- 沪深A `classify_exclusion` reject; form/stage + canonical holders; observation; gaps[]
- Period streak **FIXED** to canonical plane; stock↔holders↔form ~98% join OK
- **Must not:** treat 76/987k as net-new; claim 机构档案 deep-link (~54%); invent PnL

### Later

- 0r.5b WM/SLA + ops counter split + 机构 honesty
- Holding-cycle engine + measured returns
- Resolver-first form; A/D/B surfaces

---

## 5. NON-goals

- Optuna / StrategyRelease / loosened holdout / unscheduled E/F remeasure
- Mass org refresh / margin thaw / S7 fake COMPAT / invent BJ into universe without publication decision
- Fuse observation/behavior labels into Tier0 landing/accepted
- Greenfield rewrite of pulse / institutions / workbench
- Fake relative flow ratios or strongest scores on stale/UNTRUSTED inputs
- Sample-only holders “proof” standing in for lineage audit
- Reverting Cap E workbench/API (`799b7412d`)

---

## 6. Coordination

| Track | Owns | Avoid |
|---|---|---|
| Foundation peer | 0r.1–0r.4 **FIXED** (`6afea30fc` + ths_hot `pending_publish`); 0r.5b **FIXED** (`387eb79b5`) | Cap E revert; fake COMPAT |
| Holders audit peer | 0r.5 lineage evidence (**勿重审**); 0r.5b honesty shipped | invent 机构 deep-link |
| Cap E | workbench / ops nodes — **done** | dossier routes; do not revert |
| Product / dossier | 2F/3A/3C/4D/5B **FIXED**; closeout residuals cleared 2026-07-22 | Cap E revert; Optuna/Release |

---

## 7. Stub / delivery label

| Deliverable | Label |
|---|---|
| Cap E | **FIXED** subset |
| 0r.1–0r.3 whitelist / BJ serve / continuity | **FIXED** (`6afea30fc`) |
| 0r.4 drain | **FIXED** (share_float + ths_hot `pending_publish`; live catchup ops) |
| Holders lineage audit | **PARTIAL DONE** (0r.5); follow-ons in **0r.5b FIXED** |
| 0r.5b WM/SLA + ops + 机构 honesty | **FIXED** (`387eb79b5`) |
| F dossier / 2F deepen | **FIXED** subset (`50817db0f`) |
| 3A+3C moneyflow + tabs | **FIXED** subset (`4f70adc08`) |
| 4D 交集最强 | **FIXED** subset (`a959baf06`) |
| 5B 形态/阶段选股 | **FIXED** subset (`8fb0192f9`) |
| Schedule doc (this file) | **CLOSED authority** — closeout `product_plan_execution_closeout_20260721.md` |
| A/B/C/D/E/F (mandate scope) | **FIXED** subsets per §2 + closeout |

---

## 8. Pointers

- Capability defs: `analysis/product_decision_assist_backlog_20260721.md`
- Cap E: `analysis/capability_e_pipeline_step_cards_20260721.md`
- Holders lineage audit: `analysis/holders_stock_dossier_lineage_audit_20260721.md`
- 0r.1–0r.4 foundation knife: `analysis/foundation_bj_dualpath_ashare_whitelist_20260721.md` (`6afea30fc`)
- Degraded RCA: `analysis/foundation_daily_update_degraded_rca_20260721.md`
- Controller: `goal.md`
- Universe policy: `backend/config/universe_rules.yaml` (60/00/30/68)
