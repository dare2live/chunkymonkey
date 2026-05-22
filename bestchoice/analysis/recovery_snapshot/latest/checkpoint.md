# Workflow Checkpoint

- generated_at: `2026-05-20T18:02:47.362483+00:00`
- phase: `full_market_local_optuna_dry_run`
- cwd: `/Users/dp/Documents/M/stock/bestchoice`

## Progress

- covered_stocks: `5201`
- completed_batches: `260`
- next_offset: `5201`
- batch_rows: `26005`
- candidates: `1146`
- rejected: `24859`
- replacements: `1146`
- missing_without_reason: `0`
- consistency_ready: `True`
- formula_caches_status: `checked`
- formula_caches_ready: `True`
- formula_caches_ready_count: `5/5`
- market_db_lock_holders: `0`
- active_workers: `0`

## Next Action

- kind: `operational_ready`
- reason: `full coverage, aggregate audit, and final operational gates passed`

```bash
cat analysis/operational_delivery_readiness.md
```

## State Stores

- research_cache: ready=`True` rows=`45908`
- incremental_eval: ready=`True` rows=`45908`
- drift_trigger: ready=`True` rows=`45908`

## Tooling

- codegraph_db: ready=`True` path=`.codegraph/codegraph.db`
- complexity_optimizer_skill: ready=`True` path=`/Users/dp/.codex/skills/complexity-optimizer/SKILL.md`
- latest_snapshot: `analysis/recovery_snapshot/latest`

## Resume Commands

```bash
python scripts/formula_local_optuna_batch.py --offset 5201 --max-stocks 20 --trials 24 --max-signals-per-stock 120 --output analysis/formula_local_optuna_batch.csv --report analysis/formula_local_optuna_batch.md --resume
python scripts/formula_local_optuna_adoption.py --input analysis/formula_local_optuna_batch.csv --output analysis/formula_local_optuna_batch_adoption.csv --report analysis/formula_local_optuna_batch_adoption.md
python scripts/formula_local_optuna_merge_plan.py --input analysis/formula_local_optuna_batch_adoption.csv --plan-output analysis/formula_local_optuna_batch_merge_plan.csv --replacement-output analysis/formula_local_optuna_batch_stock_best_replacements.csv --report analysis/formula_local_optuna_batch_merge_plan.md
python scripts/research_cache_build.py
python scripts/incremental_eval_build.py
python scripts/drift_trigger_build.py
python scripts/workflow_checkpoint.py
```

## Verification Commands

```bash
python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py
python scripts/execution_model_smoke.py
python scripts/unified_data_smoke.py
python scripts/strategy_rebuild_audit.py
git diff --check
```

## Market DB Locks

- path: `/Users/dp/Documents/M/stock/chunkymonkey/data/market.duckdb`
- holder_count: `0`

```text
```

## Active BestChoice Workers

```text
```

## Recovery Rule

After terminal crash or reboot, run `bash scripts/bc_resume.sh`; if next_action is wait_active_worker, do not start another batch. If consistency.ready is true and no worker is active, continue from next_action.command; otherwise run next_action.commands in order.

## Tell Codex After A Crash

请从中断处继续。先运行 `python scripts/workflow_checkpoint.py --brief`，检查 consistency.ready 和 next_action，然后按照 next_action/命令继续；遵守 goal.md 和 agent.md，不写入生产 stock_formula_best.csv。
