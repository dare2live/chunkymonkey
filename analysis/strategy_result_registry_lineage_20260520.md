# Strategy Result Registry Lineage — 2026-05-20

## Purpose

主项目需要像 BestChoice 的 local Optuna adoption/merge plan 一样，把所有策略验证结果先登记为
candidate/reference/hold_reject, 保留参数、血缘、旧新关系与拒绝原因, 不直接覆盖 champion。

## Implemented

- `mart_strategy_result_registry` 已纳入主 `ensure_mart_schema()`, 新库会自动建表。
- `backend/scripts/backfill_strategy_result_registry.py` 继续作为刷新入口, 支持 dry-run 与 apply, 并兼容原生 DuckDB tuple rows 与 wrapped dict rows。
- `backend/scripts/run_paper_sim_v2.py` 在每次 walk-forward KPI/cache 写完后自动刷新 registry。
- `backend/scripts/run_paper_sim_lambdamart_v6_compare.py` 在 compare rows 写完后自动刷新 registry。
- registry 字段已扩展:
  - `parent_result_id`
  - `baseline_result_id`
  - `sim_config_hash`
  - `param_diff_json`
  - `params_json`
  - `lineage_url`
  - `source_artifact_uri`
- 当前生产 DuckDB 已回填 45 rows:
  - 43 rows from `mart_paper_sim_kpi`
  - 2 rows from `mart_paper_sim_lambdamart_v6_kpi_compare`

## Current Coverage

| Field | Coverage |
|---|---:|
| `params_json` | 45/45 |
| `sim_config_hash` | 41/45 |
| `parent_result_id` | 40/45 |
| `param_diff_json` | 18/45 |
| `lineage_url` | 6/45 |
| `baseline_result_id` | 2/45 |
| champion `mart_model_feature_lineage` | 27/27 known, 0 missing |

## Current Decisions

| Result | Decision | Notes |
|---|---|---|
| `lambdamart_v6` challenger | `hold_reject` | monthly_win_rate, annual return, and Sharpe trail baseline |
| v4 baseline compare row | `reference` | retained as baseline reference |
| paper_sim KPI rows | `blocked` | historical rows do not pass current all-KPI gate |
| champion feature lineage | `passed` | `baseline60_driftsafe_qfq_factor_vwap_model_selection_run_20260507_055705`: 27 features, 0 missing |

## Remaining Gaps

- v6 is not present in `mart_multidim_model`, so feature lineage cannot be generated through `build_model_feature_lineage.py`; it remains tracked as a rejected compare/registry challenger.
- `lineage_url` remains sparse for legacy KPI rows.
- Prediction-row to panel-cell trace is still not guaranteed within 5 steps.
- Layer 4 retrain warm-start remains spec-only.

## Verification

```bash
PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_mart_data_lineage_compat.py \
  backend/tests/test_backfill_strategy_result_registry.py \
  backend/tests/test_backfill_paper_sim_cache_metadata.py \
  backend/tests/test_workbench_paper_sim_timeseries.py \
  backend/tests/paper_sim/test_lambdamart_v6_compare.py \
  backend/tests/test_model_feature_lineage.py
```

Latest focused results:

- registry auto-refresh set: 15 passed.
- feature-lineage/registry/compare compatibility set: 12 passed.
