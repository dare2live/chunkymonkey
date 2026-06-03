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

Codex app/CLI should not rely on hidden SessionStart handoff injection or cron
snapshot refresh. If the previous session may have crashed, first run
`bash scripts/cm_resume.sh` in the repo, then give the new Codex session the
instruction above. Treat `SESSION_HANDOFF.md` as a context-only snapshot and
verify live state with `doctor --fast` before trusting it.

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

`doctor --fast` now includes tooling, test-tool, universe, storage-payload,
stage-opt recommendation/sensitivity, need_027 blocked-gap triage, and
system data-health snapshots. The tooling gate now comes from the shared
`moth snapshot` layer, and the old `audit_tooling_gate.py` wrapper is retired;
so the local environment must have Moth available
either on `PATH` or via `CHUNKYMONKEY_MOTH_COMMAND` (for example:
`CHUNKYMONKEY_MOTH_COMMAND=/tmp/moth-venv/bin/moth`). The data-health snapshot respects
`quality_gate_level`: `warning` and `monitor_only` assets are capped to yellow,
while blocking assets remain red. Red data-health tables are startup blockers,
and the snapshot now emits `writer_prompt` / owner / sync_step hints so new
sessions can see which writer or sync step likely owns the problem before
moving into business work.
For shared tooling state, treat Moth as the canonical entrypoint: use
`moth snapshot --repo /Users/dp/Documents/M/stock/chunkymonkey --profile chunkymonkey --format json`
when you need the raw shared snapshot, and `moth sync ...` when you want the
shared snapshot refreshed before any repo-local wrapper consumes it.
The public Moth repo lives at `https://github.com/dare2live/moth`; keep the
local `moth` binary or `CHUNKYMONKEY_MOTH_COMMAND` pointed at a current build
from that repo so shared tooling state stays reproducible across sessions.
Prefer a globally installed Moth for all repos, with the repo-local wrapper
only consuming the shared CLI. When you need a migration window or a pinned
behavior, point `CHUNKYMONKEY_MOTH_COMMAND` at a specific installed build
instead of copying Moth logic into the repo itself.
When the session snapshot only has generated handoff files dirty, the computed
`NEXT_ACTION` now points to the current goal blockers instead of the old
retrain placeholder. Treat `SESSION_HANDOFF.md` as the startup state, but read
its next action as controller guidance for the active project blockers rather
than as a separate retrain workflow when the repo itself is otherwise clean.

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

`preflight` now reuses the shared Moth-backed tooling gate for dirty/worktree/codegraph state, so the old local codegraph parser wrapper is retired with the gate.

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
| Crash/terminal recovery | `bash scripts/cm_resume.sh` | Refresh `SESSION_HANDOFF.md` and print the prompt to give Codex; no hidden auto-inject |
| Session startup | `scripts/chunkyctl doctor --fast` | Get dirty worktree, CodeGraph, complexity, storage-payload, and system data-health snapshot quickly |
| Dirty worktree reported | `scripts/chunkyctl worktree --format markdown` | Show a readable dirty-file bucket summary without mutating git |
| Dirty bucket drilldown | `scripts/chunkyctl worktree --bucket <name> --format markdown` | Review one bucket's entries and action before staging/deleting anything |
| Docs cleanup slice | `scripts/chunkyctl docs --format markdown` | Combine docs graph and docs/archive dirty-bucket readiness |
| Shared tooling state | `moth snapshot --repo /Users/dp/Documents/M/stock/chunkymonkey --profile chunkymonkey --format json` | Canonical shared gate snapshot; repo-local wrappers should consume this rather than re-derive it |
| Shared tooling refresh | `moth sync --repo /Users/dp/Documents/M/stock/chunkymonkey --profile chunkymonkey --format json` | Refresh codegraph + snapshot before relying on shared state |
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
| `data_health.verdict=FAIL` | Inspect red tables with `PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --format markdown`; treat only blocking assets as startup blockers, and remember that `warning` / `monitor_only` assets are intentionally capped to yellow |
| `data_health.blocking_yellow > 0` | Inspect `blocking_yellow_tables` and let `scripts/chunkyctl doctor --fast` prioritize those before generic yellow maintenance; blocking-quality yellow assets are actionable even when the verdict is still WARN |
| `stage_opt.top_blocked_reason_counts` exists | Use it to see which gate dominates stage-opt attrition before rerunning audits; `below_min_signals` is currently the primary blocker, and `doctor` should surface it alongside `next_action_recommendation` |
| `stage_opt.next_action_recommendation.focus=upstream_candidate_supply` | Treat this as a supply-side blocker: expand upstream formula coverage or signal density before tuning profile knobs; the 2026-06-02 config-only probe series is exhausted, so there is currently no safe code slice left for stage-opt. Future work there should be treated as structural redesign or upstream-source work, not another knob-tuning pass; `macd_golden_cross` also carries a `fact_technical_trigger` schema limit note, so do not confuse state rows with a schema-only fix |
| `need_coverage.blocked_needs` contains `need_027` | Treat `need_027` as blocked exact-flow evidence until registry/route changes reopen the source; `aif10` exact `individual_fund_flow` is unavailable, and the research-side rank snapshot is not a production fallback |
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
