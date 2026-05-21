# Verified Strategy Results — 2026-05-20

Source of truth:
- `data/smartmoney.duckdb::mart_paper_sim_kpi` (41 rows)
- `docs/paper_sim_overview_20260520.md`
- `data/reports/paper_sim/champion_baseline_20260520T102611_validation.md`
- `goal.md`

This file protects already-validated work from being overwritten by the in-flight
Phase 5 GCP challenger. The GCP model `lgbm_phase5_gcp_20260520T010718` is a
challenger until its predictions are imported and post-retrain paper sim / gates
are run.

## Current Production Candidate Ranking

Ranking logic: prefer explicitly validated `champion_*` variants, exclude known
leakage alarms, then compare Sharpe, drawdown, annual return, monthly win rate,
and turnover under the user-accepted relaxed thresholds from `goal.md`.

| Rank | sim_run_id | Role | ann | max_dd | Sharpe | monthly_win | turnover | Verdict |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | `champion_minhold15_20260520_111606_20260520_031612_9137bf` | prod-candidate alpha enhancement | +108.18% | -20.36% | 2.121 | 66.67% | 49.57x | Best validated candidate; user accepted realistic net expectation around +70-80% after frictions. |
| 2 | `champion_baseline_20260520T102611_20260520_022612_4b63c0` | champion baseline | +67.79% | -20.81% | 1.660 | 71.43% | 54.88x | Strong baseline; user accepted drawdown as soft miss; anti-churn still fails original strict threshold. |
| 3 | `champion_minhold5_20260520_105535_20260520_025539_b968ac` | lower drawdown variant | +53.50% | -17.38% | 1.564 | 66.67% | 48.82x | Best drawdown among champion variants; lower return/Sharpe than minhold15. |
| 4 | `swap_v1_20260513_120214_7fccb2` | historical long-window comparator | +65.69% | -20.75% | 1.904 | 81.58% | 231.19x | Useful historical comparator, but turnover is too high for production. |
| 5 | `swap_v1_20260515_151701_5b9c84` | historical comparator | +66.64% | -20.03% | 1.576 | 66.67% | 60.01x | Good return/Sharpe, but not a current champion path and turnover remains high. |

## Explicitly Not Current Best

- `swap_v1_20260516_105028_cb9235`: +114.15% ann / 2.570 Sharpe / -7.41% max_dd / 100% monthly win.
  It is retained as a historical strongest run but is flagged in
  `docs/paper_sim_overview_20260520.md` as a leakage alarm because monthly win is
  100%. Do not use it as production best unless a dedicated PIT ablation clears it.
- `champion_maxpos10_minhold15_20260520_121320_20260520_041321_2e4753`:
  +112.31% ann, but max_dd is -26.13%; docs mark it as withdrawn because drawdown
  crosses the accepted risk line.

## Current Champion Table

`mart_champion_model` still points to:

- `champion_id`: `lgbm_20260517_governance_v1_20d_p3_session_fixed`
- `model_id`: `lgbm_20260517_governance_v1_20d`
- P3 metrics: ann +30.68%, max_dd -10.84%, monthly_win 77.27%, n_oos_months 22.

This is the promoted champion record, while the May 20 `champion_*` paper-sim
variants are validated candidates that still need final decision/gate wiring
before replacing the champion table.

## GCP Challenger Closeout

- `lgbm_phase5_gcp_20260520T010718` materialized predictions were imported
  locally: 3,396,073 rows in `mart_p0b_lambdamart_v6_predictions`, 674 signal
  dates, 2023-07-03 to 2026-04-14.
- `mart_p0b_oos_predictions` has 0 rows for this model; the v6 paper-sim compare
  used `mart_p0b_lambdamart_v6_predictions`.
- Compare id:
  `phase5_gcp_lgbm_phase5_gcp_20260520T010718_baseline_window`.
- Final result: `lambdamart_v6` has higher RankIC than baseline, but weaker
  portfolio KPI: ann +43.67%, Sharpe 1.323, max_dd -17.16%, monthly win 50.00%.
  Baseline is ann +67.79%, Sharpe 1.660, max_dd -20.81%, monthly win 71.43%.
- Registry closeout: `mart_strategy_result_registry` records the challenger as
  `decision=hold_reject` / `production_status=challenger_hold_reject`. It must
  not replace the current champion or the May 20 validated candidate ranking.
