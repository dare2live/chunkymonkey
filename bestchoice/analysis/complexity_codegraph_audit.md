# Complexity Optimizer and CodeGraph Audit

Date: 2026-05-20

## Scope

This is a report-only baseline audit. No production strategy logic, Optuna artifacts, API behavior, UI behavior, or production merge files were changed as part of this audit.

Analyzed paths:

- `compute.py`
- `execution_model.py`
- `formula_engine.py`
- `main.py`
- `scripts/formula_local_optuna.py`
- `scripts/formula_local_optuna_batch.py` as an intended hot path, although the current CodeGraph index does not include it.
- `index.html` as an intended UI hot path, although the current CodeGraph index does not include it.

Commands used:

```text
python3 /Users/dp/.codex/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/bestchoice --format markdown
sqlite3 .codegraph/codegraph.db '.tables'
sqlite3 .codegraph/codegraph.db '.schema'
sqlite3 .codegraph/codegraph.db "select language, count(*) as files, sum(size) as bytes, sum(node_count) as nodes from files group by language order by bytes desc;"
sqlite3 .codegraph/codegraph.db "select kind, count(*) from nodes group by kind order by count(*) desc;"
sqlite3 .codegraph/codegraph.db "select count(*) from edges; select kind, count(*) from edges group by kind order by count(*) desc;"
```

Detected stack and verification gates:

- Python backend and research scripts.
- Single-file HTML/JavaScript frontend.
- DuckDB/CSV research artifacts.
- Existing verification gates:
  - `python -m py_compile main.py compute.py execution_model.py formula_engine.py scripts/*.py`
  - `python scripts/execution_model_smoke.py`
  - `python scripts/unified_data_smoke.py`
  - `python scripts/strategy_rebuild_audit.py`
  - inline JavaScript syntax check for `index.html`
  - `git diff --check`

## CodeGraph State

Current CodeGraph database:

```text
.codegraph/codegraph.db
tables: files, nodes, edges, unresolved_refs, vectors, project_metadata
indexed languages: python
indexed files: 23
indexed bytes: 431891
indexed nodes: 691
nodes by kind: function=253, variable=200, import=189, file=23, method=21, class=5
edges: 1553 total, calls=798, contains=668, imports=87
```

Assessment:

- CodeGraph is now usable for a first dependency check across the Python research pipeline, including `scripts/formula_local_optuna.py`, `scripts/formula_local_optuna_batch.py`, formula cache/state-store builders, `formula_engine.py`, `execution_model.py`, `compute.py`, and `main.py`.
- CodeGraph is not yet sufficient as the only dependency authority because it does not index the single-file frontend `index.html`.
- `codegraph status` still reports pending added files after sync. The working tree contains many untracked project artifacts and scripts, so treat CodeGraph as a navigational index plus dependency clue, not a completion gate by itself.
- Before any dependency-sensitive optimization, run `codegraph sync /Users/dp/Documents/M/stock/bestchoice`, then use `codegraph query <symbol>` or `codegraph context <task>` to identify the dependency radius. Confirm with direct source inspection before editing.

Tool availability confirmed on 2026-05-20:

```text
/opt/homebrew/bin/codegraph
/opt/homebrew/bin/codex-complexity-optimizer
@colbymchenry/codegraph@0.6.8
codex-complexity-optimizer@0.1.0
```

Latest sync command:

```text
codegraph sync /Users/dp/Documents/M/stock/bestchoice
```

Latest status:

```text
Files: 23
Nodes: 691
Edges: 1553
Files by language: python=23
```

Important limitation:

- `codegraph context 'optimize formula local optuna batch and formula engine hotspots without changing strategy behavior'` only returned coarse entry points (`OUT_CSV`, `OUT_MD`, `FORMULA_LOCAL_OPTUNA_BATCH_ADOPTION`). For hotspot work, combine CodeGraph with direct line-level inspection and the complexity scanner output instead of relying on context output alone.

## Top Findings

### 1. `compute.py` historical metric loop repeats trade construction and horizon summarization

Location:

- `compute.py:1334`
- `compute.py:1419`
- `compute.py:1484`
- `compute.py:1494`
- `compute.py:1509`
- `compute.py:1526`

Current pattern:

- Per stock, the code builds raw trades and filtered trades, then loops through horizon maps to derive returns, drawdowns, executions, and best horizons.
- The scanner flags repeated nested loops and a small sort inside the per-stock path.

