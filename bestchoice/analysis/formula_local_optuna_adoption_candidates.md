# Formula Local Optuna Adoption Candidates

- input: `/Users/dp/Documents/M/stock/bestchoice/analysis/formula_local_optuna_samples.csv`
- output: `/Users/dp/Documents/M/stock/bestchoice/analysis/formula_local_optuna_adoption_candidates.csv`

## Guardrails

- baseline_score >= `0.0`
- baseline_status must be `ok`; missing baseline rows are rejected and investigated via status/reason fields.
- optuna_status must be `ok`; missing Optuna results are rejected and investigated via status/reason fields.
- optuna_signal_count >= `6`
- score_delta >= `3.0`
- optuna_win_rate >= `0.45`
- optuna_avg_ret > `0.0`
- trials >= `20`
- optuna_validation_signal_count >= `3`
- optuna_validation_win_rate >= `0.45`
- optuna_validation_avg_ret > `0.0`
- validation_score_delta >= `0.0`

## Summary

- rows: `20`
- candidates: `2`
- rejected: `18`

## Candidates

| stock | formula | baseline | optuna | delta | val_delta | signals | val_signals | win | val_win | avg_ret | val_ret | sell_rule |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `002718` | `activity_breakout` | 79.45 | 83.97 | 4.52 | 0.77 | 38 | 11 | 84.21% | 100.00% | 34.11% | 100.63% | `fixed_60` |
| `688700` | `activity_breakout` | 65.02 | 68.26 | 3.25 | 4.75 | 57 | 17 | 63.16% | 70.59% | 8.89% | 10.36% | `fixed_20` |

## Rejection Reason Counts

```json
{
  "avg_ret<=0": 1,
  "baseline_score<0.0": 2,
  "baseline_status=missing_baseline_result": 4,
  "delta<3.0": 12,
  "optuna_status=missing_optuna_result": 2,
  "signals<6": 8,
  "validation_avg_ret<=0": 4,
  "validation_delta<0.0": 9,
  "validation_signals<3": 8,
  "validation_win_rate<0.45": 5,
  "win_rate<0.45": 3
}
```

## Production Merge Policy

- Do not merge rows marked `reject` into `stock_formula_best.csv`.
- Candidate rows pass a chronological validation split, but still require a full-market production run before replacement.
- Rows with `baseline_status!=ok` are discovery or data-quality leads, not scored improvements.
