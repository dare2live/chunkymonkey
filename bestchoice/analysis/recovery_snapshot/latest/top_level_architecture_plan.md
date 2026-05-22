# BestChoice Top-Level Architecture Plan

Date: 2026-05-20

## Objective

Keep BestChoice maintainable while the project grows from a single-page stock selector into a research, optimization, and production-merge system.

The design goal is minimal modularity: split by ownership and data lifecycle, not by over-engineered layers.

## Data Ownership Boundary

- `chunkymonkey/data/market.duckdb`: read-only upstream market data.
- `chunkymonkey/data/smartmoney.duckdb`: read-only upstream stock profile data.
- `bestchoice/analysis/*.csv`: local research artifacts and audit evidence.
- `bestchoice/analysis/research_cache.duckdb`: BestChoice-owned versioned optimization cache.
- future `bestchoice/analysis/parameter_knowledge.duckdb`: BestChoice-owned parameter effect and reuse knowledge base.
- `bestchoice/analysis/incremental_eval.duckdb`: BestChoice-owned incremental evaluation state.
- `bestchoice/analysis/drift_trigger.duckdb`: BestChoice-owned drift and re-optimization queue.
- `bestchoice/analysis/stock_formula_best.csv`: production parameter table. Do not overwrite until full coverage and aggregate audit pass.

Rule: BestChoice may read `chunkymonkey`; it must not write BestChoice research state into `chunkymonkey`.

## Minimal Modules

### 1. Source Adapters

Owner files:

- `settings.py`
- market/profile reads in `compute.py`

Responsibilities:

- Read market and profile data.
- Report data freshness and upstream availability.
- Never own research decisions.

Future extraction target:

- `bestchoice/data_sources.py`

### 2. Signal and Execution Core

Owner files:

- `formula_engine.py`
- `execution_model.py`

Responsibilities:

- Convert formulas into local daily signals.
- Apply VWAP, T+1, suspension, limit-up/down, delayed buy/sell, and sell-rule logic.
- Provide one execution model shared by backtest, current recommendation, chart, and optimization.

Non-goal:

- No UI formatting.
- No production merge decisions.

### 3. Research Pipeline

Owner files:

- `scripts/formula_parameter_search.py`
- `scripts/formula_local_optuna.py`
- `scripts/formula_local_optuna_batch.py`
- `scripts/formula_local_optuna_adoption.py`
- `scripts/formula_local_optuna_merge_plan.py`
- `scripts/research_cache_build.py`
- `scripts/incremental_eval_build.py`

Responsibilities:

- Run offline search.
- Preserve missing and failed rows as investigation leads.
- Produce adoption decisions.
- Produce dry-run merge plans.
- Build versioned research cache and incremental evaluation state.

Rule:

- Batch scripts may produce artifacts, but only merge/audit scripts decide whether something can move toward production.

### 4. Research State Store

Owner files:

- `analysis/research_cache.duckdb`
- `analysis/incremental_eval.duckdb`
- future `analysis/drift_trigger.duckdb`

Responsibilities:

- Make optimization results reusable.
- Avoid re-running unchanged history.
- Track clean/dirty/pending status.
- Track drift actions.

Current status:

- `research_cache.duckdb`: ready.
- `parameter_knowledge.duckdb`: planned; should summarize parameter effects, stable ranges, multi-objective recommendations, and warm-start priors from research cache.
- `incremental_eval.duckdb`: ready as state builder, not yet a live out-of-sample evaluator.
- `drift_trigger.duckdb`: ready as state builder, not yet automatic re-optimization.

### 4a. Parameter Knowledge Base

Owner files:

- future `scripts/parameter_knowledge_build.py`
- future `analysis/parameter_knowledge.duckdb`
- future API fields under `/api/parameter-search`

Responsibilities:

- Convert raw Optuna/cache rows into reusable parameter knowledge.
- Record how each parameter affects return, drawdown, win rate, holding period, signal count, execution feasibility, and stability.
- Produce warm-start parameter ranges for new formulas or re-optimization jobs.
- Produce multi-objective recommendations: max return, low drawdown, short holding period, balanced, industry default, and market-regime default.

Rule:

- Parameter recommendations are research guidance until full out-of-sample and drift checks pass; they must not silently overwrite production parameters.

### 5. API Aggregation

Owner files:

- `main.py`

Responsibilities:

- Serve UI data.
- Expose strategy status and research management state.
- Expose artifact fingerprints and source paths.
- Show blocking reasons when required state is missing.

Rule:

- API may aggregate; it should not hide missing state with default success values.

### 6. UI Console

Owner files:

- `index.html`

Responsibilities:

- Stock pool UI.
- Strategy research UI.
- Optuna management console.
- Show full initialization, Research Cache, Incremental Evaluator, Drift Trigger, and production merge states.

Rule:

- Missing API management fields must display as blocked/missing, not as locally inferred success.

## Pipeline

```text
Full initialization
  -> formula local Optuna batches
  -> adoption guardrails
  -> dry-run merge plan
  -> research_cache.duckdb
  -> parameter_knowledge.duckdb
  -> incremental_eval.duckdb
  -> drift_trigger.duckdb
  -> aggregate audit
  -> controlled production merge
```

Daily path after full initialization:

