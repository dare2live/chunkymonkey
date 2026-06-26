# ChunkyMonkey Docs Map

This is the active documentation map. The project keeps `docs/` to 10 current
markdown files; dated research, RCA, handoffs, old plans, and superseded specs
belong in `analysis/`.

The core rule is separation of concerns: `goal.md` is the compact live
controller board, `analysis/project_state_ledger.md` is the historical status
and completed-work ledger, this directory holds durable rules/contracts/specs,
and `analysis/` holds dated evidence. Do not copy the same status table into
multiple docs.

## Authority Order

Use this order when documents disagree. Runtime snapshots are useful context,
but they do not override the active contracts.

| Priority | Document | Role |
|---:|---|---|
| 1 | `../AGENTS.md` | Codex operating policy for this repo |
| 2 | `../goal.md` | Current phase objective, priority board, active blockers, and next actions |
| 3 | `chunkyctl_session_quickstart.md` | New-session startup contract |
| 4 | `PROJECT_CONSTITUTION.md` | Highest project rules and truth-source doctrine |
| 5 | `engineering_governance.md` | Engineering gates, deletion, agents, CodeGraph, complexity, provider jobs |
| 6 | `data_product_contract.md` | Data needs, lineage, profiles, UI contract |
| 7 | `strategy_validation_contract.md` | Strategy validation, Optuna/provider jobs, promotion contract |
| 8 | `MASTER_TOPLEVEL_DESIGN.md` | Global top-level design (data->factor->strategy->validation->KPI skeleton, discipline, roadmap); supersedes retired architecture_reform_context |
| 9 | `implementation_plan.md` | Durable execution order, gates, and acceptance criteria |

## Context-Only Snapshots

| Document | Role |
|---|---|
| `../analysis/project_state_ledger.md` | Historical completed-work/status ledger. Query by `rg`/`tail`; do not read start-to-finish during startup. |
| `../SESSION_HANDOFF.md` | Runtime snapshot. It may contain legacy Claude automation text; use facts only, not policy, when it conflicts with current Codex docs. |
| `../analysis/workflow_checkpoint.md` | Active-pipeline checkpoint only when it says active. Otherwise it is an inactive stub or historical evidence pointer. |
| `../analysis/handoff_*.md` | Dated handoff evidence. The latest relevant file can guide current work only when consistent with `goal.md` and active docs. |

## Active Docs

| Document | Role |
|---|---|
| `README.md` | This map, lifecycle rules, and archive ledger |
| `PROJECT_CONSTITUTION.md` | Constitution: truth sources, architecture layers, hard gates |
| `MASTER_TOPLEVEL_DESIGN.md` | Global top-level design skeleton (architecture/lineage/roadmap); supersedes retired architecture_reform_context (2026-06-15, lessons in `../analysis/project_state_ledger.md`) |
| `chunkyctl_session_quickstart.md` | Durable startup and controller workflow |
| `implementation_plan.md` | Durable execution plan; current phase board remains in `../goal.md` and completed evidence in `../analysis/project_state_ledger.md` |
| `engineering_governance.md` | Design review, CodeGraph + complexity, tests, agents, provider-job, deletion policy |
| `data_product_contract.md` | Data needs, lineage, profiles, market perception support, frontend contract |
| `strategy_validation_contract.md` | Backtest, Optuna/provider jobs, paper_sim, forward, and promotion contract |
| `chip_distribution_cyq_spec.md` | Active CYQ algorithm/detail spec for main-force profile and 主升浪 validation |
| (`zhushenglang_hunter_research_log_20260528.md`) | Deleted 2026-06-17 (污染期 V0-V16 原型 findings, 建于已删 LGBM/ensemble + 错方法论; 教训已 codify 进 CLAUDE §4.5 + 契约; git history) |

## 状态标头契约 (2026-06-12 新增, 机器执法)

新旧文档混用误导的机械防线。默认语义 + 例外标头 + 执法器:

- **默认语义**: `docs/` 与控制面 (`goal.md`/`CLAUDE.md`/`AGENTS.md`/`PROJECT_INDEX.md`) = live;
  `analysis/` = 按日期冻结的证据 (evidence-frozen), 只新增不改写。
- **例外必须声明** (文件前 10 行): `> 状态: live` (analysis 里的活文档如 ledger/作战图) /
  `> 状态: superseded-by: <现行文件路径>` / `> 状态: retired`。
- **引用纪律**: 控制面引用的 analysis 文件必须存在 (幽灵引用 = FAIL); 引用 retired/superseded
  文件仅限历史叙述 (执法器 WARN, 当 owner 引用须改指现行文件)。
- **执法器**: `backend/scripts/check_doc_governance.py` (C1 goal 行数 / C2 docs 文件数 /
  C3 幽灵引用 / C4 superseded 断链 / C5 退役引用), 已入 moth claims 弹仓 —
  `moth assert` / `sherpa takeover` 每次自动跑, 文档腐烂当天可见。