Estimated current complexity:

- Roughly `O(stocks * (signals * horizons + horizons * trades))`, plus repeated summarization passes.
- This is acceptable for small pools but becomes expensive when the UI/API computes many stocks repeatedly.

Recommended change:

- Extract a shared trade-summary helper that converts `trade_map` into returns, drawdowns, trade rows, execution metrics, horizon summaries, and best horizon in one pass.
- Preserve the current ordering and missing-data semantics.
- Keep formula and non-formula behavior separate at the call site; do not collapse missing optimized results into defaults.

Estimated after change:

- Same theoretical big-O for unavoidable signal/trade evaluation, but fewer repeated passes and less duplicated logic.

Risk:

- Medium. This code affects ranking/history metrics and UI-visible output.

Required checks:

- `python scripts/execution_model_smoke.py`
- `python scripts/unified_data_smoke.py`
- API comparison on `/api/recommendations` and `/api/parameter-search` before/after.

### 2. `execution_model.py` fixed holding trades create duplicated blocked/open rows per signal and period

Location:

- `execution_model.py:218`
- `execution_model.py:242`
- `execution_model.py:268`

Current pattern:

- For each signal, the code appends a row per holding period, including blocked or pending cases.
- This is semantically valid because each horizon needs its own row, but it creates repeated dictionary construction.

Estimated current complexity:

- `O(signals * holding_periods)`.

Recommended change:

- Do not optimize this first. The complexity is inherent to the output schema.
- If profiling later shows this is hot, use a small row-factory helper to reduce allocation duplication without changing row shape.

Estimated after change:

- Same big-O, lower constant factor only.

Risk:

- High if changed carelessly because execution rows are shared by backtest, optimization, charting, and audit.

Required checks:

- `python scripts/execution_model_smoke.py`
- Strategy audit before/after row-level sample comparison.

### 3. `formula_engine.py` contains real formula-level scan hotspots

Location:

- `formula_engine.py:348`
- `formula_engine.py:456`
- `formula_engine.py:458`
- `formula_engine.py:483`

Current pattern:

- `ma_base_breakout_signals` loops over every bar and calculates since-break counts with range sums.
- `volume_base_breakout_signals` searches candidate spike windows and uses range extrema/prefix sums, which is already partially optimized.

Estimated current complexity:

- `ma_base_breakout_signals`: approximately `O(n * window)` in the since-break section.
- `volume_base_breakout_signals`: approximately `O(n * candidate_spikes_in_window)` with optimized range queries.

Recommended change:

- Prioritize `ma_base_breakout_signals` before touching `volume_base_breakout_signals`.
- Replace per-bar range `np.sum` over slices with prefix-sum counters for `close < ma_l` and `close > ma_l`.
- Treat `volume_base_breakout_signals` as a second-phase target only after profiling because it already has `_RangeExtrema`, `_prefix_sum`, and `searchsorted`.

Estimated after change:

- `ma_base_breakout_signals` since-break counts can move from `O(n * window)` to `O(n)`.

Risk:

- Medium. Formula signal parity must be preserved exactly or documented as a strategy semantic change.

Required checks:

- Formula signal before/after equality on representative stocks.
- `python scripts/strategy_rebuild_audit.py`
- One small local Optuna batch smoke.

Current best first implementation candidate:

- `formula_engine.py:348-356` in `ma_base_breakout_signals`.
- This is a narrow formula-local optimization with clear semantics: replace repeated slice `np.sum(close[start : i + 1] < ma_l[start : i + 1])` and `np.sum(close[start : i + 1] > ma_l[start : i + 1])` with prefix counters for finite comparisons.
- Do not change the formula before adding a parity check that compares old/new entry arrays for representative stocks and edge cases around `b145 >= 1_000_000`.

### 4. `scripts/formula_local_optuna.py` repeats expensive evaluation per trial and per sell rule

Location:

- `scripts/formula_local_optuna.py:305`
- `scripts/formula_local_optuna.py:325`
- `scripts/formula_local_optuna.py:332`
- `scripts/formula_local_optuna.py:465`
- `scripts/formula_local_optuna.py:512`

Current pattern:

