# Data Lineage / Traceability Spec

Version: 0.1  
Date: 2026-05-19  
Status: design only, first CLI implementation in `backend/scripts/trace_lineage.py`  
Primary user question: "I see ann +48% in paper_sim. How exactly was this calculated, how clean was the input data, and when was the model trained?"

## A. Current State Inventory

### A1. Registry coverage

The project already has a partial asset registry:

| Asset | Current role | Observed state |
|---|---|---:|
| `dim_data_asset` | table-level catalog | 251 registered assets in the current DuckDB |
| `mart_lineage` | metadata-only derived table declarations | 24 registry rows, mostly mart/fact build declarations |
| `mart_data_source_watermark` | source freshness and fallback state | 11 source rows across kline, LHB, holders, industry, financial, survey, xdxr |
| `mart_pipeline_run_manifest` | script run ledger | 672 historical run rows, only partial coverage for model runs |
| `mart_model_feature_lineage` | feature-to-source map for selected model families | table exists, currently sparse for P0b LambdaMART v6 |

The existing registry is enough to identify many table-level dependencies, but not enough to answer a KPI trace without additional heuristics.

### A2. Current mart lineage fields

The main P0b and paper-sim tables already carry fragments of lineage:

| Table | Existing lineage fields | Trace value |
|---|---|---|
| `mart_paper_sim_kpi` | `sim_run_id`, `variant`, `period_start`, `period_end`, `config_snapshot`, `built_at` | root KPI row and paper-sim time window |
| `mart_p0b_lambdamart_v6_predictions` | `model_id`, `model_version`, `feature_version`, `label_version`, `train_start`, `train_end`, `test_start`, `test_end`, `built_at` | model and prediction-batch provenance |
| `mart_p0a_feature_label_panel_v4` | `signal_date`, `feature_version`, `built_at` plus feature and label columns | panel range, feature version, row counts |
| `mart_p0a_label_panel` | `label_version`, `built_at`, entry/exit dates, cost-after labels | label construction and VWAP policy evidence |
| `mart_p1_optuna_trials` | `run_id`, `trial_number`, `params_json`, `value`, `built_at` | hyperparameter search evidence |
| `mart_pipeline_run_manifest` | `commit_sha`, `command`, `input_tables_json`, `output_tables_json`, `model_id` | command and code revision when recorded |

Gaps:

- `mart_paper_sim_kpi.config_snapshot` currently stores portfolio and swap config only; model and prediction-table selection should also be stored.
- `mart_paper_sim_kpi` needs a stable `lineage_url` field so each KPI row can link to the trace command or UI.
- P0b LambdaMART v6 prediction rows have `feature_version`, but do not directly record feature-registry commit hash.
- Panel PIT fields should be standardized as `source_event_date`, `available_at_date`, and `source_revision_id` for every upstream source family.
- Existing `mart_lineage` has `input_tables` and `sql_hash`, but lacks per-run row counts, PIT cutoff, git commit, and column-level lineage.

### A3. Watermark inventory

`mart_data_source_watermark` already provides source freshness:

| Domain | Example source | Trace interpretation |
|---|---|---|
| `kline_daily` | `tdxhub_quote` tier 1 | primary price data freshness and row count |
| `kline_daily` | `akshare_multi_source` tier 3 | fallback state and fallback reason |
| `lhb_daily` | `aif10_lhb` tier 2 | LHB event freshness |
| `holders_top10_float` | `tdxhub_holders` tier 1 | holder structure freshness |
| `financial_gpcw_8q` | `tdxhub_gpcw` tier 1 | financial feature freshness |
| `industry_sw` / `stock_blocks` | `tdxhub_block` tier 1 | industry source freshness |

Trace output should show watermark rows under the raw/source layer, because the user wants to know whether ann +48% was produced from stale or fallback data.

### A4. PIT coverage estimate

Current PIT coverage is mixed:

