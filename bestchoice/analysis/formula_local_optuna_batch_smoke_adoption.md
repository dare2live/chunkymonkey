# Formula Local Optuna Adoption Candidates

- input: `analysis/formula_local_optuna_batch_smoke.csv`
- output: `analysis/formula_local_optuna_batch_smoke_adoption.csv`

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

- rows: `1`
- candidates: `0`
- rejected: `1`

## Candidates

| stock | formula | baseline | optuna | delta | val_delta | signals | val_signals | win | val_win | avg_ret | val_ret | sell_rule |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|

## Rejection Reason Counts

```json
{
  "baseline_investigation={\"reason\": \"stock_formula_best.csv has no row for this stock/formula\", \"status\": \"missing_baseline_result\"}": 1,
  "baseline_status=missing_baseline_result": 1,
  "optuna_investigation={\"reason\": {\"failure_counts\": {\"no_entry_signal\": 2}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\"}}, \"status\": \"missing_optuna_result\"}": 1,
  "optuna_status=missing_optuna_result": 1,
  "trials<20": 1
}
```

## Production Merge Policy

- Do not merge rows marked `reject` into `stock_formula_best.csv`.
- Candidate rows pass a chronological validation split, but still require a full-market production run before replacement.
- Rows with `baseline_status!=ok` are discovery or data-quality leads, not scored improvements.
- Missing metrics are reported as `missing_metric=...`; they are never treated as zero-value backtest results.
