# Operational Delivery Readiness

- operational_ready: `True`
- generated_at: `2026-05-20T18:02:43.905448+00:00`
- covered_stocks: `5201` / `5201`
- candidates: `1146`
- replacements: `1146`
- data_latest_date: `2026-05-19`

## Checks

- [x] `final_gates_passed`: 
- [x] `full_market_covered`: covered=5201 market_total=5201
- [x] `aggregate_audit_passed`: candidates=1146 replacements=1146
- [x] `state_stores_clean`: consistency=True incremental=45908 drift=45908
- [x] `production_table_not_auto_overwritten`: stock_formula_best_rows=21302 source_rows={'adoption': 26005, 'merge_plan': 26005, 'production': 21302}
- [x] `dry_run_replacements_separate`: replacement_count=1146 candidate_count=1146
- [x] `no_active_worker_or_market_lock`: active_workers=0 market_locks=0

## Gates

- [x] `py_compile`: returncode `0`
- [x] `execution_model_smoke`: returncode `0`
- [x] `unified_data_smoke`: returncode `0`
- [x] `strategy_rebuild_audit`: returncode `0`
- [x] `formula_local_optuna_aggregate_audit`: returncode `0`
- [x] `git_diff_check`: returncode `0`
- [x] `workflow_checkpoint_brief`: returncode `0`

## Production Merge Control

Ready for controlled production merge review. This audit does not write analysis/stock_formula_best.csv; human approval is still required before replacement.
