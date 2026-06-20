# Agent Operating Policy

This file is the Codex-facing operating rulebook for this repo. `CLAUDE.md` is
a legacy Claude-only artifact, not a Codex policy source. Codex must not read or
apply `CLAUDE.md` by default; open it only when the user explicitly asks for
historical comparison, and treat `AGENTS.md`, active docs, skills, and live
tooling output as authoritative when they conflict.

## First Actions

- Read `goal.md` first. Treat it as the compact live controller board: current
  objective, priority order, active blockers, and implementation plan.
- Treat `SESSION_HANDOFF.md` as a generated context-only snapshot and
  `analysis/workflow_checkpoint.md` as an active-pipeline checkpoint only when
  it says the pipeline is active. Verify both with live gates before using them
  as state.
- Use `analysis/project_state_ledger.md` for completed work and historical
  evidence by `rg`/`tail` lookup, not as a full startup read.
- Run `git status --short` before edits. The worktree is often dirty; never
  revert user or peer changes unless explicitly asked.
- When deleting or retiring scripts, provider paths, startup jobs, cron entries,
  launchd plists, installers, dashboards, or local automation, check the fan-in
  first with `moth coupling --repo . --impact <name>` (the former
  `audit_execution_surface.py` was retired in the 2026-06-16 reset; coupling
  audit + `rg` on launchd/cron/registries replace it).
- Prefer `rg`, `codegraph`, targeted tests, and read-only DuckDB inspection
  before guessing.
- Keep `goal.md` current when active objective, priority order, blocker state,
  or next actions change. Move completed details and historical evidence to
  `analysis/project_state_ledger.md`; do not grow `goal.md` as a session log.
- Keep `docs/chunkyctl_session_quickstart.md` current when startup commands,
  gate order, controller/agent workflow, tool entrypoints, or project phase
  assumptions change. New sessions must not inherit stale startup instructions.
- Treat Moth as the canonical shared tooling snapshot for this repo. When a
  task asks for project state or shared gate facts, prefer `moth snapshot` /
  `moth sync` as the source of truth; repo-local wrappers like
  `scripts/chunkyctl doctor --fast` and `scripts/chunkyctl preflight` should
  consume that layer rather than re-implementing their own parser.
- The repo-local Moth profile is `.moth/profile.yaml`. Use it for shared
  tooling metadata and evidence paths only. Business gate logic such as
  `stage_opt`, `need_027`, `storage_payload`, and `data_health` stays owned by
  ChunkyMonkey audit scripts and `chunkyctl`, with Moth consuming their outputs
  instead of re-defining their rules.

## Skill Dispatch

- Codex Mac app/CLI, hooks, plugins, startup items, local automations, remote
  compact errors, stale Codex worktrees, Terminal mail, or retired provider
  monitor residue:
  use `$codex-local-ops` before touching project rules.
- Broad architecture, controller-led decomposition, multi-agent orchestration,
  ambiguous system design, or "framework foundation before details" work: use
  `$architect-controller` to define truth source, boundary contracts,
  falsification gates, attention allocation, delegation slices, and the smallest
  reversible next step.
- Non-trivial ChunkyMonkey execution, architecture, strategy validation,
  PIT/leakage, Optuna/provider jobs, deletion, or gate-policy work: use
  `$chunkymonkey-governance` before editing or launching work.
- Rule 10, commit readiness, blocking reviews, and `.py` / `.yaml` / `.sql`
  slices before commit: use `$chunkymonkey-review-gate`.
- Moth is the shared tooling snapshot and evidence-path locator; it does not own
  business gate logic.

## Codegraph + Complexity Review

For any non-trivial code change, audit, refactor, performance task, or delivery
readiness decision:

1. Run or refresh CodeGraph first:
   - `codegraph status .`
   - `codegraph sync .` when files changed or the index may be stale
   - `codegraph context "<task>"` or `codegraph query "<symbol>"` to identify
     entry points, related tests, and dependency boundaries.
2. Combine CodeGraph with the `complexity-optimizer` workflow:
   - run `/Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey --format markdown`
     for broad scans when complexity/performance/N+1 risk is relevant;
   - treat scanner output as leads, not proof;
   - inspect surrounding code before patching;
   - rank fixes by hot path, data size, blast radius, and testability.
3. After edits, run targeted tests and `codegraph sync .` again so future
   agents do not work from stale structure.

## Test Tool Validity Gate

Before running tests or using test results as delivery evidence, audit whether
the selected tests still match the current architecture. A green test is not
evidence if the tool is proving an obsolete universe, PIT, data-source, or DB
assumption.

