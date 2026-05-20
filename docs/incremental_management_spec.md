# Incremental Management Framework

Version: 0.1
Date: 2026-05-20
Status: P0 paper_sim cache implemented; P1 overview partial; P2/P3 specs only

## 1. Objective

ChunkyMonkey needs incremental management because repeated paper_sim runs, prediction reuse, panel rebuilds, and model retrains now dominate wall time. This spec defines a 4-layer cache and data-lineage system that skips repeated work only on exact identity and keeps every derived asset traceable to config, source hashes, parent assets, and build command.

Goals:

- exact cache hits, no fuzzy match;
- old rows with `NULL` hashes remain readable;
- paper_sim parameter-impact chains are explicit;
- every mart table eventually has lineage in `mart_data_lineage`;
- full rebuilds become exceptions, not default daily work.

Non-goals for P0:

- no db.py split work;
- no interruption to GCP retrain v2;
- no Layer 3 panel incremental implementation;
- no Layer 4 warm-start retrain implementation.

## 2. Global Rules

All cache lookups must use strict equality:

```sql
WHERE config_hash = ?
```

For paper_sim:

```sql
WHERE sim_config_hash = ?
```

No nearest-neighbor match, same-name match, partial source match, or fallback match is allowed. `NULL` hashes are legacy compatibility only and must never count as cache hits.

## 3. Layer Summary

| Layer | Scope | P0 status | Expected ROI |
|---|---|---|---:|
| 1 | paper_sim repeated configs | implemented | save 10 to 60 minutes per repeated run |
| 2 | prediction cache | existing behavior documented | avoid retrain for existing immutable `model_id` |
| 3 | panel build incremental | P2 spec only | 30 minute rebuild to 1 to 5 minute append |
| 4 | retrain warm-start | P3 spec only | 1500 fits to about 50 fits for one new month |

## 4. Layer 1: paper_sim Cache

Current paper_sim v2 can take about 14 minutes for recent champion ranges and 30 to 60 minutes for larger ablations. Repeated configs during minhold, max position, sector-budget, or cost sweeps should reuse prior KPI rows instead of rebuilding NAV, position, trade, and KPI data.

### 4.1 Cache Key

Required identity:

```text
sim_config_hash = md5(yaml_content + model_id + start_date + end_date + panel_version)
```

Inputs:

- `yaml_content`: exact effective YAML config content;
- `model_id`: immutable prediction model ID;
- `start_date`: simulation start date;
- `end_date`: simulation end date;
- `panel_version`: logical or physical feature panel identity.

If the runner applies an in-memory override, the effective config content must be hashed so override runs cannot collide with raw YAML runs. This matters for `--variant baseline`, which overrides `swap.enabled`.

### 4.2 Schema Migration

`mart_paper_sim_kpi` gains:

```sql
ALTER TABLE mart_paper_sim_kpi ADD COLUMN IF NOT EXISTS sim_config_hash VARCHAR;
ALTER TABLE mart_paper_sim_kpi ADD COLUMN IF NOT EXISTS parent_sim_run_id VARCHAR;
ALTER TABLE mart_paper_sim_kpi ADD COLUMN IF NOT EXISTS param_diff_json VARCHAR;
```

Column semantics:

| Column | Meaning |
|---|---|
| `sim_config_hash` | exact paper_sim cache key |
| `parent_sim_run_id` | parent run for parameter-impact lineage |
| `param_diff_json` | JSON diff from parent to child |

The migration is idempotent and nullable for old rows.

### 4.3 Cache Lookup

Before a run:

```sql
SELECT *
FROM mart_paper_sim_kpi
WHERE sim_config_hash = ?
ORDER BY built_at DESC NULLS LAST
LIMIT 1;
```

If a row exists, print:

```text
cache hit: <hash>
```

Then return the prior KPI row and do not rewrite NAV, position, trade, or KPI rows. A partial prior run without a KPI row is not a cache hit.

### 4.4 CLI Additions

`backend/scripts/run_paper_sim_v2.py` adds:

```bash
--skip-if-cached
--parent-sim-run-id <sim_run_id>
--param-diff-json '{"exit.min_holding_days_before_exit":[5,15]}'
```

`--skip-if-cached` defaults to false. Parent arguments annotate successful runs and are optional.

### 4.5 Registration

After `write_kpi_summary` succeeds:

```sql
UPDATE mart_paper_sim_kpi
SET sim_config_hash = ?,
    parent_sim_run_id = ?,
    param_diff_json = ?
WHERE sim_run_id = ?;
```

Registration must be idempotent. Running it twice for the same `sim_run_id` must not create duplicate rows.

### 4.6 Legacy Compatibility

Old KPI rows may have all three new columns as `NULL`. They remain visible in reports and old queries, but exact cache lookup will not match them.

### 4.7 Layer 1 ROI

