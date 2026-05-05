# ChunkyMonkey Goal Implementation Audit

Updated: 2026-05-05

Objective: implement `../chunkymonkey_goal_plan.md` and move ChunkyMonkey toward a daily usable, performance-controlled, explainable, auditable production workflow.

## Evidence Snapshot

- Latest commits:
  - current change set: GPCW affected-slice rebuild, partial auto-feature rebuild, akshare startup governance, DuckDB lock wait observability
  - `99998a5f Add model feature lineage artifacts`
  - `57214aa9 Separate holder raw ingest and add topk gates`
  - `d3f3bb97 Track incremental gpcw file manifest`
  - `03ac923f Cache walkforward matrices and guard lifecycle updates`
  - `aab876e8 Bulk insert training predictions`
  - `6f796000 Load training panel as NumPy arrays`
  - `c24373e2 Retry DuckDB lock acquisition`
  - `3cec8fbc Add replayable holder parsing and kline increments`
  - `6b332f28 Productionize model pipeline observability`
- Tests: `python3 -m pytest -q` -> `397 passed`
- Data health: `python3 backend/scripts/data_health_snapshot.py --dry-run` -> `147 green / 0 yellow / 0 red`
- Stale reference / denylist: `python3 backend/scripts/audit_stale_references.py` -> pandas runtime `0`, sqlite runtime `0`, no stale references
- PIT audits:
  - `python3 backend/scripts/validate_tdx_feature_pit.py` -> passed, `features=20`, `violation_rows=0`
  - `python3 backend/scripts/validate_tdx_gpcw_auto_pit.py` -> passed, `features=233`, `violation_rows=0`

## Checklist

| Requirement | Current evidence | Status |
|---|---|---|
| Phase 0 run manifest | `mart_pipeline_run_manifest` exists, 15 rows; training, TopK, health, watermarks, cleanup, walk-forward scripts write manifest | Done |
| Phase 1.1 source watermarks | `mart_data_source_watermark` exists, 11 domains; data-health API/UI reads it | Done |
| Phase 1.2 F10 raw replay | `ingest_holders_tdxhub.py --parse-raw-only --replace-facts`; tests cover raw replay, raw-key filtering, and canonical replacement | Done |
| Phase 1.2 fetch raw only | `fetch_raw_records()` and `--fetch-raw-only` write only `raw_tdx_f10_holder_research`; default `run()` now fetches raw then replays new raw hashes; updater message exposes raw/parse/fallback counts | Done |
| Phase 1.3 kline incremental | `build_price_kline_tdxhub.py --skip-existing` filters by per-code `MAX(date)` | Done |
| Phase 1.4 gpcw file manifest | `sync_gpcw_files()` uses `mart_tdx_gpcw_file_manifest` hash/status rows, migrates older manifest schemas, skips unchanged files, and records empty/failed states | Done |
| Phase 1.4 raw wide affected-part rebuild | Changed gpcw files delete and rebuild only impacted `raw_gpcw_detail`, `raw_tdx_gpcw_wide`, and `fact_tdx_gpcw_auto_feature_quarterly` report-date slices in one transaction; updater triggers field profile + partial auto-feature rebuild for affected dates | Done |
| Phase 1.5 akshare dependency governance | `start.command` no longer runs `pip install --upgrade akshare`; startup only prints local version/import status; manual maintenance lives in `scripts/upgrade_akshare.sh` and `ops/README.md` | Done |
| Phase 2 audit coverage | Data health green and PIT scripts pass for TDX F10/GPCW paths on 2026-05-05 final validation | Done |
| Phase 2 per-feature lineage/source_tier | `build_model_feature_lineage.py` writes `mart_model_feature_lineage` from `feature_cols_json`; champion run produced 54 feature rows, missing=0, grouped by source_tier | Done |
| Phase 2 source drift/fallback gate | `evaluate_tdx_keep_promotion_gate.py` now blocks on holder fallback ratio, gpcw manifest source/status, and source watermark fallback/failures | Done |
| Phase 3.1 train list[dict] removal | `train_multidim_model.py` main uses DuckDB `fetchnumpy()` + `PanelData`; full 3,910,880 row load verified: 8.8s load, 4.1s split/matrix | Done |
| Phase 3.2 Optuna matrix reuse | Train objective uses prebuilt matrices and LightGBM datasets; timings written to manifest | Done |
| Phase 3.3 walk-forward fold cache | `run_multidim_walkforward.py` main loads one `PanelData` and slices per fold; default prediction mode remains metrics-only | Done |
| Phase 3.4 prediction bulk write | `mart_multidim_prediction` and `mart_model_walkforward_prediction` support NumPy temp view bulk insert | Done |
| Phase 3.5 DuckDB lock governance | Connection retry implemented; updater write tasks are serialized; GPCW slice replacement is transactional; `DuckConn` records `duckdb_lock_wait_s` / `connect_mutex_wait_s` into pipeline manifest perf summaries | Done |
| Phase 4 daily/research separation | `cron_daily.py` excludes full training/WF; champion TopK only by default, shadow explicit | Done |
| Phase 5 holding-period / feature search | `evaluate_holding_topk.py` writes `mart_model_holding_topk_eval`; real champion run produced 20 rows for 5/10/20/60d × top20/50/100/200/500; existing retention artifacts hold keep/watch/drop feature decisions | Done |
| Phase 6 promotion gate | Current challengers remain shadow/rejected; lifecycle update is explicit and quality-gated; source-lineage/fallback checks added; UI shows gate blockers and champion-only default | Done |
| Phase 6 legacy delete | Cleanup script executed previously; old pkl/model rows removed; champion TopK still works | Done |
| Phase 7 frontend source/model visibility | Data-health page shows source watermarks, pipeline manifest, lineage, drift, and champion/challenger model cards; model monitor calls `/api/rec/model-comparison` and `/api/rec/tdx-feature-validation` for gate, shadow, PIT, source, keep/watch/drop evidence | Done |
| Phase 8 final verification | Unit tests `397 passed`, denylist/stale audit clean, data health `147 green / 0 yellow / 0 red`, PIT audits passed | Done |

## Performance Bottlenecks And Next Actions

1. DuckDB writer contention can still appear when multiple heavy CLI validations are launched at the same time. Current mitigation is connection retry plus manifest lock-wait metrics; if contention appears in scheduled production, add a cross-process file lock around write-heavy CLI entrypoints.
2. GPCW profile still scans `raw_tdx_gpcw_wide` globally when a new file changes. It is acceptable at current scale, but can be reduced by materializing per-file field stats and merging them into the profile table.
3. TopK/holding-period search is now batch-auditable, but repeated broad grids should remain a research job. Daily production should keep champion TopK only and run challenger/shadow grids on demand.