Perform a manual pre-test check and state it in the work log (the planned
`audit_test_tool_health.py` was not built / retired in the 2026-06-16 reset;
manual check is the current process):

- identify the exact test command and files it will exercise;
- confirm the test scope and marker are appropriate (`unit`, `contract`,
  `integration`, `pipeline`, or explicit opt-in `realdb`/`perf`/`network`/`gcp`);
- confirm fixtures use current truth sources: K-line for tradeability, calendar
  for dates, `universe_rules.yaml` for board/limit rules, and
  `dim_active_a_stock` only for code-to-name/cache/schema fixtures;
- confirm DB fixtures use DuckDB/`duck_mem()` unless the test is explicitly
  about Optuna SQLite storage or another documented exception;
- reject tests that rely on fixed historical end dates, proxy/warn-only evidence,
  over-mocked gates, or legacy paths as production proof.

After the audit script exists, run it before meaningful targeted or broad test
runs and treat FAIL as blocking unless the current task is fixing that test-tool
failure. Keep the long-lived policy in `docs/engineering_governance.md`, the
current controller decision in `goal.md`, and completed evidence in
`analysis/project_state_ledger.md`.

## Parallel Execution

Use parallelism aggressively when scopes do not conflict, especially for
read-only discovery, code-path audits, test runs, and independent file scopes.
For this repo, parallel agent work is opt-out: Codex should act as the
controller and spawn bounded assistant agents by default for independent
sidecar investigation unless the user explicitly asks not to parallelize, the
tool is unavailable, or the next step is tightly coupled to the controller's
critical path. The durable policy lives in `docs/engineering_governance.md`;
keep this section and that file aligned when changing workflow rules.

Default Codex mode for this repo is controller-led execution: Codex is the
controller/architect/reviewer, not just a single-file implementer. The
controller owns direction, decomposition, risk classification, scope assignment,
final merge, final validation, and project-state updates. Sub-agents do bounded
execution inside explicit scopes; their output is a candidate until reviewed and
accepted by the controller.

When Codex sub-agents are available, prefer them for bounded sidecar work that
can proceed while the controller handles the critical path. Start with read-only
explorers for architecture audits, complexity triage, data-lineage inventory,
and failure root-cause mapping. Use worker agents only when their write scopes
are disjoint and explicitly owned.

Safe to parallelize:
- documentation reading and summarization;
- CodeGraph/context queries;
- read-only DuckDB inventory with read-only connections, while DB-heavy audits
  that may open write/materialization handles stay serialized unless they have
  an explicit read-only mode;
- independent code audits;
- tests that do not write the same DB/output path;
- implementation tasks with disjoint file ownership.

Keep serialized:
- edits to the same file or shared config;
- writes to the same DuckDB table/output directory/cache/artifact;
- Optuna studies sharing the same study name/storage;
- commit/push/final merge;
- `goal.md`, `SESSION_HANDOFF.md`, `AGENTS.md`, and project index updates
  unless one controller owns the edit. `CLAUDE.md` is legacy and should not be
  consulted or edited unless the user explicitly requests historical migration.

Controller responsibilities:
- choose what not to delegate: architecture decisions, truth-source decisions,
  final gate verdicts, shared documentation, staging, commits, pushes,
  provider-backed job control, and DuckDB write windows stay with the controller;
- define each agent's read/write scope before starting;
- tell read-only agents not to edit, delete, stage, commit, run provider jobs,
  run Optuna, or run long/expensive jobs;
- tell worker agents they are not alone in the codebase and must not revert
  others' changes;
- review returned patches before accepting them;
- run final tests and merge/update project state in one place.
- update compact `goal.md` and `analysis/project_state_ledger.md` only after
  reconciling agent results; `SESSION_HANDOFF.md` remains generated context,
  and shared docs are serialized unless one controller owns the edit.

## Compute / Experiment Jobs

Legacy project-owned GCP execution entrypoints were removed on 2026-06-05. Do
not add provider-specific shell scripts, guard-only shims, or hidden restart
paths for retired providers. Heavy data validation, backtest validation, model
training, and parameter search work must be represented as a registered job family in
`backend/config/experiment_jobs.yaml` and planned through:

```bash
scripts/chunkyctl jobs --family <job-family> --backend local \
  --input-snapshot <snapshot> \
  --objective "<why this job should run>" \
  --rollback-plan "<how to stop or discard artifacts>" \
  --gate-evidence <gate>=<artifact-or-command>
```

`local` is the only active backend. `modal` is a planned backend and must remain
blocked until a provider adapter proves its artifact manifest contract. Provider
adapters may execute commands and materialize artifact manifests; domain gates,
PIT/leakage checks, promotion criteria, and business validation stay owned by
ChunkyMonkey services, scripts, config, and tables.