```text
Read latest market date
  -> compare against research_cache/incremental_eval state
  -> mark dirty stock/formula pairs
  -> run incremental evaluator for dirty rows
  -> create drift triggers
  -> run local Optuna only for reoptimize queue
  -> update cache and management console
```

## CodeGraph + Complexity Optimizer Workflow

The project already contains `.codegraph/codegraph.db`. The `codex-complexity-optimizer` skill is installed at `/Users/dp/.codex/skills/complexity-optimizer/SKILL.md`.

### Purpose

Use CodeGraph to choose the correct optimization target and dependency radius. Use complexity optimizer to produce a safe, testable optimization proposal. Use BestChoice verification gates before accepting any change.

### Practical Workflow

1. Select a hotspot from runtime evidence or batch pain:
   - slow local Optuna batch
   - repeated CSV scans
   - repeated DuckDB reads
   - heavy per-stock formula evaluation
   - large `index.html` render cost

2. Use CodeGraph to inspect dependencies:
   - callers and callees of the target function
   - files touched by the execution path
   - whether the target affects API, UI, research artifacts, or production merge

3. Run complexity optimizer in report-only mode:
   - expected output: file/line, current complexity, proposed change, expected complexity, risk, tests needed
   - no code changes from report-only mode

4. Convert the report into a scoped implementation task:
   - one module at a time
   - preserve artifact schemas
   - preserve missing-data semantics
   - no production merge side effects

5. Verify with project gates:
   - `python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py`
   - `python scripts/execution_model_smoke.py`
   - `python scripts/unified_data_smoke.py`
   - `python scripts/strategy_rebuild_audit.py`
   - relevant batch smoke or real batch command
   - `git diff --check`

### CodeGraph Target Priority

High-value starting points:

- `scripts/formula_local_optuna_batch.py`: repeated stock/formula orchestration and CSV rewrite cost.
- `scripts/formula_local_optuna.py`: per-stock optimization and formula evaluation cost.
- `formula_engine.py`: formula signal generation.
- `execution_model.py`: shared execution semantics.
- `main.py api_parameter_search()`: large artifact aggregation and status exposure.
- `index.html renderParameterSearchSummary()`: large single-file UI rendering and management console display.

### Acceptance Rule

A complexity optimization is accepted only if it reduces repeated work or improves observability without changing strategy semantics. Any optimization that changes signal, execution, adoption, or merge behavior must include an audit note and before/after evidence.

## Implementation Order

1. Finish full-market local Optuna dry-run coverage.
2. Keep rebuilding `research_cache.duckdb` after each accepted batch.
3. Add `parameter_knowledge.duckdb` after the current full-market dry-run stabilizes, using `research_cache.duckdb` as source.
4. Keep rebuilding `incremental_eval.duckdb` after research cache changes.
5. Implement `drift_trigger.duckdb` as a state table, not as automatic re-optimization.
6. Add run registry before adding UI task-start buttons.
7. Use CodeGraph + complexity optimizer on one hotspot at a time.
8. Only after full coverage and aggregate audit, design controlled production merge.

## Current Status

- Completed batches: 183 of 261.
- Batch coverage: 3676 of 5201 stocks.
- Research Cache: ready through batch 183, 38782 rows, data date `2026-05-19`.
- Parameter Knowledge Base: planned; design added, implementation not started.
- Incremental Evaluator state: ready through batch 183, 38782 rows, clean 38782, dirty 0.
- Drift Trigger state: ready through batch 183, 38782 rows, none 32234, watch 6548, reevaluate 0, reoptimize 0.
- Workflow Checkpoint: ready at `analysis/workflow_checkpoint.json` and `analysis/workflow_checkpoint.md`; next offset `3676`.
- Formula caches: ready, 5 of 5; `formula_activity_breakout` and `formula_volume_base_breakout` were rebuilt after the restart recovery check.
- Batch 123 recovery status: external `market.duckdb` lock from `backend/scripts/run_paper_sim_v2.py` was released; state-store refresh and checkpoint update were completed before batch 124.
- Batch 128 recovery status: external `market.duckdb` lock from `backend/scripts/run_paper_sim_v2.py --variant champion_minhold15_20260520_111606` blocked `incremental_eval_build.py`; the lock later released, Incremental/Drift/checkpoint were rebuilt, and batch 129 completed cleanly.
- Recovery checkpoint now detects external `market.duckdb` lock holders and reports `next_action=wait_external_duckdb_lock` with PID/process details. This makes crash recovery safer when long-running background simulations overlap with Optuna/cache maintenance.
- Latest Recovery Snapshot: ready at `analysis/recovery_snapshot/latest`; old snapshots are deleted before writing the new one, and large CSV/DuckDB artifacts are referenced by manifest rather than copied.
- CodeGraph + complexity optimizer: baseline collaboration audit is tracked in `analysis/complexity_codegraph_audit.md`; `/opt/homebrew/bin/codegraph` and `/opt/homebrew/bin/codex-complexity-optimizer` are installed. CodeGraph has been synced to 23 Python files / 691 nodes / 1553 edges and now covers the Python research pipeline. It still does not cover `index.html`, so frontend hotspot work must combine direct source inspection with scanner output.
- Production merge: blocked.
