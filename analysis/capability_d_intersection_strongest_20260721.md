# Cap 4D — 交集最强股 decision assist (2026-07-21)

> Status: evidence-only / product knife
> Label: **FIXED** (DC 行业∩概念∩申万三链；2026-07-22 residual clear)
> Decision: `analysis/decision_4d_intersection_strongest_20260721.md` (self-adversarial synthesis)
> Authority: `product_plan_reeval_stock_dossier_20260721.md` §2/§3.5; backlog Cap D; goal.md bans intact

## Scope shipped

| Piece | Delivery |
|---|---|
| Service | `services/decision_intersection.py` — reuses `moneyflow_assist.build_sector_board` (DC 行业 + DC 概念) for sector-level honesty; reuses `market_pulse_serve_read.dc_member_mem_sql`/`dc_member_snap` (observation-date PIT, 沪深A-filtered) for constituent lookup |
| API | `GET /api/v3/decision/intersection/strongest` (board) + `GET /api/v3/decision/intersection/stock/{code}` (dossier) — under `/api/v3/decision`, not `/pulse` |
| Host | `#/market` page third tab「交集最强」; dossier `交集` tab **enabled** (was `soon="4D"`) |
| Input honesty | Chain as-of must match across dc_industry/dc_concept and not lag latest completed trading day beyond SLA (config `sla_max_lag_calendar_days`); mismatch/lag → `status=stale`, empty rows, explicit `reason` — never a fake freshness claim |
| Output | Ranked decision list, each row carries a `why` sentence naming the intersecting industry + concept sectors and their behavior labels — not a raw rank dump |
| "Strong" definition | Reuses `moneyflow_assist.behavior_from_regime` labels (`chase`/`latent`); never re-derives a second strength taxonomy |
| Per-stock/board consistency | Single unsliced `_compute_intersection` backs both endpoints — a stock ranked outside the board's display `limit` still reports correctly via the per-stock lookup |

## Honesty / NON-goals held

- Sensing cards untouched — Cap D lives in the Tier3 decision-assist router, same separation as Cap A
- No new "strong" taxonomy invented; behavior labels + guards inherited from Cap A (`moneyflow_assist.yaml`)
- HS-A gate on the per-stock endpoint (`classify_exclusion`); membership query itself is also 沪深A-filtered (`sql_where_active_a_share`) — defense in depth
- No cross-chain fusion into Tier0/Tier2 accepted state; read-only Tier3 consumer
- sw_industry 3rd chain **added 2026-07-22** via PIT `l1_code` member rollup (`sw_l1_member_mem_sql`) — not L3 fan-out invent
- No Optuna / Release / mass org / margin thaw

## Tests

`tests/test_decision_intersection.py` (blocking): 3-chain intersection hit with why-sentence,
non-intersecting members excluded, stale-on-chain-mismatch, stale-on-SLA-lag,
empty-but-ok when no strong sectors, invalid-horizon rejection, per-stock hit/miss,
API board + stock + BJ 404 + bad-horizon 400.

## Residual / next

- Trading-day-exact SLA (currently calendar-day approximation, conservative)
- Optional badge on dossier F header surfacing intersection membership at a glance (plan §3.5 "later")
- See `plan_residual_reconcile_20260722.md`
