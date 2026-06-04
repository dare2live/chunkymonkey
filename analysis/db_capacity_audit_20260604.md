# DuckDB Capacity Audit - 2026-06-04

Scope: read-only investigation of `data/smartmoney.duckdb` after the 2026-06-04
after-close refresh. Agents were instructed not to edit files, write DuckDB,
VACUUM, delete, stage, commit, push, or read `CLAUDE.md`.

## Summary

- `data/smartmoney.duckdb` is about `33.6 GiB` (`34G` on disk).
- No evidence was found for a `no2` table/path or a session snapshot loop writing
  repeated rows into DuckDB.
- No large compressed backup copy was found under `data/` in this read-only
  pass. The visible Parquet/export artifacts are small relative to the DuckDB:
  `data/phase5_exports` is about `101M`, `data/reports/v7_retrain` is about
  `21M`, and `data/reports/msaf_ensemble_run_pre_v7_backup.json` is `8K`.
- The main storage pressure is broad-table and cache retention:
  - multiple full feature-label panel versions coexist;
  - rank/cache tables overlap key sets with candidate panels;
  - index and storage metadata overhead is material;
  - small control tables show row-group bloat from repeated rewrite patterns.
- Current `storage_payload` WARN is not recursive JSON. It is total historical
  bytes in small `reason_codes_json` values:
  - `fact_technical_trigger.reason_codes_json`: about `304 MB` total;
  - `mart_macd_state_history.reason_codes_json`: about `278 MB` total;
  - recursive/path hits are `0`.

## Evidence

File-level checks:

```text
smartmoney.duckdb  34G
alpha158.duckdb   1.8G
market.duckdb     1.2G
```

DuckDB read-only `PRAGMA database_size`:

```text
database_size=33.6 GiB
used_blocks=133550
free_blocks=4174
wal_size=0 bytes
```

Largest estimated table blocks from `pragma_storage_info` and row counts:

```text
mart_p0a_feature_label_panel_v4          4,282,620 rows  ~2.32 GiB
mart_p0a_feature_label_panel_v3          4,282,620 rows  ~2.22 GiB
mart_p0a_feature_label_panel             3,695,375 rows  ~1.79 GiB
mart_p0a_feature_label_panel_unified_v1  2,715,667 rows  ~1.55 GiB
raw_tdx_f10_holder_research                 45,430 rows  ~1.54 GiB
mart_stock_regime_full                   2,576,125 rows  ~1.53 GiB
mart_p0a_feature_label_panel_v5          2,715,667 rows  ~1.45 GiB
fact_feature_panel                       4,161,982 rows  ~1.23 GiB
fact_feature_panel_tdx_keep_challenger   4,052,975 rows  ~822.8 MiB
fact_tdx_gpcw_auto_feature_quarterly    16,371,042 rows  ~736.5 MiB
```

Overlap checks:

```text
v3 vs v4 keys:                         4,282,620 / 4,282,620 overlap
v5 vs unified_v1 keys:                 2,715,667 / 2,715,667 overlap
candidate vs tdx_keep_challenger keys: 4,052,975 / 4,052,975 overlap
candidate vs rank matrix caches keys:  4,052,975 / 4,052,975 overlap
```

Snapshot writer checks:

- `data/reports/session_snapshot.json` is a fixed overwritten file, not a DuckDB
  writer.
- `mart_data_health` is append-by-run with retention, currently about `23,530`
  rows and `102` snapshot runs, not a capacity driver.
- `raw_profit_forecast_snapshot_daily`, `mart_forecast_upside_live`,
  `mart_stock_fund_flow_rank_snapshot_daily`, and `fact_stock_attention_snapshot`
  have same-day delete/insert or primary-key based idempotency paths.
- `mart_stock_fund_flow_rank_snapshot_daily` is about `5,188` rows, so it is not
  the source of the 34G file.

Backup/export checks:

- No `*.duckdb.bak`, `*.duckdb.gz`, `*.duckdb.zst`, or large compressed DuckDB
  backup was found in the top local data artifacts.
- The largest external export-like artifacts are small:

```text
data/phase5_exports                         101M
data/reports/v7_retrain                      21M
data/phase5_predictions_*.duckdb             57M
data/reports/msaf_ensemble_run_pre_v7_backup.json 8K
```

- These exports may still need retention ownership, but they do not explain the
  `smartmoney.duckdb` size.

Most suspicious redundant groups:

```text
mart_p0a_feature_label_panel_v4          4,282,620 rows  ~2.32 GiB
mart_p0a_feature_label_panel_v3          4,282,620 rows  ~2.22 GiB
mart_p0a_feature_label_panel             3,695,375 rows  ~1.79 GiB
mart_p0a_feature_label_panel_unified_v1  2,715,667 rows  ~1.55 GiB
mart_p0a_feature_label_panel_v5          2,715,667 rows  ~1.45 GiB
```

`v3` and `v4` have fully overlapping `(signal_date, stock_code)` keys; `v5`
and `unified_v1` also fully overlap. This is the concrete "multi-version
snapshot/table" class.

Second suspicious group:

```text
fact_feature_panel                       4,161,982 rows  ~1.23 GiB
fact_feature_panel_candidate             4,052,975 rows  ~370 MiB
fact_feature_panel_tdx_keep_challenger   4,052,975 rows  ~823 MiB
mart_feature_rank_matrix_cache_*         4,052,975 rows  ~389 MiB + ~613 MiB
```

`candidate`, `tdx_keep_challenger`, and both rank cache tables cover the same
`4,052,975` date/stock keys. Owners to inspect before cleanup include
`backend/scripts/build_tdx_keep_challenger_panel.py`,
`backend/scripts/build_feature_rank_matrix_duck.py`,
`backend/scripts/build_feature_drift_mitigation_panel.py`, and
`backend/scripts/build_hybrid_feature_panel.py`.

Protected raw / lineage evidence:

```text
raw_tdx_f10_holder_research  45,430 rows  ~1.54 GiB
```

This table is large, but it is not classified as cache redundancy in this audit.
It stores F10 raw text and raw hashes used by holder/F10 replay and lineage.
Owners include `backend/scripts/ingest_holders_tdxhub.py` and
`backend/services/tdx_f10_extra_client.py`. Any retention change must preserve
replayability, PIT lineage, and the ability to explain canonical holder facts;
do not put it in the same cleanup bucket as feature-panel versions or rank
caches.

## Current Interpretation

This is a capacity governance and retention problem, not a single recursive
payload bug. The highest-value follow-up is a dedicated retention/index slice:

1. classify feature-label panel versions into current, reproducibility evidence,
   cache, and obsolete;
2. add a machine-readable retention plan for full panel/cache variants;
3. review large-table indexes against actual query paths before dropping any;
4. handle `formula_engine` reason JSON as a history-volume policy issue, either
   by cap recalibration or a summarized retention path;
5. keep raw/lineage tables such as `raw_tdx_f10_holder_research` out of generic
   cache cleanup unless a replay-preserving retention design exists;
6. only after backup and owner sign-off, run cleanup/compact work in a serialized
   DuckDB write window.

Do not delete tables or run VACUUM/compaction from this audit alone.
