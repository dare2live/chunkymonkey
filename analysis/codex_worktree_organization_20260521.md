# Codex Worktree Organization - 2026-05-21

Purpose: stabilize the current dirty workspace before continuing Phase4/Phase5 work.
This is an inventory and sequencing note only; no files were reverted.

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
- `PLAN_V3.md`: architecture/validation plan; useful for intent and BestChoice positioning.
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

- `PLAN_V3.md`: still useful for ML Ranking architecture and deferred
  BestChoice positioning.
- `DATA_INTEGRITY_AUDIT_20260517.md`: still cited as evidence by
  `docs/chunkymonkey_architecture_audit_20260517.md`.

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
