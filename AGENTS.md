# ChunkyMonkey Agent Policy

Authority order: `AGENTS.md` → `goal.md` → the owner docs named by
`docs/README.md` (`MASTER_TOPLEVEL_DESIGN.md`, `strategy_validation_contract.md`,
`engineering_governance.md`) → Codex skills → live tooling/data evidence.
`CLAUDE.md` is legacy; do not read or apply it unless the user explicitly
requests historical comparison. `BOARD.md` + `data/board/agent_context.json`
are a generated status projection, never an enforcement input. History lives in
`analysis/project_state_ledger.md` — query it with targeted `rg`/`tail` only;
do not reconstruct project truth from chat or old summaries when live evidence
is available.

## 1. Boot

1. Read `goal.md`.
2. Run `scripts/chunkyctl agent-boot` — one page: git status summary + Moth
   summary + CodeGraph status + generated board. Preserve user/peer worktree
   changes.
3. Read the one owner document named by `docs/README.md` for the task.
4. Verify drift-prone facts with live code, read-only DB/API checks, and
   current command output before planning edits.

Full tool evidence when needed: `git status --short --branch`,
`moth snapshot --repo .`, `codegraph status .`, `codegraph explore "..."`.
Moth resolves the profile's relative `repo_path` from process cwd: always `cd`
into the target checkout/snapshot and use `--repo .`; `safe_commit.sh` enforces
this for its exported Git-index snapshot.

## 2. Skill dispatch

Owner table: engineering_governance §2. Shorthand: substantive judgment or
trade-offs `$mio`; broad architecture/decomposition/multi-agent
`$architect-controller`; non-trivial project/data/strategy/PIT/deletion/gate
work `$chunkymonkey-governance`; debugging/TDD/handoff
`$chunkymonkey-debug-delivery`; Rule 10 and commit readiness
`$chunkymonkey-review-gate`; local Codex configuration `$codex-local-ops`.
Sidecar or sub-agent output is evidence, not a verdict.

## 3. Hard architecture law (owner: docs/MASTER_TOPLEVEL_DESIGN.md)

- Transport axis `landing -> validate -> accepted canonical -> serve` stays
  separate from the business axis `Tier0 truth -> Tier1 stock state -> Tier2
  market sensing -> Tier3 research -> Tier4 decision/product`. Dependencies
  point downward only; higher-tier results never feed foundation data;
  Ops/Governance observes every tier but owns no business truth.
- Every reusable block is `module + data + config + contract + evidence`: one
  public boundary and one reason to change; one writer per published dataset;
  typed config for adjustable policy only; accepted evidence for claims.
- No plugin system, universal DAG, event bus, one-table-per-module,
  one-table-per-version, or central YAML programming language. Semantic
  boundaries before moving files or databases.

## 4. Tier 0 truth and data integrity (blocking)

- Landing preserves the provider response; universe/business filtering happens
  later and records a reason.
- Every formal dataset declares one typed population scope (`raw_evidence`,
  `external_aggregate`, `project_universe_pit`) and a typed availability
  `axis/rule/at` policy; transport/batch mode never defines publication
  availability. One immutable contract from one registry snapshot per
  execution flows through runner, writer, state, audit and consumers;
  downstream config reloads are forbidden.
- External venue aggregates never masquerade as project-universe metrics; a
  later delisting must not rewrite an earlier eligible observation. The formal
  daily population is `traded_on_observation_date`.
- Nominal price is execution truth; qfq is a derived view carrying
  method/as-of/lineage.
- `stage -> validate -> publish -> accepted_partition` needs a proven atomic
  boundary. Reject explicit or injected future partitions before adapter/DB/
  writer I/O; validate transport wiring and request shape before any side
  effect; never normalize duplicates into an apparently valid set. Sync
  authorization takes `trigger_mode=manual|automatic` (manual may skip the
  `same_day_at` clock on open trading days; calendar weekends/holidays bind
  both).
- 0 rows, permission pages, schema changes, timeout and connection failure are
  different outcomes; fail closed. Watermark/SLA/failure queues are
  projections of accepted facts, not parallel truth writers.
- Audits use read-only DuckDB by default; serialize all writes. Legacy naked
  `available_after=t+1` tokens stay domain-local and must not be generalized.
