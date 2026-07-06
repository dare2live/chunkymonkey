# Engineering Governance Contract

> **部分 stale (2026-06-20)**: 本文引用的 `audit_*` 治理审计套件 (`audit_execution_surface` /
> `audit_test_tool_health` / `audit_docs_graph` / `audit_storage_payloads` /
> `audit_portfolio_sizer_profile_attrition`) **在 2026-06-16 reset 已删**。**当前活跃门** =
> `data_layer_audit.py` / `check_doc_drift.py` / `check_rule_compliance.py` /
> `check_legacy_flow_integrity.py` / `check_strategy_validation_integrity.py` / `moth assert` /
> `moth coupling` / codegraph+complexity 配对扫 (详见本文 codegraph 节 + chunkymonkey-ops skill §3)。
> 下方表中 `audit_*.py` 命令为历史参考, 勿直接运行 (已悬空)。本文规则原则 (最小模块/无 god-file/
> 删层必删 caller 等) 仍有效。

This is the active engineering rulebook for architecture work after the docs
consolidation. It absorbs the durable rules from the former top-level design,
test-tool, agent-parallel, tooling, provider-job, and deprecation docs.
Historical source files are archived under `analysis/docs_archive_20260531/`.

## Risk First

| Risk | Rule |
|---|---|
| Pretty backtests replacing production evidence | Metrics are `unknown` until measured by current gates |
| Old docs or chat memory steering work | `goal.md`, `SESSION_HANDOFF.md`, `analysis/workflow_checkpoint.md`, and this docs set are the current surface |
| Legacy Claude policy steering Codex | `CLAUDE.md` is ignored by default; Codex uses `AGENTS.md`, active docs, skills, and live tooling unless the user explicitly asks for historical migration |
| Hidden complexity and stale indexes | CodeGraph and complexity optimizer are a paired gate |
| Green tests proving old assumptions | Run test-tool health before citing tests as evidence |
| Provider spend or dirty provider artifacts | Long or paid compute must be a registered `experiment_jobs` plan with gates and artifact contracts; do not revive deleted provider scripts |
| Automation points at deleted scripts | `check_dead_references.py` must PASS (2026-06-28 起替代已删 `audit_execution_surface.py`); launchd, cron, installers, dashboards, registries, and Moth evidence paths cannot reference missing or retired entrypoints |

## Skill Owner Map

| Scope | Required skill | Rule |
|---|---|---|
| Codex Mac app/CLI, plugin sync, startup items, hooks, local automations, compact errors, Codex worktrees, Terminal mail, or monitor residue | `$codex-local-ops` | Diagnose local Codex state first; do not misclassify it as a project hook or business gate |
| Non-trivial ChunkyMonkey execution, architecture, strategy validation, PIT/leakage, Optuna/provider jobs, deletion, or gate-policy work | `$chunkymonkey-governance` | Run pre-execution governance before edits or expensive commands |
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
For `portfolio_sizer` threshold tuning specifically, the former
`audit_portfolio_sizer_profile_attrition.py` was retired in the 2026-06-16
reset; produce an equivalent attrition summary manually and treat
it as the required evidence gate before changing
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
| Registry | (test_tool_registry.yaml 2026-06-28 退役: 0 代码消费 + 82% 引已删测试=烂登记从未强制; 测试覆盖由 CI offline 列表 + pytest 直管) |
| Scope | Unit/contract/integration/realdb/perf/network/gcp scope must match the command |
| Truth source | K-line for tradeability, calendar for dates, `universe_rules.yaml` for board/limit rules |
| `dim_active_a_stock` | Code-to-name/cache fixtures only, never active-universe truth |
| DB fixtures | Prefer DuckDB memory fixtures unless the test explicitly owns a real DB exception |

Primary command:

```bash
# retired (2026-06-16 reset): audit_test_tool_health.py 已删; 现手动 pre-test 检查 + 工作日志声明 (见 AGENTS.md)
```

## Controller / Agent Mode

| Role | Responsibility |
|---|---|
| Controller Codex | Direction, scope, gates, architecture decisions, final merge, shared docs, and immediate critical-path work |
| Default parallelism | Spawn bounded assistant agents by default for independent sidecar investigation unless the user explicitly says not to parallelize, the tool is unavailable, or the work is tightly coupled |
| Read-only agents | Discovery, graph queries, data inventory, stage-opt/need coverage/storage triage, complexity triage, RCA evidence |
| Worker agents | Bounded implementation only inside explicitly disjoint file scopes; never revert controller or peer work |
| `chunkyctl preflight` controller-agent gate | Broad audit/research/architecture/data/debug or 3+ scope tasks must provide `--agent-dispatch` evidence; missing evidence is `controller_agent_dispatch_missing` FAIL, while an explicit `--agent-skip-reason` is a WARN exception |