| Layer | Coverage estimate | Evidence |
|---|---|---|
| Label construction | high | `mart_p0a_label_panel` stores entry/exit dates and cost-after labels; VWAP label policy is already governed |
| P0b prediction split | high | prediction rows carry train/test windows and `walk_forward_mode`; trace can verify `train_end < signal_date` |
| Feature panel table-level PIT | medium | `mart_p0a_feature_label_panel_v4` stores `signal_date`, `feature_version`, `built_at`; source-level event/available/revision fields are not uniformly queryable |
| Raw source freshness | medium | watermarks exist for critical domains, but not every raw table has a direct watermark row |
| Column lineage | low | feature-to-source mapping is partial and should move into `mart_data_lineage.column_lineage` |

PIT coverage target:

- every table node in a trace has a `pit_cutoff`;
- every feature family records its source event date and availability date;
- every model trace proves training rows end before prediction signal dates;
- every source node reports watermark freshness and fallback state;
- unknown or missing fields are visible as `[MISSING]`, not silently skipped.

### A5. Required asset naming aliases

Historical specs and actual table names differ in a few places. Trace tooling should normalize aliases:

| Requested name | Current canonical table |
|---|---|
| `fact_alpha158_panel` | `fact_feature_panel` or `mart_p0a_feature_label_panel_v4` feature columns |
| `fact_industry_pit` | `mart_stock_industry_pit` |
| `fact_capital_flow_pit` | `fact_capital_flow_pit_daily` |
| `raw_kline` | `v_price_kline_qfq` in `market.duckdb`, plus `kline_daily` watermark |
| `raw_dzjy` | `fact_dzjy_event` and future raw DZJY staging table |

The CLI should display the requested asset name when useful, but resolve to the table that actually exists.

## B. User Scenario: Ann +48% KPI Trace

Scenario:

The user opens the paper_sim KPI table and sees:

```text
sim_run_id = paper_sim_xxx
annual_return = +48%
period = 2025-07-01..2026-04-30
```

The user wants one command:

```bash
PYTHONPATH=backend python backend/scripts/trace_lineage.py --sim-run-id paper_sim_xxx
```

The command must answer:

- which model produced the scores;
- which prediction table and signal-date range were used;
- which feature panel and label panel fed the model;
- which raw sources fed those features;
- whether key source data was fresh or fallback;
- when the model was trained and with what hyperparameters;
- which code/config commit produced the feature and model artifacts.

### Step 1. KPI to model and prediction batch

Input:

- `mart_paper_sim_kpi.sim_run_id`

Trace logic:

1. Load the KPI row from `mart_paper_sim_kpi`.
2. Resolve model linkage from, in order:
   - `mart_paper_sim_lambdamart_v6_kpi_compare.sim_run_id`;
   - future fields in `config_snapshot.selection`;
   - explicit `--model-id` override;
   - explicit `--asset-name` override when tracing a table instead of a KPI.
3. Resolve prediction batch in candidate prediction tables:
   - `mart_p0b_lambdamart_v6_predictions`;
   - `mart_p0b_oos_predictions`;
   - future model-specific prediction marts.
4. Return:
   - `model_id`;
   - `prediction_table`;
   - `model_version`;
   - `feature_version`;
   - `label_version`;
   - `signal_date` min/max;
   - row count;
   - `built_at`;
   - train/test window summary.

Output example:

```text
- mart_paper_sim_kpi sim_run_id=... ann_ret=+48.00% period=...
  - mart_p0b_lambdamart_v6_predictions model_id=... rows=...
    feature_version=p0a_v4 label_version=horizon_governance_v1
```

### Step 2. Panel to feature and label dependencies

Input:

- prediction batch `feature_version` and `label_version`

Trace logic:

1. Resolve `feature_version=p0a_v4` to `mart_p0a_feature_label_panel_v4`.
2. Query panel row count and `signal_date` min/max for the prediction range.
3. Attach parent feature families:
   - `fact_feature_panel` / `fact_alpha158_panel` for Alpha158 and technical features;
   - `mart_p0a_label_panel` for cost-after labels;
   - `mart_stock_industry_pit` / `fact_industry_pit` for industry PIT;
   - `fact_capital_flow_pit_daily` / `fact_capital_flow_pit` for LHB, holder, and executive-trade capital-flow features.
4. Flag missing canonical tables or missing expected PIT columns.

Trace output must show:

