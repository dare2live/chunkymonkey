# Product decision-assist backlog (2026-07-21)

> 状态：evidence-only / **scheduled product backlog**（未开刀；非 owner contract）
> Owner asks captured 2026-07-21. Sequencing hard-gated on foundation E2E.
> Layer authority: `analysis/data_brick_architecture_20260721.md` (L0–L3) + MASTER Tier0–4.
> Foundation dependency evidence: `analysis/foundation_e2e_frontend_update_20260721.md` (PARTIAL).

## 1. Problem / north star

**Problem:** Absolute money-flow ¥ and raw charts force manual 复盘; sectors differ in size, so naked amounts mislead; the product does not yet **state observations** that help buy/sell decisions.

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

## 4. Dependencies on foundation (must be true first)

Gate: **after** data-foundation E2E verify/optimize. Peer evidence may still be updating `analysis/foundation_e2e_frontend_update_20260721.md` — do not open A implementation while that path is PARTIAL without owner schedule.

**Must be true before Capability A build:**

1. **Accepted moneyflow bricks** — stock / industry-DC / market moneyflow land→validate→accept (or explicit publication wall) with continuity nodes distinct from “HTTP 200”.
2. **Serve freshness** — pulse / flow surfaces not stuck on prior trade_date while daily is ahead (current residual: sector/DC lag noted in foundation E2E).
3. **API completeness** — readable multi-horizon aggregates (or clear FeatureBlock contracts) for the 7 horizons; fail-closed on missing coverage.
4. **PIT / availability** — decision-time-visible only; no future return leakage into sensing features; conclusions that use forward-looking labels stay Tier3 with measured protocol if they become strategy claims.
5. **Continuity / doctor** — moneyflow-related readiness not upgraded by UI commits alone (`READY/DEGRADED/UNVERIFIED/BLOCKED` stay evidence-bound).
6. **UI update path** (or owner-accepted CLI-primary) — foundation E2E currently: missing「数据更新」button + `daily_update` margin `scope_blocked`; modular `land_then_accept` PASS for daily/ST. Product assist should not pretend one-click freshness until that residual is owned.

**Capability B additionally needs:** stable Tier1 form/stage publish + selection-strategy product contract (still deferred).

## 5. Proposed phase order

| Phase | Work | Status |
|---:|---|---|
| 0 | Foundation E2E verify / optimize (UI path, margin preflight, sector/DC serve lag) | **in flight / PARTIAL** — blocker for opening A |
| 1 | Moneyflow API + feature completeness (horizons 1…60; accepted bricks; PIT) | **scheduled after 0** |
| 2 | Relative metrics contract (denominator design; vs sector/universe; no fake ratios) | **scheduled after 1** |
| 3 | Behavior taxonomy (潜伏/抢筹/出货… + return coupling; versioned; unknown allowed) | **scheduled after 2** |
| 4 | Decision-assist UI (auto conclusions/observations; north-star buy/sell help) | **scheduled after 3** |
| 5 | Form/stage as **选股策略** surface (Capability B) | **later / deferred** after A or explicit owner reorder |

Parallel research (E/F remeasure) remains **owner-scheduled** and orthogonal; this backlog does **not** open it.

## 6. Explicit NON-goals now

- Implement frontend moneyflow decision UI or form/stage selection UI
- Open Optuna / StrategyRelease / loosen holdout / claim production candidate
- Type-B enrichment (stays deferred to institution-follow timing per prior decisions) — schedule-only mentions OK
- Fuse display conclusions into Tier0 accepted truth or rewrite landing as “labels”
- Fake relative ratios or behavior labels when denominator/coverage is unknown
- Greenfield rewrite of pulse / market sensing; strangler + resolver SSOT only
- Mass backfill / margin thaw / org invent / S7 fake COMPAT

## 7. Schedule summary (for operators)

- **A lands after** foundation E2E is verified/optimized enough that accepted moneyflow + serve freshness + multi-horizon contracts are honest.
- **B lands later** as a selection-strategy surface, after form/stage publish is a stable dependency — not in the near-term knife queue.
- **Stays deferred until owner opens:** Optuna, StrategyRelease, holdout loosen, Type-B enrichment, E/F remeasure (unless separately scheduled).

Label: **SCHEDULED** — residual owner = product/decision-assist after foundation E2E closure criteria met.
