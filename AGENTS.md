# ChunkyMonkey Agent Policy

This is the Codex operating policy for this repository. `CLAUDE.md` is legacy and
must not be read or applied unless the user explicitly requests historical
comparison. Current authority is `AGENTS.md`, `goal.md`, the owner documents in
`docs/README.md`, Codex skills, and live tooling/data evidence.

## 1. First actions

1. Read `goal.md`.
2. Run `git status --short --branch`; preserve user/peer changes.
3. Run `moth snapshot --repo .` and `codegraph status .`.
4. Read the one owner document named by `docs/README.md` for the task.
5. Verify drift-prone facts with live code, read-only DB/API checks, and current
   command output before planning edits.

Use `analysis/project_state_ledger.md` only by targeted `rg`/`tail`. The old
generated handoff/checkpoint system is retired. Do not reconstruct project truth
from chat or old summaries when live evidence is available.

## 2. Skill dispatch

- Any substantive judgment, architecture, diagnosis, or trade-off: `$mio`.
- Broad architecture, controller decomposition, multi-agent work, or ambiguous
  foundation design: `$architect-controller`.
- Non-trivial ChunkyMonkey execution, data/strategy/PIT/deletion/gate work:
  `$chunkymonkey-governance`.
- Debugging, TDD, issue slicing, or resumable handoff:
  `$chunkymonkey-debug-delivery`.
- Rule 10, `.py/.yaml/.sql` delivery, deletion review, and commit readiness:
  `$chunkymonkey-review-gate`.
- Codex app/CLI, hooks, skills, plugins, startup jobs, or local configuration:
  `$codex-local-ops`.

Moth locates shared tooling evidence; it does not own business rules. Sidecar or
sub-agent output is evidence, not a verdict.

Moth 0.3.0 resolves the profile's relative `repo_path` from process cwd. Always
`cd` into the target checkout/snapshot and use `--repo .`; never validate an
absolute staged path while cwd remains this dirty checkout. `safe_commit.sh`
enforces this for its exported Git-index snapshot.

## 3. Architecture law

The canonical design is `docs/MASTER_TOPLEVEL_DESIGN.md`.

Two axes must stay separate:

- transport: `landing -> validate -> accepted canonical -> serve`;
- business: `Tier0 truth -> Tier1 stock state -> Tier2 market sensing ->
  Tier3 research -> Tier4 decision/product`.

Dependencies point downward only. Ops/Governance observes every tier but owns no
business truth. Higher-tier results never feed foundation data.

Every reusable block is `module + data + config + contract + evidence`:

- one public boundary and one reason to change;
- one writer per published dataset;
- schema, grain, PIT/availability, lineage, version and failure policy;
- typed config for adjustable policy only;
- accepted batch/config/test/experiment evidence.

Do not create a plugin system, universal DAG, event bus, one-table-per-module,
one-table-per-version, or central YAML programming language. Establish semantic
boundaries before moving files or databases.

## 4. Tier 0 truth and data integrity

Tier 0 is blocking. It owns:

- provider landing data and batch evidence;
- trading calendar, security identity, nominal OHLCV, corporate actions and
  adjustment factors;
- accepted canonical partitions;
- versioned classification nodes and memberships.

Rules:

- landing preserves the provider response; universe/business filtering occurs
  later and must record a reason;
- every formal dataset declares one typed population scope: `raw_evidence`,
  `external_aggregate`, or `project_universe_pit`. Availability answers when;
  the universe policy answers who/which venue. Both are blocking and one
  immutable policy snapshot must flow through runner, writer, state, audit and
  consumers;
- external venue aggregates cannot prove constituent eligibility and must not
  masquerade as project-universe metrics. Project aggregates require
  security-grained PIT filtering. A later delisting must not rewrite an earlier
  eligible observation;
- the formal daily project population is `traded_on_observation_date`: accepted
  calendar + exact-date nominal Kline membership - same-day ST - excluded
  board/venue. The 90-day Kline window is legacy current enumeration only. A
  stronger rule that excludes a still-trading delisting period requires a
  separately accepted temporal-status source;