Parallelize read-only work and independent tests. DuckDB-heavy audits may run in
parallel only when they use explicit read-only connections. Serialize shared
docs, commits, provider jobs, materializing/write DuckDB scripts, shared output paths,
Optuna studies, and edits to the same file.

## Moth Ownership

Moth owns shared tooling state for this repo: profile discovery, evidence paths,
policy-source boundaries, dirty worktree status, CodeGraph freshness, and
complexity baseline/current summaries. The active repo-local profile is
`.moth/profile.yaml`. It may also list local Codex skill files as evidence paths
so new sessions can find the local-ops, governance, Rule 10, and `CLAUDE.md`
ignore contracts without relying on chat memory.

ChunkyMonkey owns business gate logic. Keep `stage_opt`, `need_027`,
`storage_payload`, `data_health`, universe truth-source rules, and test-tool
validity rules in repo audit scripts, config, and `chunkyctl`; Moth may read or
surface their generated evidence, but should not redefine their rules.

## Compute Backend / Experiment Jobs

Legacy project-local GCP execution entrypoints were removed on 2026-06-05. That
retired provider is historical evidence only, not a path to revive with
comments, guards, hidden flags, or compatibility shims.

The active contract for data validation, backtest validation, model training,
and parameter search is `backend/config/experiment_jobs.yaml`, surfaced through:

```bash
scripts/chunkyctl jobs --family <job-family> --backend local
```

`local` is the only active backend. `modal` is a planned backend and must stay
blocked until a reviewed adapter proves its artifact-manifest contract. Provider
adapters may execute commands and materialize manifests; business gates remain
owned by ChunkyMonkey config, services, scripts, and tables.

`chunkyctl jobs` must not report a family as ready to run unless the operator
supplies an input snapshot, objective, rollback plan, and one
`--gate-evidence <gate>=<artifact-or-command>` token for every required gate in
the family contract. A backend being `active` only means the backend is
permitted; it is not launch approval.

## Deletion And Deprecation

Verified-dead code, tests, docs, and data artifacts should be deleted for real,
not hidden behind comments, renamed files, disabled branches, or empty stubs.
Before deletion, use CodeGraph plus `rg` to check references and run the
narrowest relevant audit/test. If evidence is historical but still useful, move
it to `analysis/`; if it is not useful, delete it.

### 退役标准动作清单 (2026-07-06 根因根治)

> 触发: 全面数据审计 (`analysis/comprehensive_data_module_audit_20260706.md`) 诊断"残留
> 反复出现"= **同一类系统性缺口反复触发**, 非偶然: 3 个历史案例 (`check_panel_lineage.py`
> 44 天未被发现死引用已删表 / `schema_versions.py` 156 个死版本累积到 91% 才一次性清 /
> `test_tool_registry.yaml` 0 消费+82%死引用) + 1 个当场活案例 (`check_continuity_integrity.py`
> 本身刚加入就未同批注册), 结构完全相同: **退役类改动没有标准化 checklist, 每次由执行者
> 临时决定清理范围**, 而清理范围的定义本身结构性排除了旁支消费者 (治理脚本 SQL 字符串引用/
> schema 版本注册表/测试工具注册表)。

任何"删表 / 删模块 / 删脚本 / 退役子系统"类改动, commit 前必须逐项确认下列 **5 类消费者**
都已同批检查 (答不出 = 别删, 先查):

| # | 类别 | 查法 | 机器门 |
|---|---|---|---|
| 1 | `backend/services/` `backend/routers/` 代码引用 (import/调用) | `rg -l "<模块名\|表名>" backend/services backend/routers` | `check_dead_references.py` A/B 扫 |
| 2 | `backend/config/*.yaml` 注册 (路径字面量/module=字面量) | `rg -l "<名称>" backend/config` | `check_dead_references.py` C/D 扫 |
| 3 | `backend/scripts/check_*.py` 等治理/审计脚本的 **SQL 字符串**引用 (`FROM`/`JOIN` 内联表名, 不是 import) | `rg -l "<表名>" backend/scripts` | `check_dead_references.py` **E 扫** (2026-07-06 新增, 专治这道盲区) |
| 4 | `backend/tests/` fixture / 测试直连表名 | `rg -l "<名称>" backend/tests` | 无专门机器门 (靠跑测试红) |
| 5 | `docs/` `analysis/` 文档引用 (历史记录可以留, 但当前生效文档不能悬空指已删对象) | `rg -l "<名称>" docs analysis` | `check_doc_drift.py` (部分覆盖, `analysis/` 按设计排除在扫描外) |

