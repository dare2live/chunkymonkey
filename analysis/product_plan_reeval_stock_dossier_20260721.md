# Product plan re-eval — stock dossier + decision-assist (2026-07-21)

> 状态：evidence-only / product plan re-eval authority for **product track sequencing** after foundation click-proof
> Supersedes phase order in `analysis/product_decision_assist_backlog_20260721.md` §7 for near-term knives
> Layer authority: brick L0–L4 + MASTER Tier0–4; conclusions stay Tier3/product — **never** fused into Tier0
> Companion backlog (capabilities A–E definitions unchanged): `analysis/product_decision_assist_backlog_20260721.md`

## 0. Verdict

**YES — re-adjust the plan.**

Foundation one-click path is no longer the hard gate it was when A–E were first scheduled. Click-proof ran end-to-end; moneyflow/pulse serve freshness caught up to `20260721`. Remaining foundation items are **degradation residuals + ops hygiene**, not “no UI / no accepted bricks.” That unlocks a **stock-dossier-first** product slice (decision-assist over layered bricks) while Capability **E** step cards land on workbench (separate surface).

| Question | Answer |
|---|---|
| Adjust phase order? | **Yes** — see §2 |
| Open Optuna / StrategyRelease / E/F remeasure? | **No** — still paused / banned |
| Fuse form labels / holder conclusions into Tier0? | **No** |
| Stock dossier vs workbench? | **Separate route/files** (`#/stock/:code`) to cut merge pain with E |

## 1. What changed (evidence)

### 1.1 Foundation gate moved PARTIAL → operable

| Item | Prior backlog assumption | Live evidence (2026-07-21) |
|---|---|---|
| UI「数据更新」 | missing button / PlaceholderPage | **FIXED** — `#/workbench` → `POST /api/v3/ops/jobs/daily_update/run` |
| margin preflight | `scope_blocked` hard-stop | **FIXED** — `on_demand`+frozen; no thaw |
| formal daily/ST on click | skipped (on_demand not in all-due) | **FIXED** — orchestrator land_then_accept latest eligible |
| DC / pulse / strongest freshness | lag vs daily | **FIXED** ops catchup — flow_board / strongest **`20260721`** |
| Click wall-clock proof | not run | **PARTIAL** — owner click ~21:53→22:08 DONE **with degraded rc=1** |
| Workbench observability | “更新中” only | **FIXED** — `current_activity` phase/progress |
| Org on daily_update | fear of ~830k mass | **FIXED policy proven** — incremental check+skip when plannable present |
| Holders acquire | watermark gap | **DID fetch** incremental (`affected=76`); accept/drain residual owned separately |

Sources: `foundation_daily_update_unblock_20260721.md`, `foundation_daily_update_ui_click_20260721.md`, `goal.md`.

### 1.2 Residuals that still bind honesty (not fake-green)

1. `sync_registry` drain residual / domain errors
2. `data_audit` `cross_table_consistency` (327 kline codes ∉ universe)
3. continuity/integrity FAIL + alert flags
4. watermark SLA post-acquire alert

Also: **org provider land still BLOCKED**; period holes = log-not-fill; moneyflow **multi-horizon decision taxonomy** still unbuilt (A).

### 1.3 Why stock dossier now (before A moneyflow assist)

- Owner north star: **per-stock decision assist** — basic + **阶段** + **形态** + holders (周期/变化/收益).
- Bricks exist for honest MVP: `fact_stock_form_daily`, `fact_top10_holder_period` / `canonical_top10_float_holders_period`, DC industry dims.
- Institution archive UX already proves hero + tabs + independent widgets — stock dossier is the mirror object.
- A still needs denominator/taxonomy design; dossier can ship observations from existing bricks first.

## 2. Revised phase order

