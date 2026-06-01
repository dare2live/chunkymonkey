# ChunkyCtl Session Quickstart

## Recommended New Session Instruction

For a fresh Codex session, the simplest user message is:

```text
请按照 /Users/dp/Documents/M/stock/chunkymonkey/docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查。
```

The new session must then do the startup sequence below before changing files,
then continue from the current user request, `goal.md`, and the latest handoff.
This entrypoint is for the whole project lifecycle, not only the current
architecture-reform phase.

## Startup Sequence

1. Read the required docs:
   - `AGENTS.md`
   - `goal.md`
   - `docs/README.md`
   - latest relevant `analysis/handoff_*.md` by date, if one exists
   - `docs/architecture_reform_context.md`
   - `docs/engineering_governance.md`
   - `docs/data_product_contract.md`
   - `docs/strategy_validation_contract.md`
   - `SESSION_HANDOFF.md` and `analysis/workflow_checkpoint.md` as context-only
     snapshots; ignore legacy Claude automation instructions when they conflict
     with the current Codex contracts.
   - dated bootstrap files such as `analysis/codex_bootstrap_20260527.md` only
     for command context when the latest handoff or `goal.md` still points to it.
   - Do not default to old `analysis/next_session_prompt_*.md` files; they are
     historical prompts unless `goal.md` explicitly makes one current.
2. Run:

```bash
scripts/chunkyctl doctor --fast
```

`doctor --fast` now includes tooling, test-tool, universe, storage-payload, and
system data-health snapshots. Red data-health tables are startup blockers, so
new sessions should inspect them before moving into business work.

3. If `doctor` reports a dirty worktree, run:

```bash
scripts/chunkyctl worktree --format markdown
```

4. If the dirty worktree includes docs cleanup or archive moves, run:

```bash
scripts/chunkyctl docs --format markdown
```

5. Report FAIL/risk first, then state the next scoped action.
6. Before a concrete task, run:

```bash
scripts/chunkyctl preflight "task" path...
```

7. After `.py` edits, run:

```bash
scripts/chunkyctl audit --run path...
```

## Maintenance Rule

This file is the durable startup contract. Update it in the same change batch
whenever any startup behavior changes. Do not leave a new startup rule only in
chat, `goal.md`, a handoff, or a tool implementation.

| Change | Required quickstart update |
|---|---|
| Required startup docs | Add/remove the exact document paths in `Startup Sequence` |
| Startup command or `chunkyctl` subcommands | Update `Daily Flow` and `Minimal Use` |
| Gate order or evidence rules | Update the numbered sequence before relying on the new gate |
| Controller / worker-agent workflow | Update `Operating Model` |
| Project phase changes | Keep the instruction project-lifecycle based, not tied to a temporary phase |
| New durable workflow/tool/doc convention | Add the command, owner, and validation point here before handing off |

Acceptance rule: any change that affects how a new Codex session should start is
incomplete until this document is updated and the final handoff states whether
`docs/chunkyctl_session_quickstart.md` changed or was explicitly unchanged.

## Daily Flow

| Moment | Command | Purpose |
|---|---|---|
| New session | Point Codex at this document | Lowest-friction default |
| Session startup | `scripts/chunkyctl doctor --fast` | Get dirty worktree, CodeGraph, complexity, storage-payload, and system data-health snapshot quickly |
| Dirty worktree reported | `scripts/chunkyctl worktree --format markdown` | Show a readable dirty-file bucket summary without mutating git |
| Dirty bucket drilldown | `scripts/chunkyctl worktree --bucket <name> --format markdown` | Review one bucket's entries and action before staging/deleting anything |
| Docs cleanup slice | `scripts/chunkyctl docs --format markdown` | Combine docs graph and docs/archive dirty-bucket readiness |
| Before a task | `scripts/chunkyctl preflight "task" path...` | Get required gates and scope-specific risks |
| After edits | `scripts/chunkyctl audit --run path...` | Run scoped validation for touched files |
| Data freshness repair | Use compute/read start plus explicit `--write-start` where available | Keep rolling lookback context separate from the DB replacement window |

## Data Freshness Repair Pattern

For local DuckDB freshness repair, fix the writer before running a narrow
window. Any table with rolling indicators or formula lookback must separate the
read/compute window from the write/delete window.

