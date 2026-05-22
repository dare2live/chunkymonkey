# Local Optuna Aggregate Audit

- passed: `True`
- market_total: `5201`
- adoption_rows: `26005`
- candidates: `1146`
- replacements: `1146`
- data_latest_date: `2026-05-19`

## Candidate Formula Distribution

- `activity_breakout`: `652`
- `gs_pullback_confirm`: `144`
- `gs_raw_buy`: `233`
- `volume_base_breakout`: `117`

## Checks

- [x] `full_market_coverage`: covered=5201 market_total=5201
- [x] `row_count_matches_stock_formula_grid`: rows=26005 stocks=5201 formulas=5
- [x] `merge_plan_matches_adoption`: merge_rows=26005 adoption_rows=26005
- [x] `replacements_match_candidates`: replacements=1146 candidates=1146
- [x] `replacement_schema_compatible`: missing_headers=
- [x] `missing_rows_have_investigation`: missing_without_reason=0
- [x] `research_cache_source_rows_match`: source_rows={'adoption': 26005, 'merge_plan': 26005, 'production': 21302}
- [x] `incremental_eval_clean`: incremental_rows=45908 dirty_rows=0
- [x] `drift_trigger_current`: drift_rows=45908 incremental_rows=45908