- Each Optuna trial computes signals, fixed-holding trades for all horizons, formula-exit trades for all horizons, and split metrics.
- Baseline evaluation and optimized evaluation both call the same evaluator logic.

Estimated current complexity:

- `O(stocks * formulas * trials * (signal_cost + signals * sell_rules))`.

Recommended change:

- Add a report-only benchmark first around per `(stock, formula)` trial time and signal count.
- Then consider caching reusable formula indicators inside a single trial only if params do not alter them, or split fixed-holding and formula-exit evaluation so repeated split metrics are summarized by a shared helper.
- Do not cache across different Optuna params unless the cache key includes formula id, params, execution model version, data fingerprint, and max signal cap.

Estimated after change:

- Likely same big-O, lower constants. Larger gains require formula-specific indicator reuse and strict cache keys.

Risk:

- Medium to high. Incorrect caching would silently corrupt parameter search.

Required checks:

- Resume idempotence: rerun batch with `--resume` and confirm `new_rows=0`.
- Candidate/rejection counts unchanged on a fixed small batch.
- Missing investigation fields remain populated.

Current CodeGraph coverage:

- After sync, `codegraph query 'formula_local_optuna'` finds `scripts/formula_local_optuna.py`, `scripts/formula_local_optuna_batch.py`, adoption/merge-plan scripts, and API constants in `main.py`.
- Use this to bound edits to the research pipeline and avoid accidental changes to production merge files.

### 5. `main.py` aggregates artifact summaries with repeated sorting and counting

Location:

- `main.py:355`
- `main.py:365`
- `main.py:376`
- `main.py:632`
- `main.py:636`
- `main.py:646`

Current pattern:

- Local Optuna status, rejection, and missing-investigation counts scan rows separately.
- Formula parameter search aggregation sorts lists multiple times inside formula grouping.

Estimated current complexity:

- `O(rows * passes + formulas * variants log variants)`.

Recommended change:

- Combine local Optuna count functions into one single-pass accumulator.
- For formula grouping, sort `items` once per formula and reuse the sorted list for `top` and `variants`; sort sell rules once.
- Preserve API field names exactly.

Estimated after change:

- Counting: from several `O(rows)` passes to one `O(rows)` pass.
- Formula grouping: fewer repeated sorts; same asymptotic sort cost where output requires ordering.

Risk:

- Low to medium. API shape preservation is the main risk.

Required checks:

- `/api/parameter-search` before/after key counts.
- inline JavaScript syntax check.
- UI management console manual smoke if localhost is running.

### 6. `index.html` and `scripts/formula_local_optuna_batch.py` are not covered by current CodeGraph index

Location:

- `index.html`
- `scripts/formula_local_optuna_batch.py`

Current pattern:

- These are known high-change/high-impact paths, but the current CodeGraph index cannot provide dependency radius for them.

Estimated current complexity:

- Unknown from CodeGraph. Complexity scanner can inspect JavaScript-like patterns only when files are included, but dependency graph coverage is absent.

Recommended change:

- Treat CodeGraph refresh/expansion as a prerequisite for major UI or batch-runner refactors.
- Add these files to the CodeGraph audit target list after refresh.
- Until then, changes to these files require manual dependency tracing.

Risk:

- Medium. Missing graph coverage increases the chance of optimizing a caller without seeing downstream API/UI coupling.

Required checks:

- CodeGraph file coverage check.
- Existing Python/JS syntax and smoke gates.

## Recommended Optimization Queue

1. `main.py` API aggregation single-pass counts and one-sort reuse.
2. `formula_engine.py` `ma_base_breakout_signals` prefix-sum parity optimization.
3. `compute.py` trade-summary helper extraction with before/after API sample comparison.
4. `scripts/formula_local_optuna.py` benchmark-only instrumentation, then scoped constant-factor reductions.
5. CodeGraph refresh/expansion to include `scripts/` and `index.html`.
6. `execution_model.py` only after profiling proves row construction is a bottleneck.

## Governance Rule

Every future complexity optimization must include:

- CodeGraph target coverage check.
- Complexity optimizer report or short audit note.
- Explicit statement of whether strategy semantics changed.
- Before/after verification commands.
- No production merge side effects.

## Current Decision

No optimization implementation is accepted from this audit yet. The safest next implementation candidate is `main.py` aggregation cleanup because it is API-summary-only and has lower strategy semantic risk than formula or execution changes.
