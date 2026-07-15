# ChunkyCtl Session Quickstart

> **部分 stale (2026-06-20)**: 下方"手动重建表"表格里的 `build_alpha158_duck` /
> `build_stage_formula_fitness` / `build_signal_context` / `build_formula_signals_history` 及
> `audit_execution_surface` / `audit_storage_payloads` / `probe_source_capability` 等命令引用的脚本
> **在 2026-06-16 reset 已删** (旧 alpha158/formula 管道退役)。**当前管道重建** = `build_feature_panel.py`
> (L2 因子面板) + `build_rally_ground_truth/entry_pit/negative/episode_strata/stage.py` (主升浪 episode 层) +
> `build_segment_panel.py`。**启动检查** (doctor/gates/handoff) 部分仍有效。当前阶段/命令以 `../goal.md` +
> `chunkymonkey-ops` skill 为准, 勿直接运行下方悬空 build_*/audit_* 命令。

## Recommended New Session Instruction

For a fresh Codex session, the simplest user message is:

```text
请按照 /Users/dp/Documents/M/stock/chunkymonkey/docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查。
```

The new session must then do the startup sequence below before changing files,
then continue from the current user request, compact `goal.md`, and live gates.
This entrypoint is for the whole project lifecycle, not only the current
architecture-reform phase.

Codex app/CLI should not rely on hidden SessionStart handoff injection or cron
snapshot refresh. If the previous session may have crashed, first run
`bash scripts/cm_resume.sh` in the repo, then give the new Codex session the
instruction above. Treat `SESSION_HANDOFF.md` as a context-only snapshot and
verify live state with `doctor --fast` before trusting it.

## Startup Sequence

1. Read the minimal startup docs:
   - `AGENTS.md`
   - `goal.md`
   - `docs/README.md`
   - `SESSION_HANDOFF.md` and `analysis/workflow_checkpoint.md` as context-only
     snapshots; ignore legacy Claude automation instructions when they conflict
     with the current Codex contracts.
   - Use `analysis/project_state_ledger.md` only by `rg` or `tail` when the
     current task needs historical evidence; do not read it start-to-finish
     during startup.
   - Load owner docs on demand:
     `docs/engineering_governance.md` for gates/deletion/provider jobs,
     `docs/data_product_contract.md` for data-source/PIT/freshness work,
     `docs/strategy_validation_contract.md` for strategy/model promotion, and
     `docs/MASTER_TOPLEVEL_DESIGN.md` for global architecture/lineage/roadmap
     (architecture_reform_context retired 2026-06-15).
   - Do not read `CLAUDE.md` as a Codex startup or policy source; it is legacy
     Claude-only history unless the user explicitly asks for historical
     migration.
   - dated bootstrap files (pre-reset, now deleted) are not a startup source;
     use `goal.md` + the generated handoff + `docs/MASTER_TOPLEVEL_DESIGN.md`.
   - Do not default to old `analysis/next_session_prompt_*.md` files; they are
     historical prompts unless `goal.md` explicitly makes one current.
   - Skill dispatch: `$codex-local-ops` owns Codex app/CLI local issues;
     `$architect-controller` owns broad architecture/controller decomposition;
     `$chunkymonkey-governance` owns non-trivial project execution planning;
     `$chunkymonkey-review-gate` owns Rule 10 and commit-readiness review.
2. Run:

```bash
scripts/chunkyctl doctor --fast
```