| Scenario | Hit rate | Saved time |
|---|---:|---:|
| exact champion rerun | 100% | about 14 minutes |
| minhold grid rerun | 60% | about 40 to 60 minutes |
| paper_sim ablation notebook | 50% | about 1.5 to 3 hours |

P0 target:

```text
paper_sim cache hit rate >= 80% for repeated configs
```

## 5. Layer 2: Prediction Cache

This layer already exists operationally. `mart_p0b_lambdamart_v6_predictions` acts as the prediction cache; paper_sim can reuse rows when the prediction table and immutable `model_id` are the same.

Lookup:

```sql
SELECT COUNT(*)
FROM mart_p0b_lambdamart_v6_predictions
WHERE model_id = ?;
```

Required model identity:

```text
model_id = hash(model_config + train_data_hash)
```

`model_config` includes objective, label horizon, feature list, hyperparameters, walk-forward split policy, ranking group policy, and cost-aware settings. `train_data_hash` includes panel table, feature version, label version, train date range, source partition hashes, row count, and selected feature columns.

This prevents same-name collisions such as two different models both named `lgbm_baseline_v1`.

Layer 2 ROI:

| Scenario | Result |
|---|---|
| rerun paper_sim on same model | no retrain |
| minhold parameter grid | no retrain |
| sector-budget ablation | no retrain |
| changed model config or train data | new model ID, no cache hit |

P0 only documents this layer; no prediction-cache code changes are included.

## 6. Layer 3: Panel Build Incremental

Status: P2 spec only.

Current state:

```text
mart_p0a_feature_label_panel_v4
about 4M rows
about 30 minutes full rebuild
```

Target: partition by `signal_date_month`, append new months, and skip existing months when source hashes are identical.

Target CLI:

```bash
PYTHONPATH=backend python backend/scripts/build_p0a_feature_panel_v4.py \
  --incremental \
  --start-month 2026-05
```

Optional flags:

```bash
--end-month 2026-05
--force-month 2026-04
```

Metadata table:

```sql
CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v4_meta (
    signal_date_month TEXT PRIMARY KEY,
    last_built_month TEXT,
    source_hashes JSON,
    row_count BIGINT,
    min_signal_date DATE,
    max_signal_date DATE,
    built_at TIMESTAMP,
    build_command TEXT
);
```

Source hashes should cover price rows plus lookback, financial PIT rows, holder rows, LHB rows, industry PIT rows, label-panel rows, and feature registry version.

Skip rule:

```text
if current_source_hashes == stored_source_hashes:
    skip month
else:
    rebuild month
```

Rebuild rule:

1. Build month rows into a temporary table.
2. Validate row count and null coverage.
3. Delete old rows for that month.
4. Insert new rows.
5. Upsert metadata.
6. Upsert `mart_data_lineage`.

Late data can invalidate old months, so daily update should recheck at least the last 3 months.

Layer 3 ROI:

| Workload | Current | Incremental |
|---|---:|---:|
| no source change | about 30 minutes | less than 1 minute |
| one new month | about 30 minutes | about 1 to 5 minutes |
| late-data month rebuild | about 30 minutes | about 3 to 8 minutes |

ETA: 2 to 4 engineering days.

## 7. Layer 4: Retrain Warm-start

Status: P3 spec only.

Current cost:

```text
50 trials x 30 windows = 1500 LightGBM fits
```

Target behavior: a new month adds one OOS window; unchanged historical windows reuse fitted LightGBM artifacts; changed windows refit; predictions are written under a new content-addressed `model_id`.

Target CLI:

```bash
PYTHONPATH=backend python backend/scripts/run_p0b_lambdamart_v6.py \
  --warm-start-from <prev_model_id>
```

Optional:

```bash
--artifact-root data/models
--force-window <window_id>
--no-warm-start
```

Artifact option A:

```sql
CREATE TABLE IF NOT EXISTS mart_lambdamart_v6_model_artifacts (
    model_id TEXT NOT NULL,
    window_id TEXT NOT NULL,
    train_start DATE,
    train_end DATE,
    test_start DATE,
    test_end DATE,
    config_hash VARCHAR,
    train_data_hash VARCHAR,
    artifact_uri TEXT,
    artifact_sha256 VARCHAR,
    built_at TIMESTAMP,
    PRIMARY KEY (model_id, window_id)
);
```

Artifact option B:

```text
data/models/<model_id>/window_<n>.pkl
data/models/<model_id>/manifest.json
```

Reuse is allowed only if config hash, train data hash, train range, test range, and artifact checksum all match exactly. Any mismatch forces refit.

Layer 4 ROI:

| Scenario | Current | Warm-start target |
|---|---:|---:|
| add one month, full Optuna | 1500 fits | about 50 fits |
| materialize best params only | 30 fits | 1 to 3 fits |
| source backfill changes old data | 1500 fits | changed windows only |

ETA: 4 to 8 engineering days.

## 8. Data Lineage Contract