Before any long or paid provider-backed job, state objective, job family,
backend, expected runtime/cost, input snapshot, artifact directory, required
gates, and stop/rollback plan. The controller owns backend selection, final gate
verdicts, and shared state updates.

## Long-Run Checkpoint Reuse

Do not rerun expensive completed work just because a long job was interrupted.
For any replay, Optuna study, parameter sweep, or train-log backfill that can run
long enough to be preempted or manually stopped, design the job around reusable
verified checkpoints before launching.

Completion is not inferred from a log line. A result is reusable only when the
stored checkpoint proves:
- the same model/input snapshot and stable params/config hash;
- the same expected window/trial/entity key and date boundaries;
- positive train/test row counts where applicable;
- parseable metrics/artifact JSON;
- `checkpoint_status='complete'` or equivalent terminal state;
- observed completed count equals expected count before any aggregate/promotion
  row is written.

For LambdaMART train-log replay, use `--train-log-only --resume-train-log`.
Completed replay windows live in `fact_model_train_log_window` at
`model_id + replay_id + window_key` grain. Restarted runs must skip only verified
matching windows and compute missing windows. `fact_model_train_log` is allowed
to receive the aggregate evidence row only after every expected replay window has
been verified against the current params hash and window boundaries.

## Strategy Validation Rules

This project controls real-money stock strategy decisions. Treat every KPI as a
deployment risk signal, not a scoreboard.

- Measured, not estimated: returns, Sharpe, drawdown, hit rate, uplift, weights,
  and thresholds must come from historical rows, Optuna output, or documented
  backtests. If not measured, mark `unknown`.
- No leakage: time `t` decisions may only use information available at or before
  `t`. Check `built_at`, `as_of_date`, PIT universe, adjustment factors, purged
  labels, embargo, and train/test time splits.
- Suspiciously good numbers are warnings: Sharpe > 5, win rate > 95%, annualized
  return > 100%, or large relative uplift needs leakage/PIT ablation before any
  promotion claim.
- Production claims must use cost-aware paper sim / Phase4 / PBO / DSR evidence,
  not in-sample metrics.
- `warn_only_proxy` is not a hard promote. A hard promote requires the gate's
  actual promotion action to be `promote`, with non-proxy evidence where the gate
  requires it.

## Optuna and Parameter Search

- Prefer the central optimization/governance helpers and YAML/config-backed
  search spaces. Do not hardcode strategy thresholds or weights in business code.
- Use time-aware walk-forward splits; Optuna must not see future OOS periods.
- Record OOS metrics and reject/governance reasons. In-sample `sharpe` is not a
  selector for forward decisions.
- Heavy search should be planned through `experiment_jobs`; `local` is active
  and `modal` remains blocked until its adapter contract is reviewed.

## Root Cause and Data Integrity

- Do not hide failures with `try/except: pass`, `--skip-step`, fixed end dates,
  environment bypasses, or silent fallbacks.
- Fix the first bad writer or bad join path, not just the symptom. If cleanup is
  needed, do both: clean historical residue and add a regression guard.
- For tdxhub/miaoxiang-backed data, treat missing listed-company data as a sync
  or ingestion bug first. Do not assume the upstream truly lacks it without
  evidence.
- After bug/leakage/schema/cache fixes, actively look for stale artifacts:
  generated JSON, old model rows, cache tables, lineage rows, dashboards, and
  background processes.

## Engineering Rules

- Keep changes narrowly scoped and match existing style.
- Do not invent frameworks for one-off changes. Abstract only when it removes
  real duplication or matches an existing local pattern.
- Configurable thresholds, paths, dates, model IDs, and table names belong in
  config or arguments unless they are test fixtures or mathematical constants.
- Use structured SQL/parsers/APIs instead of ad hoc string parsing when available.
- Add focused tests proportional to risk. For shared behavior, add regression
  tests.
- Run the narrowest meaningful tests first, then broader checks when the blast
  radius warrants it.
- Run `git diff --check` before handing off.

## Hardcoding Governance

Business rules are governed assets, not incidental constants. Before adding or
modifying thresholds, strategy weights, date windows, stock codes, table/path
catalogs, data-source priorities, universe/board/limit rules, or audit criteria
in Python, decide the owner first.

Default ownership:
- YAML/config owns stable rules, thresholds, parameters, switches, and resource
  policy.
- Data tables own observed facts, source inventories, lineage, gate evidence,
  runtime status, and reusable artifacts.
