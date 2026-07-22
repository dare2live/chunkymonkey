# Phase 4 — E/F schedule gate honesty (2026-07-22)

> Status: evidence-only
> Label: **BLOCKED** (remeasure) + **FIXED** (schedulable checklist / gate honesty)
> Owner plan: `analysis/architecture_fix_treadmill_first_principles_20260722.md` Phase 4
> Authority: `AGENTS.md` / `goal.md` — E/F paused; ban Optuna / StrategyRelease / holdout loosen / margin thaw

## What Phase 4 allows vs bans

| Item | Allowed by plan? | This knife |
|---|---|---|
| Owner-signed E/F remeasure (same protocol) | Only after owner signature | **NOT run** — no signature |
| Optuna / StrategyRelease / 松 holdout | **BANNED** | untouched |
| Margin thaw / mass backfill / org invent | **BANNED** | untouched |
| Schedulable checklist + gate honesty | **YES** | **FIXED** (this doc) |

## Schedulable checklist (when owner signs)

Do **not** start until `goal.md` next-step explicitly schedules E/F. Then:

1. **Freeze baseline** — confirm F0–F3 artifacts still frozen (`main_rally_v1` / B0–B2 rejects; `claimable=false`).
2. **Same protocol** — one DatasetSnapshot; same universe/folds/costs/execution; PIT truncation; purged WF; embargo; one-touch holdout; T+1; nominal; 停牌/涨跌停; costs.
3. **E remeasure** — longer window / frontier already `20190102→20260721` daily — re-run E protocol only; record measured accept/reject honestly.
4. **F remeasure** — F0–F3 ladder re-eval on same snapshot family; **no** Optuna search; **no** StrategyRelease; reject remains a valid delivery.
5. **Gate honesty** — unmeasured=`unknown` never 0; holdout lift gates stay; do not self-upgrade Continuity READY from code.
6. **Evidence** — dated analysis + ledger pointer; update `goal.md` only after measured closeout.

## Current honest state (post CX-4, 2026-07-23)

| Gate | State |
|---|---|
| CX-1…CX-4 | **PASS** (see `cx_closeout_rx_honesty_20260723.md`) |
| F0–F3 ladder | **FIXED** (protocol-complete measured reject) |
| E/F remeasure (RX) | **BLOCKED / paused** — waiting owner schedule |
| Optuna / Release / 松 holdout | **BANNED** (unchanged; Phase N) |
| Continuity READY / margin / org | ops residuals ≠ knives (treadmill C3) |

## Kill criteria

- Starting remeasure without owner signature → abort.
- Any Optuna / Release / holdout loosen → abort.
- Pretending Continuity READY via code knife → abort (跑步机回归).

Label: **PARTIAL close for Phase 4** — checklist/honesty **FIXED**; actual E/F compute **BLOCKED** until owner schedules. Residual owner: owner signature.