| Table family | Safe command shape |
|---|---|
| `fact_alpha158_panel` | `PYTHONPATH=backend python backend/scripts/build_alpha158_duck.py --start <read_start> --write-start <write_start> --end <end>` |
| `fact_stock_technical_stage` | `PYTHONPATH=backend python backend/scripts/build_stage_formula_fitness.py --start <read_start> --write-start <write_start> --end <end> --stage-only` |
| `fact_signal_context` | `PYTHONPATH=backend python backend/scripts/build_signal_context.py --start <read_start> --write-start <write_start> --end <end>` |
| `fact_technical_trigger` | `PYTHONPATH=backend python backend/scripts/build_formula_signals_history.py --start <read_start> --write-start <write_start> --end <end>` |

Do not pass a target window as `--start` when the script needs lookback. Use a
wider `--start` for computation and `--write-start` for replacement. After
refreshing production DuckDB tables, rerun the relevant data gates and record
FAIL/WARN state in `goal.md`/handoff instead of claiming readiness from row
counts alone.

## Doctor Interpretation

| Field | Rule |
|---|---|
| `complexity.diff.status=baseline_unavailable` | Treat current complexity findings as historical/unclassified debt, not new regressions |
| `complexity.diff.status=compared` | `new_high_count` is meaningful and blocks delivery when non-zero |
| `complexity.identity_mode=path_kind_message` | Default diff ignores line-number drift and compares finding counts by file/type/message; line numbers remain locating hints |
| `data/reports/tooling/complexity_baseline.json` exists | `doctor` loads it by default; explicit `--baseline` still overrides |
| `codegraph.pending.added` matches untracked indexable files | Review/stage by worktree bucket; do not force-sync or bulk stage to silence status |
| `storage_payload.verdict=FAIL` | Inspect recursive JSON keys and oversized opaque DB payloads with `PYTHONPATH=backend python backend/scripts/audit_storage_payloads.py --format markdown` |
| `storage_payload.summary.reviewed > 0` | Treat as reviewed PASS only when the matching `storage_retention.yaml` rule has owner, classification, caps, and recursive/path-marker guards |
| `data_health.verdict=FAIL` | Inspect red tables with `PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --format markdown`; treat missing tables and stale writers as startup blockers, not cosmetic warnings |
| `--skip-storage-payload` | Use only for emergency startup when the local DuckDB is unavailable; do not claim circular-reference cleanup from a skipped audit |

## Dirty Resolution Mode

When the user asks to clean or settle a long-running dirty worktree, do this in
order:

| Step | Action |
|---:|---|
| 0 | Remove only proven local generated residue: `.DS_Store`, `__pycache__`, `.pytest_cache`, `.pyc` |
| 1 | Run `scripts/chunkyctl worktree --format markdown` and confirm `unknown_count=0` or investigate unknowns first |
| 2 | Drill into one bucket at a time with `scripts/chunkyctl worktree --bucket <name> --format markdown` |
| 3 | Validate the bucket with its matching gates before staging: docs graph for docs, test-tool/py_compile/pytest for tests/tools, CodeGraph+complexity for `.py` |
| 4 | Stage explicit file lists only after review; never use `git add .` |
| 5 | Commit only through `scripts/safe_commit.sh`; use `SAFE_COMMIT_NO_PUSH=1` for local cleanup batches, and include Rule 10 review evidence for `.py` slices |

## Operating Model

| Role | Rule |
|---|---|
| Controller Codex | Owns priority, scope, gate decisions, docs, final acceptance |
| Worker agents | Work only inside assigned read/write scope and return evidence, not decisions |
| `chunkyctl` | Emits machine-readable facts and command plans; it does not replace review |
| Project docs | Keep durable rules in `AGENTS.md`, `goal.md`, handoff, and docs, not chat memory |

## Minimal Use

For normal development, remember only this:

```bash
scripts/chunkyctl doctor --fast
scripts/chunkyctl worktree --format markdown
scripts/chunkyctl worktree --bucket startup_tooling --format markdown
scripts/chunkyctl docs --format markdown
PYTHONPATH=backend python backend/scripts/audit_storage_payloads.py --format markdown
PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --format markdown
scripts/chunkyctl preflight "what I am about to change" path/to/file.py
scripts/chunkyctl audit --run path/to/file.py path/to/test_file.py
```
