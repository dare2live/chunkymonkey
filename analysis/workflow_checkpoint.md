# Workflow Checkpoint

Business-level pipeline tracker. Session-level state remains in SESSION_HANDOFF.md.

- generated_at: `2026-05-21T07:06:15Z`
- model_id: `lgbm_phase5_gcp_20260520T010718`
- current_step: `stability_retrain_running_after_fix`
- next_step: `monitor_stability_retrain_to_first_complete_then_import_artifacts`
- resume_command: `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 TAIL_LINES=80 bash scripts/gcp_stability_status.sh`

## Current Local Readiness Update

- updated_at: `2026-05-21T07:06:15Z`
- main_project_delivery: `NOT_READY`
- delivery_readiness: `92.83%`
- frontier_verdict: `NO_PROMOTABLE_PROBE`
- preserved_probe_artifacts:
  - `data/reports/msaf_ensemble_gcp_v6_lm735_sniper265_h10_k3_probe_20260521.json`
  - `data/reports/phase4_gate_msaf_gcp_v6_lm735_sniper265_h10_k3_probe_20260521.json`
  - `data/reports/msaf_ensemble_gcp_v6_lm735_sniper265_h10_k3_neutralcash20_probe_20260521.json`
  - `data/reports/phase4_gate_msaf_gcp_v6_lm735_sniper265_h10_k3_neutralcash20_probe_20260521.json`