- nominal price is execution truth; qfq is a derived analysis view and must
  carry method/as-of/lineage;
- `stage -> validate -> publish -> accepted_partition` must have a proven
  atomic boundary;
- transport/batch mode never defines publication availability. A formal
  dataset declares a typed `axis/rule/at` policy in its versioned contract and
  config hash; consumer/`available_at`/continuity frontiers stay clocked.
  Sync authorization takes `trigger_mode=manual|automatic`: manual
  (chunkyctl/UI) may fetch a calendar-eligible open trading day without waiting
  for `same_day_at` clock; automatic keeps that clock. Calendar
  weekends/holidays bind both. Early capture stamps
  `available_at=max(observed_at, publication_cutoff)`;
- reject an explicit or injected future partition before adapter, database or
  writer I/O. A historical replay cap must not replace the live eligibility
  frontier in status or projections;
- derive one immutable contract from one registry snapshot per execution and
  pass that same object through acceptance, state, reconcile, projections,
  pipeline and audits; downstream config reloads are forbidden;
- validate a formal dataset's transport wiring and request shape before
  calendar, writer-lock, authorization, provider-adapter or target-DB side
  effects; never normalize duplicates into an apparently valid set;
- legacy naked `available_after=t+1` tokens retain their pre-migration behavior
  and must not be generalized; migrate each domain only after its event-time
  axis is proven;
- 0 rows, permission pages, schema changes, timeout and connection failure are
  different outcomes; fail closed;
- watermark/SLA/failure queue should be projections of accepted facts, not
  parallel truth writers;
- audits use read-only DuckDB by default; serialize all writes.

After any data/PIT/schema/cache fix, run `$post-fix-audit` and inspect stale
tables, reports, caches, watermarks, UI consumers and background residue.

## 5. Classification and market sensing

Unified classification means one temporal/identity contract, not one forced
tree. Preserve namespaces such as `sw_industry`, `dc_industry`, `dc_concept`,
`listing_venue`, `region/style/event`. Concepts remain many-to-many. Names are
labels, never cross-system keys; crosswalks need version and evidence.

“Money flow” fields are vendor-defined directional-imbalance proxies unless
proven otherwise. Market sensing must distinguish activity, imbalance method,
participation/breadth and price response. Do not sum overlapping concepts or
multiple hierarchy levels as conserved market money. Every research feature
needs `available_at`, method, unit, denominator, coverage and config hash.

## 6. Stock state and strategy validation

Tier 1 state describes only information visible by decision time: position,
trend, purity, volume, volatility, tradability and pattern events. Future return,
probability and buy/sell signals belong to Tier 3. State outputs require a
definition version, config hash, input snapshot and coverage reason.

Strategy work follows `docs/strategy_validation_contract.md`:

```text
B0 bare K
B1 + stock state
B2 + market sensing
B3 + one money-activity evidence block
B4 + institution/event
B5 + one formula or formula ensemble
```

Use identical snapshot, universe, folds, costs and execution while adding one
block. Require PIT truncation, purged walk-forward, embargo, one-touch holdout,
T+1, nominal-price execution,停牌/涨跌停, costs and capacity. Report return,
drawdown, win rate, payoff ratio, turnover and stability; unmeasured is
`unknown`, never 0.

BestChoice remains a frozen challenger. Read `bestchoice/FROZEN.md` and
`docs/strategy_validation_contract.md` before integration; never overwrite or
merge it into production without namespaced lineage, this project's paper
execution, and an accepted verdict.

## 7. Compute and automation

Data update and research execution are `manual_only`. Do not install or retain
ChunkyMonkey cron/launchd/hidden restart paths. A script on disk is not proof of
active automation; check real launchd/cron/launchctl/installer fan-in.

Supported data entrypoints:

```bash
scripts/chunkyctl doctor --fast
scripts/chunkyctl sync --domain DOMAIN [--drain --max-dates N]
scripts/chunkyctl sync --domain DOMAIN --backfill --start YYYYMMDD --end YYYYMMDD
bash scripts/daily_update.sh --date YYYYMMDD
```