**新增 `check_*.py` 治理脚本的强制义务**: 任何新脚本落地的同一个 commit 里, 必须二选一
接入 `scripts/safe_commit.sh` (真正影响正确性的用 hard-block) 或 `.github/workflows/ci.yml`
(至少 WARN 级曝光) —— 否则该脚本自己就是下一个"登记但从未强制"的重演 (2026-07-06 审计
当场抓到 `check_continuity_integrity.py` 正在犯这个错误, 已随本次一并接入)。

**清理范围的定义本身要包含旁支消费者**: 历史退役 commit 抽查 (`9b82d943`/`639e0dfb`) 显示
diff 老实对应 commit message 声称的范围, **问题不是清理时偷懒漏做, 而是"这次要清的东西"
这个概念从一开始就没把 `backend/scripts/check_*.py` 这类治理脚本纳入盘点对象**——所以
第 3 类必须显式列进每次退役的检查清单, 不能靠"反正 fan-in 审计会扫到"的默认假设。

## Exploration Sandbox (隔离探索区, owner: `sandbox/README.md`)

2026-06-17 用户根治: ephemeral 探索 (一次性 runner / findings 草稿 / 中间结果 / scratch
数据) **只住 `sandbox/`**, 绝不进 `backend/scripts/`、`analysis/`、`docs/` 或主 DB —— 否则
探索散进主代码/文档 = 反复污染 = 反复大清理 (这是本次 ~100 文件清理的根因)。

| 规则 | 机制 |
|---|---|
| 探索脚本/草稿/结果 → `sandbox/<exp>/`, scratch 数据 → `sandbox/scratch.duckdb` (主 6 库只读) | `bash scripts/sandbox.sh new/list/wipe/wipe-all/check` |
| `sandbox/` gitignored (除 README) — 探索进不了 git/主代码 | `.gitignore: sandbox/*` + `!sandbox/README.md` |
| 探索 runner (`experiment_*`/`analyze_*`) 不许在 `backend/scripts/` | moth `exploration-isolated-in-sandbox` (==0) |
| 用完直接删, 主代码/文档/git 0 残留 | `scripts/sandbox.sh wipe-all` |
| 唯一跨删存活 = 裁决 → `experiment_store.duckdb` (record_verdict); 真 edge 才 promote (干净重写进 `backend/services/` + 单测, 非 copy) | `services.experiment_store` + `experiment_harness` |

## Active Tool Commands

| Purpose | Command |
|---|---|
| Startup health | `scripts/chunkyctl doctor --fast` |
| System data health snapshot | `PYTHONPATH=backend python backend/scripts/data_health_snapshot.py --dry-run --format text` (reports `writer_prompt` / owner / sync_step hints in red/yellow rows; use `--format json` for machine consumers) |
| Dirty worktree buckets | `scripts/chunkyctl worktree --format markdown` |
| Docs cleanup slice | `scripts/chunkyctl docs --format markdown` |
| Storage payload / recursive JSON audit | retired (2026-06-16 reset; `audit_storage_payloads.py` 已删, 无直接替代) |
| Pre-task gate | `scripts/chunkyctl preflight "<task>" path/to/scope.py --agent-dispatch "agent:NAME scope/evidence"` |
| Experiment job plan | `scripts/chunkyctl jobs --family <job-family> --backend local --input-snapshot <snapshot> --objective <why> --rollback-plan <plan> --gate-evidence <gate>=<artifact>` |
| Docs graph / 文档漂移 | `PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check` (replaces retired `audit_docs_graph.py`) |
| Execution surface | `moth coupling --repo . --impact <name>` (replaces retired `audit_execution_surface.py`) |
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
| `../analysis/docs_archive_20260531/gcp_controlled_execution_runbook.md` | Archived historical evidence; active provider work now uses `experiment_jobs` |
| `../analysis/docs_archive_20260531/deprecation_sop.md` | Archived; deletion/deprecation rule consolidated here |
