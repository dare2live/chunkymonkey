# ChunkyMonkey Agent Policy

Authority: `AGENTS.md` → `goal.md` → `docs/README.md` owners
(`MASTER_TOPLEVEL_DESIGN.md`, `strategy_validation_contract.md`,
`engineering_governance.md`) → Codex skills → live tooling/data.
`CLAUDE.md` legacy (explicit historical request only). `BOARD.md` +
`data/board/agent_context.json` = generated projection, never enforcement.
History: targeted `rg`/`tail` of `analysis/project_state_ledger.md` only.

## 1. Boot
1. Read `goal.md`.
2. `scripts/chunkyctl agent-boot` (git + Moth summary + CodeGraph + board).
   Preserve user/peer worktree changes.
3. Read the one owner doc named by `docs/README.md` for the task.
4. Verify drift-prone facts with live code / read-only DB/API / current commands.
Full evidence: `git status --short --branch`, `moth snapshot --repo .`,
`codegraph status .` / `explore`. Always `cd` target checkout + `--repo .`
(Moth 0.3.0 cwd; `safe_commit.sh` enforces for staged snapshots).

## 2. Skills
Owner: engineering_governance §2. `$mio` judgment; `$architect-controller`
broad/multi-agent; `$chunkymonkey-governance` data/strategy/PIT/deletion/gate;
`$chunkymonkey-debug-delivery` debug/TDD/handoff; `$chunkymonkey-review-gate`
Rule 10/commit; `$codex-local-ops` local Codex. Sidecar = evidence not verdict.

## 3. Architecture (MASTER_TOPLEVEL_DESIGN.md)
Transport `landing→validate→accepted canonical→serve` ≠ business
`Tier0→1→2→3→4`. Dependencies down only; higher never feeds foundation; Ops
observes, owns no business truth. Block =
`module+data+config+contract+evidence` (one boundary, one writer, typed policy
config, accepted evidence). No plugin/DAG/event-bus, one-table-per-module/
version, or YAML programming language. Semantic boundaries before file/DB moves.

## 4. Tier 0 truth (blocking)
- Landing preserves provider response; universe/business filter later + reason.
- Formal dataset: typed population scope (`raw_evidence` /
  `external_aggregate` / `project_universe_pit`) + typed availability
  `axis/rule/at`; transport/batch never defines publication availability. One
  immutable contract from one registry snapshot through runner/writer/state/
  audit/consumers; no downstream config reload.
- External venue aggregates ≠ project-universe metrics; later delisting must
  not rewrite earlier eligibility. Daily pop = `traded_on_observation_date`.
  Nominal = execution truth; qfq derived (method/as-of/lineage).
- `stage→validate→publish→accepted_partition` atomic. Reject future partitions
  before adapter/DB/writer I/O; validate transport before side effects; never
  normalize duplicates into valid. Sync `trigger_mode=manual|automatic`
  (manual may skip `same_day_at` on open days; weekends/holidays bind both).
- 0 rows / permission / schema / timeout / connection are distinct; fail closed.
  Watermark/SLA/failure queues project accepted facts — not parallel writers.
  Audits default read-only DuckDB; serialize writes. Naked `available_after=t+1`
  stays domain-local. After data/PIT/schema/cache fix: `$post-fix-audit`.

## 5. Classification / sensing / state / strategy
Namespaces separate; names=labels not keys; concepts M2M; crosswalks need
version+evidence. "Money flow"=vendor imbalance proxy unless proven; never sum
overlapping concepts/levels as conserved money. Features need `available_at`,
method, unit, denominator, coverage, config hash. Tier1=decision-time-visible
only; future return/prob/signals→Tier3. Strategy:
`strategy_validation_contract.md` B0→B5 one block, same snapshot/universe/
folds/costs/execution; PIT truncation, purged WF, embargo, one-touch holdout,
T+1, nominal exec, 停牌/涨跌停, costs; unmeasured=`unknown` never 0.
BestChoice frozen (`bestchoice/FROZEN.md`).

## 6. Compute
`manual_only`. No cron/launchd/hidden restart. Script-on-disk ≠ active
automation. Entrypoints (eng_gov §11): `chunkyctl agent-boot|doctor|sync`,
`daily_update.sh`. No revived retired provider/`chunkyctl` commands. Long/paid
work states objective/consumer/snapshot/runtime/gates/artifact/stop/rollback;
completion = verified checkpoints, never a log line.

## 7. Tests / gates
Test-tool validity (eng_gov §5) before citing green; obsolete universe/PIT/
provider/table/retired-command greens are no evidence. Code fitness ≠ live
readiness: continuity `READY/DEGRADED/UNVERIFIED/BLOCKED` never upgraded by a
code commit. Tiers L1/L2/L3 machine-classified (`commit_tiers.yaml`); agents
cannot self-downgrade. `codegraph explore` before broad grep; `sync .` after
non-trivial code changes.

## 8. Parallel / deletion / hygiene
Serialize: architecture/truth; AGENTS/goal/docs/Moth/maps; same file/config/
table/DB/output/study; provider jobs, DuckDB writes, stage/commit/push/verdict.
Agents never revert/stage/commit peer work. Before delete:
`moth coupling --repo . --impact <name>`, `codegraph explore "<name> callers"`,
`rg -n "<name>" backend scripts docs analysis .moth`; migrate durable evidence;
no tombstones/renamed-dead/stubs/archive-of-archive. Exploration in `sandbox/`
only. Cache cleanup source-tree scoped — never recurse `.venv/`/`node_modules`/
managed runtimes (TinyShare SDK = versioned `.pyc`).

## 9. Commits
`git status --short --branch` first; peer changes stay. Stage explicit files;
never `git add .` / hard reset / broad clean / `--no-verify` / amend pushed.
Rule 10 for `.py/.yaml/.sql` or high-risk deletion. Default:
`SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"`. No push unless asked.

## 10. Done
Requirement↔evidence mapped; targeted tests current+pass; Moth/CodeGraph
current; false-greens challenged; PIT/data fixes include stale checks;
docs/AGENTS/skills/config agree; `git diff --check` passes; residue owned or
blocked; label `FIXED|PARTIAL|BLOCKED` + residual owner + next verification.
