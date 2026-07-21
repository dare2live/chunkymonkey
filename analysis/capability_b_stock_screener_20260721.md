# Cap 5B — 形态/阶段选股面 (2026-07-21)

> Status: evidence-only / product knife
> Label: **FIXED** subset (form/stage filter surface + `#/market` 4th tab)
> Decision: `analysis/decision_5b_stock_screener_20260721.md` (self-adversarial synthesis)
> Authority: `product_plan_reeval_stock_dossier_20260721.md` §2/§3.6; backlog Cap B; goal.md bans intact

## Scope shipped

| Piece | Delivery |
|---|---|
| Service | `services/stock_screener.py` — same production-read boundary as Cap F via `form_production_read` (fact brick + ACCEPTED_CUTOVER overlay); no new Tier1 concept |
| API | `GET /api/v3/screener/options` (live facet menu: form_name/form_sub + 4 axes, counted at current as-of) + `GET /api/v3/screener/form_stage` (filtered decision list) — under new `/api/v3/screener` router |
| Host | `#/market` page 4th tab「形态/阶段选股」; result rows click through to `#/stock/:code` (dossier) |
| Input honesty | Global `MAX(trade_date)` must not lag `calendar.latest_completed_trade_date` beyond SLA (config `sla_max_lag_calendar_days`, default 1) → `status=stale`, empty rows/facets, explicit `reason` — mirrors Cap 4D / `/pulse/strongest` fail-closed pattern |
| Universe | `sql_where_active_a_share` applied in every query (belt-and-suspenders — `fact_stock_form_daily` population is itself already 00/30/60/68-only, verified empirically, but the filter is defensive against future population drift) |
| Filters | Multi-select `form_name` (repeated query param) + single-select `axis_pos`/`axis_trend`/`axis_purity`/`axis_vol` + `is_breakout_event` boolean — all optional, AND-combined |
| Output | Plain filtered list ordered by `stock_code` + per-row `why` observation sentence (independent implementation, same composition style as dossier's `_compose_observation`) — **no score, no rank, no model** |
| Name resolution | Best-effort bulk lookup from `fact_top10_holder_period` (same source dossier uses); missing → `stock_name: null`, never invented |

## Honesty / NON-goals held

- No scoring/ranking/backtest model over the filtered result — plain filter, plan §3.6 hard gate
- No Optuna / StrategyRelease / mass org / margin thaw
- Axis zh-label vocabulary matches live `fact_stock_form_daily` (`trending`/`choppy`, `heavy`/`shrink`/`normal`); dossier dict **aligned 2026-07-22** (same vocabulary)
- Read path: shared `form_production_read` with F (2026-07-22 cutover knife) — hybrid ACCEPTED_CUTOVER overlay
- Facet menu is live-computed, never hardcoded, so the filter UI can never offer a value with zero real matches

## Tests

`tests/test_stock_screener.py` (blocking, 10 cases): form_name filter + why-sentence,
axis filter exclusion, breakout-event filter, no-filter full snapshot, stale-on-SLA-lag
(red case, mirrors 4D's), empty-but-ok on no match, live facet counts, options
stale-on-lag, API options + form_stage + bad-axis 400, multi-value `form_name` query.

## Residual / next

- Full accepted-only form read blocked until accept enrich adds purity/vol/sub (hybrid stays)
- No dedicated `#/screener` route (tab-in-MarketPage chosen; revisit if screener UX outgrows a tab)
- `axis_volregime` / `axis_*_memb` (membership confidence) columns not exposed as filters yet (no plan requirement this knife)
- See `plan_residual_reconcile_20260722.md`