- panel version;
- panel row count;
- built timestamp;
- source dependencies;
- PIT cutoff, usually `MAX(signal_date)` for panel nodes.

### Step 3. Fact to raw/source dependencies

Input:

- feature and fact parent assets from step 2

Trace logic:

| Fact/mart asset | Raw/source parents |
|---|---|
| `fact_feature_panel` | `v_price_kline_qfq`, `fact_financial_pit_daily`, `fact_top10_holder_period`, `fact_lhb_event`, `raw_institution_surveys`, `raw_aif10_*` |
| `mart_p0a_label_panel` | `v_price_kline_qfq`, `dim_trading_calendar`, pricing policy config |
| `mart_stock_industry_pit` | `dim_stock_tdx_industry_history`, `dim_stock_tdx_industry`, `raw_tdx_industry_file_snapshot` |
| `fact_capital_flow_pit_daily` | `raw_lhb_daily`, `raw_executive_trade`, `fact_top10_holder_period`, `raw_capital_*` |
| `fact_dzjy_event` | DZJY source sync tables and future raw DZJY staging |

For raw/source assets, trace output must include:

- source system;
- source tier;
- `last_success_at`;
- `last_data_date`;
- row count;
- fallback active flag and reason;
- parser version when available;
- `sync_source` and `sync_timestamp` when the raw table exposes those columns.

If a raw table lacks `sync_source` or `sync_timestamp`, trace output should show `[MISSING sync metadata]`.

### Step 4. Feature version to registry and commit

Input:

- `feature_version` from prediction rows

Trace logic:

1. Resolve `p0a_v4` to `backend/config/feature_registry.yaml`.
2. Report current feature-registry git hash from:
   - `mart_data_lineage.git_commit_hash` when present;
   - `mart_pipeline_run_manifest.commit_sha` when a matching run exists;
   - current repository `git rev-parse --short=12 HEAD` as fallback.
3. Report registry path and feature version.
4. In future, report feature list hash and per-column lineage from `mart_data_lineage.column_lineage`.

Output example:

```text
- backend/config/feature_registry.yaml feature_version=p0a_v4 commit=abc123...
```

### Step 5. Model artifact to Optuna study and hyperparameters

Input:

- `model_id`

Trace logic:

1. Locate model training run from:
   - `mart_pipeline_run_manifest.model_id`;
   - model artifact path convention under `models/`;
   - P0b LambdaMART built timestamp;
   - optional future `fact_model_train_log`.
2. Locate Optuna evidence from:
   - `mart_p1_optuna_trials`;
   - future `study_id` or `run_id` columns on model artifacts.
3. Report:
   - study/run ID;
   - best or latest trial;
   - objective value;
   - `params_json`;
   - training command;
   - commit hash;
   - model artifact path.

If Optuna linkage is not exact, the trace must mark it as inferred:

```text
[INFERRED] latest mart_p1_optuna_trials rows, no exact model_id key
```

## C. Lineage Table Schema

Create a run-level lineage table:

```sql
CREATE TABLE IF NOT EXISTS mart_data_lineage (
    lineage_run_id       TEXT PRIMARY KEY,
    asset_id             TEXT NOT NULL,
    asset_name           TEXT NOT NULL,
    asset_type           TEXT NOT NULL, -- table | file | model | config | source
    parent_asset_id      TEXT[],        -- DuckDB list of direct parent asset ids
    column_lineage       JSON,          -- feature or KPI column to source columns
    build_command        TEXT,
    git_commit_hash      TEXT,
    model_id             TEXT,
    feature_version      TEXT,
    panel_version        TEXT,
    sim_run_id           TEXT,
    built_at             TIMESTAMP,
    pit_cutoff           TIMESTAMP,
    source_records_count BIGINT,
    source_watermark_json JSON,
    quality_status       TEXT,          -- pass | warn | fail | unknown
    notes                TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Recommended indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_mdl_asset
    ON mart_data_lineage(asset_name, built_at DESC);

CREATE INDEX IF NOT EXISTS idx_mdl_sim_run
    ON mart_data_lineage(sim_run_id);

CREATE INDEX IF NOT EXISTS idx_mdl_model
    ON mart_data_lineage(model_id);

CREATE INDEX IF NOT EXISTS idx_mdl_feature_version
    ON mart_data_lineage(feature_version);
```

