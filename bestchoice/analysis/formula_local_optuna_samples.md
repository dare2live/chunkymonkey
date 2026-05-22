# Formula Local Optuna Samples

- codes: `301511, 301658, 688700, 002718`
- formulas: `gs_pullback_confirm, gs_raw_buy, ma_base_breakout, activity_breakout, volume_base_breakout`
- trials_per_stock_formula: `24`
- max_signals_per_stock: `120`
- execution_model: `vwap_tradable_v1`
- elapsed_sec: `3.4`

## Top Score Deltas

| stock | formula | baseline_status | optuna_status | baseline | optuna | delta | train | validation | val_delta | sell_rule |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `688700` | `gs_pullback_confirm` | `ok` | `ok` | -14.04 | 49.66 | 63.70 | 9.79 | 83.49 | 104.75 | `fixed_5` |
| `301658` | `volume_base_breakout` | `ok` | `ok` | 77.57 | 86.16 | 8.59 | 83.84 | 64.57 | 0.00 | `fixed_30` |
| `002718` | `activity_breakout` | `ok` | `ok` | 79.45 | 83.97 | 4.52 | 70.83 | 95.00 | 0.77 | `fixed_60` |
| `688700` | `activity_breakout` | `ok` | `ok` | 65.02 | 68.26 | 3.25 | 67.13 | 70.90 | 4.75 | `fixed_20` |
| `301511` | `volume_base_breakout` | `ok` | `ok` | 72.82 | 75.70 | 2.88 | 79.19 | 66.95 | -12.77 | `formula_exit_or_5` |
| `301658` | `gs_raw_buy` | `ok` | `ok` | 67.68 | 68.16 | 0.48 | 45.85 | 86.16 | 0.00 | `fixed_20` |
| `301511` | `activity_breakout` | `ok` | `ok` | 66.25 | 66.49 | 0.24 | 70.92 | 48.40 | -33.99 | `fixed_10` |
| `301658` | `gs_pullback_confirm` | `ok` | `ok` | -19.50 | -19.50 | 0.00 | -22.88 | -22.63 | 0.00 | `fixed_30` |
| `002718` | `gs_pullback_confirm` | `ok` | `ok` | 86.16 | 86.09 | -0.06 | 92.68 | 60.50 | -20.08 | `formula_exit_or_60` |
| `301658` | `activity_breakout` | `ok` | `ok` | 65.79 | 65.28 | -0.51 | 60.23 | 69.58 | -16.58 | `fixed_15` |
| `688700` | `gs_raw_buy` | `ok` | `ok` | 73.56 | 71.73 | -1.83 | 69.57 | 76.19 | 0.04 | `fixed_15` |

## Missing Result Reasons

```json
{
  "missing_baseline_result": 4,
  "missing_optuna_result": 2
}
```

## Notes

- This is an exploratory local Optuna audit and does not overwrite production `stock_formula_best.csv`.
- Positive deltas identify candidates where continuous local search may justify a production integration pass.
