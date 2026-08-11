# ChunkyMonkey Agent Policy

Authority: `AGENTS.md` → `goal.md` → `docs/README.md` owners
(`MASTER_TOPLEVEL_DESIGN.md`, `strategy_validation_contract.md`,
`engineering_governance.md`) → Codex skills → live tooling/data.
`CLAUDE.md` legacy (explicit historical request only). Board = **live projection**
(`chunkyctl status` / `agent-boot`; zero files since P2.3), never enforcement.
History: `chunkyctl history --grep <term>` (git log) / `--eras` (annotated tags).

## 1. Boot
1. Read `goal.md`.
2. `scripts/chunkyctl agent-boot` (git + Moth summary + CodeGraph + board +
   §15 knife-merge reminder). Preserve user/peer worktree changes.
3. Read the one owner doc named by `docs/README.md` for the task.
4. Verify drift-prone facts with live code / read-only DB/API / current commands.
Full evidence: `git status --short --branch`, `moth snapshot --repo .`,
`codegraph status .` / `explore`. Always `cd` target checkout + `--repo .`
(Moth 0.3.0 cwd; `safe_commit.sh` enforces for staged snapshots).
**Delivery (eng_gov §15 binding)**: one logical knife = one Rule 10 + one
`safe_commit`; async CI (no sync `gh run watch`); L3 knives run
`chunkyctl pre-knife <name>` first; parallel agents only when moth proves
non-overlap. Never loosen L3 / Rule10 / PIT / ≤40d.

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
本节曾把 Tier 0 语义抄一遍；15 条里 12 条是 `MASTER §5.1/§6.1` 的近逐字副本 —— 两份副本
只会各自漂移，出分歧时没人知道哪份算数。**语义归 MASTER，这里只留 agent 要做的动作。**

- **契约本身**：population scope 三类 / availability `axis/rule/at` / `trigger_mode` 与
  `same_day_at` / 一次执行一份 immutable contract / 拒未来分区 / `stage→validate→publish→
  accepted_partition` / watermark 是投影不是第二套写面 —— 全部见 `docs/MASTER_TOPLEVEL_DESIGN.md`
  §5.1 与 §6.1。**动 Tier 0 前读那两节，不要读本节的转述。**
- **调用失败分类**：0 行 / 空响应 / 权限页 / 字段缺失 / **超时** / **连接失败** 必须分开归类，
  不得用 0 行冒充成功 —— 见 `docs/engineering_governance.md` §6。注意它与 MASTER §5.1 的
  「伪证据清单」**不是同一份**：那份管“什么证据不配进 accepted”，这份管“这次调用为什么没拿到数据”。
- **审计姿势**：DuckDB 审计默认 `read_only=True`，写入串行 —— 同 §6。
- 改完数据 / PIT / schema / cache：跑 `$post-fix-audit`。

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
Rule 10 = independent-review **discipline** for `.py/.yaml/.sql`/high-risk
deletion; the msg gate blocks only an explicit `REQUEST_CHANGES` — a
self-written `APPROVE` proves nothing and no longer blocks (2026-08-10).
Safety rests on gates that read code/data, never on wording. Gate distribution
(`governance_gates.yaml`, eng_gov §14.1) is orthogonal to tier: `diff_correctness`
blocks; `system_health` runs in `daily_update`, not at commit; `scaffold` warns
only (`chunkyctl scaffold-fix`). Bad policy file → all gates block. Default:
`SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"`. No push unless asked.

## 10. Done
Requirement↔evidence mapped; targeted tests current+pass; Moth/CodeGraph
current; false-greens challenged; PIT/data fixes include stale checks;
docs/AGENTS/skills/config agree; `git diff --check` passes; residue owned or
blocked; label `FIXED|PARTIAL|BLOCKED` + residual owner + next verification.
