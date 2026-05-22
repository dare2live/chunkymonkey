# Formula Parameter Search

- formulas: `gs_pullback_confirm, gs_raw_buy, ma_base_breakout, activity_breakout, volume_base_breakout`
- formula_variants: `19`
- stocks: `5201`
- workers: `2`
- max_signals_per_stock: `120`
- execution_model: `vwap_tradable_v1`
- elapsed_sec: `957.3`

## Artifacts

- `analysis/formula_variant_metrics.csv`
- `analysis/stock_formula_best.csv`

## Scope

- First-stage grid search across named formula variants and fixed holding periods.
- Sell-rule search now compares fixed holding rules with formula-exit capped rules per stock and variant.
- Dense formulas are capped to the latest signals per stock during exploratory search so an over-broad formula cannot dominate runtime.
- Per-stock best rows are selected by score within each formula.
- Later Optuna/local search can expand the same output schema without changing the UI contract.