Current `mart_data_lineage` coverage is partial, about 24 rows. Target coverage is all mart tables.

Required lineage fields per mart asset:

```text
config_hash
parent_asset_id[]
source_hashes
built_at
build_command
params_diff_from_parent
```

Recommended migration:

```sql
ALTER TABLE mart_data_lineage ADD COLUMN IF NOT EXISTS config_hash VARCHAR;
ALTER TABLE mart_data_lineage ADD COLUMN IF NOT EXISTS source_hashes JSON;
ALTER TABLE mart_data_lineage ADD COLUMN IF NOT EXISTS params_diff_from_parent JSON;
```

Use JSON text if DuckDB JSON compatibility is an issue.

Asset ID examples:

```text
table:mart_paper_sim_kpi:sim_run_id=<sim_run_id>
table:mart_p0b_lambdamart_v6_predictions:model_id=<model_id>
table:mart_p0a_feature_label_panel_v4:month=2026-05
file:data/models/<model_id>/window_12.pkl
config:backend/config/paper_sim_ml_score_champion_minhold15.yaml
```

paper_sim parent chain:

```text
baseline -> minhold5 -> minhold15
```

Each child row stores:

```text
parent_sim_run_id = previous sim_run_id
param_diff_json = exact parameter delta
```

Example:

```json
{"exit.min_holding_days_before_exit":[5,15]}
```

## 9. show_param_impact_curve.py Target

Target script:

```bash
PYTHONPATH=backend python backend/scripts/show_param_impact_curve.py \
  --sim-run-id <leaf_sim_run_id>
```

Behavior:

1. Load leaf KPI row.
2. Walk `parent_sim_run_id` to root.
3. Reverse root to leaf.
4. Print markdown table.
5. Add KPI deltas for each parent-child pair.

Required columns:

| Column | Meaning |
|---|---|
| `sim_run_id` | child run |
| `parent_sim_run_id` | parent run |
| `param_diff_json` | changed parameters |
| `annual_return` | KPI |
| `sharpe` | KPI |
| `max_dd` | KPI |
| `monthly_win_rate` | KPI |
| `delta_annual_return` | child minus parent |
| `delta_sharpe` | child minus parent |
| `delta_max_dd` | child minus parent |

## 10. paper_sim Overview

P1 partial implementation:

```bash
PYTHONPATH=backend python backend/scripts/paper_sim_overview.py
```

The script connects to production DuckDB by default, selects all rows from `mart_paper_sim_kpi`, walks `parent_sim_run_id` chains, prints a markdown KPI table, prints a lineage tree, prints parent-child KPI deltas, and handles `NULL` `sim_config_hash` gracefully.

Overview columns:

```text
sim_run_id
ann_ret
sharpe
max_dd
win_rate
config_diff_vs_parent
parent_sim_run_id
sim_config_hash
```

## 11. P0 Scope

Implemented:

- `backend/services/paper_sim/sim_cache.py`
- `backend/services/paper_sim/ddl.py` migration
- `backend/scripts/run_paper_sim_v2.py` cache flags
- `backend/scripts/paper_sim_overview.py`
- `backend/tests/services/paper_sim/test_sim_cache.py`
- `docs/incremental_management_spec.md`
- `goal.md` update

Not implemented:

- panel monthly incremental builder;
- warm-start retrain artifacts;
- complete `mart_data_lineage` coverage;
- `show_param_impact_curve.py` target script.

## 12. Acceptance Criteria

P0:

- deterministic config hash;
- exact cache lookup;
- old `NULL` hashes remain readable;
- schema migration idempotent;
- repeated registration creates no duplicate KPI rows;
- `--skip-if-cached` prints `cache hit: <hash>`;
- unit tests pass;
- overview script runs on production DuckDB.

P1:

- param-impact CLI exists;
- paper_sim lineage rows are upserted;
- cache hit rate is reported.

P2:

- identical monthly source hashes skip rebuild;
- changed source hashes rebuild impacted months only;
- metadata records row count and source hashes;
- lineage row exists per built month.

P3:

- unchanged windows reuse verified artifacts;
- changed windows refit;
- predictions are reproducible from manifest;
- new model ID is content-addressed.

## 13. Rollout ETA and Risks

| Phase | Scope | ETA |
|---|---|---:|
| P0 | paper_sim cache, overview, tests, docs | same day |
| P1 | param impact CLI and lineage upsert expansion | 1 to 2 days |
| P2 | panel monthly incremental build | 2 to 4 days |
| P3 | retrain warm-start artifacts | 4 to 8 days |

Risks and mitigations:

- Layer 1 override collision: hash effective config when overrides are applied.
- Layer 2 model-name collision: require content-addressed `model_id`.
- Layer 3 lookback boundaries: include lookback windows in monthly source hashes.
- Layer 4 artifact leakage: require exact train/test window and checksum match.
- Lineage incompleteness: missing source hashes must be visible in overview and audits.