| Phase | Work | Gate / notes | Owner surface |
|---:|---|---|---|
| **0r** | Foundation **residuals** (drain/audit/continuity/SLA hygiene) | Honesty only — does **not** block dossier stub | ops / Tier0 |
| **1E** | Capability **E** modular pipeline **step cards** | Workbench; after click-proof + `current_activity` | `#/workbench` only |
| **2S** | **Stock dossier MVP** (this doc) | Separate route; layered read; unknown > fake | `#/stock/:code` |
| **3A** | Capability **A** moneyflow decision assist | After 0r enough that moneyflow bricks/serve stay honest | market / dossier tab |
| **3C** | Capability **C** tabbed layout | Rides with 2S (dossier tabs) and early 3A UI | dossier + A |
| **4D** | Capability **D** 交集最强股 | After A conclusions **or** parallel once inputs honest | market / later |
| **5B** | Capability **B** form/stage as **选股策略** surface | After dossier proves display useful; no Optuna/Release | selection UI later |
| — | E/F remeasure / Optuna / StrategyRelease | **paused / banned** until owner schedule | research |

**Dropped as near-term default:** old backlog order `0 foundation → 1–3 moneyflow API/metrics/taxonomy → 4 A+C UI → 5 D → 5b E → 6 B`.

## 3. Stock dossier — information architecture

### 3.1 Product intent

**Decision-assist archive for one code**, not a raw dump and not dashboard soup.

First viewport: identity hero; one observation sentence (阶段+形态); CTA to tabs; no KPI strip / fake buy signal.

### 3.2 Tabs / sections

| Tab | Job | MVP | Later |
|---|---|---|---|
| **概况** | Who + freshness + one observation | yes | moneyflow 1-line → full A |
| **形态·阶段** | Tier1 axes + form names | yes | history; B hooks |
| **股东** | Top holders + period change; cycle/PnL honest | holders + Δ; cycle/PnL **PARTIAL** | episode reverse + measured return |
| **资金** | A assist | no | after 3A |
| **交集** | D context | no | after 4D |

### 3.3 Field → data source map

| UI field | Source | Layer | PIT / availability |
|---|---|---|---|
| code / name | `fact_top10_holder_period.stock_name` fallback | identity context | show source |
| Industry L1/L2/L3 | `dim_stock_dc_industry` | taxonomy snap | snapshot `updated_at` |
| **阶段** axes | `fact_stock_form_daily` (MVP); later accepted stock_states resolver | Tier1 | `trade_date` observation day; no forward return |
| **形态** | same | Tier1 | same |
| Holders | Prefer `canonical_top10_float_holders_period`; else fact | E0 disclosure | `available_at` / `notice_date` |
| 变化 | holders period cols | disclosure | period grain |
| 持仓周期 | consecutive report presence (MVP heuristic) | product observation | label `approx_periods_present` |
| 收益 | episode reverse × price | Tier3 | **MVP = unknown** |
| Moneyflow | `fact_stock_moneyflow_*` | sensing | later A |

### 3.4 Clever UX rules

- One job per tab; observation sentence is the conclusion surface.
- Independent widget fetch; degraded foundation → banner, not READY claim.
- Missing PnL → **未知**, never 0.
- Link to `#/institutions/:holder`; route isolated from workbench.

### 3.5 Observation sentence

Product label `stock_dossier_obs_v0` in API only — **not** written to accepted partitions.

## 4. MVP vs later

**MVP:** `#/stock/:code` + `GET /api/v3/stock/{code}/dossier`; basic + form/stage + holders + gaps[].

**Later:** stock-side episodes+returns; A moneyflow tab; B selection; D badge; resolver-first form read.

## 5. NON-goals

- Optuna / StrategyRelease / loosened holdout / unscheduled E/F remeasure
- Fusing observations into Tier0
- Greenfield rewrite; fake ratios; org mass refresh; margin thaw; S7 fake COMPAT
- Building E inside dossier files; claiming foundation READY via UI stub

## 6. Coordination

| Agent | Owns | Avoid |
|---|---|---|
| E track | Workbench step cards; ops nodes | stock dossier routes |
| Dossier track | re-eval doc, `#/stock/:code`, `routers/stock_dossier.py`, `StockDossierPage.tsx` | workbench step UI |

## 7. Stub status (same-day)

**PARTIAL** — API + page with real form/holders bricks; gaps include holder return/cycle engine, moneyflow assist, accepted stock_states resolver overlay.

## 8. Pointers

- Backlog A–E: `analysis/product_decision_assist_backlog_20260721.md`
- Foundation click/unblock: `foundation_daily_update_ui_click_20260721.md` / `foundation_daily_update_unblock_20260721.md`
- Capability E evidence: `analysis/capability_e_pipeline_step_cards_20260721.md`
- Controller board: `goal.md`