- best_proxy_candidate: none after true train-log replay. Former `lm735/sniper265/h10/k3/neutralcash20` still has n_obs=68, Sharpe=2.0994, max_dd=-19.9865%, PBO=0.1192 PASS, but true train-test IS/OOS fails with relative_drop=81.36%.
- hard_promote_status: no hard promote; true train-log evidence has now rejected the former best proxy candidate.
- phase4_train_log_work: future retrain has a local code path to emit `fact_model_train_log`; Phase4 now accepts true RankIC only when the train-log has positive train rows/window count, at least 2 windows by default, `walk_forward_mode='expanding_monthly'`, and no partial-window coverage versus existing predictions. Rejected rows stay split-half proxy and record `train_log_rejected`.
- train_log_replay_entrypoint: `retrain_lambdamart_v6.py --train-log-only --resume-train-log` computes true train/OOS evidence without replacing existing prediction rows. It now persists verified per-window checkpoints in `fact_model_train_log_window` and skips only windows whose `model_id/replay_id/params_hash/window_key`, boundaries, positive row counts, and metrics JSON match the current replay. `fact_model_train_log` is written only after all expected windows are verified.
- train_log_replay_result: GCP replay completed exit 0; local artifacts `data/reports/train_log_replay/lgbm_phase5_gcp_20260520T010718_train_log_20260521T024117Z.{json,log}` and GCS `gs://chunkymonkey-data-0517/phase5/train_log_replay/`. Verified `expected_windows=34`, `verified_windows=34`, `window_metrics_len=34`, `window_integrity_bad_count=0`, test_range 2023-07-03..2026-04-14, OOS rows sum 3,396,073, `oos_rank_ic_avg=0.0222157486`, `oos_rank_ic_ir=1.53523385`.
- true_train_log_gate_result: imported train-log artifact into local `fact_model_train_log`; reran `lm735/sniper265/h10/k3/neutralcash20` Phase4 gate and got verdict `block` because true IS/OOS `IS=0.1192`, `OOS=0.0222`, relative_drop=81.36% > 30%, despite PBO/DSR/conservative PASS.
- stability_audit_result: new report `data/reports/lambdamart_train_log_stability_lgbm_phase5_gcp_20260520T010718_lm735_sniper265_h10_k3_neutralcash20_20260521.json`; OOS RankIC positive_rate=67.65%, negative windows=11/34, std=0.08438, bull regime mean OOS RankIC=-0.01195, strategy-return corr=0.089. Same-model cash/position/weight sweeps cannot clear the model-level true IS/OOS blocker.
- stability_objective_entrypoint: `run_p0b_lambdamart_v6.py` and `retrain_lambdamart_v6.py` support opt-in `--window-rank-ic-std-penalty-weight` and `--window-rank-ic-negative-rate-penalty-weight`; defaults stay 0 for backward compatibility.
- stability_gcp_wrapper: `scripts/gcp_stability_retrain.sh` dry-run verified. Default search starts from old checkpoint and uses penalty weights `0.50/0.20`; writes current pid/log/artifact/gcs pointers and uploads summary/best/train-log/log. Deprecated `scripts/run_phase5_extended_retrain.sh` and `scripts/run_phase5_auto_chain.sh` now block before launch.
- stability_retrain_abort_1219: stopped first launch `lgbm_phase5_stability_20260521T035555Z` after discovering 4 Optuna trials inherited `OMP_NUM_THREADS=32` (outer x inner oversubscription risk). It had `0 COMPLETE` trials and no best checkpoint. Aborted evidence is preserved locally/GCS: `data/reports/stability_retrain/lgbm_phase5_stability_20260521T035555Z_stability_retrain_20260521T035616Z.{json,log}`, summary has `retrain_exit=137`, `prediction_rows=0`, `train_log_found=false`, `best_artifact=null`.
- stability_retrain_threadcap_fix: `run_p0b_lambdamart_v6.py` now caps inner LightGBM threads so `OPTUNA_N_JOBS * OMP_NUM_THREADS` cannot silently exceed CPU count; `scripts/gcp_stability_retrain.sh` defaults to `OPTUNA_N_JOBS_REMOTE=8`, `OMP_NUM_THREADS_REMOTE=4`, and refuses `optuna_jobs * omp > REMOTE_MAX_THREADS`. Validation: local targeted tests `21 passed`, py_compile pass, bash -n pass, wrapper 8x4 dry-run pass, wrapper 8x8 expected reject, CodeGraph sync 77 files, complexity scan only old `assets/js/app.js` hotspots. Remote scoped backup before sync: `data/reports/code_sync_backup/20260521T042540Z_threadcap`; remote smoke: py_compile pass, thread-cap assertion pass, wrapper dry-run pass.
- stability_retrain_launch_result: relaunched controlled-use run `MODEL_ID=lgbm_phase5_stability_20260521T042830Z`, parent `pid=1744`, child `pid=1748`, log `data/reports/stability_retrain/lgbm_phase5_stability_20260521T042830Z_stability_retrain_20260521T042822Z.log`, summary `data/reports/stability_retrain/lgbm_phase5_stability_20260521T042830Z_stability_retrain_20260521T042822Z.json`, GCS `gs://chunkymonkey-data-0517/phase5/stability_retrain`. Cost at 12:28 CST OK: projected `$5.456` / 54.5%, remaining `$6.4794` / ~17.23 spot h.
- stability_retrain_monitor_1231: running with corrected thread cap. Feature panel loaded 4,240,940 rows, after filter 3,933,543 rows, warm-start queued, log confirms `optuna parallelism: outer_jobs=8 inner_lightgbm_threads=4`; Optuna DB has 8 RUNNING trials and 0 COMPLETE, summary/GCS completion artifacts pending.
- stability_retrain_monitor_1338: still running and CPU-bound, not idle. Child `pid=1748` elapsed `01:10:11`, CPU ~3028%, RSS ~26.4G, 70 threads, load avg ~31; Optuna DB size 135,168 bytes with 8 RUNNING / 0 COMPLETE. No reusable checkpoint exists until the first COMPLETE trial lands. Cost at 13:37 CST OK: projected `$5.6017` / 56.0%, remaining `$6.3854` / ~16.98 spot h.
- stability_retrain_abort_1343: stopped `lgbm_phase5_stability_20260521T042830Z` before any COMPLETE trial after code review found the LambdaMART branch did not feed per-window RankIC into the requested stability penalty. Summary/log pulled back locally and present in GCS: `data/reports/stability_retrain/lgbm_phase5_stability_20260521T042830Z_stability_retrain_20260521T042822Z.{json,log}`; summary has `retrain_exit=137`, `prediction_rows=0`, `train_log_found=false`, `best_artifact=null`. No reusable completed result was lost. Cost at 13:50 CST OK and VM TERMINATED: projected `$5.6017` / 56.0%, remaining `$6.3854` / ~16.98 spot h.
- stability_objective_fix_1350: `run_p0b_lambdamart_v6.py` now collects finite window `rank_ic` for both LambdaMART and regressor branches before applying `_rank_ic_stability_adjustment`. Added test `test_lambdamart_optuna_collects_window_rank_ic_for_stability_penalty`; validation: `test_lambdamart_v6.py + test_retrain_lambdamart_v6.py` = 22 passed, py_compile pass, bash -n GCP wrappers pass, CodeGraph sync 77 changed files, complexity scan still only historical `assets/js/app.js` hotspots.
- stability_retrain_relaunch_1358: remote scoped backup `data/reports/code_sync_backup/20260521T055600Z_stability_rankic_fix`; remote smoke passed with `window_rank_ic_mean=0.04`, `window_rank_ic_negative_rate=0.5`, `rank_ic_stability_penalty=0.3349`; wrapper dry-run pass. Active run `MODEL_ID=lgbm_phase5_stability_20260521T055800Z`, parent `pid=1597`, child `pid=1601`, log `data/reports/stability_retrain/lgbm_phase5_stability_20260521T055800Z_stability_retrain_20260521T055750Z.log`, summary `data/reports/stability_retrain/lgbm_phase5_stability_20260521T055800Z_stability_retrain_20260521T055750Z.json`, GCS `gs://chunkymonkey-data-0517/phase5/stability_retrain`. 13:58 monitor: child entered feature panel load. Cost OK: projected `$6.0388` / 60.3%, remaining `$6.1034` / ~16.23 spot h.
- stability_retrain_monitor_1403: active run reached formal Optuna optimization with correct thread cap. Log shows panel loaded 4,240,940 rows, after filter 3,933,543 rows, RankPanel built, warm-start queued, `optuna parallelism: outer_jobs=8 inner_lightgbm_threads=4`, checkpoint enabled. Optuna DB exists with 8 RUNNING / 0 COMPLETE; no reusable checkpoint or summary artifact yet.
- stability_retrain_monitor_1408: active child remains CPU-bound, not idle. `pid=1601` elapsed `10:23`, CPU ~2388%, RSS ~12.0GB, 70 threads. Still no COMPLETE checkpoint; continue monitoring rather than export/import. Cost at 14:06 CST OK: projected `$6.1845` / 61.8%, remaining `$6.0094` / ~15.98 spot h.
- local_codegraph_complexity_1408: while waiting on GCP, ran CodeGraph + complexity-optimizer. `codegraph sync .` completed with `Synced 75 changed files` and `Updated 946 nodes`; follow-up status still reports 75 Added pending because the worktree remains dirty/untracked-heavy. Complexity scanner still only reports historical `assets/js/app.js` hotspots; no new stability/import/export-path blocker.
- stability_retrain_monitor_1415: still no reusable result. DB state 8 RUNNING / 0 COMPLETE, no `.best.json`, no summary JSON. Child `pid=1601` elapsed `17:11`, CPU ~2674%, RSS ~15.5GB, 70 threads. Cost at 14:11 CST OK: projected `$6.3302` / 63.3%, remaining `$5.9154` / ~15.73 spot h.
- delivery_audit_gcp_status_fix_1415: fixed stale GCP status reporting in `audit_delivery_readiness.py`. Active `gcp_cost_summary.json` with `vm_status=RUNNING` now overrides legacy `phase5_chain/status.json step=gcp_disabled`; controlled-idle is only used when no active cost evidence exists. Validation: `test_audit_delivery_readiness.py` 15 passed, py_compile pass, delivery audit expected exit 1 but GCP criterion now reports `source=gcp_cost_summary`, `vm_status=RUNNING`, `alert_level=OK`, `pct_of_budget=63.3`; frontier remains `NO_PROMOTABLE_PROBE`; CodeGraph sync 77 changed files / 1008 nodes; complexity scan only old `assets/js/app.js`.
- stability_retrain_monitor_1420: still no reusable result. DB state 8 RUNNING / 0 COMPLETE, no `.best.json`, no summary JSON. Child `pid=1601` elapsed `22:41`, CPU ~2782%, RSS ~17.0GB, 70 threads. Cost at 14:19 CST OK: projected `$6.4759` / 64.7%, remaining `$5.8214` / ~15.48 spot h.
- delivery_audit_daily_active_cost_test_1420: added `test_daily_automation_uses_active_cost_report_over_legacy_idle` so daily automation criterion also prefers active `gcp_cost_summary.json` over legacy controlled-idle. Validation: `test_audit_delivery_readiness.py` 16 passed, py_compile pass, delivery audit expected exit 1 avg=92.83 / NOT_READY with `gcp_cost_report_active=true` in daily criterion; CodeGraph sync 76 changed files / 966 nodes; complexity scan only old `assets/js/app.js`.
- stability_retrain_monitor_1431: standard read-only wrapper reports parent `pid=1597`, child `pid=1601`, child elapsed `33:15`, CPU ~2888%, RSS ~19.7GB, 70 threads. Optuna DB state remains 8 RUNNING / 0 COMPLETE, latest trials have no `completed_at`, `.best.json` and final summary are absent. Cost at 14:31 CST OK: projected `$6.7673` / 67.6%, remaining `$5.6334` / ~14.98 spot h. No export/import allowed before COMPLETE plus best checkpoint/final summary.
- doc_hygiene_1431: cleaned stale active-doc wording in `goal.md` and `CLAUDE.md` that still implied GCP disabled/VM terminated. Current active policy is controlled-use with latch, and current VM state is RUNNING for `lgbm_phase5_stability_20260521T055800Z`.
- stability_retrain_monitor_1439: standard read-only wrapper reports child `pid=1601` elapsed `41:22`, CPU ~2935%, RSS ~21.3GB, 70 threads. Optuna DB mtime advanced to `2026-05-21T06:38:25Z`, but state remains 8 RUNNING / 0 COMPLETE; `.best.json` and final summary are still absent. Cost at 14:39 CST OK: projected `$6.9130` / 69.1%, remaining `$5.5394` / ~14.73 spot h. No export/import performed.
- local_frontend_complexity_1440: optimized `assets/js/app.js` institution-management type filter from repeated `r.data.filter(...)` per type to one `typeCounts` pass. Validation: `node --check assets/js/app.js` pass; frontend contract/render smoke 3 passed; complexity scan no longer reports the prior 2884 repeated-scan hotspot, while other legacy app.js nested-loop/sort leads remain.
- stability_retrain_monitor_1444: standard read-only wrapper reports parent `pid=1597`, child `pid=1601`, child elapsed `45:57`, CPU ~2953%, RSS ~22.4GB, 70 threads. Optuna DB mtime advanced to `2026-05-21T06:43:36Z`, but state remains 8 RUNNING / 0 COMPLETE; `.best.json` and final summary are still absent. Cost at 14:44 CST OK: projected `$7.0587` / 70.5%, remaining `$5.4454` / ~14.48 spot h. No export/import performed.
- stability_retrain_monitor_1448: standard read-only wrapper reports parent `pid=1597`, child `pid=1601`, child elapsed `50:41`, CPU ~2968%, RSS ~23.3GB, 70 threads. Optuna DB mtime advanced to `2026-05-21T06:48:03Z`, but state remains 8 RUNNING / 0 COMPLETE; `.best.json` and final summary are still absent. Cost at 14:48 CST OK: projected `$7.2044` / 72.0%, remaining `$5.3514` / ~14.23 spot h. No export/import performed.
- local_market_schema_split_1451: advanced #8 modularity by moving market core schema DDL out of `market_db.py` into `market_schema.ensure_market_schema()`. `market_db.py` is now 392 lines, `market_schema.py` 208 lines, `market_read.py` 169 lines; old `market_db.init_market_db()` and re-exported constants/import paths remain compatible. Validation: py_compile pass; market/calendar/xdxr/audit-financial targeted 22 passed; delivery audit expected exit 1 at 92.83% / NOT_READY; CodeGraph query+sync pass (`Synced 76 changed files`, `Updated 972 nodes`); complexity scan still only reports legacy `assets/js/app.js` leads.
- stability_retrain_monitor_1454: standard read-only wrapper reports child `pid=1601` elapsed `56:14`, CPU ~2983%, RSS ~24.2GB, 70 threads. Optuna DB mtime advanced to `2026-05-21T06:53:16Z`, but state remains 8 RUNNING / 0 COMPLETE; `.best.json` and final summary are still absent. No export/import performed.
- stability_retrain_monitor_1457: standard read-only wrapper reports parent `pid=1597`, child `pid=1601`, child elapsed `59:26`, CPU ~2990%, RSS ~25.0GB, 70 threads. Optuna DB mtime advanced to `2026-05-21T06:56:58Z`, but state remains 8 RUNNING / 0 COMPLETE; `.best.json` and final summary are still absent. Cost at 14:57 CST OK: projected `$7.3501` / 73.5%, remaining `$5.2574` / ~13.98 spot h. No export/import performed.
- stability_retrain_monitor_1502: standard read-only wrapper reports parent `pid=1597`, child `pid=1601`, child elapsed `01:04:56`, CPU ~3001%, RSS ~26.0GB, 70 threads. Optuna DB mtime advanced to `2026-05-21T07:02:30`, but state remains 8 RUNNING / 0 COMPLETE; `.best.json` and final summary are still absent. No export/import performed.
- local_frontend_filter_meta_1505: advanced #8 frontend complexity by changing stock list search/filter to build `_stockFilterMetaByCode` once during `renderStockList()` instead of rebuilding a `stockMap` from `stockListState.getData()` on each `applyStockFilters()` call. Validation: `node --check assets/js/app.js` pass; frontend contract/render smoke 3 passed; filtered complexity scan no longer sees the old `stockMap` / `matchGateFilter` / `matchIndustryFilter` path; CodeGraph sync pass (`Synced 76 changed files`, `Updated 949 nodes`); `git diff --check -- assets/js/app.js` pass.
- post_run_lightweight_import_path: ready for a future active stability model. `scripts/gcp_export_model_predictions.sh` exports only the selected `MODEL_ID` prediction rows to parquet/GCS and refuses partial export while same-model retrain is still running; `import_phase5_remote_predictions.py --remote-parquet-dir` imports prediction parquet locally and can mirror LambdaMART rows to OOS; `import_model_train_log_artifact.py` imports train-log JSON. Do not use aborted `042830Z` for post-run import because it produced zero prediction rows.
- gcp_spot_preempt_lessons: 2026-05-21 Spot preemptions should be handled by restartable unit checkpoints, not full reruns. For train-log replay, restart the same wrapper and reuse completed windows after verification.
- current_top5_strategy_evidence: former best delivery candidate `lm735/sniper265/h10/k3/neutralcash20` is now blocked by true train-log IS/OOS. Highest Sharpe completed variants are not automatically promotable: `sniperfloor` variants are sample-short, and `rank_decay`/scorefloor strict variants lack a passing Phase4 gate or fail stability checks.
- n_plus_one_audit: `19 findings / 10 HIGH / 9 LOW / baseline 19 OK`.
- gcp_scope: controlled use; GCP is available for heavy computation, parameter optimization, long replays, and main+BestChoice integrated search when it materially improves throughput. State wall time, cost/risk, input snapshot, output paths, artifact preservation, and stop/rollback plan before starting. Commands still require `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.
- bestchoice_status: discussion doc reviewed at `/Users/dp/Documents/M/stock/bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md`; no import/merge yet. New plan is recorded in `goal.md` as `BestChoice 条件化持有/退出策略计划 (2026-05-21 14:58)`: first do local POC using BestChoice formula candidates plus main-project PIT context, then only run GCP if portfolio-level Sharpe/return/drawdown or champion-complementarity thresholds justify expanded search.
- doc_hygiene_status: current GCP policy text and machine-readable audit fields were synchronized to controlled-use across active docs, recovery scripts, GCP guards, cron notes, and tests; old no-cloud wording remains only as clearly marked historical context or legacy status step names.

## Steps

| Step | Name | Status | Evidence Found |
|---:|---|---|---|
| 1 | verify local prediction artifacts | done | json:data/reports/phase5_chain/status.json step=gcp_disabled (legacy step name; controlled-use idle)<br>db:mart_p0b_lambdamart_v6_predictions model_id rows=3396073 |
| 2 | pre-sim audit | done | json:data/reports/pit_audit_lgbm_phase5_gcp_20260520T010718.json (model_id mismatch)<br>json:data/reports/pit_audit.json fresh PASS |
| 3 | paper_sim execution | done | db:mart_paper_sim_lambdamart_v6_kpi_compare model_id rows=1<br>db:mart_paper_sim_nav sim_run_id contains model_id rows=614 |
| 4 | KPI ingestion | done | db:mart_paper_sim_kpi joined to model compare rows=1 |
| 5 | KPI comparison | done | db:mart_paper_sim_lambdamart_v6_kpi_compare model_id rows=1 |
| 6 | Pareto verdict gatekeeper | done | json:data/reports/phase4_gate_lgbm_phase5_gcp_20260520T010718.json<br>json:data/reports/phase4_gate_result.json matching model (model_id mismatch) |
| 7 | decision promote/reject/retrain | done | json:data/reports/decision_lgbm_phase5_gcp_20260520T010718.json |
| 8 | resumable train-log replay | done | json:data/reports/train_log_replay/lgbm_phase5_gcp_20260520T010718_train_log_20260521T024117Z.json expected_windows=34 verified_windows=34<br>log:data/reports/train_log_replay/lgbm_phase5_gcp_20260520T010718_train_log_20260521T024117Z.log exit 0 |
| 9 | true train-log Phase4 gate | done | json:data/reports/phase4_gate_msaf_gcp_v6_lm735_sniper265_h10_k3_neutralcash20_probe_true_trainlog_20260521.json verdict=block IS/OOS relative_drop=81.36% |
| 10 | OOS RankIC stability diagnostic | done | json:data/reports/lambdamart_train_log_stability_lgbm_phase5_gcp_20260520T010718_lm735_sniper265_h10_k3_neutralcash20_20260521.json positive_rate=67.65% negative_windows=11/34 |
| 11 | stability-aware retrain entrypoint | done | script:scripts/gcp_stability_retrain.sh dry-run pass; deprecated scripts `run_phase5_extended_retrain.sh` / `run_phase5_auto_chain.sh` expected block |
| 12 | stability-aware retrain run | running | aborted old model:`lgbm_phase5_stability_20260521T035555Z` retrain_exit=137 rows=0; aborted model:`lgbm_phase5_stability_20260521T042830Z` retrain_exit=137 rows=0 after LambdaMART stability objective bug; active model:`lgbm_phase5_stability_20260521T055800Z`; parent pid=1597 child pid=1601; remote smoke proved `rank_ic_stability_penalty=0.3349`; log:data/reports/stability_retrain/lgbm_phase5_stability_20260521T055800Z_stability_retrain_20260521T055750Z.log; 15:02 child CPU-bound, DB 8 RUNNING / 0 COMPLETE, no best/summary yet |
| 13 | post-run lightweight artifact import path | ready | active model:`lgbm_phase5_stability_20260521T055800Z`; script:scripts/gcp_export_model_predictions.sh; script:backend/scripts/import_phase5_remote_predictions.py --remote-parquet-dir; script:backend/scripts/import_model_train_log_artifact.py; validation: importer tests 8 passed, py_compile pass, bash -n pass, export dry-run pass |

## Blockers

- none

## Expected Evidence

### Step 1: verify local prediction artifacts
- file:data/smartmoney_post_lgbm_phase5_gcp_20260520T010718.duckdb.bak
- file:data/smartmoney_post_lgbm_phase5_gcp_20260520T010718.duckdb
- json:data/reports/phase5_chain/status.json step=pull_done
- file:data/reports/phase5_chain/monitor_done_lgbm_phase5_gcp_20260520T010718.sentinel (weak)
- db:mart_p0b_oos_predictions model_id
- db:mart_p0b_lambdamart_v6_predictions model_id

### Step 2: pre-sim audit
- json:analysis/pre_sim_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pre_sim_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pit_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pit_audit.json fresh PASS
- db:mart_champion_candidate_evaluation PIT pass

### Step 3: paper_sim execution
- json:data/reports/msaf_ensemble_phase5_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/paper_sim_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/paper_sim_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_lambdamart_v6_kpi_compare model_id
- db:mart_paper_sim_nav sim_run_id contains model_id

### Step 4: KPI ingestion
- json:data/reports/msaf_ensemble_phase5_lgbm_phase5_gcp_20260520T010718.json kpi
- json:data/reports/kpi_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/kpi_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_kpi joined to model compare

### Step 5: KPI comparison
- json:data/reports/kpi_compare_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/kpi_compare_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_lambdamart_v6_kpi_compare model_id

### Step 6: Pareto verdict gatekeeper
- json:data/reports/phase4_gate_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/pareto_verdict_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pareto_verdict_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/phase4_gate_result.json matching model
- db:mart_tdx_keep_promotion_gate challenger_model_id

### Step 7: decision promote/reject/retrain
- json:analysis/decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/promote_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/ensemble_decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/retrain_decision_lgbm_phase5_gcp_20260520T010718.json
- db:mart_champion_model model_id
- db:mart_champion_candidate_evaluation final status
- db:mart_tdx_keep_promotion_gate final decision
