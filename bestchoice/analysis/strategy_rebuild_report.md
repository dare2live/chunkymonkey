# Strategy Rebuild Audit

- data_latest_date: `2026-05-19`
- global_latest_data_date: `2026-05-19`
- execution_model: `vwap_tradable_v1`
- formula_profiles: `5`
- ready_formula_caches: `5`
- missing_formula_caches: `none`
- parameter_search_ready: `yes`
- formula_variant_metric_rows: `228`
- stock_formula_best_rows: `21302`

## Formula Cache Status

- `GS回调确认` `formula_gs_pullback_confirm`: cache=yes, stocks_with_signal=4413
- `GS原始买点` `formula_gs_raw_buy`: cache=yes, stocks_with_signal=5131
- `均线筑底突破` `formula_ma_base_breakout`: cache=yes, stocks_with_signal=1496
- `活跃度大牛突破` `formula_activity_breakout`: cache=yes, stocks_with_signal=5131
- `巨量蓄势启动` `formula_volume_base_breakout`: cache=yes, stocks_with_signal=5131

## Unified Pool Summary

- unavailable

## Key Sample Verification

- unified sample verification unavailable: unified pool not ready

## Recommendation Guard Samples

- unified sample verification unavailable: unified pool not ready

## Generated Artifacts

- `analysis/formula_parameter_search_summary.csv`
- `analysis/formula_stock_best_params.csv`
- `analysis/formula_variant_metrics.csv`
- `analysis/stock_formula_best.csv`
- `analysis/execution_model_audit.csv`

## Notes

- This audit summarizes caches that already exist and are fresh.
- Missing formula caches must be computed before final completion.
- Parameter search is considered ready only when both variant metrics and per-stock best outputs contain rows.
- Key sample lines are generated from `/api/unified`-equivalent engine data, not hand-written observations.
