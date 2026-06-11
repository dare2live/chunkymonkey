# ChunkyMonkey Goal

> Current phase contract and implementation plan only. Keep this file compact:
> current objective, active priorities, live gates, and long-term direction.
> Completed work and detailed evidence belong in
> `analysis/project_state_ledger.md`; generated resume state belongs in
> `SESSION_HANDOFF.md`; pipeline artifact checkpoints belong in
> `analysis/workflow_checkpoint.md`.

## Document Contract

| Document | Owns | Startup use |
|---|---|---|
| `goal.md` | Current phase objective, priority board, implementation plan, long-term roadmap | Read first |
| `analysis/project_state_ledger.md` | Completed work, historical status, evidence notes formerly appended to `goal.md` | Query with `rg` or `tail`, not full startup read |
| `SESSION_HANDOFF.md` | Generated/manual runtime resume snapshot | Context-only; verify with live gates |
| `analysis/workflow_checkpoint.md` | Retired 2026-06-11 (inactive since 2026-06-05); physical removal tracked as P2 slice in `docs/implementation_plan.md` | Do not read for resume; historical evidence only |
| `docs/chunkyctl_session_quickstart.md` | Startup procedure and controller workflow | Startup contract |
| `docs/README.md` | Active docs map and ownership rules | Docs authority map |

Update rule: if an item is done, move the evidence to
`analysis/project_state_ledger.md` and keep only the resulting current decision
or blocker here. `goal.md` should stay under roughly 150 lines unless the active
phase genuinely needs a larger plan.

## North-Star KPI (唯一 owner: 本文件; 其他文档只指针引用)

| 指标 | 目标 | 口径 |
|---|---|---|
| 年化收益 | >= +30% | 含成本 OOS paper_sim, 2023-01-03 起 100 万初始 |
| 最大回撤 | >= -20% | 同上 |
| 超额 vs HS300 | > 0 | 真实 HS300 基准 (基准=0 的结果无效) |
| 月胜率 | >= 55% | walk-forward OOS 月度胜率分布, 不是均值 |

当前实测状态 (2026-06-11 体检): 无任何同时满足四项、含成本、gate 通过且数据新鲜的数字;
最近可信 artifact 均为 2026-05-23 前。正确答案是 `unknown`, 修复路径见
`docs/implementation_plan.md` Active Repair Plan。

## Strategy Portfolio (2026-06-11 策略锻造定稿)

Owner: `analysis/strategy_portfolio_20260611.md` (三套组合 + 12 周路线图 + 13 条全局纪律件;
三评委 judge panel 定稿: B 回调增强 8.0 主书 / D v7 排序 7.6 公共增强层 / C 题材扩散 6.0
降级数据采集)。宪法 v2 草案: `analysis/constitution_v2_draft_20260611.md` (13 条全带反例,
待用户最终确认后替换 docs/PROJECT_CONSTITUTION.md)。
当前周: **W1** — E0 双口径收敛 / need_027 余 5 gates / E1 B-V0 零数据分桶 / E7 快照自养 /
E8 四接口探底 / E4 limit_list_d 等回填。验证期 (W1-W12) 新策略 0 真金白银, 全部 paper_sim 候选态。

## Current Phase

**Phase:** architecture/data governance foundation before deeper strategy work.

**Objective:** make the project controllable and auditable before expanding
data sources, provider compute, model search, or production promotion.

**Controller rule:** Codex owns direction, truth-source decisions, shared docs,
gates, staging, commits, and risky write windows. Side agents provide bounded
evidence or disjoint patches; their output is not a verdict.

**Commit ownership (2026-06-11 裁决, 双轨合法):** Codex 会话按上行规则;
Claude controller 会话直接走 `scripts/safe_commit.sh` 现行契约 (message 满足
`Codex-Reviewed:` 或 `codex-review: skipped reason=<...>` + self-check fallback)。
两轨共用同一套 pre-commit hook, 不另开第三条路。

## Active Priority Board