## Lifecycle Rules

| Case | Action |
|---|---|
| Current objective, priorities, active blockers, next work | Update `../goal.md` |
| Completed work, historical state, detailed validation evidence | Append or move to `../analysis/project_state_ledger.md` or a dated `../analysis/` artifact |
| Durable rule/design contract | Keep in one of the 10 active docs |
| Execution order, phase boundaries, acceptance criteria | Update `implementation_plan.md` |
| New topic that fits an active owner | Extend the owner doc instead of creating a new doc |
| Dated evidence, RCA, old prompt, session plan, superseded spec | Move to `../analysis/`; do not list as active authority |
| Verified obsolete content | Delete for real after CodeGraph/`rg`/test evidence |
| Need to add a new active doc | Merge/archive/delete another active doc in the same slice; net count must stay <=10 |

## Content Rules

| Rule | Reason |
|---|---|
| Active docs should be stable enough to survive a new session | Avoid stale 2026-xx status misleading future work |
| Put only the latest accepted gate summary in `goal.md`; detailed PASS/WARN/FAIL evidence goes to the ledger or dated artifacts | Prevent duplicated status drift and startup bloat |
| Put old but useful details in `analysis/` with a dated filename | Preserve evidence without treating it as policy |
| Do not keep obsolete text as comments, disabled sections, or "for later" docs | Residue becomes false authority |
| Large research logs may stay active only when they are a named north star/spec and clearly marked non-production proof | Keeps 主升浪/CYQ usable without overclaiming |

## Active Gate

Doc-drift gate (活索引引用已删代码检测) — the current authoritative gate
(`audit_docs_graph.py` was retired in the 2026-06-16 reset):

```bash
PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check
```

For controller-facing docs cleanup readiness, use:

```bash
scripts/chunkyctl docs --format markdown
```

Current target:

| Metric | Required |
|---|---:|
| `docs/*.md` | <=10 |
| Unlisted docs | 0 |
| Unresolved live refs | 0 |
| Missing cleanup archive targets | 0 |
| Forbidden authority cycles | 0 |
| Archive content status | Report exact/changed/no-HEAD/skipped counts |

## Consolidation Map

| New active doc | Superseded / archived docs |
|---|---|
| `engineering_governance.md` | Former top-level design, test-tool, agent-parallel, tooling, provider-job, and deprecation docs |
| `data_product_contract.md` | Former lineage, profile, technical, incremental, market-perception, stock-graph, UI, and trade-date migration docs |
| `strategy_validation_contract.md` | Former backtester, leakage, paper-sim, forward, phase4, retrain, SUE, MSAF, and stock-scheme docs |
| `implementation_plan.md` | Current durable roadmap only; detailed slice progress moved back to `../goal.md` |
| `architecture_reform_context.md` | Deleted 2026-06-15 (A6); 300616 rationale + architecture-layer doctrine superseded by `MASTER_TOPLEVEL_DESIGN.md` + `PROJECT_CONSTITUTION.md`; lessons in `../analysis/project_state_ledger.md` (git history) |

## Recent Cleanup Ledger

Archive notes:

- `Archived as/under` means the target exists; archive integrity is currently
  maintained manually (the former `audit_docs_graph.py` checker was retired in
  the 2026-06-16 reset; `check_doc_drift.py` covers live-index dangling refs).
- Some archives intentionally normalize stale `docs/...` references to
  `analysis/...` or add a short historical/status note. Treat archived files as
  evidence, not current operating authority.
- The root moves `PLAN_V3.md`, `DATA_INTEGRITY_AUDIT_20260517.md`, and
  `市场感知开发计划.md` were content-hash checked against `HEAD` and match exactly.

