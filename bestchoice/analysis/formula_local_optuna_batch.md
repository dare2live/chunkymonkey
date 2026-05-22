# Formula Local Optuna Batch

- rows: `26005`
- new_rows: `25`
- codes_requested: `5`
- formulas: `gs_pullback_confirm, gs_raw_buy, ma_base_breakout, activity_breakout, volume_base_breakout`
- trials: `24`
- max_signals_per_stock: `120`
- validation_ratio: `0.3`
- execution_model: `vwap_tradable_v1`
- elapsed_sec: `3.7`

## Missing Status Counts

```json
{
  "missing_baseline_result: stock_formula_best.csv has no row for this stock/formula": 4703,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 16, \"no_executable_trade\": 8}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 1,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 17, \"no_executable_trade\": 7}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 1,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 21, \"no_executable_trade\": 3}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 4,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 22, \"no_executable_trade\": 2}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 2,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 23, \"no_executable_trade\": 1}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 6,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 24}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\"}}": 1382,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 6, \"no_executable_trade\": 18}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 1,
  "missing_optuna_result: {\"failure_counts\": {\"no_entry_signal\": 8, \"no_executable_trade\": 16}, \"failure_examples\": {\"no_entry_signal\": \"formula produced no entry signals\", \"no_executable_trade\": \"entry signals produced no executable trades\"}}": 2
}
```

## Top Raw Deltas

| stock | formula | baseline | optuna | delta | validation_delta | status |
|---|---|---:|---:|---:|---:|---|
| `002322` | `ma_base_breakout` | -30.68 | 86.16 | 116.84 | 0.00 | `ok/ok` |
| `603027` | `ma_base_breakout` | -33.91 | 77.64 | 111.55 | 86.00 | `ok/ok` |
| `300769` | `gs_pullback_confirm` | -18.73 | 92.68 | 111.41 | 105.89 | `ok/ok` |
| `600117` | `ma_base_breakout` | -18.86 | 90.66 | 109.52 | 106.18 | `ok/ok` |
| `600938` | `ma_base_breakout` | -22.57 | 85.66 | 108.23 | 0.00 | `ok/ok` |
| `605179` | `gs_pullback_confirm` | -21.79 | 86.16 | 107.95 | 0.00 | `ok/ok` |
| `600470` | `ma_base_breakout` | -22.27 | 83.84 | 106.11 | 0.00 | `ok/ok` |
| `300776` | `ma_base_breakout` | -21.06 | 83.84 | 104.90 | 0.00 | `ok/ok` |
| `002560` | `ma_base_breakout` | -32.95 | 71.09 | 104.04 | 0.00 | `ok/ok` |
| `300237` | `ma_base_breakout` | -23.25 | 80.58 | 103.83 | 0.00 | `ok/ok` |
| `000822` | `ma_base_breakout` | -17.14 | 86.16 | 103.30 | 0.00 | `ok/ok` |
| `301060` | `ma_base_breakout` | -21.98 | 80.58 | 102.55 | 0.00 | `ok/ok` |

## Notes

- This batch artifact is for full-market expansion planning only.
- It does not write to production `analysis/stock_formula_best.csv`.
- Missing baseline/Optuna rows are preserved as investigation leads and are not filled with default metrics.
- Run `scripts/formula_local_optuna_adoption.py --input <batch.csv>` to apply adoption guardrails.
- raw_delta_rows_ge_3: `9308`
