# Codex Main Working Tree Organization - 2026-05-21

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


Purpose: stabilize the current dirty main working tree before continuing Phase4/Phase5 work.
This is an inventory and sequencing note only; no files were reverted.

Terminology note (2026-05-27): this document uses "working tree" in the Git
status sense for the main checkout at
`/Users/dp/Documents/M/stock/chunkymonkey`. The repository also has a Codex
detached worktree under `/Users/dp/.codex/worktrees/...`, but the active
architecture work in this note is on `main`.

## Snapshot

- HEAD: `6005aadb Resolve HS300 source gap # PIT-strict`
- Initial dirty state: `168` tracked files modified, `80` untracked files.
- After first hygiene pass: deleted 8 obsolete top-level docs and rewrote
  `gcp/README_GCP_BATCH.md` as a policy-only GCP entry note.
- CodeGraph: `codegraph sync .` completed after the 71-added-file warning.
- Complexity scanner: ran `complexity-optimizer`; broad scanner output mostly flags old `assets/js/app.js` nested-loop noise, so use it as leads, not as a direct patch queue.
- GCP: not used in this pass. Current repo policy is controlled-use: suitable
  for heavy compute/optimization/replay work after stating scope, cost/risk,
  artifacts, and stop/rollback; commands still require
  `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.

## Document Truth Hierarchy

Use these as current:
- `AGENTS.md`: Codex-facing operating policy, GCP controlled-use, CodeGraph + complexity workflow.
- `goal.md`: latest business ledger and delivery-readiness state.
- `SESSION_HANDOFF.md`: latest session handoff and recovery state.
- `analysis/workflow_checkpoint.md`: pipeline evidence and local readiness update.

Use these as background, not current state:
- `PROJECT_INDEX.md`: project map, but many status sections are older than Phase5/Phase4 work.
- `analysis/plan_v3_20260514_archived.md`: architecture/validation plan; useful for intent and BestChoice positioning.
- old top-level handoff/session/final docs were removed after their useful facts
  were folded into the current ledger or `PROJECT_INDEX.md`.

## Dirty Work Buckets

### A. Operating Rules / GCP Guard / Session State

Representative files:
- `AGENTS.md`
- `CLAUDE.md`
- `backend/config/gcp_policy.yaml`
- `scripts/lib/gcp_guard.sh`
- `gcp/*.sh`
- `configs/cron/*`, `configs/launchd/*`
- `SESSION_HANDOFF.md`, `goal.md`, `analysis/workflow_checkpoint.*`

Intent:
- Codify GCP controlled-use behavior.
- Preserve latest delivery and Phase4 state.
- Prevent future agents from accidental cloud spend or untracked artifact movement.

Risk:
- High operational risk if mixed with code changes without separate validation.

### B. Phase4 / Phase5 / MSAF Delivery Readiness

Representative files:
- `backend/scripts/run_phase4_gate_on_msaf.py`
- `backend/scripts/run_msaf_ensemble_paper_sim.py`
- `backend/scripts/audit_msaf_probe_frontier.py`
- `backend/scripts/audit_delivery_readiness.py`
- `backend/scripts/audit_msaf_pbo_diagnostics.py`
- `backend/scripts/retrain_lambdamart_v6.py`
- `backend/scripts/run_p0b_lambdamart_v6.py`
- `backend/services/ml_ranking/ddl.py`
- related tests under `backend/tests/test_*phase4*`, `test_*msaf*`, `test_*delivery*`, `test_retrain_lambdamart_v6.py`.

Current business state:
- No hard promote.
- Best proxy candidate: `lm735/sniper265/h10/k3/neutralcash20`.
- Delivery readiness remains `92.83% / NOT_READY`.
- Frontier verdict remains `PROXY_READY_NEEDS_TRUE_IS_OOS`.

Safety gap closed in this pass:
- Phase4 now rejects partial/smoke `fact_model_train_log` evidence unless
  train rows/window count/mode/coverage are credible.
- `retrain_lambdamart_v6.py --train-log-only` can compute true train/OOS
  evidence without deleting or replacing existing prediction rows.

### C. Workbench / Read-Model Modularization

Representative files:
- `backend/services/workbench_read.py` large deletion.
- Many new `backend/services/workbench_*_read.py` files.
- `backend/routers/workbench.py`
- `assets/js/workbench-view.js`, `assets/css/main.css`
- workbench contract/render tests.

Intent:
- Split `workbench_read.py` into smaller read-model modules.
- Add or expose delivery, KPI, champion, research, storage, signal, and temporal-synergy read paths.

Risk:
- Large blast radius. Must be validated as a slice, not interleaved with Phase4 gate work.

### D. Pricing / Market DB Modularization

Representative files:
- `backend/services/pricing_policy.py` split into:
  - `pricing_policy_model.py`
  - `pricing_schema.py`
  - `pricing_policy_records.py`
  - `pricing_policy_readiness.py`
  - `pricing_policy_evidence.py`
- `backend/services/market_db.py` split into:
  - `market_schema.py`
  - `market_read.py`

Intent:
- Continue #8 modularization and reduce god modules.

Risk:
- Medium to high; backward-compatible facade behavior must be tested.

### E. N+1 / Complexity / Batch Query Cleanup

Representative files:
- `backend/scripts/audit_n_plus_one.py`
- `backend/scripts/audit_n_plus_one_results.json`
- `backend/scripts/audit_n_plus_one_report.md`
- many scripts/services with query batching changes.
- `backend/tests/scripts/test_audit_n_plus_one.py`

Current state:
- Latest recorded N+1 audit: `19 findings / 10 HIGH / 9 LOW / baseline 19 OK`.

Risk:
- Broad file count but mostly localized performance patches. Needs targeted tests and `audit_n_plus_one.py`.

### F. Paper Sim Cache / Lineage / Data Artifacts

Representative files:
- `backend/services/paper_sim/sim_cache.py`
- `backend/scripts/backfill_paper_sim_cache_metadata.py`
- `backend/tests/test_backfill_paper_sim_cache_metadata.py`
- `data/phase5_exports/lgbm_phase5_gcp_20260520T010718/*.parquet`
- `analysis/strategy_result_registry_lineage_20260520.md`
- `analysis/verified_strategy_results_20260520.md`

Intent:
- Preserve prior validation artifacts.
- Backfill cache metadata without overwriting existing `sim_config_hash`.

Risk:
- Data artifacts may be too large or unsuitable for git; decide deliberately before commit.

## Suggested Sequencing

1. Keep Phase4/Phase5 delivery work as the active critical path.
2. Decide whether the long train-log replay should run locally or on GCP using
   the controlled-use policy.
3. Run targeted tests for Phase4/retrain/delivery/frontier/cache/N+1.
4. Keep workbench/pricing/market modularization as separate validation slices.
5. Commit or shelve by bucket only after tests; do not commit all dirty files together.
6. Use GCP for long replay/optimization only after scope, cost/risk, artifacts,
   and stop/rollback are stated; include `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.

## Hygiene Pass 1

Deleted obsolete top-level state/research files that were superseded by current
ledgers or contained stale GCP/process guidance:

- `HANDOFF.md`
- `SESSION_HANDOFF_20260517.md`
- `SESSION_FINAL_20260518.md`
- `TEST_PLAN_CODEX_R24.md`
- `RESEARCH_QUANT_TOOLS_R27.md`
- `RESEARCH_COMMUNITY_STRATEGIES_R28.md`
- `RESEARCH_AWESOME_QUANT_R29.md`
- `ORCHESTRATION.md`

Kept intentionally:

- `analysis/plan_v3_20260514_archived.md`: still useful for ML Ranking architecture and deferred
  BestChoice positioning.
- `analysis/data_integrity_audit_20260517.md`: still cited as evidence by
  `analysis/chunkymonkey_architecture_audit_20260517.md`.

Also updated:

- `PROJECT_INDEX.md`: old handoff/orchestration entries now state the files
  were removed after facts were folded into current docs.
- `docs/backtester_mcp_integration_20260517.md`: historical evidence references
  now point to `PROJECT_INDEX.md` instead of deleted `HANDOFF.md`.
- `gcp/README_GCP_BATCH.md`: now controlled-use policy-only; no direct VM/GCS
  runbook commands.

## Hygiene Pass 2

Aligned stale GCP policy wording after the 2026-05-21 user clarification that
GCP is available under controlled-use:

- `AGENTS.md`, `CLAUDE.md`, `gcp/README_GCP_BATCH.md`,
  `backend/config/gcp_policy.yaml`, and `docs/agent_parallel_execution_policy.md`
  now describe GCP as controlled-use rather than disabled.
- Recovery and guard scripts (`scripts/session_snapshot.sh`, `scripts/cm.sh`,
  `scripts/session_status.sh`, `scripts/lib/gcp_guard.sh`,
  `scripts/monitor_phase5_gcp_retrain_probe.sh`, `gcp/vm_start.sh`) now use the
  controlled-use wording while preserving `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.
- `audit_delivery_readiness.py` now reports `gcp_controlled_idle` and
  `CONTROLLED_USE_IDLE`; the old `status.json step=gcp_disabled` string is
  treated as a legacy status name only.
- `analysis/session_archive_20260520_2248.md` is explicitly marked as a
  historical archive predating the controlled-use policy update.

## Hygiene Pass 3 - 2026-05-27 Main Dirty Plan

Current branch: `main`.

Current dirty state after the architecture-reform handoff work:

| Status | Count | Meaning |
|---|---:|---|
| `M` | 53 | tracked files modified; mix of architecture changes, governance annotations, docs, and state ledgers |
| `D` | 3 | root-level obsolete docs moved/archived; keep only after reference checks |
| `??` | 35 | new archives/docs/tests/router modules; must be reviewed by bucket before staging |

| Bucket | Scope | Decision |
|---|---|---|
| A | `dim_active_a_stock` governance and `check_universe_filter.py` changes | Keep as P0 architecture evidence; validate with `PYTHONPATH=backend python backend/scripts/check_universe_filter.py --all`. |
| B | `scripts/safe_commit.sh` Rule 10 gate | Keep as P0 review-control evidence; validate with `bash -n scripts/safe_commit.sh`. |
| C | `backend/routers/updater.py` split plus new `backend/routers/updater_*.py` modules | Keep as P1 modularization work; latest slice is fifty-eight moves: `updater.py` 5136 -> 723 LOC, `updater_execution.py` 823 LOC for execution bookkeeping and group/full/single/smart execution loop helpers, plus `updater_launcher.py` 278 LOC for `UpdaterExecutionDeps`, background task failure/cleanup launcher helper, smart/full/single/group background launcher deps, and group route request scheduling; `updater_status.py` is 593 LOC and owns smart-update plan/calendar preflight assembly, `/update/smart` plan connection lifecycle, update status/smart-plan response connection lifecycle, and run context/noop/finish/heartbeat helpers; `updater_reset.py` is 161 LOC and owns reset table cleanup plus reset route payloads plus reset connection lifecycle. Existing split modules remain `updater_infra.py` 258 LOC, `updater_calendar.py` 157 LOC, `updater_steps.py` 232 LOC, `updater_connectivity.py` 156 LOC, `updater_sync.py` 443 LOC, `updater_calc.py` 196 LOC, `updater_runtime.py` 34 LOC, `updater_institution.py` 533 LOC, `updater_trends.py` 303 LOC, `updater_profiles.py` 455 LOC, `updater_market_data.py` 765 LOC, `updater_lifeboat.py` 88 LOC, `updater_plan.py` 130 LOC, `updater_audit.py` 53 LOC, and `updater_completeness.py` 108 LOC. The thirty-eighth moved update status payload building into `build_update_status_payload`; the thirty-ninth moved audit snapshot refresh helper into `updater_audit.py`; the fortieth moved audit route payload into `build_update_audit_payload`; the forty-first moved group pipeline execution into `run_group_steps`; the forty-second moved full DAG execution into `run_all_steps`; the forty-third moved single-step chain execution into `run_single_steps`; the forty-fourth moved smart plan execution into `run_smart_steps`; the forty-fifth moved full/smart/single/group background-task failure and cleanup flow into `run_background_update_task`; the forty-sixth moved smart-update plan/calendar preflight assembly into `prepare_smart_update_plan`; the forty-seventh moved smart background launcher parameter injection into `UpdaterExecutionDeps` + `run_smart_update_background`; the forty-eighth moved launcher plumbing into `updater_launcher.py` and `backend/tests/test_updater_launcher.py`; the forty-ninth moved full/group/single launcher parameter injection into `run_full_update_background` / `run_group_update_background` / `run_single_update_background`; the fiftieth moved reset-derived/reset-industry response payloads into `build_reset_derived_payload` / `build_reset_industry_payload`; the fifty-first moved `sync_industry` body/gap queue/progress JSON into `updater_institution.py::_step_sync_industry_with_hooks` and added `backend/tests/test_updater_institution.py`; the fifty-second moved `/update/status` connection lifecycle and step_status catalog sync into `updater_status.py::build_update_status_response` and added status catalog-sync/close coverage; the fifty-third moved `/update/smart-plan` connection lifecycle and plan budget response into `updater_status.py::build_smart_plan_response` and added critical-only budget/close coverage; the fifty-fourth moved reset-derived/reset-industry connection lifecycle into `updater_reset.py::build_reset_derived_response` / `build_reset_industry_response` and added close coverage; the fifty-fifth moved pre-run step_status priming connection lifecycle into `updater_steps.py::prime_run_step_status_for_steps` and added close coverage; the fifty-sixth moved `/update/smart` plan connection lifecycle into `updater_status.py::build_smart_update_plan` and added close coverage; the fifty-seventh moved run context/noop/finish/heartbeat helpers into `updater_status.py` and added state-helper coverage; the fifty-eighth moved group route request scheduling into `updater_launcher.py::launch_group_update_request` and added launcher request coverage. Validate each slice with CodeGraph, complexity scan, targeted tests, and review-gate. |
| D | Current-state docs: `goal.md`, `docs/implementation_plan.md`, `SESSION_HANDOFF.md`, `analysis/handoff_20260527.md`, `analysis/codex_bootstrap_20260527.md` | Keep, but only with current facts. Older slice labels and counts must be removed once a newer slice lands. |
| E | Root document archive cleanup: `DATA_INTEGRITY_AUDIT_20260517.md`, `PLAN_V3.md`, `市场感知开发计划.md` | Keep cleanup. Content-level diffs confirm the historical bodies were moved intact to `analysis/data_integrity_audit_20260517.md`, `analysis/plan_v3_20260514_archived.md`, and `analysis/market_perception_development_plan_20260520.md`; the root `PLAN_V3.md` redirect was removed so it cannot look like an execution entry point. |
| F | Auto/runtime snapshots: `SESSION_HANDOFF.md`, `analysis/workflow_checkpoint.*` | Do not mix with code-only review conclusions. Treat as state ledger updates and re-check before any handoff. |
| G | Other historical dirty files | Do not revert blindly. Inspect references and diff before deciding whether they belong to the current architecture batch or an older bucket. |

Clean-up order:

1. Finish the current `updater.py` split slice review before starting another slice.
2. Keep each architecture slice independently green: `py_compile`, targeted pytest, `check_universe_filter`, `git diff --check`, `codegraph sync .`, and backend complexity scan.
3. Keep document cleanup separate from code behavior changes. Root-level stale plans should move to `analysis/`; temporary redirects are allowed only when live references require them. Current execution must point to `goal.md` and `docs/implementation_plan.md`.
4. Do not stage everything together. Stage, review, and eventually commit only by bucket.
5. Do not revert user/peer changes to make the working tree look clean. A clean-looking tree is not the goal; a categorized, verified tree is.

## Hygiene Pass 4 - 2026-05-27 Ignored Local Trash Cleanup

Executed a narrow cleanup of ignored local filesystem artifacts only. No tracked
files, untracked source files, analysis archives, docs, data evidence, or Git
state were reverted.

Removed:

- `.claude/.DS_Store`
- `backend/.DS_Store`
- `Users/.DS_Store`
- `Users/dp/.DS_Store`
- `assets/.DS_Store`
- empty `data/market.duckdb.tmp/`

Verification:

- `find . -maxdepth 3 \( -name '*.tmp' -o -name '*.bak' -o -name '.DS_Store' -o -name '*~' -o -name '*.orig' -o -name '*.rej' \) -print` returned empty.
- `git status --short` counts did not change during that pass, because it only
  removed ignored local trash.

## Hygiene Pass 5 - 2026-05-27 Root Plan Entry Cleanup

Removed the root-level `PLAN_V3.md` redirect after verifying its historical body
matches `analysis/plan_v3_20260514_archived.md` exactly and current references
point to the archived path. Root execution entry points are now limited to
`goal.md`, `SESSION_HANDOFF.md`, and the documented operating policies.

Verification:

- `git show HEAD:PLAN_V3.md | diff -u - analysis/plan_v3_20260514_archived.md`
  returned no diff.
- `rg -n "PLAN_V3\\.md|DATA_INTEGRITY_AUDIT_20260517|市场感知开发计划\\.md" .`
  returned only historical/archive notes after the cleanup.

## Hygiene Pass 6 - 2026-05-27 Dirty Cleanup Plan Refresh

Current `git status --short` after the market-data, lifeboat, plan, execution,
data_completeness, connectivity, step-status bookkeeping, audit/status,
group/full/single/smart execution-loop helper, background launcher,
smart-update plan/preflight helper, smart background launcher dependency,
launcher submodule split, full/group/single launcher dependency split, and
reset route payload and connection lifecycle split
slice is
`53 M + 3 D + 35 ??`. The untracked updater split files in these slices are
intentional bucket C architecture work, not disposable scratch:
`backend/routers/updater_infra.py`, `backend/routers/updater_calendar.py`,
`backend/routers/updater_steps.py`, `backend/routers/updater_connectivity.py`,
`backend/routers/updater_sync.py`, `backend/routers/updater_calc.py`,
`backend/routers/updater_runtime.py`, `backend/routers/updater_audit.py`,
`backend/routers/updater_status.py`,
`backend/routers/updater_reset.py`, `backend/routers/updater_institution.py`,
`backend/routers/updater_trends.py`, `backend/routers/updater_profiles.py`,
`backend/routers/updater_market_data.py`, `backend/routers/updater_lifeboat.py`,
`backend/routers/updater_plan.py`, `backend/routers/updater_execution.py`,
`backend/routers/updater_launcher.py`,
`backend/routers/updater_completeness.py`, `backend/tests/test_updater_audit.py`,
`backend/tests/test_updater_plan.py`, `backend/tests/test_updater_execution.py`,
`backend/tests/test_updater_launcher.py`, `backend/tests/test_updater_status.py`,
and `backend/tests/test_updater_completeness.py`.

Cleanup plan:

1. Keep bucket C together: `updater.py` plus `backend/routers/updater_*.py`
   modules, then verify with CodeGraph, backend complexity scan, targeted pytest,
   universe checker, and review-gate before any staging. Smart/full/single/group
   launcher helpers now live in `updater_launcher.py`; do not add route-level
   launcher plumbing back into `updater_execution.py`.
2. Keep bucket A/B together only if the commit scope is governance: universe
   checker/evidence changes plus `scripts/safe_commit.sh` Rule 10 gate.
3. Keep bucket D/F as state ledgers; update current facts, but do not mix
   auto/runtime checkpoint noise into code-only review conclusions.
4. Keep bucket E archive moves only after `rg` confirms no current consumer.
5. Do not delete, revert, or stage bucket G historical dirty files without a
   file-level owner and a fresh diff review.

Next cleanup must operate on the real dirty buckets above, not on broad
filesystem deletion:

| Order | Bucket | Cleanup action | Gate before staging/deletion |
|---:|---|---|---|
| 1 | C | Finish/review updater split as one architecture slice; no partial `git add .` | `py_compile`, targeted pytest, CodeGraph, complexity, universe checker, review-gate |
| 2 | D/F | Keep only current state ledgers; update counts and next actions after each slice | `check_project_index_sync.py`, `git diff --check` |
| 3 | E | Keep root doc archive moves only where redirected or migrated facts are verified | `rg` old names, compare archived content when needed |
| 4 | A/B | Stage governance changes separately from updater code if committing | universe checker CLEAN, `bash -n scripts/safe_commit.sh` |
| 5 | G | Decide historical dirty files file-by-file; either assign owner, migrate facts, or leave untouched | fresh diff review; never revert blind |

1. Keep root-document archive cleanup as bucket E, after reference checks.
2. Keep updater split files as bucket C, after CodeGraph/complexity/tests.
3. Keep universe governance and Rule 10 as buckets A/B.
4. Do not delete analysis archives unless their useful facts are migrated and
   `rg` shows no current consumer.