`chunkyctl sync` is the manual single-domain boundary for controlled repair,
canary and replay. It loads the project provider environment, then delegates to
the production runner's authorization, calendar and writer-lock gates; it does
not bypass the full pipeline's Tier0 blocking rules or install automation.

Before long/paid work state objective, consumer, snapshot, runtime/cost, gates,
artifact path, stop and rollback plan. Current local execution is the only
allowed compute surface. Retired provider scripts and retired `chunkyctl`
commands must not be revived as shims.

Long work must use verified reusable checkpoints. Completion requires matching
snapshot/config hash, boundaries, positive row counts, parseable artifacts and
expected-count parity; never infer completion from a log line.

## 8. CodeGraph, Moth, and tests

When `.codegraph/` exists, use it before broad grep/file reading:

```bash
codegraph explore "<question or symbols>"
```

For non-trivial code changes run `codegraph sync .` afterward. Use complexity
scans as leads and validate hotspots against real paths and data sizes.

Before citing tests, perform the test-tool validity check from
`docs/engineering_governance.md`: exact command/scope, current truth sources,
real source shape, writable outputs, and a bad case that turns red. A green test
for an obsolete universe, PIT, provider, table or retired command is no evidence.

Code fitness and live data readiness are separate gates. `safe_commit.sh` reports
live continuity as `READY`, `DEGRADED`, `UNVERIFIED`, or `BLOCKED` without hiding
warnings, skipped checks, or provider/DB failures. Non-ready states continue to
block Tier0 consumption and release; a successful code commit never upgrades
them to ready.

## 9. Controller and parallel work

Parallel read-only discovery, independent tests and disjoint file scopes by
default. Keep serialized:

- architecture/truth-source decisions;
- `AGENTS.md`, `goal.md`, shared docs, Moth profile and generated maps;
- the same file/config/table/DB/output/study;
- provider jobs, DuckDB writes, staging, commit, push and final verdict.

Define every agent's read/write scope. Agents must not revert, stage or commit
other work. Controller re-reads patches, checks scope, reruns gates and owns
shared state updates.

## 10. Deletion and repository hygiene

Before deleting scripts, modules, tables, configs, docs, tests or automation:

```bash
moth coupling --repo . --impact <name>
codegraph explore "<name> callers consumers owners"
rg -n "<name>" backend scripts docs analysis .moth
```

Check code, config, governance SQL strings, tests and docs/Moth/skills. Migrate
durable evidence, run the narrowest tests, then delete for real. Do not leave
comments, renamed dead files, disabled branches, empty stubs or archive-of-
archive directories.

Do not leave scratch scripts, anonymous reports, notebooks or debug dumps in the
repo. Exploration belongs in `sandbox/` and is deleted when done. Important
artifacts need a named consumer and ledger reference.

## 11. Dirty worktree and commits

Always inspect `git status --short --branch`. Existing changes belong to the
user/peer unless proven otherwise. Work in disjoint slices; do not use
`git reset --hard`, `git checkout --`, broad clean commands, or `git add .`.

Delete only proven generated residue first. Stage explicit reviewed files. For
`.py/.yaml/.sql` or high-risk deletion, run Rule 10 review before commit. Do not
use `--no-verify`; fix a bad verifier instead. Do not amend pushed commits.

Cache cleanup is source-tree scoped. Never recurse into `.venv/`, `node_modules/`
or another managed dependency/runtime directory when deleting `*.pyc` or
`__pycache__`; TinyShare ships its executable SDK payload as versioned `.pyc`,
so a generic cache sweep can destroy the provider while metadata still appears
installed.

Default local commit command when a commit is requested/appropriate:

```bash
SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"
```

No push unless the user asks.

## 12. Definition of done

A change is not done until:

- requirement-by-requirement evidence is mapped;
- targeted tests use current architecture and pass;
- Moth/CodeGraph are current and verifier false-greens were challenged;
- PIT/data fixes include stale-artifact checks;
- docs/AGENTS/skills/config owners agree;
- `git diff --check` passes;
- worktree residue is either intentionally owned or explicitly blocked;
- delivery is labeled `FIXED`, `PARTIAL`, or `BLOCKED`, with residual owner and
  exact next verification.
