# Product decision-assist backlog (2026-07-21)

> 状态：evidence-only / **capability definitions**（A–F defs still valid; near-term knives **CLOSED**)
> Layer authority: `analysis/data_brick_architecture_20260721.md` (L0–L3) + `docs/MASTER_TOPLEVEL_DESIGN.md` Tier0–4.
> Execution backlog: `analysis/FOUNDATION_EXECUTION_PLAN.md`（产品残差）· Cap F 证据 `dossier_100_usable_20260723.md`.
> **Complex-viz:** `analysis/frontend_complex_viz_plan_20260722.md`（consumes Cap A/D；无新 backend MVP）.

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
- Evidence: commit `799b7412d`（Cap E pipeline step cards；详见 git）.

## 5c. Capability F — 股票档案 (+股东关联)

- Per-stock decision-assist archive: basic; **阶段**; **形态**; holders; 持仓周期; 变化; 收益.
- Clever IA (tabs C-seed; observation sentence; not dashboard soup).
- **Must** verify associations + lineage + process/serve (peer audit) — **not** sample-only greens.
- Serve gate: **沪深A whitelist** (same policy as sensing; **FIXED** at serve `6afea30fc`).
- Stub: `#/stock/:code` + `GET /api/v3/stock/{code}/dossier` — **PARTIAL** (HS-A gate + canonical streak FIXED; stock↔holders↔form ~98%; **机构档案 ~54% honesty** before deep UX). Full schedule + audit fold-in: re-eval doc §1.2 / §2.

## 6. Dependencies on foundation (must be true first)

Gate: foundation E2E / Cap mandate **CLOSED**（见 `FOUNDATION_EXECUTION_PLAN.md` §2）；defs 仍有效，近端刀不开。

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

> **Near-term knives CLOSED** — 执行面见 `analysis/FOUNDATION_EXECUTION_PLAN.md`。
> Historical table kept for audit; do not schedule from this section alone.

| Phase | Work | Status |
|---:|---|---|
| 0 | Foundation E2E verify / optimize | **operable** — unblock+click FIXED/PARTIAL |
| 0r.1–0r.3 | 沪深A whitelist; BJ sensing serve; continuity dual-path | **FIXED** (`6afea30fc` / `foundation_bj_dualpath_ashare_whitelist_20260721.md`) |
| 0r.4 | Drain share_float / ths_hot | **FIXED** mechanism — share_float FIXED; ths_hot typed `pending_publish` (live catchup ops) |
| 0r.5 | Holders lineage audit | **PARTIAL DONE** — `holders_stock_dossier_lineage_audit_20260721.md` (do not re-audit) |
| 0r.5b | Holders WM/SLA + split ops counters + 机构档案 honesty | **FIXED** (`387eb79b5`) |
| 1E | **Capability E** step cards | **DONE** (`799b7412d`) — do not revert |
| 2F | **Capability F** 股票档案 MVP | **FIXED** subset (`50817db0f`); 机构 deep-link still honesty-gated |
| 3A/3C | Moneyflow assist + tabbed UX | **FIXED** subset (`4f70adc08`) |
| 4D | 交集最强股 | **FIXED** (`a959baf06` + 2026-07-22) — DC∩概念∩申万三链 |
| 5B | 形态/阶段选股面 | **FIXED** (`8fb0192f9` + 2026-07-22) — filter + F cutover hybrid |

Parallel research (E/F remeasure) remains **owner-scheduled** and orthogonal; this backlog does **not** open it.

## 8. Explicit NON-goals now

- Re-open Optuna / StrategyRelease / loosen holdout / claim production candidate
- Type-B enrichment (stays deferred to institution-follow timing per prior decisions)
- Fuse display conclusions into Tier0 accepted truth or rewrite landing as “labels”
- Fake relative ratios, behavior labels, or intersection “strongest” scores when denominator/coverage/freshness is unknown
- Greenfield rewrite of pulse / market sensing; strangler + resolver SSOT only
- Mass backfill / margin thaw / org invent / S7 fake COMPAT
- Revert Cap E / silently drop cleared residuals (sw_industry 3-chain, screener cutover-with-F — now FIXED)

## 9. Schedule summary (for operators)

- **Mandate closed:** 0r.5b → 2F → 3A/3C → 4D → 5B all **FIXED** subsets (closeout `product_plan_execution_closeout_20260721.md`).
- **0r.1–0r.4 FIXED** (`6afea30fc` + 2026-07-22 ths_hot `pending_publish`); live ths_hot day catchup = ops.
- **A/C FIXED** (`4f70adc08`); **D FIXED** (3-chain); **B FIXED** (+F cutover hybrid); **E shipped** (`799b7412d`) — **do not revert**.
- **Closeout residuals cleared** 2026-07-22 — see `plan_residual_reconcile_20260722.md`.
- **Next** = ops ths_hot catchup (post-22:30; not missing token) + owner-scheduled E/F remeasure only.
- **Stays deferred until owner opens:** Optuna, StrategyRelease, holdout loosen, Type-B enrichment, E/F remeasure (unless separately scheduled).

Label: **CLOSED** — closeout residuals cleared; foundation 0r.1–0r.4 mechanism closed; product A–F mandate subsets shipped.