- Service modules own validation, typed access, fallback order, and API shape.
- Python literals are acceptable for test fixtures, mathematical constants,
  schema/enum names, SQL DDL, and small local implementation details that are
  not business policy.

If a business value must remain in code, document why config/table ownership
would be worse and add a focused regression check. Do not copy the same rule
into YAML, SQL, and Python; one rule should have one source of truth.

## Repository Hygiene

Keep the codebase clean and organized at all times. A task is not finished while
it leaves behind unexplained scratch files, dead code, obsolete documents, or
unowned output directories.

- Do not leave temporary files, debug dumps, one-off notebooks, ad hoc reports,
  partial exports, or scratch scripts in the repo unless they are deliberate
  evidence artifacts with stable names and a documented consumer.
- Delete dead code, dead files, and unnecessary folders when they are clearly
  superseded. Do not hide validated-dead code behind comments, flags, renamed
  files, dead branches, or "kept for later" stubs; if evidence proves it is safe
  to remove, remove it. Before deleting code or tests, use CodeGraph
  (`codegraph query` / `codegraph context`) plus `rg` to check references,
  owners, and call paths; run the narrowest relevant tests/audits after removal.
  Preserve anything that is still part of audit evidence, lineage,
  reproducibility, or historical validation.
- Keep documents organized by purpose:
  - current objectives, priority order, blockers, and implementation plan belong
    in the compact `goal.md`;
  - completed work, historical status, and detailed evidence belong in
    `analysis/project_state_ledger.md` or a dated `analysis/` artifact;
  - `SESSION_HANDOFF.md` is generated resume context only;
  - `analysis/workflow_checkpoint.md` is only for one active multi-step
    pipeline, otherwise it should be an inactive stub;
  - durable design/audit references belong under `docs/`;
  - dated evidence and session archives belong under `analysis/`;
  - old handoffs, stale prompts, duplicated status docs, and obsolete plans
    should be removed once their useful facts are migrated.
- Do not create extra directories just to hold one-off work. Reuse existing
  `analysis/`, `docs/`, `data/reports/`, or module-local test locations when
  they are the right owner.
- After any substantial change, inspect `git status --short` and the relevant
  generated/report directories. Either commit intentional artifacts with clear
  names or remove them before handoff.
- Preserve validation artifacts that support strategy decisions, but do not let
  them become anonymous clutter. If an artifact matters, reference it from the
  current ledger or a dated analysis document; if it does not matter, remove it.

### Dirty Worktree Resolution

Treat a dirty worktree as a delivery risk, not as background noise. Always run
`scripts/chunkyctl doctor --fast` and `scripts/chunkyctl worktree --format
markdown` before staging or deleting broad changes. Unknown bucket count must be
0 before any commit planning; if not, inspect with CodeGraph, `rg`, and owner
docs first.

Resolve dirty state in layers:

1. Delete only proven generated local residue first: `.DS_Store`,
   `__pycache__`, `.pytest_cache`, and `.pyc`. Do not delete logs, reports,
   database files, or analysis artifacts until their owner and evidence value
   are clear.
2. Review and stage one bucket/slice at a time. Default order is controller
   state/docs archive cleanup, startup tooling, universe/data-source governance,
   updater split, then remaining service/script/test domains.
3. A CodeGraph `Added` count that matches untracked `.py`/`.js`/`.jsx` files is
   not an index bug; it means those files must be accepted into a reviewed slice
   or deleted if obsolete.
4. Never use `git add .` to solve dirty state. Stage explicit file lists only
   after the slice has current gates and a clear commit message. Use
   `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "message"` for local cleanup
   commits unless the user explicitly wants to push.
5. Do not revert or discard user/peer work to make status clean. If ownership is
   unclear, keep the file dirty and record the evidence gap in `goal.md` or the
   handoff instead of hiding it.

## Delivery Readiness

- The project is not "done" just because one score improves. Update delivery
  state only after the relevant audit scripts and evidence artifacts agree.
- Preserve previous validation artifacts. Do not overwrite or delete probe/gate
  results unless the user explicitly asks; add new evidence and refresh summary
  JSON instead.
- When BestChoice is involved, read
  `/Users/dp/Documents/M/stock/bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md`
  directly before making integration decisions. Follow that plan first:
  Phase 0 artifact freeze/hash/lineage, namespaced challenger import, daily
  candidate feed, main-project paper sim, KPI registry, and complementarity
  comparison. Do not directly merge BestChoice logic into production
  ChunkyMonkey, and do not run BestChoice cloud expansion until local portfolio
  paper_sim or complementarity evidence satisfies the plan's trigger conditions.
