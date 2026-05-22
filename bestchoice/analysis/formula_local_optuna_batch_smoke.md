# Formula Local Optuna Batch

- rows: `1`
- new_rows: `1`
- codes_requested: `1`
- formulas: `gs_pullback_confirm`
- trials: `2`
- max_signals_per_stock: `20`
- validation_ratio: `0.3`
- execution_model: `vwap_tradable_v1`
- elapsed_sec: `0.0`

## Missing Status Counts

```json
{
  "missing_baseline_result: stock_formula_best.csv has no row for this stock/formula": 1,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 2}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\"}}": 1
}
```

## Top Raw Deltas

| stock | formula | baseline | optuna | delta | validation_delta | status |
|---|---|---:|---:|---:|---:|---|
| `301511` | `gs_pullback_confirm` | 0.00 | 0.00 | 0.00 | 0.00 | `missing_baseline_result/missing_optuna_result` |

## Notes

- This batch artifact is for full-market expansion planning only.
- It does not write to production `analysis/stock_formula_best.csv`.
- Missing baseline/Optuna rows are preserved as investigation leads and are not filled with default metrics.
- Run `scripts/formula_local_optuna_adoption.py --input <batch.csv>` to apply adoption guardrails.
- raw_delta_rows_ge_3: `0`
