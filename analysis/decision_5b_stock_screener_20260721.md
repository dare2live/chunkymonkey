# Decision log — Cap 5B 形态/阶段选股面 scope/definition (2026-07-21)

> Status: **DECIDED** (self-adversarial synthesis) / evidence-only
> Mandate: ambiguous → 2 adversarial positions → synthesize per north star, log decision.
> Tooling note: same as Cap 4D (`decision_4d_intersection_strongest_20260721.md`) —
> no Task-subagent tool available in this session's catalog; per §1 exception the
> mandate itself directs the adversarial process, carried out as a self-authored
> two-position debate (Advocate A / B), disclosed here rather than silently skipped.

## Question

Plan §3.6 says only "形态/阶段 as strategy surface consuming same Tier1 bricks as
F; gate = after F display proven; still no Optuna/Release." Left open: which Tier1
read path to reuse (legacy direct-read vs cutover-aware resolver), how the filter
menu is sourced (hardcoded enum vs live facet), how strict the freshness gate is,
and where the surface lives in the frontend.

## Adversarial positions

| | Advocate A (richer/forward-looking) | Advocate B (narrower/ship-safe, literal "same bricks as F") |
|---|---|---|
| Read path | Use `resolve_tier12_production_read` (the cutover-aware resolver already used by `market_pulse_serve_read.drill_leaf_rows`) — future-proof against the pending Tier1 accept cutover | Read `fact_stock_form_daily` directly, exactly like `stock_dossier.py::_load_form` — F itself has **not** cut over to the resolver yet (`cutover yaml 未回翻`, goal.md); "same Tier1 bricks as F" should mean the literal same read path F uses today, not a more-advanced path F hasn't adopted. Forking read-paths between the screener and the dossier tab risks the two surfaces disagreeing on a stock's form the moment cutover flips for one but not the other |
| Filter menu | Hardcode the known form_name enum from a one-time inspection — simpler, no extra query | Live facet-count endpoint (`/screener/options`) computed from the current as-of snapshot — a hardcoded enum can silently drift (already observed: dossier's own `_AXIS_PURITY`/`_AXIS_VOL` zh-maps reference values like `clean`/`mixed`/`light` that don't actually occur in the live table — `trending`/`choppy` and `heavy`/`shrink`/`normal` are the real values). Live facets can never offer a filter with zero real matches, and self-corrects if the builder's vocabulary changes |
| Freshness gate | Skip a dedicated gate — form table update cadence historically tracks daily accept, low risk | Mirror the Cap 4D / `/pulse/strongest` fail-closed pattern exactly (global `MAX(trade_date)` vs `calendar.latest_completed_trade_date`, SLA-gated) — plan's cross-cutting honesty mandate ("fail-closed on stale/UNTRUSTED") wasn't scoped to 4D only, and the marginal cost of one more `_freshness_gate` clone is near zero |
| Frontend host | New dedicated route/page (`#/screener`) with its own nav entry — a filter-heavy screen deserves first-class real estate, matches "选股面" naming | A new tab on `MarketPage` (alongside 市场感知/资金决策辅助/交集最强) — every prior decision-assist cap (A, D) landed as a `MarketPage` tab; a 4th tab keeps one consistent "product decision surface" location instead of fragmenting nav, and ships faster without new routing/layout work |

## North-star synthesis

Plan §3.6's own words are the tie-breaker: "same Tier1 bricks as F." F's current,
shipped read path is the *legacy direct read* of `fact_stock_form_daily` — adopting
the cutover-aware resolver here would make the screener technically newer than the
dossier it's supposed to mirror, and the two could disagree the moment one cuts
over before the other (a correctness risk, not a feature). Precedent from Cap 4D
("fail-closed on stale/UNTRUSTED... mirrors `/pulse/strongest`") generalizes: every
decision-assist surface added this plan cycle carries the same honesty gate, not
just the one plan section that happened to spell it out. And a live facet menu is
strictly safer than a hardcoded one given the dossier's own axis-label dict already
drifted from the live vocabulary (a residual, noted below, left untouched since 2F
is a closed phase).

**DECIDE:**

1. **Read path: legacy direct read of `fact_stock_form_daily`**, byte-for-byte the
   same table/columns `stock_dossier.py::_load_form` uses. When Tier1 cutover
   eventually flips for F, this module should flip in the same knife — not before,
   not independently.
2. **Filter menu: live facet-count endpoint** (`GET /screener/options`), never a
   hardcoded enum. Corrected the axis zh-label dicts to match the *actual* live
   vocabulary (`trending`/`choppy`, `heavy`/`shrink`/`normal`) rather than copying
   dossier's stale dict — this is a **new, independent dict** in
   `stock_screener.yaml`, not a shared import, so it does not touch or "fix" 2F's
   already-shipped code (documented residual, not silently patched).
3. **Freshness gate: same shape as Cap 4D** — global `MAX(trade_date)` must not lag
   `calendar.latest_completed_trade_date` by more than
   `sla_max_lag_calendar_days` (config, default 1) → `status=stale` + empty
   rows/facets, never a quietly-outdated screen.
4. **Frontend host: 4th `MarketPage` tab** ("形态/阶段选股"), consistent with A/D
   precedent. Result rows are click-through to `#/stock/:code` (the dossier),
   closing the loop between "find a stock" and "see its full form/holders/context."
5. **Output shape**: plain filtered list + per-row observation sentence (reusing
   the same axis-composition style as dossier's `_compose_observation`, but as an
   independent implementation) — never a score/rank. No Optuna, no
   StrategyRelease, no backtest-derived ordering; default `ORDER BY stock_code`.

## NON-goals

- Adopting `resolve_tier12_production_read` cutover awareness (deferred until F itself cuts over)
- Fixing the pre-existing 2F dossier axis-label dict drift (`clean`/`mixed`/`light` unused values) — documented residual, not touched (2F is a closed, already-committed phase; out of this knife's scope)
- A dedicated `#/screener` route/nav entry (tab-in-MarketPage chosen instead)
- Any scoring/ranking/backtest model over the filtered result (hard ban, plan §3.6 gate)
- Optuna / StrategyRelease / mass org / margin thaw
