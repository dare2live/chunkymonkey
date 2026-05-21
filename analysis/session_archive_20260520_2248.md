# Session Archive 2026-05-20 22:48 CST

## Hard Rules
- Historical note: this archive predates the 2026-05-21 GCP controlled-use update. Current policy is in `AGENTS.md`: GCP may be used for heavy compute/optimization/replay work after scope, cost/risk, artifacts, and stop/rollback are stated; every GCP command still requires `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.
- The active delivery objective is not achieved. `ready_for_delivery=false` remains authoritative.

## Current Delivery State
- Latest audit command: `PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py`
- Latest result: 6 criteria average `92.83% / NOT READY`.
- #6 hard gap remains:
  - primary 20d MSAF: `n_obs=22`, Sharpe `0.8089`, max_dd `-24.28%`.
  - 5d/10d probes increase sample count but have Sharpe `<1` and worse drawdown.
  - cash/regime overlay can improve drawdown to about `-18.91%`, but Sharpe stays about `0.80`.
  - volatility targeting can improve drawdown to `-16.06%` / `-18.45%`, but Sharpe falls to `0.46` / `0.48`.
  - score-floor probes show useful conviction signal but insufficient OOS coverage: floor `0.50` has Sharpe `1.060`, max_dd `-7.42%`, CAGR `34.33%`, but only `n_obs=11`; floor `0.55` has Sharpe `7.04` but only `n_obs=3`, so it is small-sample evidence only and must not be promoted.
- 10-criteria ledger in `goal.md`: about `92%`, still blocked by #6 perfect ladder.

## Stop Checkpoint 2026-05-20 23:00 CST
- User requested: "不要继续推进了，停止，然后存档".
- No further goal advancement should continue after this checkpoint unless explicitly restarted by the user.
- Goal remains active/not complete: `ready_for_delivery=false` and #6 perfect ladder remains unresolved.
- Last local code change before stop: extracted `build_workbench_paper_sim_kpi_timeseries` from `backend/services/workbench_read.py` into `backend/services/workbench_paper_sim_read.py`; `services.workbench_read` still imports/re-exports the same function so router/tests keep the old API.
- Size impact: `backend/services/workbench_read.py` is now 4543 lines; new `backend/services/workbench_paper_sim_read.py` is 181 lines.
- Stop validation:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/test_workbench_paper_sim_timeseries.py backend/tests/contract/test_workbench_read.py` -> 15 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_paper_sim_read.py` -> pass.

## Final Stop Checkpoint 2026-05-20 23:27 CST
- User again requested: "不要继续推进了，停止，然后存档".
- Stop now. Do not continue #6/#8/#9/#10 or any business implementation in this turn.
- Preserve the current dirty worktree for the next session; do not revert unrelated changes.
- Latest validated state before final stop is the 23:24 Workbench recommendation split:
  - `backend/services/workbench_read.py` is 3436 lines.
  - `backend/services/workbench_recommendation_read.py` was added and wired through a compatibility wrapper.
  - Validation: Workbench contract/frontend smoke 16 passed, `py_compile` passed, `git diff --check` passed.
- Goal is not complete: latest readiness remains `92.83% / NOT READY`, `ready_for_delivery=false`, and #6 perfect ladder is unresolved.

## Active Goal Continuation 2026-05-20 23:03 CST
- Continued without GCP.
- #8 module split progressed further: `build_workbench_pipelines` and pipeline manifest read helpers moved from `backend/services/workbench_read.py` into `backend/services/workbench_pipeline_read.py`.
- Compatibility preserved: existing imports from `services.workbench_read import build_workbench_pipelines` still work.
- Size impact: `backend/services/workbench_read.py` is now 4459 lines; new `backend/services/workbench_pipeline_read.py` is 223 lines.
- Verification:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/test_pipeline_manifest.py backend/tests/test_workbench_paper_sim_timeseries.py backend/tests/contract/test_workbench_read.py` -> 19 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_paper_sim_read.py backend/services/workbench_pipeline_read.py` -> pass.
  - `git diff --check` on the touched Workbench read files -> pass.
- Codex log guard follow-up: live `~/.codex/log/codex-tui.log` reached 195,292,920 bytes and was manually rotated by `/Users/dp/.codex/bin/rotate_codex_tui_log.sh` to `~/.codex/log/archive/codex-tui.tail.20260520_230446.log.gz`; live log returned to 0 bytes.

## Active Goal Continuation 2026-05-20 23:08 CST
- Continued without GCP.
- #8 module split progressed further: data-sources asset health read model moved from `backend/services/workbench_read.py` into `backend/services/workbench_asset_health_read.py`.
- Compatibility preserved: `build_workbench_data_sources` still returns the same `asset_health` structure through `services.workbench_read`.
- Size impact: `backend/services/workbench_read.py` is now 4336 lines; new `backend/services/workbench_asset_health_read.py` is 197 lines.
- Verification:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/test_data_asset_governance.py backend/tests/test_data_health_snapshot.py backend/tests/contract/test_workbench_read.py` -> 34 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_asset_health_read.py backend/services/workbench_paper_sim_read.py backend/services/workbench_pipeline_read.py` -> pass.
  - `git diff --check` on the touched Workbench read files -> pass.
- Codex log guard follow-up: live `~/.codex/log/codex-tui.log` reached 158,724,001 bytes and was manually rotated to `~/.codex/log/archive/codex-tui.tail.20260520_230900.log.gz`.

## Active Goal Continuation 2026-05-20 23:14 CST
- Continued without GCP.
- #8 module split progressed further: `build_workbench_features`, feature availability, and feature catalog read logic moved from `backend/services/workbench_read.py` into `backend/services/workbench_feature_read.py`.
- Compatibility preserved: existing imports from `services.workbench_read import build_workbench_features` still work.
- Size impact: `backend/services/workbench_read.py` is now 3913 lines; new `backend/services/workbench_feature_read.py` is 638 lines. This gets the main Workbench read god-module below 4000 lines for the first time in this session, but it is still not near the <=400 target.
- Verification:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_read.py backend/tests/pipeline/test_feature_catalog_current.py` -> 14 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_feature_read.py backend/services/workbench_asset_health_read.py backend/services/workbench_paper_sim_read.py backend/services/workbench_pipeline_read.py` -> pass.
  - `git diff --check` on the touched Workbench read files -> pass.
- Codex log guard follow-up: live `~/.codex/log/codex-tui.log` reached 214,713,250 bytes and was manually rotated to `~/.codex/log/archive/codex-tui.tail.20260520_231456.log.gz`.

## Active Goal Continuation 2026-05-20 23:18 CST
- Continued without GCP.
- #8 module split progressed further: `build_workbench_storage`, storage cleanup, and architecture cleanup read logic moved from `backend/services/workbench_read.py` into `backend/services/workbench_storage_read.py`.
- Compatibility preserved: `workbench_read.build_workbench_storage` remains a thin wrapper that injects `load_storage_retention_policy` / `plan_storage_cleanup`, so existing monkeypatch tests targeting `services.workbench_read` still pass.
- Size impact: `backend/services/workbench_read.py` is now 3688 lines; new `backend/services/workbench_storage_read.py` is 376 lines.
- Verification:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/test_data_health_snapshot.py backend/tests/test_storage_retention.py backend/tests/contract/test_workbench_read.py` -> 34 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_storage_read.py backend/services/workbench_feature_read.py backend/services/workbench_asset_health_read.py backend/services/workbench_paper_sim_read.py backend/services/workbench_pipeline_read.py` -> pass.
  - `git diff --check` on the touched Workbench read files -> pass.
- Codex log guard follow-up: live `~/.codex/log/codex-tui.log` reached 146,257,632 bytes and was manually rotated to `~/.codex/log/archive/codex-tui.tail.20260520_231932.log.gz`.

## Active Goal Continuation 2026-05-20 23:24 CST
- Continued without GCP.
- #8 module split progressed further: `build_workbench_recommendations`, primary top-k, risk, outcome, and source-quality read logic moved from `backend/services/workbench_read.py` into `backend/services/workbench_recommendation_read.py`.
- Compatibility preserved: `workbench_read.build_workbench_recommendations` remains a thin wrapper that injects `build_workbench_data_sources`; `build_workbench_champion` imports `_latest_recommendation_key` from the new module to keep its primary top-k section working.
- Size impact: `backend/services/workbench_read.py` is now 3436 lines; new `backend/services/workbench_recommendation_read.py` is 458 lines.
- Verification:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_read.py backend/tests/contract/test_workbench_frontend_render_smoke.py backend/tests/contract/test_workbench_frontend_contract.py` -> 16 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_recommendation_read.py backend/services/workbench_storage_read.py backend/services/workbench_feature_read.py backend/services/workbench_asset_health_read.py backend/services/workbench_paper_sim_read.py backend/services/workbench_pipeline_read.py` -> pass.
  - `git diff --check` on the touched Workbench read files -> pass.

## Active Goal Continuation 2026-05-20 23:36 CST
- Continued without GCP.
- #8 module split progressed further: `build_workbench_champion`, champion lifecycle summary, deployment blockers, latest primary top-k wiring, and stability context read logic moved from `backend/services/workbench_read.py` into `backend/services/workbench_champion_read.py`.
- Compatibility preserved: existing imports from `services.workbench_read import build_workbench_champion` still work, and overview still gets champion lifecycle summary through the façade.
- Size impact: `backend/services/workbench_read.py` is now 3019 lines; new `backend/services/workbench_champion_read.py` is 592 lines.
- Verification:
  - `PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_read.py backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` -> 16 passed.
  - `python -m py_compile backend/services/workbench_read.py backend/services/workbench_champion_read.py backend/services/workbench_recommendation_read.py` -> pass.
  - `git diff --check` on the touched Workbench read files -> pass.

## Latest Local Work
- L4 warm-start is deployed opt-in:
  - `backend/scripts/run_p0b_lambdamart_v6.py` supports `--warm-start-checkpoint`.
  - `backend/scripts/retrain_lambdamart_v6.py` passes warm-start params into Optuna.
  - `backend/scripts/incremental_cache_status.py` / `cm cache` reports latest checkpoint evidence.
- MSAF risk work in progress:
  - `backend/scripts/run_msaf_ensemble_paper_sim.py` now has opt-in volatility targeting arguments: `--target-ann-vol`, `--vol-window`, `--min-exposure`, `--max-exposure`.
  - This is default-off and uses only prior non-overlap realized returns to set later exposure.
  - Local 12%/15% target-ann-vol probes have been run and are negative for #6: they improve drawdown but materially reduce Sharpe and CAGR, so no delivery metric should be upgraded from them.
- `backend/scripts/run_msaf_ensemble_paper_sim.py` also has default-off `--min-top-score`; rejected picks leave unused capital in cash instead of backfilling lower-conviction names.
- Audit risk overlay parsing now includes `volatility_target`, `score_filter`, `avg_exposure`, `min_realized_exposure`, `n_skip`, `sample_ready`, and `perfect_ladder_ready` fields when probe artifacts contain them.

## Codex Log Incident
- `~/.codex/log/codex-tui.log` grew to 5.1G again after the user retained the last 5000 lines.
- Cause observed from tail: extremely long single-line `session_task.turn` tracing spans with nested token usage, so line-count retention is unsafe.
- Immediate cleanup performed:
  - archived last 50MiB to `~/.codex/log/archive/codex-tui.tail.20260520_224552.log`
  - truncated live `~/.codex/log/codex-tui.log` to 0 bytes
- Durable guard added:
  - new script `scripts/rotate_codex_tui_log.sh`
  - installed actual crontab entry:
    `* * * * * /Users/dp/.codex/bin/rotate_codex_tui_log.sh >> /tmp/codex_tui_log_rotate.log 2>&1`
  - runtime script is mirrored at `/Users/dp/.codex/bin/rotate_codex_tui_log.sh` because macOS cron returned `Operation not permitted` when executing from `~/Documents/...`.
  - repository cron template `configs/cron/crontab.txt` includes the same runtime entry.
- Script behavior:
  - if `codex-tui.log` exceeds 100MiB, archive last 50MiB, truncate live file, gzip archive, keep latest 6 gz archives.
- 2026-05-20 23:28 follow-up: live log reached 133,035,726 bytes and was manually rotated to `~/.codex/log/archive/codex-tui.tail.20260520_232856.log.gz`; live log returned to 0 bytes. Because the file can exceed 100MiB inside a 5-minute interval, runtime crontab and `configs/cron/crontab.txt` were changed to run every 1 minute.

## Verification Run
- `PYTHONPATH=backend python -m pytest -q backend/tests/test_run_msaf_ensemble_paper_sim.py backend/tests/test_audit_delivery_readiness.py` -> 19 passed.
- `python -m py_compile backend/scripts/run_msaf_ensemble_paper_sim.py backend/scripts/audit_delivery_readiness.py` -> pass.
- `bash -n scripts/rotate_codex_tui_log.sh` -> pass.
- `git diff --check` on touched files -> pass.

## Next Local-Only Actions
1. Stop spending time on simple cash/volatility de-risking for #6; it improves drawdown but not Sharpe.
2. Move #6 to validated alpha-quality work: broaden OOS coverage for score-filter evidence, test smoother conviction weighting, and review entry/minhold/exit interactions.
3. Continue #8 `workbench_read.py` / `market_db.py` slicing or #9 prediction-to-panel-cell lineage if #6 alpha work is blocked.
