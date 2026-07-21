# Product decision-assist backlog (2026-07-21)

> 状态：evidence-only / **scheduled product backlog**（未开刀；非 owner contract）
> Owner asks captured 2026-07-21（+ C/D follow-up same day）. Sequencing hard-gated on foundation E2E.
> Layer authority: `analysis/data_brick_architecture_20260721.md` (L0–L3) + MASTER Tier0–4.
> Foundation dependency evidence: `analysis/foundation_e2e_frontend_update_20260721.md` (PARTIAL) → unblock+click follow-ups FIXED/PARTIAL.
> **Phase-order authority (2026-07-21 re-eval):** `analysis/product_plan_reeval_stock_dossier_20260721.md` — supersedes §7 near-term order; capability defs A–E here remain.

## 1. Problem / north star

**Problem:** Absolute money-flow ¥ and raw charts force manual 复盘; sectors differ in size, so naked amounts mislead; the product does not yet **state observations** that help buy/sell decisions. Long single-page dumps also bury conclusions.

**North star:** Help the user decide **which stocks to buy/sell** by layering fine-grained accepted data into **conclusions/observations** (not display-only numbers). UI should reduce manual reconstitution work — without fusing product conclusions into Tier0 truth.

## 2. Capability A — Money-flow decision assist

Not a display-only pulse card. Intended surface:

| Piece | Intent |
|---|---|
| Multi-horizon flows | **1 / 3 / 5 / 10 / 20 / 30 / 60** day windows (stock / sector / concept as data allows) |
| Analysis, not just totals | e.g. capital **starting to enter** XX industry / sector / concept |
| Behavior labels | Combine flow with **return / 涨幅** → classify e.g. **潜伏 / 抢筹 / 出货** (taxonomy to design; not fake-filled) |
| Relative ratios | Absolute ¥ alone is weak → ratios vs sector/universe size or other **comparable denominator** (contract first; no invented denominators) |
| Auto conclusions | UI **states** observations/conclusions from layered bricks so user is not forced to manual 复盘 |

**Brick / Tier alignment (do not collapse):**

- Moneyflow **sensing features** = Tier1-ish / L2–L3 composites with `available_at`, method, unit, denominator, coverage, config hash (PIT).
- **Behavior labels + decision conclusions** = Tier3 / product-strategy surface (L4-ish consumption) — versioned taxonomy + evidence, **never** written back as Tier0 canonical truth.
- Display must consume **accepted** partitions / published features via resolvers — not landing raw, not silent legacy fill.

## 3. Capability B — Form + stage as selection strategy surface

- Stock **形态** and **阶段识别** may later appear as part of a **选股策略** display surface.
- Explicitly **deferred**: record + schedule only; no UI, no Optuna, no StrategyRelease in this track.
- Existing Tier1 accept (`stock_state_stage_pattern_v1` / `fact_stock_form_daily`) is a **dependency**, not a product claim that B is shipped.

## 4. Capability C — Tabbed page layout UX

- Prefer **multiple tabs within a page** over flattening all sections onto one long scroll.
- Applies to decision-assist / market-sensing product surfaces when they open — not a standalone near-term knife.
- **Can ride with early decision-assist UI** (phase with A’s first UI slice); still **after** foundation E2E residuals.
- NON-goal now: redesign existing pages or ship tabs without A/foundation readiness.

## 5. Capability D — 交集最强股 / multi-factor strongest intersection

- Surface stocks strongest at the **intersection** of factors / sectors / concepts / etc. (or similar strongest-stock surface).
- **Decision-assist**, not a raw dump of rankings.
- Explicitly **deferred** — schedule only; no Optuna / StrategyRelease.
- Sequencing: likely **after A moneyflow conclusions**, **or parallel** once intersection inputs exist.
- **Dependency note:** existing pulse `/strongest` (and related sector/DC surfaces) already show **serve lag** vs daily frontier in foundation E2E — D must not pretend freshness or invent intersection scores on stale/UNTRUSTED inputs; fail-closed / unknown when coverage lags.

## 5b. Capability E — Modular pipeline step cards / independent node ops

- One-click full `daily_update` stays the primary path (preflight→acquire→land/accept→derive→process→serve).
- **Also** show a workbench **stepper / flowchart of modular stages** so when stuck, owner can click **one stage** independently (S1 land / S2 accept / derive / process / store-ish ops that already exist as caller-only APIs/CLIs).
- Aligns with brick transport: acquire → land/raw → accept → derive → process → serve — UI mirrors boundaries; does **not** invent a second orchestration DAG.
- **Status (2026-07-21):** **DONE (shipped subset)** — workbench tabs「一键更新 / 分步节点」; runnable jobs = `pipeline_acquire|clean|process|store` + `derive_qfq` via `POST /api/v3/ops/jobs/{job}/run`; catalog `GET /api/v3/ops/pipeline/nodes`. **Disabled (honest):** 预检（嵌在链内）、S1/S2 land·accept（需 domain+dates/batch-id）。NON-goal remains: beautiful polish / parameterized S1-S2 UI / moneyflow tabs.
- Evidence: `analysis/capability_e_pipeline_step_cards_20260721.md`.

## 6. Dependencies on foundation (must be true first)