- After any data/PIT/schema/cache fix run `$post-fix-audit`: stale tables,
  reports, caches, watermarks, UI consumers and background residue.

## 5. Classification, sensing, state and strategy

- Taxonomy namespaces stay separate; names are labels, never cross-system
  keys; concepts remain many-to-many; crosswalks need version and evidence.
- "Money flow" fields are vendor directional-imbalance proxies unless proven
  otherwise; never sum overlapping concepts or hierarchy levels as conserved
  money. Research features need `available_at`, method, unit, denominator,
  coverage and config hash.
- Tier 1 state describes only decision-time-visible information; future
  return, probability and buy/sell signals belong to Tier 3.
- Strategy work follows `docs/strategy_validation_contract.md`: B0→B5 adding
  one block at a time on identical snapshot/universe/folds/costs/execution,
  with PIT truncation, purged walk-forward, embargo, one-touch holdout, T+1,
  nominal-price execution,停牌/涨跌停 and costs; unmeasured is `unknown`,
  never 0.
- BestChoice stays a frozen challenger (`bestchoice/FROZEN.md`); never
  overwrite or merge it into production without namespaced lineage, this
  project's paper execution, and an accepted verdict.

## 6. Compute and automation

Data update and research execution are `manual_only`. Never install or retain
cron/launchd/hidden restart paths; a script on disk is not proof of active
automation. Supported entrypoints (owner: engineering_governance §11):
`scripts/chunkyctl agent-boot | doctor | sync`, `bash scripts/daily_update.sh`.
Retired provider scripts and retired `chunkyctl` commands must not be revived
as shims. Before long/paid work state objective, consumer, snapshot,
runtime/cost, gates, artifact path, stop and rollback plan; long work uses
verified reusable checkpoints and never infers completion from a log line.

## 7. Tests and gates

- Perform the test-tool validity check (engineering_governance §5) before
  citing green; a green test for an obsolete universe, PIT, provider, table or
  retired command is no evidence.
- Code fitness and live data readiness are separate gates: `safe_commit.sh`
  reports continuity as `READY/DEGRADED/UNVERIFIED/BLOCKED` without hiding
  failures, and a successful code commit never upgrades Tier0 readiness.
- Use `codegraph explore` before broad grep; run `codegraph sync .` after
  non-trivial code changes.

## 8. Parallel work, deletion and hygiene

- Keep serialized: architecture/truth-source decisions; `AGENTS.md`, `goal.md`,
  shared docs, Moth profile and generated maps; the same
  file/config/table/DB/output/study; provider jobs, DuckDB writes, staging,
  commit, push and final verdict. Agents never revert, stage or commit other
  work; the controller re-reads patches, checks scope and reruns gates.
- Before deleting anything: `moth coupling --repo . --impact <name>`,
  `codegraph explore "<name> callers"`, `rg -n "<name>" backend scripts docs
  analysis .moth`; migrate durable evidence; no comment tombstones, renamed
  dead files, disabled branches, stubs or archive-of-archive directories.
- Exploration belongs in `sandbox/` and is deleted when done. Cache cleanup is
  source-tree scoped: never recurse into `.venv/`, `node_modules/` or managed
  runtimes — TinyShare ships its SDK payload as versioned `.pyc`.

## 9. Commits

Inspect `git status --short --branch`; existing changes belong to the
user/peer unless proven otherwise. Stage explicit reviewed files; never
`git add .`, `git reset --hard`, `git checkout --`, broad clean commands,
`--no-verify`, or amending pushed commits. Run Rule 10 review for
`.py/.yaml/.sql` or high-risk deletion before commit. Default local commit:

```bash
SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"
```

No push unless the user asks. Commit tiers L1/L2/L3 are machine-classified
(`backend/config/commit_tiers.yaml`); agents cannot self-downgrade a tier.

## 10. Definition of done

Requirement-by-requirement evidence mapped; targeted tests use current
architecture and pass; Moth/CodeGraph current and verifier false-greens
challenged; PIT/data fixes include stale-artifact checks;
docs/AGENTS/skills/config owners agree; `git diff --check` passes; worktree
residue intentionally owned or explicitly blocked; delivery labeled
`FIXED | PARTIAL | BLOCKED` with residual owner and exact next verification.