| Priority | Workstream | Current state | Next action |
|---|---|---|---|
| P0 | Documentation control plane | Thin `goal.md` / ledger split committed in `8371e60c` | Keep current-state facts here; put completed evidence in `analysis/project_state_ledger.md` |
| P0 | Provider-neutral execution surface | Retired GCP execution surface removed and `experiment_jobs` contract committed in `8371e60c` | Treat `local` as active and `modal` as planned/blocked until adapter gates exist |
| P0 | Dirty worktree governance | Clean after `3e9fafc8`; `worktree` gate reports `unknown=0` | Keep future commits slice-based; never `git add .` |
| P0 | Platform reliability convergence | Three-track audit done 2026-06-11 (`analysis/platform_top_level_design_20260611.md`); Platform Runtime Contract adopted into `docs/data_product_contract.md`. Verified gaps: 12+ WARN-and-continue swallow steps in `scripts/daily_update.sh`, 7 sources without watermark, failure_queue has writers but no drain consumer, 4 `except Exception: pass` in backfill scripts, frontend silent mock fallback plus 10+ hardcoded thresholds | **Done 2026-06-11 evening**: failure-level semantics live (29 swallow points → `step_degraded` + ALERT flag + end-of-chain notification) and calendar-gap drain live (`sync_runner --drain`, completeness-aware via min_rows, newest-first truncation, non-daily domains fall back to incremental run_domain; 16 unit tests + live lock-path probe exit 1). Registry-derived SLA audit also live: `update_watermark_sla.py` auto-generates `sync:*` probes from sync_registry.yaml with explicit NO_PROBE_RULE / NO_QUERY_MAPPING / DB_LOCKED_UNVERIFIED / NEVER_SYNCED states (no more silent-OK); first live dry-run caught real rot: `lhb_daily` stale 13d (aif10 path) and `xdxr` stale 17d (dividend season — affects adjustment correctness). Remaining: root-cause xdxr staleness, migrate legacy sources to tushare registry (top_list/top_inst/stk_surv/report_rc/ths_hot/moneyflow_hsgt all within 10000 points), dead-dates dampening, doctor ALERT-flag check; frontend rescue trio as separate batch |
| P1 | `need_027` exact order-flow | **No-persist gate PASS (2026-06-11)**: TuShare 3/3 probes ok via vendor gateway (`TUSHARE_HTTP_URL`, token in local `.env`, tushare 1.4.29 in project `.venv`), `selected_source=tushare`, `field_mapping`/`date_coverage` pass; AkShare group still `akshare_remote_disconnected=3`; vendor gateway shows intermittent empty responses (~15s, retry succeeds) so writers must treat 0-row as failure | Complete the 5 `required` post-probe gates (`pit_key`/`freshness_sla`/`writer`/`watermark`/`failure_queue_resolution`) per `docs/implementation_plan.md` Active Repair Plan Phase 1; separately restore AkShare source stability or formally retire it from need_027 candidates (open since 2026-06-06) |
| P1 | Data-source capability routing | Capability contracts committed in `3e9fafc8`; TuShare `moneyflow` adapter/gate wiring is local no-persist probe scope only; iFinD MCP is a research-only semantic/industry-chain snapshot candidate, not a行情 or exact-flow production replacement; 2026-06-11 live probes passed with user-provided token (initialize / tools-list / `sector_data` real data), token kept in local `.env` only, 3 servers registered local-scope for research sessions; concept add/drop event-stream design in `analysis/concept_event_chain_mining_20260611.md` | Keep new providers in capability-level probe mode before any DB writer or provider promotion; route iFinD MCP through daily PIT snapshot contracts for sector/theme/news/notice evidence; implement `fact_concept_event` after concept-domain backfill lands |
| P1 | DB retention/modularization | Capacity pressure is internal table/version/cache overlap inside `smartmoney.duckdb`, not external NO2/backup/snapshot files; retention inventory has owner/consumer/truth/compact contracts and no executable delete candidates | Migrate or retire panel/cache consumers before any cleanup; keep production delete/VACUUM blocked unless copied-DuckDB validation and manifests exist |
| P1 | Stage-opt supply | Freshness repair is complete through trusted K-line max `2026-06-04`: `fact_signal_context`, `fact_technical_trigger`, and `mart_macd_state_history` all max at `2026-06-04`; latest 5-K-line-day audit has `source_freshness_warnings=0`, default readiness `3010/23661=12.72%`, reversal-only readiness `1401/9218=15.2%`; YTD reversal-only readiness is nonzero (`21530` ready keys / `62.7%`) | Treat remaining stage-opt blocker as upstream signal-density / state-source design, not stale tables; do not tune formula thresholds again until a no-persist source/state POC proves useful PIT candidate supply |
| P2 | Microsoft RD-Agent(Q) research | Tracked as follow-up only; no production integration approved | Create a dated `analysis/rd_agent_q_research_*.md` deep-dive note mapping RD-Agent(Q)/Qlib reusable components, Co-STEER feedback, factor mining, report-to-code flow, experiment loop, agent role split, and experiment-manager contracts into borrow/reject/POC decisions under ChunkyMonkey gates |
| P2 | Data-health warning-only assets | No red or blocking-yellow in latest live doctor; 7 warning-quality tables remain yellow | Treat as owner-specific maintenance, not startup blockers |

## Latest Live Gate Snapshot

As of the latest checked state, with full doctor/stage-opt evidence and focused
`need_027` source-probe evidence from 2026-06-06:

| Gate | Current result | Meaning |
|---|---|---|
| `scripts/chunkyctl doctor --fast` | `WARN` | `need_027` remains blocked and `data_health` has 7 warning-only tables; execution surface, Moth, universe, worktree, and stage-opt freshness are clear |
| `scripts/chunkyctl worktree --format markdown` | Use live gate before staging | Latest pre-commit check had `unknown=0`; keep future commits slice-based |
| `moth snapshot --repo . --format markdown` | CodeGraph `PASS` | CodeGraph is up to date; complexity diff has `new_high_count=0` |
| `data_health_snapshot.py --dry-run` | `green=335 / yellow=7 / red=0 / blocking_yellow=0` | No startup data-health blocker; yellow assets are maintenance debt |
| `plan_storage_retention.py` | `candidate_count=0 / table_inventory_count=12 / policy_contract=PASS / compaction.recommended=false` | Contract is complete, but no production delete/VACUUM path is open |
| `audit_storage_retention_consumers.py` | `PASS / audited_tables=11 / runtime_ref_tables=11` | Cleanup candidates are protected by explicit consumer evidence instead of unknown placeholders |
| `audit_execution_surface.py --include-live-launchd` | `PASS / 0 findings` | Retired execution-surface references are not currently detected |
| `need_coverage` | `need_027` no-persist gate **PASS** (2026-06-11) | TuShare source group `ok` 3/3 (`selected_source=tushare`); AkShare group still `akshare_remote_disconnected=3`; `field_mapping`/`date_coverage` pass; remaining `required`: `pit_key`/`freshness_sla`/`writer`/`watermark`/`failure_queue_resolution` before production writes |
| `audit_stage_opt_candidate_supply.py` | Freshness clear / density low | Latest actual 5-K-line-day audit `2026-05-29..2026-06-04`: default `raw_signal_rows=54206`, `ready_keys=3010`, `ready_coverage_pct=12.72`, `source_freshness_warnings=0`; remaining blocker is `below_min_signals`, not source staleness |

Live gates override this section. Refresh before using the numbers as evidence.

## Implementation Plan

1. **Data-source contracts:** keep iFinD MCP / TuShare / tdxhub work in
   capability-scoped research/probe mode until PIT/freshness/watermark and
   no-persist probes pass. iFinD MCP is for sector/theme/news/notice/industry
   chain snapshots first; TuShare remains the structured exact-flow/batch probe
   candidate; tdxhub remains the current backbone for stable local facts.
2. **Storage/DB governance:** continue from manifest + retention dry-run toward
   owner-based retention and compact policy. No production deletion without
   consumer migration/retirement evidence, copied-DuckDB validation, manifests,
   and rollback.
3. **Strategy/model work:** resume stage-opt and model exploration only after
   upstream signal density, data-source truth contracts, and live audit gates
   are explicit. Stage-opt freshness is now repaired to trusted K-line max
   `2026-06-04`; the next stage-opt decision is whether a no-persist
   state/source POC can improve PIT candidate supply without adding another
   stale writer.
4. **RD-Agent(Q) follow-up research:** run a read-only deep dive after P0
   cleanup or in an explicitly bounded parallel research slot, with output as a
   dated `analysis/` research note. Compare the upstream RD-Agent(Q)/Qlib
   research loop, Co-STEER feedback, factor mining, report-to-code flow,
   experiment manager, and agent role split against this project's PIT,
   data-source, paper-sim, promotion, and `experiment_jobs` contracts. The
   expected result is a borrow/reject table, an integration-risk map, a mature
   component reuse list, and the smallest reversible POC plan. Mature ideas may
   be copied as isolated research tooling; no upstream framework, agent loop,
   generated factor, or model result can bypass ChunkyMonkey's truth-source,
   leakage, paper-sim, and promotion gates.

## Long-Term Roadmap

| Area | Direction |
|---|---|
| Data sources | Capability-level routing: `tdxhub` backbone, iFinD MCP semantic/industry-chain research snapshots, TuShare structured exact-flow/batch probes, each gated by PIT/freshness/watermark |
| Compute | `experiment_jobs` is the only approved job contract; `local` active, `modal` planned/blocked until adapter manifest, sandbox, cost boundary, and rollback exist |
| Research agents | Microsoft RD-Agent(Q) / Qlib ideas may feed isolated candidate generation and experiment design; production data, PIT, backtest, and promotion gates stay in ChunkyMonkey |
| Database | Manifest-owned DB aliases, read-only attached DBs by default, owner-based retention, no ad hoc scripts or hidden DB paths |
| Promotion | Measured paper sim / Phase4 / PBO / forward evidence only; in-sample or proxy metrics remain `unknown` |

## Operating Reminders

- Do not read or apply `CLAUDE.md` by default.
- Use `$architect-controller` for architecture/controller work,
  `$chunkymonkey-governance` before risky project execution, and
  `$chunkymonkey-review-gate` before commits or after `.py/.yaml/.sql` changes.
- Prefer first-principles truth sources: K-line for tradeability, calendar for
  dates, config/table/service owners for business rules.
- Remove proven-dead paths directly; do not keep obsolete code by comments,
  hidden flags, or compatibility shims.