Column semantics:

| Column | Required | Meaning |
|---|---|---|
| `asset_id` | yes | stable ID, e.g. `table:mart_p0b_lambdamart_v6_predictions:model_id=...` |
| `parent_asset_id` | yes | direct parents only; recursive tree is derived at read time |
| `column_lineage` | yes for marts | JSON map from output columns to source columns and transforms |
| `build_command` | yes for derived assets | exact CLI or cron entrypoint |
| `git_commit_hash` | yes for derived assets | build-time commit, not current checkout |
| `built_at` | yes | asset build timestamp |
| `pit_cutoff` | yes for PIT-sensitive assets | latest allowed source timestamp/date |
| `source_records_count` | best effort | row count for this build or source snapshot |

Minimal column-lineage example:

```json
{
  "annual_return": {
    "parents": ["mart_paper_sim_nav.total_value"],
    "transform": "compute_metrics(navs).annual_return"
  },
  "score": {
    "parents": ["mart_p0a_feature_label_panel_v4.*"],
    "transform": "LambdaMART prediction score"
  }
}
```

Write contract:

- every build script that writes a mart/fact table should upsert one `mart_data_lineage` row;
- every model-training script should write one model artifact row and one prediction-batch row;
- every paper-sim KPI writer should write or link a root KPI row;
- source sync scripts should write source watermark JSON and raw row counts;
- missing parent assets are allowed only with `quality_status='warn'` and a note.

## D. One-Click Command Design

Command:

```bash
PYTHONPATH=backend python backend/scripts/trace_lineage.py --sim-run-id SIM_RUN_ID
```

Alternate roots:

```bash
PYTHONPATH=backend python backend/scripts/trace_lineage.py --model-id lgbm_phase5_session_20260518T160747
PYTHONPATH=backend python backend/scripts/trace_lineage.py --panel-version p0a_v4
PYTHONPATH=backend python backend/scripts/trace_lineage.py --asset-name mart_p0a_feature_label_panel_v4
```

Output format:

- Markdown only, so output can be pasted into an issue, PR, or report.
- First section is the root summary.
- Second section is the dependency tree.
- Third section is source freshness.
- Fourth section is gaps and warnings.

Required tree fields per asset:

```text
- asset_name
  build_command: ...
  commit_hash: ...
  row_count: ...
  pit_cutoff: ...
```

Performance target:

- one trace must complete in under 10 seconds on `data/smartmoney.duckdb`;
- no full table materialization;
- only `COUNT`, `MIN`, `MAX`, and indexed/equality filters;
- large prediction tables must always filter by `model_id`;
- recursive tree depth defaults to 5 and should be configurable later.

Failure behavior:

- unknown root ID exits non-zero with a clear message;
- missing optional lineage emits `[MISSING]` in Markdown and exits zero if the root was found;
- SQL errors on one child asset are isolated to that asset node;
- trace should never mutate DuckDB.

## E. Paper-Sim KPI Reporter Integration

`mart_paper_sim_kpi` should include:

```sql
ALTER TABLE mart_paper_sim_kpi ADD COLUMN lineage_url TEXT;
```

Writer behavior in `backend/services/paper_sim/reporter.py`:

- each KPI row keeps its existing `sim_run_id` primary key;
- each KPI row writes `lineage_url = 'lineage://paper-sim/' || sim_run_id`;
- the UI can map that URL to:

```bash
PYTHONPATH=backend python backend/scripts/trace_lineage.py --sim-run-id <sim_run_id>
```

Future reporter enhancement:

- extend `config_snapshot` to include selection config:
  - `ml_score_model_id`;
  - `ml_score_prediction_table`;
  - `panel_version`;
  - `feature_version`;
  - `pricing_policy_id`.
- optionally upsert the root `mart_data_lineage` row during `write_kpi_summary`.

Acceptance:

- a user can click or copy one field from any KPI row;
- the trace identifies KPI math, prediction source, panel source, raw source freshness, model training evidence, and commit/config provenance;
- missing evidence is explicit and actionable, not hidden.