Gate: **after** data-foundation E2E verify/optimize. Peer evidence may still be updating `analysis/foundation_e2e_frontend_update_20260721.md` — do not open A/C/D implementation while that path is PARTIAL without owner schedule.

**Must be true before Capability A build:**

1. **Accepted moneyflow bricks** — stock / industry-DC / market moneyflow land→validate→accept (or explicit publication wall) with continuity nodes distinct from “HTTP 200”.
2. **Serve freshness** — pulse / flow surfaces not stuck on prior trade_date while daily is ahead (current residual: sector/DC lag noted in foundation E2E).
3. **API completeness** — readable multi-horizon aggregates (or clear FeatureBlock contracts) for the 7 horizons; fail-closed on missing coverage.
4. **PIT / availability** — decision-time-visible only; no future return leakage into sensing features; conclusions that use forward-looking labels stay Tier3 with measured protocol if they become strategy claims.
5. **Continuity / doctor** — moneyflow-related readiness not upgraded by UI commits alone (`READY/DEGRADED/UNVERIFIED/BLOCKED` stay evidence-bound).
6. **UI update path** (or owner-accepted CLI-primary) — foundation E2E currently: missing「数据更新」button + `daily_update` margin `scope_blocked`; modular `land_then_accept` PASS for daily/ST. Product assist should not pretend one-click freshness until that residual is owned.

**Capability B additionally needs:** stable Tier1 form/stage publish + selection-strategy product contract (still deferred).

**Capability C:** same foundation gate as A UI; no new data brick required beyond what the tab hosts.

**Capability D additionally needs:** honest intersection inputs (factor/sector/concept membership + strength signals) with serve freshness; pulse/strongest lag residual owned or explicitly degraded — not greenwashed.

## 7. Proposed phase order

> **Superseded for near-term knives** by `analysis/product_plan_reeval_stock_dossier_20260721.md` §2.
> Historical table kept for audit; do not schedule from this section alone.

| Phase | Work | Status |
|---:|---|---|
| 0 | Foundation E2E verify / optimize (UI path, margin preflight, sector/DC serve lag) | **operable** — unblock+click FIXED/PARTIAL; residuals = drain/audit/continuity/SLA |
| 0r | Foundation residuals hygiene (honest continuity; not product fake-green) | **scheduled** |
| 1E | **Capability E** modular pipeline step cards (peer workbench) | **DONE (shipped subset)** — `capability_e_pipeline_step_cards_20260721.md` |
| 2S | **Stock dossier MVP** (`#/stock/:code`) | **scheduled / stub** — see re-eval doc |
| 1 | Moneyflow API + feature completeness (horizons 1…60; accepted bricks; PIT) | **scheduled after 2S** (was “after 0”) |
| 2 | Relative metrics contract (denominator design; vs sector/universe; no fake ratios) | **scheduled after 1** |
| 3 | Behavior taxonomy (潜伏/抢筹/出货… + return coupling; versioned; unknown allowed) | **scheduled after 2** |
| 4 | Decision-assist UI + **Capability C tabbed layout** (auto conclusions; north-star buy/sell help) | **C rides with dossier + early A** |
| 5 | **Capability D** 交集最强股 | **scheduled / deferred** |
| 6 | Form/stage as **选股策略** surface (Capability B) | **later** after dossier proves display |

Parallel research (E/F remeasure) remains **owner-scheduled** and orthogonal; this backlog does **not** open it.

## 8. Explicit NON-goals now

- Implement frontend moneyflow decision UI, tabs, strongest-intersection UI, or form/stage selection UI
- Open Optuna / StrategyRelease / loosen holdout / claim production candidate
- Type-B enrichment (stays deferred to institution-follow timing per prior decisions) — schedule-only mentions OK
- Fuse display conclusions into Tier0 accepted truth or rewrite landing as “labels”
- Fake relative ratios, behavior labels, or intersection “strongest” scores when denominator/coverage/freshness is unknown
- Greenfield rewrite of pulse / market sensing; strangler + resolver SSOT only
- Mass backfill / margin thaw / org invent / S7 fake COMPAT

## 9. Schedule summary (for operators)

- **A lands after** foundation E2E is verified/optimized enough that accepted moneyflow + serve freshness + multi-horizon contracts are honest.
- **C lands with** early decision-assist UI (same post-foundation gate as A UI) — tabs-within-page, not one long flatten.
- **D lands after A conclusions** (preferred), or **parallel** once intersection inputs exist and pulse/strongest lag is owned — decision-assist, not raw dump.
- **E lands after** foundation one-click click-proof + current-activity observability; prefer ride near **C tabs** as ops stepper — independent stage buttons, not a second orchestrator. **(2026-07-21 shipped subset: workbench 分步节点 + pipeline/derive jobs; S1/S2 still CLI-parameterized / disabled in UI.)**
- **B lands later** as a selection-strategy surface, after form/stage publish is a stable dependency — not in the near-term knife queue.
- **Stays deferred until owner opens:** Optuna, StrategyRelease, holdout loosen, Type-B enrichment, E/F remeasure (unless separately scheduled).

Label: **SCHEDULED** — residual owner = product/decision-assist after foundation E2E closure criteria met.
