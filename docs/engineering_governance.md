# Engineering Governance Contract

This is the active engineering rulebook for architecture work after the docs
consolidation. It absorbs the durable rules from the former top-level design,
test-tool, agent-parallel, tooling, GCP, and deprecation docs. Historical source
files are archived under `analysis/docs_archive_20260531/`.

## Risk First

| Risk | Rule |
|---|---|
| Pretty backtests replacing production evidence | Metrics are `unknown` until measured by current gates |
| Old docs or chat memory steering work | `goal.md`, `SESSION_HANDOFF.md`, `analysis/workflow_checkpoint.md`, and this docs set are the current surface |
| Legacy Claude policy steering Codex | `CLAUDE.md` is ignored by default; Codex uses `AGENTS.md`, active docs, skills, and live tooling unless the user explicitly asks for historical migration |
| Hidden complexity and stale indexes | CodeGraph and complexity optimizer are a paired gate |
| Green tests proving old assumptions | Run test-tool health before citing tests as evidence |
| Cloud spend or dirty cloud artifacts | GCP work requires explicit scope and `CHUNKYMONKEY_GCP_EXPLICIT_OK=1` |

## Skill Owner Map

| Scope | Required skill | Rule |
|---|---|---|
| Codex Mac app/CLI, plugin sync, startup items, hooks, local automations, compact errors, Codex worktrees, Terminal mail, or monitor residue | `$codex-local-ops` | Diagnose local Codex state first; do not misclassify it as a project hook or business gate |
| Non-trivial ChunkyMonkey execution, architecture, strategy validation, PIT/leakage, Optuna/GCP, deletion, or gate-policy work | `$chunkymonkey-governance` | Run pre-execution governance before edits or expensive commands |
| Rule 10, commit readiness, blocking code review, or `.py` / `.yaml` / `.sql` slices before commit | `$chunkymonkey-review-gate` | Produce a review verdict and commit trailer before `safe_commit.sh` |
| Shared tooling state and evidence path discovery | Moth profile `.moth/profile.yaml` | Locate evidence paths and current tooling state; do not redefine business gate rules |

## Dirty Worktree Resolution

Dirty worktree cleanup is a staged delivery process. It is not complete until
each dirty file is either deleted as proven residue, accepted into a reviewed
slice, or explicitly left as an owned evidence gap.

| Layer | Scope | Rule |
|---:|---|---|
| 0 | Local generated residue | Delete `.DS_Store`, `__pycache__`, `.pytest_cache`, and `.pyc`; do not delete logs/reports/DB artifacts without owner evidence |
| 1 | Classification | `scripts/chunkyctl worktree --format markdown`; `unknown_count` must be 0 before commit planning |
| 2 | Slice review | Use `scripts/chunkyctl worktree --bucket <name> --format markdown`; review docs/tooling/updater/data/service/test buckets separately |
| 3 | Gate proof | Run the slice's CodeGraph, complexity, docs graph, test-tool, py_compile, pytest, or domain audit gates before staging |
| 4 | Explicit staging | Stage only reviewed file lists; never `git add .` |
| 5 | Safe commit | Use `scripts/safe_commit.sh`; for local cleanup batches use `SAFE_COMMIT_NO_PUSH=1`; `.py` slices require Rule 10 review or a meaningful skip reason |

CodeGraph pending `Added` that equals untracked indexable files is a worktree
acceptance problem, not something to silence with repeated syncs. Accept the
files into a reviewed slice or delete them when verified obsolete.

## Design Review Gate

Every new flow, feature, module, table, config file, or threshold must answer:

| Check | Requirement |
|---|---|
| First principles | What must exist for real-money A-share decisions to be trustworthy? |
| Occam | Can an existing module/table/config own this without a new abstraction? |
| Owner | Is the owner a YAML/config, data table, service module, or code exception? |
| Truth source | Which source is authoritative, and which sources are caches or evidence? |
| Failure mode | What 300616-style failure does this prevent or expose? |
| Gate | Which script/test blocks drift before production use? |

Hardcoded business rules are blocked by default. Thresholds, source priority,
dates, table catalogs, weights, strategy parameters, and resource policies
belong in config or tables unless a documented exception is safer.
For `portfolio_sizer` threshold tuning specifically, run
`backend/scripts/audit_portfolio_sizer_profile_attrition.py` first and treat
its attrition summary as the required evidence gate before changing
`min_n_signals` / `min_wilson_win`.

## CodeGraph + Complexity

| Timing | Required action |
|---|---|
| Before non-trivial `.py` edits | `codegraph query "<symbol>"` and `codegraph context "<task>"` |
| After `.py` edits | `codegraph sync .` |
| After `.py` edits | `/Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/backend --format markdown` |
| Finding review | Treat scanner output as leads; inspect code before patching |
| Completion claim | State whether findings are new, historical, fixed, or accepted with reason |

## Test Tool Validity

Before running tests as evidence:

| Check | Requirement |
|---|---|
| Registry | Selected test files must be registered in `backend/config/test_tool_registry.yaml` |
| Scope | Unit/contract/integration/realdb/perf/network/gcp scope must match the command |
| Truth source | K-line for tradeability, calendar for dates, `universe_rules.yaml` for board/limit rules |
| `dim_active_a_stock` | Code-to-name/cache fixtures only, never active-universe truth |
| DB fixtures | Prefer DuckDB memory fixtures unless the test explicitly owns a real DB exception |

Primary command:

```bash
PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope <registry-id-or-path>
```

## Controller / Agent Mode

| Role | Responsibility |
|---|---|
| Controller Codex | Direction, scope, gates, architecture decisions, final merge, shared docs, and immediate critical-path work |
| Default parallelism | Spawn bounded assistant agents by default for independent sidecar investigation unless the user explicitly says not to parallelize, the tool is unavailable, or the work is tightly coupled |
| Read-only agents | Discovery, graph queries, data inventory, stage-opt/need coverage/storage triage, complexity triage, RCA evidence |
| Worker agents | Bounded implementation only inside explicitly disjoint file scopes; never revert controller or peer work |
| `chunkyctl preflight` controller-agent gate | Broad audit/research/architecture/data/debug or 3+ scope tasks must show `controller_agent_gate.required=true`; controller satisfies it by dispatching bounded agents or recording a concrete skip reason |

Parallelize read-only work and independent tests. DuckDB-heavy audits may run in
parallel only when they use explicit read-only connections. Serialize shared
docs, commits, GCP, materializing/write DuckDB scripts, shared output paths,
Optuna studies, and edits to the same file.

## Moth Ownership

Moth owns shared tooling state for this repo: profile discovery, evidence paths,
dirty worktree status, CodeGraph freshness, and complexity baseline/current
summaries. The active repo-local profile is `.moth/profile.yaml`. It may also
list local Codex skill files as evidence paths so new sessions can find the
local-ops, governance, and Rule 10 contracts without relying on chat memory.

ChunkyMonkey owns business gate logic. Keep `stage_opt`, `need_027`,
`storage_payload`, `data_health`, universe truth-source rules, and test-tool
validity rules in repo audit scripts, config, and `chunkyctl`; Moth may read or
surface their generated evidence, but should not redefine their rules.

## GCP Controlled Use

GCP is allowed only for scoped work where cloud runtime materially helps. Before
launching, state objective, command family, expected runtime/cost, input
snapshot, output path, monitor plan, and stop plan. All GCP-touching commands
must include:

```bash
CHUNKYMONKEY_GCP_EXPLICIT_OK=1
```

Compute jobs need a wrapper or heredoc that records pid/log/artifact/GCS path,
handles final upload, and schedules shutdown only after verified finalization.

## Deletion And Deprecation

Verified-dead code, tests, docs, and data artifacts should be deleted for real,
not hidden behind comments, renamed files, disabled branches, or empty stubs.
Before deletion, use CodeGraph plus `rg` to check references and run the
narrowest relevant audit/test. If evidence is historical but still useful, move
it to `analysis/`; if it is not useful, delete it.

## Active Tool Commands

| Purpose | Command |
|---|---|
| Startup health | `scripts/chunkyctl doctor --fast` |
| System data health snapshot | `PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --dry-run --format text` (reports `writer_prompt` / owner / sync_step hints in red/yellow rows; use `--format json` for machine consumers) |
| Dirty worktree buckets | `scripts/chunkyctl worktree --format markdown` |
| Docs cleanup slice | `scripts/chunkyctl docs --format markdown` |
| Storage payload / recursive JSON audit | `PYTHONPATH=backend python backend/scripts/audit_storage_payloads.py --format markdown` |
| Pre-task gate | `scripts/chunkyctl preflight "<task>" path/to/scope.py` |
| Docs graph | `PYTHONPATH=backend python backend/scripts/audit_docs_graph.py --format markdown` |
| Test tool health | `PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope <scope>` |
| Safe commit | `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "message"` for local batches; omit `SAFE_COMMIT_NO_PUSH` only when pushing is intended |

## Archived Sources

This contract supersedes these former active docs:

| Former doc | Current state |
|---|---|
| `../analysis/docs_archive_20260531/top_level_design_review.md` | Archived; rules consolidated here |
| `../analysis/docs_archive_20260531/test_tool_governance.md` | Archived; registry/gate contract consolidated here |
| `../analysis/docs_archive_20260531/agent_parallel_execution_policy.md` | Archived; controller/agent rules consolidated here |
| `../analysis/docs_archive_20260531/tooling_update_review_20260527.md` | Archived; active commands consolidated here |
| `../analysis/docs_archive_20260531/gcp_controlled_execution_runbook.md` | Archived; controlled-use rule consolidated here |
| `../analysis/docs_archive_20260531/deprecation_sop.md` | Archived; deletion/deprecation rule consolidated here |
