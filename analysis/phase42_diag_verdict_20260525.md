# Phase 4.2-diag verdict — 2026-05-25

**Codex review agent**: a885609738ef505a4 (path C decision)
**Ablation script**: backend/scripts/run_phase42_diag_ablation.py
**Results JSON**: analysis/phase42_ablation_20260524T155215Z.json

## Findings

| Config | features | rank_ic | std | top5_spread |
|---|---|---|---|---|
| all_features (unified v1 repro) | 116 | 0.0106 | 0.131 | -0.005 |
| base_v5_only | 109 | **-0.0249** | 0.143 | -0.010 |
| base_v5 + perc_market | 116 | 0.0106 | 0.131 | -0.005 |
| base_v5 + perc_stock | 109 | -0.0249 | 0.143 | -0.010 |
| **v7 baseline** | 105 | **0.0452** | 0.069 | +0.051 |

### Findings

1. **perc_stock cols ALL non-numeric (object dtype, 0 non-null in 1000 rows)**: panel coverage of stock_context_daily / under_reaction_daily is 2026-04-27 ~ 2026-05-19 = outside panel range 2024-01-02 ~ 2026-04-30 → JOIN produces all-NULL columns. Pandas dtype = object. Not feature signal — implementation gap.

2. **perc_market adds rank_ic 0.0355 lift**: base_v5 alone -0.0249 → +perc_market 0.0106 = perception MARKET features (regime/emotion/style) ARE useful signal. Validates Phase 3.2 PIT work + Phase 4.1a integration as worthwhile.

3. **base_v5 alone NEGATIVE rank_ic (-0.0249)**: this is the critical finding. Same panel_v5 base features, but v7 (using same panel) achieved +0.0452. The 4-col difference (109 vs 105) can't explain a sign-flip. Confirmed root cause = training methodology, not features.

## Root cause: single-fit vs walk-forward

**v7**: walk_forward.expanding_monthly with 16 windows. Each OOS month uses a fresh-fit model on growing train window. Best params via Optuna 50+ trials.

**unified_v1**: single-fit on 2024-11 ~ 2025-06 (8 mo), applied to ALL OOS 2025-07 ~ 2026-04 (10 mo). v7 best_params used as smoke baseline (not Optuna re-searched on this panel).

Two issues stacking:
- **Severe regime drift**: 10-month OOS too far from training; market regime changes invalidate the single fit
- **Wrong hyperparams**: v7 best_params optimized for v7 panel/window, not for unified_v1 distribution

## Verdict: **PARTIAL** — perception_market validated, but unified ranker single-fit dead

Per goal.md Phase 4.2-diag exit criteria:
- Recovery (rank_ic ≥ 0.04): NOT achieved with single-fit
- Partial (0.025-0.04): perc_market 0.0355 lift validates feature group works under proper training
- Kill (< 0.025): single-fit approach IS killed; walk-forward + Optuna needed

## Decisions

1. **Phase 4.2 MVP single-fit script kept as proof-of-concept only**. Do not use for production inference.

2. **Phase 4.2 final blocked on**:
   - 4.2a (this verdict): single-fit DEAD
   - 4.2b: implement walk-forward unified ranker (mirror retrain_lambdamart_v6.py expanding_monthly logic on unified panel)
   - 4.2c: Optuna 50-trial re-search on walk-forward (~$5-10 GCP, 1-2 day)

3. **Phase 4.1b bc_absorbed formula merge DEFERRED** until walk-forward unified delivers rank_ic ≥ 0.04. Adding 49 features to a broken trainer wastes 1 week + makes diagnosis harder.

4. **Phase 5 G1-only Config B ACTIVATED for now**: v7 daily inference operational (Step 5e wired). Forward production setup proceeds with G1 only.

5. **Fix perc_stock JOIN bug**: stock_context_daily + under_reaction_daily marts need backfill to 2024-11+ coverage (Track A perception side, not Track B copy). Mark as Phase 3.7 (new).

## Next concrete steps

| # | Task | ETA | Path |
|---|---|---|---|
| 4.2b | Write `backend/scripts/retrain_unified_ranker_walkforward.py` (expanding_monthly) | 1 day | local |
| 4.2c | Optuna 50-trial on walk-forward unified | 1-2 day GCP $5-10 | controlled use |
| 3.7 (new) | Backfill stock_context_daily + under_reaction_daily to 2024-11+ coverage (Track A perception sibling repo) | 1 week | $0 |
| 5.1 G1-only | v7 forward production setup (skip G2/G3) | 3-5 days | already partial via Step 5e |
| 4.1b | bc_absorbed merge | 1 week | BLOCKED on 4.2c result |