`doctor --fast` now includes tooling, test-tool, universe, storage-payload,
stage-opt recommendation/sensitivity, need_027 blocked-gap triage,
execution-surface audit, and system data-health snapshots. The tooling gate now comes from the shared
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
`moth snapshot --repo /Users/dp/Documents/M/stock/chunkymonkey --profile /Users/dp/Documents/M/stock/chunkymonkey/.moth/profile.yaml --format json`
when you need the raw shared snapshot, and `moth sync ...` when you want the
shared snapshot refreshed before any repo-local wrapper consumes it.
The active repo-local profile is `.moth/profile.yaml`; keep it limited to
shared tooling metadata and evidence paths, including pointers to the local
Codex skills that govern local-ops, architecture/controller decomposition,
ChunkyMonkey governance, and Rule 10 review. It also exposes
`instruction_sources`, where `CLAUDE.md` is listed under
`ignored_by_default` so Codex startup and preflight flows can enforce that
boundary without relying on chat memory.
Do not move business gate rules such as `stage_opt`, `need_027`,
`storage_payload`, or `data_health` into Moth.
The public Moth repo lives at `https://github.com/dare2live/moth`; keep the
local `moth` binary or `CHUNKYMONKEY_MOTH_COMMAND` pointed at a current build
from that repo so shared tooling state stays reproducible across sessions.
Prefer a globally installed Moth for all repos, with the repo-local wrapper
only consuming the shared CLI. When you need a migration window or a pinned
behavior, point `CHUNKYMONKEY_MOTH_COMMAND` at a specific installed build
instead of copying Moth logic into the repo itself.
When the session snapshot only has generated handoff files dirty, read its
`NEXT_ACTION` as a hint, not authority. The compact `goal.md` plus live
`doctor --fast` output decide the actual next action. `analysis/workflow_checkpoint.md`
is active only when it explicitly says an active pipeline is in progress; an
inactive checkpoint must not revive completed or retired provider workflows.

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
scripts/chunkyctl preflight "task" path... --agent-dispatch "agent:NAME scope/evidence"
```

`preflight` now reuses the shared Moth-backed tooling gate for dirty/worktree/codegraph state, so the old local codegraph parser wrapper is retired with the gate.
It also emits `design_review_gate`, which machine-surfaces the first-principles,
Occam, owner, truth-source, failure-mode, and drift-blocking gate checks from
`docs/engineering_governance.md`.
The shell wrapper accepts `--agent-dispatch` and `--agent-skip-reason` in both
positional and flag-style preflight calls; use the wrapper, not the Python
module path, as the normal startup entrypoint.
For broad audit/research/architecture/data/debug or 3+ scope tasks, missing
agent evidence is a FAIL. If dispatch is genuinely impossible, pass
`--agent-skip-reason "concrete reason"` and expect a WARN.

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
| Architecture / first-principles workflow | Update `Operating Model` and keep local mechanisms aligned with Moth evidence paths |
| Project phase changes | Keep the instruction project-lifecycle based, not tied to a temporary phase |
| New durable workflow/tool/doc convention | Add the command, owner, and validation point here before handing off |

Acceptance rule: any change that affects how a new Codex session should start is
incomplete until this document is updated and the final handoff states whether
`docs/chunkyctl_session_quickstart.md` changed or was explicitly unchanged.

## Manual Data-Update Contract (2026-07-15)

- ChunkyMonkey data jobs are `manual_only`. There is no supported cron or
  launchd data batch; a scheduled plist/script in the repo, user account, or
  system directories is a blocking residue, not an inactive convenience.
- The supported full-chain command is
  `bash scripts/daily_update.sh --date <YYYYMMDD>`. The frontend manual button
  is an accepted/enqueued request for that same chain; the child flock decides
  whether execution actually starts, so the API must not claim `started=true`
  before a lock handshake.
- Before provider writes, the chain validates today's SSE raw calendar row,
  raw-to-dim agreement, and at least 60 future trading days. A failed calendar
  gate may self-repair exactly once under the same writer lease via
  `trade_cal full refresh -> calendar_builder -> identical gate recheck`;
  `--dry` validates but never repairs.
- Authorization uses one parameterless `user()` probe per parent chain, with a
  config-owned hard timeout. Only a direct child holding the inherited real
  flock descriptor and matching auth proof may reuse that result. Runtime
  authorization denial is a hard stop, not a degraded continuation.
- The supported pipeline/sync entrypoints share a single real flock. Internal
  one-off writer scripts are not advertised as independent public entrypoints
  and must never be installed as background automation.

## Daily Flow

| Moment | Command | Purpose |
|---|---|---|
| New session | Point Codex at this document | Lowest-friction default |
| Crash/terminal recovery | `bash scripts/cm_resume.sh` | Refresh `SESSION_HANDOFF.md` and print the prompt to give Codex; no hidden auto-inject |
| Session startup | `scripts/chunkyctl doctor --fast` | Get dirty worktree, CodeGraph, complexity, execution-surface, storage-payload, and system data-health snapshot quickly |
| Manual full data update | `bash scripts/daily_update.sh --date <YYYYMMDD>` | Run calendar/auth preconditions, acquire, clean, process, and store under one inherited writer lease; inspect exit code and ALERT flags |
| Historical state lookup | `rg "<topic>" analysis/project_state_ledger.md` or `tail -120 analysis/project_state_ledger.md` | Find completed evidence without loading the whole ledger |
| Retiring scripts/providers/automation | `moth coupling --repo . --impact <name>` + `PYTHONPATH=backend python backend/scripts/check_dead_references.py` (2026-06-28 replaced retired `audit_execution_surface.py`, see `engineering_governance.md` §Automation points) | Prove launchd, cron, installers, dashboards, registries, Moth evidence paths, and code/config/SQL-string references do not point at deleted or retired execution paths |
| Dirty worktree reported | `scripts/chunkyctl worktree --format markdown` | Show a readable dirty-file bucket summary without mutating git |
| Dirty bucket drilldown | `scripts/chunkyctl worktree --bucket <name> --format markdown` | Review one bucket's entries and action before staging/deleting anything |
| Docs cleanup slice | `scripts/chunkyctl docs --format markdown` | Combine docs graph and docs/archive dirty-bucket readiness |
| Shared tooling state | `moth snapshot --repo /Users/dp/Documents/M/stock/chunkymonkey --profile /Users/dp/Documents/M/stock/chunkymonkey/.moth/profile.yaml --format json` | Canonical shared gate snapshot; repo-local wrappers should consume this rather than re-derive it |
| Shared tooling refresh | `moth sync --repo /Users/dp/Documents/M/stock/chunkymonkey --profile /Users/dp/Documents/M/stock/chunkymonkey/.moth/profile.yaml --format json` | Refresh codegraph + snapshot before relying on shared state |
| Before a task | `scripts/chunkyctl preflight "task" path...` | Get required gates and scope-specific risks |
| After edits | `scripts/chunkyctl audit --run path...` | Run scoped validation for touched files |
| Data freshness repair | Use compute/read start plus explicit `--write-start` where available | Keep rolling lookback context separate from the DB replacement window |

## Data Freshness Repair Pattern

For local DuckDB freshness repair, fix the writer before running a narrow
window. Any table with rolling indicators or formula lookback must separate the
read/compute window from the write/delete window.

| Table family | Safe command shape (2026-07-02 批5 更新 — 旧 alpha158/stage/signal 表+builder 已随纯数据平台重建物删) |
|---|---|
| `price_kline_qfq_tushare` (K线真相源) | `PYTHONPATH=backend .venv/bin/python backend/scripts/build_price_kline_qfq_tushare.py` (全量 CTAS 重建 + 自 sanity) |
| raw_tushare_* 任一域回填 | `PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain <d> --drain` |
| PIT 行业视图 | `build_sw_industry_view.py` / `build_dc_industry_view.py` |
| reference dim (2026-07-07 起 2 张: active_a_stock/trading_calendar; all_ever_listed/listing_status 已整表退役) | `services/security_master.refresh_active_a_stock_master` + `services/calendar_builder.build_latest` (均已挂 daily pipeline acquire 阶段, 不再需要手动跑) |

After
refreshing production DuckDB tables, rerun the relevant data gates, update the
active decision in `goal.md`, and move detailed evidence to
`analysis/project_state_ledger.md` or a dated artifact instead of claiming
readiness from row counts alone.

## Doctor Interpretation

| Field | Rule |
|---|---|
| `complexity.diff.status=baseline_unavailable` | Treat current complexity findings as historical/unclassified debt, not new regressions |
| `complexity.diff.status=compared` | `new_high_count` is meaningful and blocks delivery when non-zero |
| `complexity.identity_mode=path_kind_message` | Default diff ignores line-number drift and compares finding counts by file/type/message; line numbers remain locating hints |
| `data/reports/tooling/complexity_baseline.json` exists | `doctor` loads this ignored local artifact by default; refresh it after intentionally accepting the current scanner scope, otherwise stale baselines can make old debt look new |
| `codegraph.pending.added` matches untracked indexable files | Review/stage by worktree bucket; do not force-sync or bulk stage to silence status |
| `storage_payload.verdict=FAIL` | Inspect recursive JSON keys and oversized opaque DB payloads manually (the `audit_storage_payloads.py` helper was retired in the 2026-06-16 reset) |
| `storage_payload.summary.reviewed > 0` | Treat as reviewed PASS only when the matching `storage_retention.yaml` rule has owner, classification, caps, and recursive/path-marker guards |
| `data_health.verdict=FAIL` | Inspect red tables with `PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --dry-run --format text`; treat only blocking assets as startup blockers, and remember that `warning` / `monitor_only` assets are intentionally capped to yellow |
| `data_health.blocking_yellow > 0` | Inspect `blocking_yellow_tables` and let `scripts/chunkyctl doctor --fast` prioritize those before generic yellow maintenance; blocking-quality yellow assets are actionable even when the verdict is still WARN |
| `preflight.instruction_sources.ignored_by_default` contains `CLAUDE.md` | Treat `CLAUDE.md` as legacy Claude-only history; use `AGENTS.md`, active docs, Codex skills, and live tooling as policy |
| `.moth/profile.yaml instruction_sources.ignored_by_default` contains `CLAUDE.md` | Moth profile carries the same policy-source boundary for new sessions and raw snapshots |
| `preflight.design_review_gate.required=true` | Answer the first-principles, Occam, owner, truth-source, failure-mode, and drift-blocking gate checks before accepting architecture/data/config/table/threshold work |
| `preflight.controller_agent_gate.required=true` | Spawn bounded sidecar agents for independent read-only/review/RCA or disjoint worker scopes, then reconcile their output as controller evidence |
| `preflight.risk=controller_agent_dispatch_missing` | Stop before editing and rerun preflight with `--agent-dispatch` evidence or `--agent-skip-reason`; this is a process gate, not a documentation reminder |
| `worktree.bucket=legacy_context` | Historical Claude-only context; do not merge it into Codex controller state unless explicitly migrating legacy content |
| `stage_opt.verdict=WARN` | Advisory audit warning: candidate supply still has blocked keys or unknown-stage drops; it is not a strategy promote/reject verdict |
| `stage_opt.top_blocked_reason_counts` exists | Use it to see which gate dominates stage-opt attrition before rerunning audits; `below_min_signals` is currently the primary blocker, and `doctor` should surface it alongside `next_action_recommendation` |
| `stage_opt.attrition_funnel` / `top_blocked_stage_formula_cells` / `top_blocked_registry_family_cells` exists | Use these evidence fields to locate raw -> filtered -> unique -> blocked -> ready loss, the worst `stage_bin × formula_id` cells, and the worst `registry_scope × formula_family` cells before changing upstream contracts or schemas |
| `stage_opt.candidate_supply_contract` exists | Treat this as the machine-readable source contract for stage-opt supply: source role, grain, PIT/diagnostic eligibility, allowed consumers, allowed stage bins, and formula scope overrides live in `backend/config/stage_opt_candidate_supply.yaml`; do not re-create these rules in scripts or docs |
| `stage_opt.next_action_recommendation.focus=upstream_candidate_supply` | Treat this as a supply-side blocker: first check freshness and dependency windows (`fact_signal_context` before `fact_technical_trigger`), then expand upstream formula coverage or signal density only if freshness is aligned. The 2026-06-02 config-only probe series is exhausted, so there is no safe strategy/threshold knob slice left for stage-opt. Evidence-tooling slices are allowed when they make the structural blocker more legible; future production work should be structural redesign or upstream-source work, not another knob-tuning pass. `macd_golden_cross` also carries a `fact_technical_trigger` schema limit note, so do not confuse state rows with a schema-only fix |
| `need_coverage.blocked_needs` contains `need_027` | evaluate exact-flow source readiness manually (the `probe_source_capability.py` no-persist gate was retired in the 2026-06-16 reset); read `exact_flow.source_groups` by provider because AkShare and token-backed TuShare are alternative exact-flow candidates, not an all-sources-AND requirement; treat `need_027` as production-blocked until one source group passes small-batch stability and PIT/freshness, writer, watermark, and failure-queue resolve evidence also pass; `aif10` exact `individual_fund_flow` is unavailable, and the research-side rank snapshot is not a production fallback |
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
| Controller Codex | Owns priority, scope, gate decisions, docs, final acceptance; does the immediate critical-path work locally |
| Architecture review | Start from truth source, minimal mechanism, and ownership boundary before local fixes; use Moth/CodeGraph/audit outputs as evidence, not as business-rule owners |
| Depth control | Build framework foundations first, then improve one layer at a time; do not follow one technical direction into broad historical migration unless it blocks the current architecture layer |
| Default parallelism | Spawn bounded assistant agents by default for independent sidecar investigation unless the user explicitly says not to parallelize, the tool is unavailable, or the work is tightly coupled |
| Explorer agents | Use read-only scopes for architecture audits, data-lineage checks, stage-opt/need coverage triage, storage payload review, and failure root-cause mapping |
| Worker agents | Work only inside assigned disjoint read/write scope and return evidence, not decisions; never revert peer/controller work |
| `preflight` controller-agent gate | Broad audit/research/architecture/data/debug or 3+ scope tasks FAIL with `controller_agent_dispatch_missing` unless preflight receives `--agent-dispatch`; `--agent-skip-reason` records an explicit exception as WARN |
| DB-heavy audits | Parallelize only when they use explicit read-only connections; serialize scripts that materialize tables, write DuckDB, or share output paths |
| `chunkyctl` | Emits machine-readable facts and command plans; it does not replace review |
| Skill dispatch | Local Codex ops use `$codex-local-ops`; broad architecture/controller work uses `$architect-controller`; project governance uses `$chunkymonkey-governance`; Rule 10 / commit review uses `$chunkymonkey-review-gate` |
| Project docs | Keep durable rules in `AGENTS.md`/`docs`, current controller state in compact `goal.md`, completed evidence in `analysis/project_state_ledger.md`, and generated resume facts in `SESSION_HANDOFF.md` |

## Minimal Use

For normal development, remember only this:

```bash
scripts/chunkyctl doctor --fast
scripts/chunkyctl worktree --format markdown
scripts/chunkyctl worktree --bucket startup_tooling --format markdown
scripts/chunkyctl docs --format markdown
PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --dry-run --format text
# (audit_execution_surface / audit_storage_payloads / probe_source_capability retired 2026-06-16 reset)
scripts/chunkyctl preflight "what I am about to change" path/to/file.py
scripts/chunkyctl audit --run path/to/file.py path/to/test_file.py
```