| Former file | Current state |
|---|---|
| `PLAN_V3.md` | Archived as `../analysis/plan_v3_20260514_archived.md` |
| `DATA_INTEGRITY_AUDIT_20260517.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `市场感知开发计划.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/implementation_plan_20260611.md` | Deleted 2026-06-15 (A6); 2026-06-11 checkup findings 多已被地基-reset 偏离, active plan in `implementation_plan.md` + `../goal.md` (git history) |
| `docs/cron_automation_breakage_rca_20260529.md` | Archived as `../analysis/cron_automation_breakage_rca_20260529.md` |
| `docs/market_perception_codex_prompt.md` | Deleted; superseded by `chunkyctl_session_quickstart.md`, `goal.md`, and active contracts |
| `../goal.md` 2026-05-24 and earlier sections | Archived as `../analysis/goal_legacy_20260531.md` |
| `docs/feasibility_analysis_20260517.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/first_principles_diagnosis_20260517.md` | Deleted 2026-06-15 (A6); 针对已删模型/特征/serving 层, 地基-reset 偏离 (git history) |
| `docs/v4_panel_feature_audit_20260517.md` | Deleted 2026-06-17 (污染期 v4 panel 特征审计, 已删 apparatus; git history) |
| `docs/MASTER_SYNTHESIS_20260523.md` | Deleted 2026-06-17 (污染期已删 model/ensemble/寻优层验证 findings; git history) |
| `docs/project_synthesis_20260523.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/project_audit_20260523.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/integration_master_plan_20260523.md` | Deleted 2026-06-17 (污染期 Track A/B 整合计划, 针对已删模型层; git history) |
| `docs/optimization_plan_consolidated_20260523.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/chunkymonkey_architecture_audit_20260517.md` | Deleted 2026-06-15 (A6); 针对已删架构层, 地基-reset 偏离 (git history) |
| `docs/codegraph_audit_integration_spec.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/gcp_reliability_root_cause_fix.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/modularization_refactor_plan.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/structured_complexity_audit_20260520.md` | Deleted 2026-06-26 (doc治理: pre-reset stale, git history) |
| `docs/agent_parallel_execution_policy.md` | Archived as `../analysis/docs_archive_20260531/agent_parallel_execution_policy.md`; active rules in `engineering_governance.md` |
| `docs/top_level_design_review.md` | Archived as `../analysis/docs_archive_20260531/top_level_design_review.md`; active rules in `engineering_governance.md` |
| `docs/test_tool_governance.md` | Archived as `../analysis/docs_archive_20260531/test_tool_governance.md`; active rules in `engineering_governance.md` |
| `docs/tooling_update_review_20260527.md` | Archived as `../analysis/docs_archive_20260531/tooling_update_review_20260527.md`; active rules in `engineering_governance.md` |
| `docs/gcp_controlled_execution_runbook.md` | Archived as `../analysis/docs_archive_20260531/gcp_controlled_execution_runbook.md`; historical evidence only |
| `docs/deprecation_sop.md` | Archived as `../analysis/docs_archive_20260531/deprecation_sop.md`; active rules in `engineering_governance.md` |
| `docs/data_lineage_spec.md` | Archived as `../analysis/docs_archive_20260531/data_lineage_spec.md`; active rules in `data_product_contract.md` |
| `docs/profile_lineage_roadmap.md` | Archived as `../analysis/docs_archive_20260531/profile_lineage_roadmap.md`; active rules in `data_product_contract.md` |
| `docs/technical_specification.md` | Archived as `../analysis/docs_archive_20260531/technical_specification.md`; active rules in `data_product_contract.md` |
| `docs/incremental_management_spec.md` | Archived as `../analysis/docs_archive_20260531/incremental_management_spec.md`; active rules in `data_product_contract.md` |
| `docs/market_perception_*` | Archived under `../analysis/docs_archive_20260531/`; active support contract in `data_product_contract.md` |
| `docs/market_regime_framework.md` | Archived as `../analysis/docs_archive_20260531/market_regime_framework.md`; active support contract in `data_product_contract.md` |
| `docs/block_trade_alpha_spec.md` | Archived as `../analysis/docs_archive_20260531/block_trade_alpha_spec.md`; active support contract in `data_product_contract.md` |
| `docs/stock_relationship_graph_spec.md` | Archived as `../analysis/docs_archive_20260531/stock_relationship_graph_spec.md`; active support contract in `data_product_contract.md` |
| `docs/ui_ux_interaction_plan.md` | Archived as `../analysis/docs_archive_20260531/ui_ux_interaction_plan.md`; active frontend contract in `data_product_contract.md` |
| `docs/backtester_mcp_integration_20260517.md` | Archived as `../analysis/docs_archive_20260531/backtester_mcp_integration_20260517.md`; active rules in `strategy_validation_contract.md` |
| `docs/leakage_pattern_catalog.md` | Archived as `../analysis/docs_archive_20260531/leakage_pattern_catalog.md`; active rules in `strategy_validation_contract.md` |
| `docs/paper_sim_*` | Archived under `../analysis/docs_archive_20260531/`; active rules in `strategy_validation_contract.md` |
| `docs/v7_forward_decision_framework.md` | Deleted 2026-06-17 (污染期 v7 forward 框架; active rules 在 strategy_validation_contract.md; git history) |
| `docs/phase4_alpha_root_cause_roadmap.md` | Archived as `../analysis/docs_archive_20260531/phase4_alpha_root_cause_roadmap.md`; active rules in `strategy_validation_contract.md` |
| `docs/retrain_stall_fix1_patch_draft.md` | Archived as `../analysis/docs_archive_20260531/retrain_stall_fix1_patch_draft.md`; active rules in `strategy_validation_contract.md` |
| `docs/sue_pit_design_20260517.md` | Archived as `../analysis/docs_archive_20260531/sue_pit_design_20260517.md`; active rules in `strategy_validation_contract.md` |
| `docs/msaf_*` | Archived under `../analysis/docs_archive_20260531/`; historical strategy research only |
| `docs/only_stock_scheme_design_20260517.md` | Archived as `../analysis/docs_archive_20260531/only_stock_scheme_design_20260517.md`; historical strategy research only |
